"""
The control state of a device: how the balancer's commands reach it.

A DeviceControl follows a device's relay and power sensor and the balancer's
commands to it (docs/concepts/stability.md):

- An attempt fails only when the device's own response time (the longest of
  its last replies, with a margin, at least one strategy step) has passed; in
  the red zone the wait of a series has a hard limit.
- After the configured number of failed attempts the device is deferred: the
  strategies skip it, then try it again. Any change of its relay ends that.
- A relay or power sensor that is gone is a problem only after the tolerance;
  a problem ends only after stable work for the hysteresis. Many short
  dropouts in an hour make the connection unstable.

Changes are told once (docs/concepts/problem-reporting.md): the control state
for its sensor, events for its event entity, one log line per transition.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.singleton import singleton
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .const import (
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DOMAIN,
    LOGGER,
    RESPONSE_MEMORY_SIZE,
    SIGNAL_CONTROL_CHANGED,
    SIGNAL_CONTROL_EVENT,
    STEP_JITTER_SECONDS,
    ControlSettings,
)
from .helpers import get_device_name_from_entry

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State

STORAGE_KEY = f"{DOMAIN}.control"
STORAGE_VERSION = 1
SAVE_DELAY_SECONDS = 10
UNSTABLE_WINDOW = timedelta(hours=1)
JITTER = timedelta(seconds=STEP_JITTER_SECONDS)
GONE_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN)

DATA_RESPONSE_MEMORY: HassKey[ResponseMemory] = HassKey(f"{DOMAIN}_response_memory")


class ControlState(StrEnum):
    """The control state of a device, the most important problem first."""

    SWITCH_UNAVAILABLE = "switch_unavailable"
    MODE_OFF_FAILED = "mode_off_failed"  # the mode's turn-off ran out of attempts
    UNRESPONSIVE = "unresponsive"
    UNSTABLE = "unstable"
    NO_POWER_DATA = "no_power_data"
    NORMAL = "normal"


class ControlEvent(StrEnum):
    """What happened to a device that does not respond."""

    DEFERRED = "deferred"
    RETRYING = "retrying"
    RECOVERED = "recovered"
    MODE_OFF_FAILED = "mode_off_failed"  # once a duty, instead of its first deferral


class Direction(StrEnum):
    """A command to a relay; the value is its switch service."""

    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"

    @property
    def expected_state(self) -> str:
        """Return the relay state the command asks for."""
        return STATE_ON if self is Direction.TURN_ON else STATE_OFF


class ResponseMemory:
    """How long each device took to carry out its last commands, in a Store."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Start empty; call async_load to read the store."""
        self._store: Store[dict[str, list[float]]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        self._seconds: dict[str, deque[float]] = {}

    async def async_load(self) -> None:
        """Read the stored response times."""
        stored = await self._store.async_load() or {}
        self._seconds = {
            entry_id: deque(seconds, maxlen=RESPONSE_MEMORY_SIZE)
            for entry_id, seconds in stored.items()
        }

    def longest(self, entry_id: str) -> float | None:
        """Return the longest remembered response time of a device."""
        seconds = self._seconds.get(entry_id)
        return max(seconds) if seconds else None

    def add(self, entry_id: str, seconds: float) -> None:
        """Remember a response time."""
        self._seconds.setdefault(entry_id, deque(maxlen=RESPONSE_MEMORY_SIZE)).append(
            seconds
        )
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY_SECONDS)

    def remove(self, entry_id: str) -> None:
        """Forget a removed device entry."""
        self._seconds.pop(entry_id, None)
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY_SECONDS)

    def _data_to_save(self) -> dict[str, list[float]]:
        return {entry_id: list(seconds) for entry_id, seconds in self._seconds.items()}


@singleton(DATA_RESPONSE_MEMORY, async_=True)
async def async_get_response_memory(hass: HomeAssistant) -> ResponseMemory:
    """Return the loaded response memory, shared by all device entries."""
    memory = ResponseMemory(hass)
    await memory.async_load()
    return memory


@dataclass
class _Condition:
    """A problem as it is now (raw) and as it is confirmed."""

    raw: bool = False
    since: datetime | None = None  # when raw last changed
    confirmed: bool = False

    def set(self, raw: bool, now: datetime) -> None:  # noqa: FBT001
        """Set the raw value, remembering when it changed."""
        if raw != self.raw:
            self.raw, self.since = raw, now

    def clear(self, now: datetime) -> None:
        """End the raw problem now; a later clear starts the wait to leave again."""
        self.raw, self.since = False, now

    def settle(self, now: datetime, enter: timedelta, leave: timedelta) -> None:
        """Confirm a problem that lasted enter, or end one stable for leave."""
        if (when := self.next_change(enter, leave)) is not None and now >= when:
            self.confirmed = self.raw

    def next_change(self, enter: timedelta, leave: timedelta) -> datetime | None:
        """Return when the confirmed value changes if raw stays as it is."""
        if self.raw == self.confirmed or self.since is None:
            return None
        return self.since + (enter if self.raw else leave)


@dataclass
class _Series:
    """The commands of one direction to a device until it answers."""

    direction: Direction
    started: datetime
    limit: datetime | None  # the red zone's hard limit of the series' wait
    failures: int = 0
    deadline: datetime | None = None  # of the attempt waiting for an answer
    deferred_until: datetime | None = None


class DeviceControl:
    """The control state of one device."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, memory: ResponseMemory
    ) -> None:
        """Follow the device of the entry; call async_start."""
        self._hass = hass
        self._entry = entry
        self._memory = memory
        self._name = get_device_name_from_entry(entry)
        self._relay: str = entry.data[CONF_SWITCH_ENTITY_ID]
        self._power: str = entry.data[CONF_POWER_SENSOR_ENTITY_ID]
        self._series: _Series | None = None
        self._last_attempt: datetime | None = None
        # The relay's last state that was not gone, to tell an answer from a return
        self._known_state: str | None = None
        self._switch_gone = _Condition()
        self._power_gone = _Condition()
        self._unstable = _Condition()
        self._unresponsive = _Condition()
        self._dropouts: deque[datetime] = deque()
        # Since when the relay or the power sensor is gone: one device, one dropout
        self._gone_since: datetime | None = None
        self._watching_availability = False
        # The mode wants the device off until it turns off (docs: off by mode)
        self.mode_off = False
        self._mode_off_failed = False
        self.state = ControlState.NORMAL
        self._unsubscribe: list[CALLBACK_TYPE] = []
        self._cancel_timer: CALLBACK_TYPE | None = None

    @property
    def entry(self) -> ConfigEntry:
        """Return the device's entry."""
        return self._entry

    @property
    def settings(self) -> ControlSettings:
        """Return the device's control settings, read on every use."""
        return ControlSettings.of(self._entry)

    @callback
    def async_start(self) -> None:
        """Follow the relay and the power sensor; availability once HA has started."""
        if not _gone(relay := self._hass.states.get(self._relay)):
            self._known_state = relay.state
        self._unsubscribe = [
            async_track_state_change_event(
                self._hass, [self._relay, self._power], self._on_state
            ),
            async_at_started(self._hass, self._on_started),
        ]

    @callback
    def async_stop(self) -> None:
        """Stop following the device."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        if self._cancel_timer:
            self._cancel_timer()
        self._unsubscribe, self._cancel_timer = [], None

    # The mode's duty to turn the device off

    def start_mode_off(self) -> None:
        """Open the mode's duty: the device is on and the mode wants it off."""
        self.mode_off, self._mode_off_failed = True, False

    def end_mode_off(self) -> None:
        """Close the duty: the device turned off, or the mode no longer says off."""
        if self.mode_off:
            self.mode_off, self._mode_off_failed = False, False
            self._refresh(dt_util.utcnow())

    # Commands of the strategies

    def may_command(self, direction: Direction, now: datetime) -> bool:
        """Return whether a strategy may send the device a command now."""
        series = self._settle_series(direction, now)
        return series is None or (
            series.deferred_until is None and series.deadline is None
        )

    def waiting(self, direction: Direction, now: datetime) -> bool:
        """Return whether a command of the direction still waits for its answer."""
        series = self._settle_series(direction, now)
        return series is not None and series.deadline is not None

    def first_attempt(self, direction: Direction) -> bool:
        """Return whether the next command of the direction starts a series."""
        series = self._series
        return series is None or series.direction is not direction

    def command_sent(
        self, direction: Direction, now: datetime, *, step_s: float, red: bool
    ) -> None:
        """Wait for the answer to a command for the device's response time."""
        settings = self.settings
        if self._series is None or self._series.direction is not direction:
            limit = now + timedelta(seconds=settings.red_max_wait_seconds)
            self._series = _Series(direction, now, limit if red else None)
        series = self._series
        response = self._memory.longest(self._entry.entry_id)
        if response is None:
            response = settings.default_response_seconds
        deadline = now + timedelta(
            seconds=max(step_s, response * settings.response_margin)
        )
        self._last_attempt = now
        series.deadline = min(deadline, series.limit) if series.limit else deadline
        self._changed()

    def _settle_series(self, direction: Direction, now: datetime) -> _Series | None:
        """
        Count a failed attempt, defer the device or try it again.

        A question about the other direction changes nothing: only a command of
        the other direction starts its own series (command_sent).
        """
        series = self._series
        if series is None or series.direction is not direction:
            return None
        if series.deferred_until is not None:
            if now + JITTER < series.deferred_until:
                return series
            self._series = None
            self._event(ControlEvent.RETRYING, series)
            return None
        if series.deadline is not None and now + JITTER >= series.deadline:
            series.failures += 1
            series.deadline = None
            if series.failures >= self.settings.attempts or (
                series.limit is not None and now + JITTER >= series.limit
            ):
                self._defer(series, now)
            else:
                self._changed()
        return series

    def _defer(self, series: _Series, now: datetime) -> None:
        series.deferred_until = now + timedelta(minutes=self.settings.defer_minutes)
        self._unresponsive.set(True, now)  # noqa: FBT003
        if self.mode_off and not self._mode_off_failed:
            self._mode_off_failed = True
            self._event(ControlEvent.MODE_OFF_FAILED, series)
        else:
            self._event(ControlEvent.DEFERRED, series)
        self._refresh(now)

    # States of the relay and the power sensor

    @callback
    def _on_started(self, _hass: HomeAssistant) -> None:
        self._watching_availability = True
        now = dt_util.utcnow()
        self._switch_gone.set(_gone(self._hass.states.get(self._relay)), now)
        self._power_gone.set(_gone(self._hass.states.get(self._power)), now)
        self._refresh(now)

    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if old is not None and new is not None and old.state == new.state:
            return
        now = dt_util.utcnow()
        if event.data["entity_id"] == self._relay:
            self._on_relay_changed(new, now)
            condition = self._switch_gone
        else:
            condition = self._power_gone
        if self._watching_availability:
            condition.set(_gone(new), now)
            self._count_dropout(now)
        self._refresh(now)

    def _count_dropout(self, now: datetime) -> None:
        """Count the device gone (relay or power sensor) shorter than the tolerance."""
        gone = self._switch_gone.raw or self._power_gone.raw
        if gone and self._gone_since is None:
            self._gone_since = now
        elif not gone and self._gone_since is not None:
            lasted, self._gone_since = now - self._gone_since, None
            if lasted < self._tolerance:
                LOGGER.debug("%s: short dropout of %s", self._name, lasted)
                self._dropouts.append(now)

    def _on_relay_changed(self, new: State | None, now: datetime) -> None:
        """
        Any change of the relay ends the series and its deferral.

        "Not responding" ends only when the relay is back with the expected
        state or another state than before: a dead device that drops out and
        returns as it was did not answer. Each such change starts the
        hysteresis again. The response time counts from the series' first
        command, while the device was not deferred.
        """
        series, self._series = self._series, None
        if _gone(new):
            return
        if new.state == Direction.TURN_OFF.expected_state:
            self.mode_off = self._mode_off_failed = False
        expected = series is not None and new.state == series.direction.expected_state
        if expected and series.deferred_until is None:
            self._memory.add(
                self._entry.entry_id, (now - series.started).total_seconds()
            )
        if expected or new.state != self._known_state:
            self._unresponsive.clear(now)
        self._known_state = new.state

    # The control state

    @property
    def _tolerance(self) -> timedelta:
        return timedelta(seconds=self.settings.tolerance_seconds)

    def _refresh(self, now: datetime) -> None:
        """Settle the problems, tell a new control state, plan the next check."""
        settings = self.settings
        hysteresis = timedelta(seconds=settings.hysteresis_seconds)
        while self._dropouts and self._dropouts[0] + UNSTABLE_WINDOW <= now:
            self._dropouts.popleft()
        self._unstable.set(
            len(self._dropouts) >= settings.unstable_dropouts_per_hour, now
        )
        was_unresponsive = self._unresponsive.confirmed
        conditions = {
            ControlState.SWITCH_UNAVAILABLE: (self._switch_gone, self._tolerance),
            ControlState.UNRESPONSIVE: (self._unresponsive, timedelta()),
            ControlState.UNSTABLE: (self._unstable, timedelta()),
            ControlState.NO_POWER_DATA: (self._power_gone, self._tolerance),
        }
        for condition, enter in conditions.values():
            condition.settle(now, enter, hysteresis)
        if was_unresponsive and not self._unresponsive.confirmed:
            self._event(ControlEvent.RECOVERED, None)
        state = next(
            (state for state, (c, _) in conditions.items() if c.confirmed),
            ControlState.NORMAL,
        )
        # More important than "not responding": the mode could not turn it off
        if self._mode_off_failed and state is not ControlState.SWITCH_UNAVAILABLE:
            state = ControlState.MODE_OFF_FAILED
        if state is not self.state:
            if state is ControlState.NORMAL:
                LOGGER.info(
                    "%s: control state back to normal (was %s)", self._name, self.state
                )
            else:
                LOGGER.warning("%s: control state %s", self._name, state)
            self.state = state
        self._changed()
        changes = [
            when
            for condition, enter in conditions.values()
            if (when := condition.next_change(enter, hysteresis))
        ]
        if self._dropouts:
            changes.append(self._dropouts[0] + UNSTABLE_WINDOW)
        self._plan_check(min(changes, default=None))

    def _plan_check(self, when: datetime | None) -> None:
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
        if when is not None:
            self._cancel_timer = async_track_point_in_utc_time(
                self._hass, self._on_timer, when
            )

    @callback
    def _on_timer(self, now: datetime) -> None:
        self._cancel_timer = None
        self._refresh(now)

    # What the entities show

    @property
    def attributes(self) -> dict[str, Any]:
        """Return the details of the control state."""
        series = self._series
        return {
            "direction": series.direction if series else None,
            "failed_attempts": series.failures if series else 0,
            "last_attempt": self._last_attempt,
            "next_attempt": (series.deferred_until or series.deadline)
            if series
            else None,
            "response_time": self._memory.longest(self._entry.entry_id),
            "dropouts_last_hour": len(self._dropouts),
        }

    def _changed(self) -> None:
        async_dispatcher_send(
            self._hass, SIGNAL_CONTROL_CHANGED.format(self._entry.entry_id)
        )

    def _event(self, event: ControlEvent, series: _Series | None) -> None:
        async_dispatcher_send(
            self._hass,
            SIGNAL_CONTROL_EVENT.format(self._entry.entry_id),
            event,
            {
                "direction": series.direction if series else None,
                "failed_attempts": series.failures if series else 0,
                "next_attempt": series.deferred_until if series else None,
            },
        )


def _gone(state: State | None) -> bool:
    return state is None or state.state in GONE_STATES
