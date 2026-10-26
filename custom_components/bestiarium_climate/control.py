"""
The control of a thermostat's switch and its control state (DEC-014).

A switch or temperature gone for longer than the tolerance is a problem; a
problem ends after the hysteresis. The commands to a switch are its control's
(SwitchCommand); the series of a direction is its commands sent without an
answer, and only the switch's answer ends it (DEC-014). A switch out of
attempts, or whose command outlasts their budget, is deferred for the
recovery timeout and tried again. What the balancer must remember across
restarts lives in a Store, not in the thermostat's attributes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
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
    CONF_HEATER_POWER,
    CONF_RECOVERY_TIMEOUT,
    CONF_TARGET_SWITCH,
    CONF_TEMPERATURE_SENSOR,
    DOMAIN,
    LOGGER,
    SAVE_DELAY_SECONDS,
    SIGNAL_CONTROL_CHANGED,
    STORAGE_KEY,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
    ControlSettings,
    ControlState,
)
from .util import duration_dict_to_timedelta

if TYPE_CHECKING:
    import asyncio

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
# The pause after a heater turned off, when its thermostat sets no minimum
# cycle duration (decision 29: the compressor's protection)
DEFAULT_PAUSE = timedelta(minutes=5)


class _MemoryStore(Store[dict[str, dict[str, Any]]]):
    """The thermostats' store; 1.1 kept one count of tries, not the series."""

    async def _async_migrate_func(
        self,
        old_major_version: int,  # noqa: ARG002
        old_minor_version: int,
        old_data: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if old_minor_version < 2:  # noqa: PLR2004
            for kept in old_data.values():
                # The tries counted the direction the balancer wanted
                tries = kept.pop("tries", 0)
                kept["series"] = {_direction(kept.get("expected_on", False)): tries}
                kept["unresponsive"] = kept.get("deferred_until") is not None
        return old_data


class ThermostatMemory:
    """What the thermostats keep across restarts, by subentry id."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Start empty; call async_load to read the store."""
        self._store = _MemoryStore(
            hass, STORAGE_VERSION, STORAGE_KEY, minor_version=STORAGE_MINOR_VERSION
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


@dataclass(eq=False)
class SwitchCommand:
    """One command to the switch: its direction, its task, its times."""

    on: bool
    sent_at: datetime
    # The deferral's "off": its answer ends no "Not responding"
    deferral: bool = False
    task: asyncio.Task[None] | None = None
    ended_at: datetime | None = None
    # Its series is closed: the switch answered, or the power was cut
    closed: bool = False

    @property
    def running(self) -> bool:
        """Return whether its task has not ended yet."""
        return self.ended_at is None

    def waiting(self, now: datetime) -> bool:
        """Return whether it waits for its answer: running, or in the window after."""
        return not self.closed and (
            self.ended_at is None or now < self.ended_at + ATTEMPT_INTERVAL
        )


def _direction(on: bool) -> str:  # noqa: FBT001
    return "on" if on else "off"


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
        # The power the thermostat's form gives its heater, if any
        self.heater_power: float | None = subentry.data.get(CONF_HEATER_POWER)
        self._memory = memory
        kept = memory.get(self.subentry_id)
        self.expected_on: bool = kept.get("expected_on", False)
        self.expected_changed_at: datetime = _time(kept.get("expected_changed_at"))
        # The series of each direction: its commands sent without an answer
        series = kept.get("series", {})
        self._series = {on: series.get(_direction(on), 0) for on in (True, False)}
        self.deferred_until: datetime | None = (
            _time(kept["deferred_until"]) if kept.get("deferred_until") else None
        )
        self._switch_gone = _Condition()
        self._no_temperature = _Condition()
        self._no_power = _Condition()
        # When the heater was last wanted off or its switch turned off: a
        # start waits for the pause after it
        self.off_at: datetime | None = (
            _time(kept["off_at"]) if kept.get("off_at") else None
        )
        # The pause after it: the thermostat's minimum cycle duration
        self.pause = DEFAULT_PAUSE
        # "Not responding" lasts across a restart until a command is carried out
        unresponsive = kept.get("unresponsive", False)
        self._unresponsive = _Condition(raw=unresponsive, confirmed=unresponsive)
        # A restored problem is no new one: no second warning
        self.state = ControlState.UNRESPONSIVE if unresponsive else ControlState.NORMAL
        # The commands running or waiting for their answer, and the last of
        # each direction
        self._commands: list[SwitchCommand] = []
        # A deferral turns the switch off once
        self._off_owed = False
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

    def heating(self, now: datetime) -> bool:
        """
        Return whether the switch is wanted on and is on, or was just told to.

        A start under way (its answer not in yet) is heating: a zone that the
        start itself moves to yellow does not cancel it.
        """
        return self.expected_on and bool(self.switch_is_on() or self.starting(now))

    def starting(self, now: datetime) -> bool:
        """
        Return whether a first start is under way: told on, not on, unchanged since.

        A switch that changed since the command (on, then turned off by the
        load protection) is no start under way, nor is one that did not take
        its first attempt: it holds no other start back.
        """
        command = self._latest()
        if not (
            self.expected_on
            and command is not None
            and command.on
            and self._series[True] <= 1
            and now - command.sent_at < ATTEMPT_INTERVAL
        ):
            return False
        state = self._hass.states.get(self.switch)
        return (
            state is not None
            and state.state != STATE_ON
            and state.last_changed <= command.sent_at
        )

    def turned_off_by_another(self) -> bool:
        """
        Return whether the switch, wanted on, was turned off after the last command.

        The load protection of the devices (or a person) did it: no failed
        attempt; a new start has to fit again.
        """
        command = self._latest()
        if not (self.expected_on and command is not None and command.on):
            return False
        state = self._hass.states.get(self.switch)
        return (
            state is not None
            and state.state == STATE_OFF
            and state.last_changed > command.sent_at
        )

    def set_expected(self, on: bool, now: datetime) -> None:  # noqa: FBT001
        """
        Set what the balancer wants the switch to be; the series go on.

        Wanted off while its switch is on, the heater pauses from now, before
        its switch shows it (a virtual switch shows its power a delay later).
        """
        if on != self.expected_on:
            if not on and self.switch_is_on():
                self.off_at = now
            self.expected_on, self.expected_changed_at = on, now
            self._keep()

    def wanted(self, now: datetime) -> bool:
        """Return the state the switch should have now."""
        return self.expected_on and self.in_rotation(now)

    def paused_until(self, now: datetime) -> datetime | None:
        """Return the end of the pause after the switch turned off, while it lasts."""
        if self.off_at is None or now >= self.off_at + self.pause:
            return None
        return self.off_at + self.pause

    def set_power(self, *, cut: bool, now: datetime) -> None:
        """Show whether the device powering the heater is off (no fault)."""
        if cut != self._no_power.raw:
            self._no_power.set(cut, now)
            self._refresh(now)

    def next_command(self, now: datetime) -> bool | None:
        """
        Return the state to send the switch now, or None.

        A command waits while one of its direction waits for its answer: runs,
        or is in the window after its end (a cycle), an overtaken one too, so
        a toggling IR code is never sent twice. One the other way overtakes
        the running one at once. A deferral sends its "off" once, then nothing
        until it ends.
        """
        self._observe(now)
        actual = self.switch_is_on()
        if actual is None:
            return None
        if self.deferred_until is not None:
            return False if self._off_owed else None
        wanted = self.wanted(now)
        if any(c.on is wanted and c.waiting(now) for c in self._commands):
            return None
        latest = self._latest()
        if latest is not None and latest.running:
            return None if latest.on is wanted else wanted
        return None if actual is wanted else wanted

    def send(self, on: bool, now: datetime) -> SwitchCommand:  # noqa: FBT001
        """
        Count a command sent now; the one still running the other way is cancelled.

        A command is an attempt of its direction's series when it is sent; the
        deferral's "off" is none.
        """
        latest = self._latest()
        if latest is not None and latest.running and latest.task is not None:
            latest.task.cancel()
        command = SwitchCommand(on, now, deferral=self.deferred_until is not None)
        self._commands.append(command)
        if command.deferral:
            self._off_owed = False
        else:
            self._series[on] += 1
        self._keep()
        self._refresh(now)
        return command

    def ended(self, command: SwitchCommand, now: datetime) -> None:
        """
        Note the end of a command, whatever ended it.

        Only that command: an overtaken one that ends late frees or restamps
        none newer. The ended ones are forgotten once their window is over,
        but the last of each direction.
        """
        command.ended_at = now
        self._commands = [
            c
            for c in self._commands
            if c.waiting(now) or c.running or c is self._latest(c.on)
        ]

    def cancel_commands(self) -> None:
        """Cancel the commands still running (the entry unloads)."""
        for command in self._commands:
            if command.running and command.task is not None:
                command.task.cancel()

    def defer_if_exhausted(self, now: datetime) -> None:
        """
        Defer the switch now if it is out of attempts or a command hangs.

        Out of attempts: a series has as many failed attempts as the
        attempts, the switch not in its state, whatever is wanted now (a
        heater out of attempts never takes another's place). Hung: a command
        not answered runs longer than the budget of the attempts (attempts x
        a cycle). Called before the capacity is shared, so that its place goes
        to another thermostat in this cycle.
        """
        self._observe(now)
        actual = self.switch_is_on()
        if actual is None or self.deferred_until is not None:
            return
        attempts = self.settings.attempts
        hung = any(
            c.running
            and not c.closed
            and now - c.sent_at >= ATTEMPT_INTERVAL * attempts
            for c in self._commands
        )
        if hung or any(
            actual is not on and self._failed(on, now) >= attempts
            for on in (True, False)
        ):
            self._defer(now)

    def _latest(self, on: bool | None = None) -> SwitchCommand | None:  # noqa: FBT001
        """Return the last command, or the last of a direction."""
        return next(
            (c for c in reversed(self._commands) if on is None or c.on is on), None
        )

    def _failed(self, on: bool, now: datetime) -> int:  # noqa: FBT001
        """
        Return the failed attempts of a direction's series.

        A command is a failed attempt once its window is over without an
        answer (DEC-014, rule 3), an overtaken one too: the ones still waiting
        are not failed yet.
        """
        waiting = sum(
            c.on is on and not c.deferral and c.waiting(now) for c in self._commands
        )
        return self._series[on] - waiting

    def _observe(self, now: datetime) -> None:
        """
        End the series the switch answered: it is in the state of its command.

        Only the switch's answer ends a series, never a new wanted state; an
        answer to a command sent out of a deferral ends "Not responding".
        """
        actual = self.switch_is_on()
        if actual is None:
            return
        command = self._latest(actual)
        if command is not None and not command.closed:
            if not command.deferral and self.deferred_until is None:
                self._unresponsive.set(False, now)  # noqa: FBT003
        elif not self._series[actual]:
            return
        self._close(actual, now)

    def lost_power(self, now: datetime) -> None:
        """
        Close the series "on": the power of the heater is cut.

        A power cut is protection, not a fault (review 18): a start it cut
        short is no failed attempt.
        """
        if self._series[True] or (
            (command := self._latest(on=True)) is not None and not command.closed
        ):
            self._close(True, now)  # noqa: FBT003

    def _close(self, on: bool, now: datetime) -> None:  # noqa: FBT001
        for c in self._commands:
            c.closed = c.closed or c.on is on
        self._series[on] = 0
        self._keep()
        self._refresh(now)

    def _defer(self, now: datetime) -> None:
        self.deferred_until = now + duration_dict_to_timedelta(
            self._entry.data.get(CONF_RECOVERY_TIMEOUT)
        )
        self.expected_on, self.expected_changed_at = False, now
        # The retry after the deferral starts its series again
        self._series = dict.fromkeys(self._series, 0)
        self._off_owed = True
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
            series={_direction(on): n for on, n in self._series.items()},
            off_at=self.off_at.isoformat() if self.off_at else None,
            unresponsive=self._unresponsive.raw,
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
            old = event.data["old_state"]
            if (
                old is not None
                and old.state == STATE_ON
                and new is not None
                and new.state == STATE_OFF
            ):
                self.off_at = now
                self._keep()
        else:
            self._no_temperature.set(_no_temperature(new), now)
        self._refresh(now)

    def _refresh(self, now: datetime) -> None:
        """Settle the problems, tell a new state, plan the next check."""
        settings = self.settings
        tolerance = timedelta(seconds=settings.tolerance_seconds)
        hysteresis = timedelta(seconds=settings.hysteresis_seconds)
        # Enter and leave of each; no power is no fault: it leaves at once
        conditions = {
            ControlState.SWITCH_UNAVAILABLE: (self._switch_gone, tolerance, hysteresis),
            ControlState.UNRESPONSIVE: (self._unresponsive, timedelta(), hysteresis),
            ControlState.NO_TEMPERATURE: (self._no_temperature, tolerance, hysteresis),
            ControlState.NO_POWER: (self._no_power, tolerance, timedelta()),
        }
        for condition, enter, leave in conditions.values():
            condition.settle(now, enter, leave)
        state = next(
            (state for state, (c, _, _) in conditions.items() if c.confirmed),
            ControlState.NORMAL,
        )
        if state is not self.state:
            if state is ControlState.NORMAL:
                LOGGER.info(
                    "%s: control state back to normal (was %s)", self.name, self.state
                )
            elif state is ControlState.NO_POWER:
                LOGGER.info("%s: control state %s", self.name, state)
            else:
                LOGGER.warning("%s: control state %s", self.name, state)
            self.state = state
        async_dispatcher_send(
            self._hass, SIGNAL_CONTROL_CHANGED.format(self.subentry_id)
        )
        changes = [
            when
            for condition, enter, leave in conditions.values()
            if (when := condition.next_change(enter, leave))
        ]
        # The pause's end is shown: refresh then
        if (until := self.paused_until(now)) is not None:
            changes.append(until)
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
        now = dt_util.utcnow()
        return {
            "failed_attempts": max(self._failed(on, now) for on in (True, False)),
            "next_attempt": self.deferred_until,
            "pause_until": self.paused_until(now),
        }


def _time(value: str | None) -> datetime:
    return dt_util.parse_datetime(value) if value else dt_util.utcnow()
