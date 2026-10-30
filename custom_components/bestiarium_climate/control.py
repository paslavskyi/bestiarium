"""
The control of a thermostat's switch and its control state (DEC-014).

A switch or temperature gone for longer than the tolerance is a problem; a
problem ends after the hysteresis. The commands to a switch and their series
are its control's (command.py). A switch out of attempts to turn on, or whose
"on" outlasts their budget, is deferred for the recovery timeout and tried
again; one that runs while wanted off is turned off stepwise (stepwise.py).
What the balancer must remember across restarts lives in a Store, not in the
thermostat's attributes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.singleton import singleton
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .command import (
    ATTEMPT_INTERVAL,
    MIN_RESPONSE_BUDGET,
    SwitchCommand,
    SwitchCommands,
    direction,
)
from .const import (
    CONF_HEATER_POWER,
    CONF_POWER_ENTITY,
    CONF_RECOVERY_TIMEOUT,
    CONF_TARGET_SWITCH,
    CONF_TEMPERATURE_SENSOR,
    DEFAULT_UNIVERSAL_MIN_POWER,
    DOMAIN,
    LOGGER,
    SAVE_DELAY_SECONDS,
    SHEDDING_ZONES,
    SIGNAL_CONTROL_CHANGED,
    SIGNAL_SWITCH_CHANGED,
    STORAGE_KEY,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
    ControlSettings,
    ControlState,
    Zone,
)
from .devices import PowerSupply, power_supply, switch_power
from .power_reading import PowerReading, PowerThresholds
from .stepwise import Step, StepwiseOff
from .util import duration_dict_to_timedelta

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import (
        CALLBACK_TYPE,
        Event,
        EventStateChangedData,
        HomeAssistant,
    )

    from .data import BestiariumClimateConfigEntry

GONE_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN)
# The pause after a heater turned off, when its thermostat sets no minimum
# cycle duration (decision 29: the compressor's protection)
DEFAULT_PAUSE = timedelta(minutes=5)


class Reading(StrEnum):
    """What the balancer reads from a thermostat's switch (DEC-014, rule 1)."""

    ON = "on"
    # Told on and not on yet: the start's first command waits for its answer
    STARTING = "starting"
    # Wanted on, turned off by another for less than the tolerance: a glitch
    # so far
    OFF_UNCONFIRMED = "off_unconfirmed"
    OFF = "off"


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
                kept["series"] = {direction(kept.get("expected_on", False)): tries}
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
        *,
        zone: Callable[[], Zone],
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
        # A universal thermostat's own power sensor; else its virtual switch's
        self._power_entity: str | None = subentry.data.get(CONF_POWER_ENTITY)
        # A universal thermostat with a power sensor reads its switch by it
        self._power_reading = (
            PowerReading(
                hass,
                self.name,
                self._power_entity,
                PowerThresholds.of(
                    subentry.data, min_power=DEFAULT_UNIVERSAL_MIN_POWER
                ),
                self._on_power_reading,
            )
            if self._power_entity
            else None
        )
        self._power_was_on = False
        # In the manual mode: what the temperature asks, the last command sent
        # and the thermostat's minimum cycle (FR-028)
        self._manual: bool | None = None
        self._manual_sent: bool | None = None
        self._manual_cycle = timedelta()
        self._memory = memory
        kept = memory.get(self.subentry_id)
        self.expected_on: bool = kept.get("expected_on", False)
        self.expected_changed_at: datetime = _time(kept.get("expected_changed_at"))
        self._commands = SwitchCommands(kept.get("series", {}))
        self.deferred_until: datetime | None = (
            _time(kept["deferred_until"]) if kept.get("deferred_until") else None
        )
        self._switch_gone = _Condition()
        self._no_temperature = _Condition()
        self._no_power = _Condition()
        self._stepwise = StepwiseOff()
        self._not_turning_off = _Condition()
        # The load zone, read where the balancer reads it
        self._zone = zone
        # What powers its switch, found by the balancer (follow_power)
        self.power = PowerSupply(self.switch)
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
        # A deferral turns the switch off once
        self._off_owed = False
        # The failed attempts that deferred the switch, shown while deferred
        self._failed_at_deferral: int = kept.get("failed_at_deferral", 0)
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
            )
        ]
        # Read by power before HA's start is seen: on a reload it is at once
        if (reading := self._power_reading) is not None:
            reading.start()
            self._unsubscribe.append(reading.stop)
            self._power_was_on = reading.available and reading.is_on
        self._unsubscribe.append(async_at_started(self._hass, self._on_started))

    @callback
    def async_stop(self) -> None:
        """Stop following."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        if self._cancel_timer:
            self._cancel_timer()
        self._unsubscribe, self._cancel_timer = [], None

    def _switch_state(self) -> State | None:
        """
        Return the switch's state: its own, or read by its power sensor.

        Read by power, it is gone while the switch is missing or unavailable,
        or the power data is gone. An optimistic switch (a template switch
        over scenes) is unknown until its first command: no reason to be gone.
        """
        state = self._hass.states.get(self.switch)
        reading = self._power_reading
        if reading is None:
            return state
        if state is None or state.state == STATE_UNAVAILABLE or not reading.available:
            return None
        return State(
            self.switch,
            STATE_ON if reading.is_on else STATE_OFF,
            last_changed=reading.last_changed,
        )

    # What the balancer asks

    def measured_power(self) -> float | None:
        """Return what the switch's power sensor reads, if it has one."""
        return switch_power(self._hass, self.switch, self._power_entity)

    def follow_power(self) -> bool:
        """Find again what powers the switch; return whether it changed."""
        power = power_supply(self._hass, self.switch, self._power_entity)
        changed, self.power = power != self.power, power
        return changed

    @property
    def socket(self) -> str | None:
        """Return the socket of its virtual switch, if it has one."""
        return self.power.socket

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

    def reading(self, now: datetime) -> Reading | None:
        """
        Return what the switch reads now; None while it is gone.

        A start under way is starting: the start's first command (the only
        "on" since the balancer wanted it on) waits for its answer, as any
        command, and the switch has not changed since. A switch that changed
        since (on, then turned off by the load protection) is no start under
        way, nor is one that did not take its first attempt.

        A switch wanted on that another turned off after its last "on" (a
        person, the load protection, the switch itself) is off only once
        that lasted the tolerance (DEC-014, rule 1); until then it gets no
        command and heats, for the arrangement and the zones alike.
        """
        state = self._switch_state()
        if _gone(state):
            return None
        if state.state == STATE_ON:
            return Reading.ON
        if self.expected_on:
            start = self._commands.start
            if (
                start is not None
                and start.waiting(now)
                and state.last_changed <= start.sent_at
            ):
                return Reading.STARTING
            off = self._turned_off_at()
            if off is not None and now - off < self._tolerance:
                return Reading.OFF_UNCONFIRMED
        return Reading.OFF

    def switch_is_on(self, now: datetime) -> bool | None:
        """Return whether the switch is on; None while it is gone."""
        reading = self.reading(now)
        return None if reading is None else reading is Reading.ON

    def heating(self, now: datetime) -> bool:
        """
        Return whether the switch is wanted on and heats.

        A start under way heats (a zone that the start itself moves to yellow
        does not cancel it), and so does an "off" not confirmed yet.
        """
        return self.expected_on and self.reading(now) not in (None, Reading.OFF)

    def starting(self, now: datetime) -> bool:
        """Return whether a start is under way: told on, not on, unchanged since."""
        return self.reading(now) is Reading.STARTING

    def turned_off_by_another(self, now: datetime) -> bool:
        """
        Return whether the switch, wanted on, was turned off after its last "on".

        The load protection of the devices (or a person) did it, for at least
        the tolerance: no failed attempt; a new start waits for the pause
        from that "off" and has to fit again.
        """
        return self.reading(now) is Reading.OFF and self._turned_off_at() is not None

    def off_confirmed_at(self, now: datetime) -> datetime | None:
        """Return when an "off" not confirmed yet is confirmed, while it is not."""
        off = self._turned_off_at()
        if off is None or self.reading(now) is not Reading.OFF_UNCONFIRMED:
            return None
        return off + self._tolerance

    def _turned_off_at(self) -> datetime | None:
        """Return when another turned the switch, wanted on, off after its last "on"."""
        state = self._switch_state()
        command = self._commands.latest()
        if (
            self.expected_on
            and command is not None
            and command.on
            and state is not None
            and state.state == STATE_OFF
            and state.last_changed > command.sent_at
        ):
            return state.last_changed
        return None

    @property
    def _tolerance(self) -> timedelta:
        return timedelta(seconds=self.settings.tolerance_seconds)

    def set_expected(self, on: bool, now: datetime) -> None:  # noqa: FBT001
        """
        Set what the balancer wants the switch to be; the series go on.

        Wanted off while it heats, or its start sent (cut short: the
        compressor may have begun), the heater pauses from now, before its
        switch shows it (a virtual switch shows its power a delay later);
        turned off by another, from that "off". A new pause is shown.
        """
        if on != self.expected_on:
            off_at = self.off_at
            if not on and self.turned_off_by_another(now):
                self.off_at = self._turned_off_at()
            elif not on and (self.heating(now) or self._commands.start_sent):
                self.off_at = now
            if on:
                self._commands.new_start()
            self.expected_on, self.expected_changed_at = on, now
            self._keep()
            if self.off_at != off_at:
                self._refresh(now)

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

    @property
    def manual(self) -> bool:
        """Return whether the thermostat is in its manual mode."""
        return self._manual is not None

    def set_manual(
        self,
        demand: bool | None,  # noqa: FBT001
        now: datetime,
        min_cycle: timedelta,
    ) -> bool:
        """
        Set what the manual mode asks for (None: not manual).

        Entering it, the switch is the person's: the balancer wants nothing
        of it and its stepwise "off" ends; the switch as it reads counts as
        the last command. A cycle of the manual mode lasts at least the
        thermostat's own minimum cycle duration. Return whether it left the
        manual mode with its switch running: the balancer keeps it running
        if it has a place (keep_running).
        """
        left_running = False
        if (demand is None) != (self._manual is None):
            running = self.reading(now) is Reading.ON
            self._manual_sent = running if demand is not None else None
            if demand is not None:
                self.expected_on, self.expected_changed_at = False, now
                self._stepwise = StepwiseOff()
            left_running = demand is None and running
            self._keep()
            self._refresh(now)
        self._manual, self._manual_cycle = demand, min_cycle
        return left_running

    def keep_running(self, now: datetime) -> None:
        """Take the switch left running by the manual mode as running: no start."""
        self.expected_on, self.expected_changed_at = True, now
        self._keep()
        self._refresh(now)

    def _manual_command(self, now: datetime) -> bool | None:
        """
        Return the manual mode's command: once for each change of the demand.

        As a thermostat of HA: no attempts, no "Not responding", no stepwise
        "off"; the switch changes no sooner than the minimum cycle after its
        last change.
        """
        demand = self._manual
        if demand is None or demand is self._manual_sent:
            return None
        state = self._switch_state()
        if state is not None and now - state.last_changed < self._manual_cycle:
            return None
        return demand

    def next_command(self, now: datetime) -> bool | None:  # noqa: PLR0911
        """
        Return the state to send the switch now, or None.

        A command waits while one of its direction waits for its answer: runs,
        or is in the window after its end (a cycle), an overtaken one too, so
        a toggling IR code is never sent twice. One the other way overtakes
        the running one at once. A deferral sends its "off" once, by the same
        rule: not while an "off" runs or is in its window, even one the
        deferral closed. Then nothing until it ends. An "off" by another not
        confirmed yet gets no command either way (a switch deferred is wanted
        off: none of its "off"s is by another).

        A switch on while wanted off, deferred too, is turned off stepwise:
        its attempts are not the series' (stepwise.py).
        """
        self._observe(now)
        reading = self.reading(now)
        if reading in (None, Reading.OFF_UNCONFIRMED):
            return None
        if self.manual:
            return self._manual_command(now)
        actual = reading is Reading.ON
        wanted = self.wanted(now)
        if actual and not wanted:
            return False if self.off_step(now) is Step.ATTEMPT else None
        if self.deferred_until is not None:
            owed = self._off_owed and not self._commands.in_flight(now, on=False)
            return False if owed else None
        if self._commands.waiting(now, on=wanted):
            return None
        latest = self._commands.latest()
        if latest is not None and latest.running:
            return None if latest.on is wanted else wanted
        return None if actual is wanted else wanted

    def send(
        self,
        on: bool,  # noqa: FBT001
        now: datetime,
        *,
        counted: bool = True,
        target: str | None = None,
    ) -> SwitchCommand:
        """
        Count a command sent now; the deferral's "off" is owed no more.

        An "off" counted to a switch that is on is an attempt of the stepwise
        "off"; a command to a deferred switch counts in no series. Not counted:
        the "off" outside the attempts and the cut of the socket (command.py).
        """
        if counted and target is None and not on and self.reading(now) is Reading.ON:
            self._stepwise.attempt(now)
        deferred = self.deferred_until is not None
        command = self._commands.send(
            on, now, counted=counted and not deferred, target=target
        )
        if deferred and target is None:
            self._off_owed = False
        if self.manual and target is None:
            self._manual_sent = on
        self._keep()
        self._refresh(now)
        return command

    def ended(self, command: SwitchCommand, now: datetime) -> None:
        """Note the end of a command, whatever ended it; its window starts."""
        self._commands.ended(command, now)
        # Not once stopped: an entry unloading cancels its commands
        if self._unsubscribe:
            self._refresh(now)

    def cancel_commands(self) -> None:
        """Cancel the commands still running (the entry unloads)."""
        self._commands.cancel()

    def defer_if_exhausted(self, now: datetime) -> None:
        """
        Defer the switch now if it is out of attempts to turn on or an "on" hangs.

        Out of attempts: the series "on" has as many failed attempts as the
        attempts, the switch off, whatever is wanted now (a heater out of
        attempts never takes another's place). Hung: an "on" not answered
        runs longer than the budget of the attempts (attempts x a cycle, at
        least MIN_RESPONSE_BUDGET), seen at the next cycle. A switch that
        does not turn off is never deferred: it is turned off stepwise.
        Called before the capacity is shared, so that its place
        goes to another thermostat in this cycle.
        """
        self._observe(now)
        if (
            self.manual
            or self.switch_is_on(now) is not False
            or self.deferred_until is not None
        ):
            return
        attempts = self.settings.attempts
        failed = self._commands.failed(now, on=True)
        budget = max(ATTEMPT_INTERVAL * attempts, MIN_RESPONSE_BUDGET)
        if self._commands.hung(now, budget, on=True):
            # The hung command is a failed attempt too
            self._defer(now, failed + 1)
        elif failed >= attempts:
            self._defer(now, failed)

    def _observe(self, now: datetime) -> None:
        """
        End the series the switch answered: it is in the state of its command.

        Only the switch's answer ends a series, never a new wanted state; an
        answer to a command sent out of a deferral ends "Not responding". The
        stepwise "off" ends with the switch wanted on again, or off for a whole
        step: an air conditioner idling at its setpoint draws little for a
        minute or two and runs on (review 06, R1). Off after its socket was
        cut, it ends at once: the power back is a new chain.
        """
        actual = self.switch_is_on(now)
        if actual is None:
            return
        if self._stepwise.active and (
            self.wanted(now)
            or (
                not actual
                and (
                    self._stepwise.cut
                    or now - self._switch_state().last_changed
                    >= timedelta(seconds=self.settings.off_interval_seconds)
                )
            )
        ):
            self._stepwise = StepwiseOff()
            self._refresh(now)
        before = self._commands.kept
        command = self._commands.answer(actual)
        # A switch in the state already when the command went is no answer of
        # it: only a change after the command ends "Not responding"
        state = self._switch_state()
        if (
            command is not None
            and command.counted
            and self.deferred_until is None
            and state is not None
            and state.last_changed >= command.sent_at
        ):
            self._unresponsive.set(False, now)  # noqa: FBT003
        if command is not None or self._commands.kept != before:
            self._keep()
            self._refresh(now)

    def lost_power(self, now: datetime) -> None:
        """
        Close the series "on": the power of the heater is cut.

        A power cut is protection, not a fault (review 18): a start it cut
        short is no failed attempt.
        """
        if self._commands.close(on=True):
            self._keep()
            self._refresh(now)

    # The stepwise "off"

    @property
    def _off_interval(self) -> timedelta:
        """Return the step: a cycle at most in red or overload (DEC-014, hard limit)."""
        interval = timedelta(seconds=self.settings.off_interval_seconds)
        if self._zone() in SHEDDING_ZONES:
            return min(interval, ATTEMPT_INTERVAL)
        return interval

    def _off_next(self) -> tuple[Step, datetime | None] | None:
        return self._stepwise.next(
            self.settings.off_attempts,
            self._off_interval,
            socket=self.socket is not None,
        )

    def off_step(self, now: datetime) -> Step | None:
        """
        Return the step of the stepwise "off" due now, or None.

        Only while the switch is on: one off for a moment waits. An attempt
        due at once (the first, or one the red zone asks for) waits while an
        "off" runs; a later one cuts it short.
        """
        if self.reading(now) is not Reading.ON or (step := self._off_next()) is None:
            return None
        kind, due = step
        if due is None:
            return None if self._commands.running(on=False) else kind
        return kind if now >= due else None

    def off_step_at(self) -> datetime | None:
        """Return when the next step of the stepwise "off" is due, while it waits."""
        step = self._off_next() if self._stepwise.active else None
        return step[1] if step is not None else None

    @property
    def off_attempts(self) -> int:
        """Return the "off"s the stepwise "off" sent."""
        return self._stepwise.attempts

    def reported(self, now: datetime) -> None:
        """Note the event "out of control" fired now."""
        self._stepwise.reported_at = now
        self._refresh(now)

    def cut(self, now: datetime) -> None:
        """Note the socket cut now: never again in this stepwise "off"."""
        self._stepwise.cut = True
        self._refresh(now)

    def force_off_attempt(self, now: datetime) -> bool:
        """
        Make the next attempt of a stepwise "off" under way due now (red zone).

        Return whether it is due now: not with no attempt left, nor while an
        "off" runs.
        """
        if (
            not self._stepwise.active
            or self._stepwise.attempts >= self.settings.off_attempts
        ):
            return False
        self._stepwise.forced = True
        return self.off_step(now) is Step.ATTEMPT

    def blind_off(self, now: datetime) -> bool:
        """
        Return whether an "off" outside the attempts may go now (red zone).

        To a switch that reads off (its power sensor may be late), or on with
        no attempt left; never while a command of it runs.
        """
        if self._commands.running():
            return False
        reading = self.reading(now)
        return reading is Reading.OFF or (
            reading is Reading.ON and self._stepwise.active
        )

    def _defer(self, now: datetime, failed: int) -> None:
        self.deferred_until = now + duration_dict_to_timedelta(
            self._entry.data.get(CONF_RECOVERY_TIMEOUT)
        )
        self.expected_on, self.expected_changed_at = False, now
        # The retry after the deferral starts every series again; no command
        # sent before it answers for the retry
        self._commands.reset()
        self._failed_at_deferral = failed
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
            series=self._commands.kept,
            off_at=self.off_at.isoformat() if self.off_at else None,
            failed_at_deferral=self._failed_at_deferral,
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
        self._switch_gone.set(_gone(self._switch_state()), now)
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
            old = event.data["old_state"]
            if self._power_reading is None:
                self._switch_changed(
                    was_on=old is not None and old.state == STATE_ON, now=now
                )
            else:
                self._switch_changed(was_on=self._power_was_on, now=now)
        else:
            self._no_temperature.set(_no_temperature(new), now)
        self._refresh(now)

    @callback
    def _on_power_reading(self) -> None:
        """Follow the switch read by power, as a change of the switch's own state."""
        was_on = self._power_was_on
        reading = self._power_reading
        self._power_was_on = reading is not None and reading.available and reading.is_on
        if not self._watching:
            return
        now = dt_util.utcnow()
        self._switch_changed(was_on=was_on, now=now)
        self._refresh(now)
        async_dispatcher_send(
            self._hass, SIGNAL_SWITCH_CHANGED.format(self.subentry_id)
        )

    def _switch_changed(self, *, was_on: bool, now: datetime) -> None:
        self._switch_gone.set(_gone(self._switch_state()), now)
        # An "off" by another pauses once confirmed (set_expected)
        if was_on and self.reading(now) is Reading.OFF:
            self.off_at = now
            self._keep()

    def _refresh(self, now: datetime) -> None:
        """Settle the problems, tell a new state, plan the next check."""
        settings = self.settings
        tolerance = timedelta(seconds=settings.tolerance_seconds)
        hysteresis = timedelta(seconds=settings.hysteresis_seconds)
        # Enter and leave of each; no power is no fault: it leaves at once
        # Entered only while the switch runs (an ordinary "off" taken within
        # the tolerance shows nothing); once confirmed, a dip of it holds it
        self._not_turning_off.set(
            self._stepwise.active
            and (self.reading(now) is Reading.ON or self._not_turning_off.confirmed),
            now,
        )
        conditions = {
            ControlState.SWITCH_UNAVAILABLE: (self._switch_gone, tolerance, hysteresis),
            ControlState.NOT_TURNING_OFF: (
                self._not_turning_off,
                tolerance,
                hysteresis,
            ),
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
        # The pause's end and a window's end (a failed attempt) are shown
        if (until := self.paused_until(now)) is not None:
            changes.append(until)
        if (window := self._commands.next_window_end(now)) is not None:
            changes.append(window)
        if (step := self.off_step_at()) is not None and step > now:
            changes.append(step)
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
        if self.state is ControlState.NOT_TURNING_OFF:
            return {
                "failed_attempts": self._stepwise.failed(now, self._off_interval),
                "next_attempt": self.off_step_at(),
                "pause_until": self.paused_until(now),
            }
        failed = self._commands.failed(now, on=True)
        return {
            "failed_attempts": self._failed_at_deferral
            if self.deferred_until is not None
            else failed,
            "next_attempt": self.deferred_until,
            "pause_until": self.paused_until(now),
        }


def _time(value: str | None) -> datetime:
    return dt_util.parse_datetime(value) if value else dt_util.utcnow()
