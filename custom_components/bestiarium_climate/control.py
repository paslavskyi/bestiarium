"""
The control of a thermostat's switch and its control state (DEC-014).

A switch or temperature gone for longer than the tolerance is a problem; a
problem ends after the hysteresis. A switch that does not take the expected
state for its attempts is deferred for the recovery timeout and tried
again. What the balancer must remember across restarts lives in a Store,
not in the thermostat's attributes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.singleton import singleton
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    BALANCING_INTERVAL_SECONDS,
    CONF_RECOVERY_TIMEOUT,
    CONF_TARGET_SWITCH,
    CONF_TEMPERATURE_SENSOR,
    DOMAIN,
    LOGGER,
    SAVE_DELAY_SECONDS,
    SIGNAL_CONTROL_CHANGED,
    STORAGE_KEY,
    STORAGE_VERSION,
    ControlSettings,
    ControlState,
)
from .util import duration_dict_to_timedelta

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import (
        CALLBACK_TYPE,
        Event,
        EventStateChangedData,
        HomeAssistant,
        State,
    )

    from .data import BestiariumClimateConfigEntry

GONE_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN)
# One attempt a balancing cycle; a little less, as the timers jitter
ATTEMPT_INTERVAL = timedelta(seconds=BALANCING_INTERVAL_SECONDS - 5)


class ThermostatMemory:
    """What the thermostats keep across restarts, by subentry id."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Start empty; call async_load to read the store."""
        self._store: Store[dict[str, dict[str, Any]]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        self._data: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        """Read the store."""
        self._data = await self._store.async_load() or {}

    def get(self, subentry_id: str) -> dict[str, Any]:
        """Return what a thermostat keeps."""
        return self._data.get(subentry_id, {})

    def update(self, subentry_id: str, **values: Any) -> None:
        """Change what a thermostat keeps."""
        self._data.setdefault(subentry_id, {}).update(values)
        self._store.async_delay_save(lambda: self._data, SAVE_DELAY_SECONDS)

    def keep_only(self, subentry_ids: set[str]) -> None:
        """Forget the thermostats that are no longer configured."""
        if gone := self._data.keys() - subentry_ids:
            for subentry_id in gone:
                del self._data[subentry_id]
            self._store.async_delay_save(lambda: self._data, SAVE_DELAY_SECONDS)


@singleton(f"{DOMAIN}_thermostat_memory", async_=True)
async def async_get_thermostat_memory(hass: HomeAssistant) -> ThermostatMemory:
    """Return the loaded memory, shared by all climate entries."""
    memory = ThermostatMemory(hass)
    await memory.async_load()
    return memory


@dataclass
class _Condition:
    """A problem as it is now (raw) and as it is confirmed."""

    raw: bool = False
    since: datetime | None = None
    confirmed: bool = False

    def set(self, raw: bool, now: datetime) -> None:  # noqa: FBT001
        if raw != self.raw:
            self.raw, self.since = raw, now

    def settle(self, now: datetime, enter: timedelta, leave: timedelta) -> None:
        if (when := self.next_change(enter, leave)) is not None and now >= when:
            self.confirmed = self.raw

    def next_change(self, enter: timedelta, leave: timedelta) -> datetime | None:
        if self.raw == self.confirmed or self.since is None:
            return None
        return self.since + (enter if self.raw else leave)


def _gone(state: State | None) -> bool:
    return state is None or state.state in GONE_STATES


def _no_temperature(state: State | None) -> bool:
    if _gone(state):
        return True
    try:
        float(state.state)
    except ValueError:
        return True
    return False


class ThermostatControl:
    """How the balancer controls one thermostat's switch."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumClimateConfigEntry,
        subentry: ConfigSubentry,
        memory: ThermostatMemory,
    ) -> None:
        """Follow the thermostat's switch and sensor; call async_start."""
        self._hass = hass
        self._entry = entry
        self.subentry_id = subentry.subentry_id
        self.name = subentry.title
        self.switch: str = subentry.data[CONF_TARGET_SWITCH]
        self._sensor: str = subentry.data[CONF_TEMPERATURE_SENSOR]
        self._memory = memory
        kept = memory.get(self.subentry_id)
        self.expected_on: bool = kept.get("expected_on", False)
        self.expected_changed_at: datetime = _time(kept.get("expected_changed_at"))
        self._tries: int = kept.get("tries", 0)
        self.deferred_until: datetime | None = (
            _time(kept["deferred_until"]) if kept.get("deferred_until") else None
        )
        self._switch_gone = _Condition()
        self._no_temperature = _Condition()
        self._unresponsive = _Condition(
            raw=self.deferred_until is not None,
            confirmed=self.deferred_until is not None,
        )
        # A restored deferral is no new problem: no second warning
        self.state = (
            ControlState.UNRESPONSIVE
            if self.deferred_until is not None
            else ControlState.NORMAL
        )
        # When the switch got its last command: an attempt is one a cycle
        self._last_command: tuple[datetime, bool] | None = None
        self._watching = False
        self._unsubscribe: list[CALLBACK_TYPE] = []
        self._cancel_timer: CALLBACK_TYPE | None = None

    @property
    def settings(self) -> ControlSettings:
        """Return the entry's control settings, read on every use."""
        return ControlSettings.of(self._entry)

    @callback
    def async_start(self) -> None:
        """Follow the switch and the sensor; their availability once HA started."""
        self._unsubscribe = [
            async_track_state_change_event(
                self._hass, [self.switch, self._sensor], self._on_state
            ),
            async_at_started(self._hass, self._on_started),
        ]

    @callback
    def async_stop(self) -> None:
        """Stop following."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        if self._cancel_timer:
            self._cancel_timer()
        self._unsubscribe, self._cancel_timer = [], None

    # What the balancer asks

    @property
    def no_temperature(self) -> bool:
        """Return whether the thermostat has had no temperature for too long."""
        return self._no_temperature.confirmed

    def in_rotation(self, now: datetime) -> bool:
        """
        Return whether the thermostat takes part in the capacity.

        A switch gone, a switch that does not respond (deferred) and no
        temperature all free its place for the others.
        """
        self._end_deferral(now)
        return not (
            self._switch_gone.confirmed
            or self._no_temperature.confirmed
            or self.deferred_until is not None
        )

    def switch_is_on(self) -> bool | None:
        """Return the switch's state; None while it is gone."""
        state = self._hass.states.get(self.switch)
        return None if _gone(state) else state.state == STATE_ON

    def set_expected(self, on: bool, now: datetime) -> None:  # noqa: FBT001
        """Set what the balancer wants the switch to be."""
        if on != self.expected_on:
            self.expected_on, self.expected_changed_at, self._tries = on, now, 0
            self._keep()

    def to_command(self, now: datetime) -> bool | None:
        """
        Return the state to send the switch now, or None.

        Each balancing cycle (a minute) the switch is not as wanted counts an
        attempt; the cycles a thermostat's change runs in between wait for
        the answer. A new wanted state is sent at once and starts the count
        again. Out of attempts the switch is deferred and turned off once.
        """
        actual = self.switch_is_on()
        wanted = self.expected_on and self.in_rotation(now)
        if actual is None or self.deferred_until is not None:
            return None
        if actual == wanted:
            if self._tries or self._unresponsive.raw:
                self._tries = 0
                self._unresponsive.set(False, now)  # noqa: FBT003
                self._keep()
                self._refresh(now)
            return None
        if self._last_command is not None:
            if self._last_command[1] != wanted:
                self._tries = 0
            elif now - self._last_command[0] < ATTEMPT_INTERVAL:
                return None
        self._last_command = (now, wanted)
        if self._tries >= self.settings.attempts:
            self._defer(now)
            return False
        self._tries += 1
        self._keep()
        self._refresh(now)
        return wanted

    def _defer(self, now: datetime) -> None:
        self.deferred_until = now + duration_dict_to_timedelta(
            self._entry.data.get(CONF_RECOVERY_TIMEOUT)
        )
        self.expected_on, self.expected_changed_at, self._tries = False, now, 0
        self._unresponsive.set(True, now)  # noqa: FBT003
        self._keep()
        self._refresh(now)

    def _end_deferral(self, now: datetime) -> None:
        if self.deferred_until is not None and now >= self.deferred_until:
            self.deferred_until = None
            self._keep()

    def _keep(self) -> None:
        self._memory.update(
            self.subentry_id,
            expected_on=self.expected_on,
            expected_changed_at=self.expected_changed_at.isoformat(),
            tries=self._tries,
            deferred_until=self.deferred_until.isoformat()
            if self.deferred_until
            else None,
        )

    # States of the switch and the sensor

    @callback
    def _on_started(self, _hass: HomeAssistant) -> None:
        self._watching = True
        now = dt_util.utcnow()
        self._switch_gone.set(_gone(self._hass.states.get(self.switch)), now)
        self._no_temperature.set(
            _no_temperature(self._hass.states.get(self._sensor)), now
        )
        self._refresh(now)

    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        if not self._watching:
            return
        now = dt_util.utcnow()
        new = event.data["new_state"]
        if event.data["entity_id"] == self.switch:
            self._switch_gone.set(_gone(new), now)
        else:
            self._no_temperature.set(_no_temperature(new), now)
        self._refresh(now)

    def _refresh(self, now: datetime) -> None:
        """Settle the problems, tell a new state, plan the next check."""
        settings = self.settings
        tolerance = timedelta(seconds=settings.tolerance_seconds)
        hysteresis = timedelta(seconds=settings.hysteresis_seconds)
        conditions = {
            ControlState.SWITCH_UNAVAILABLE: (self._switch_gone, tolerance),
            ControlState.UNRESPONSIVE: (self._unresponsive, timedelta()),
            ControlState.NO_TEMPERATURE: (self._no_temperature, tolerance),
        }
        for condition, enter in conditions.values():
            condition.settle(now, enter, hysteresis)
        state = next(
            (state for state, (c, _) in conditions.items() if c.confirmed),
            ControlState.NORMAL,
        )
        if state is not self.state:
            if state is ControlState.NORMAL:
                LOGGER.info(
                    "%s: control state back to normal (was %s)", self.name, self.state
                )
            else:
                LOGGER.warning("%s: control state %s", self.name, state)
            self.state = state
        async_dispatcher_send(
            self._hass, SIGNAL_CONTROL_CHANGED.format(self.subentry_id)
        )
        changes = [
            when
            for condition, enter in conditions.values()
            if (when := condition.next_change(enter, hysteresis))
        ]
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
        if changes:
            self._cancel_timer = async_track_point_in_utc_time(
                self._hass, self._on_timer, min(changes)
            )

    @callback
    def _on_timer(self, now: datetime) -> None:
        self._cancel_timer = None
        self._refresh(now)

    @property
    def attributes(self) -> dict[str, Any]:
        """Return the details of the control state."""
        return {
            "failed_attempts": self._tries,
            "next_attempt": self.deferred_until,
        }


def _time(value: str | None) -> datetime:
    return dt_util.parse_datetime(value) if value else dt_util.utcnow()
