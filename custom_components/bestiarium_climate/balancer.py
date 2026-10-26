"""Balancing coordinator for bestiarium_climate."""

from __future__ import annotations

import contextvars
from collections import Counter
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING

from homeassistant.components.climate import (
    DATA_COMPONENT as CLIMATE_DATA_COMPONENT,
)
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    BALANCING_INTERVAL_SECONDS,
    CAPACITY_MAX,
    CAPACITY_MIN,
    CONF_MIN_WORKING_TIME,
    CONF_TARGET_SWITCH,
    DEFAULT_CAPACITY,
    DOMAIN,
    DOMAIN_BESTIARIUM_MODE,
    EVENT_DEVICE_ROTATED_IN,
    EVENT_DEVICE_ROTATED_OUT,
    ISSUE_SWITCH_IS_DEVICE,
    ISSUE_SWITCH_SHARED,
    LARGE_HEATER_POWER_W,
    LOGGER,
    MODE_AC_LOAD_LIMITS,
    MODE_AC_POWER_SENSOR,
    MODE_RED_THRESHOLD,
    RED_STEP_SECONDS,
    SENSOR_ZONES,
    SIGNAL_CONTROL_CHANGED,
    START_STEP_SECONDS,
    SUBENTRY_TYPE_THERMOSTAT,
    ZONE_SENSOR_UNIQUE_ID,
    ControlState,
    Zone,
)
from .control import DEFAULT_PAUSE, ThermostatControl, async_get_thermostat_memory
from .devices import (
    device_of_relay,
    expected_power,
    power_cut,
    power_device,
    power_entity,
    power_relays,
)
from .rotation import Heater, arrange
from .util import duration_dict_to_timedelta

if TYPE_CHECKING:
    import asyncio

    from homeassistant.core import CALLBACK_TYPE, EventStateChangedData, HomeAssistant

    from .climate import VirtualThermostat
    from .command import SwitchCommand
    from .data import BestiariumClimateConfigEntry

# The zones in which the balancer starts heaters
STARTING_ZONES = (Zone.GREEN, Zone.NONE)


def shared_switches(hass: HomeAssistant, *, besides: str | None = None) -> set[str]:
    """Return the switches more than one thermostat controls (any climate entry)."""
    targets = Counter(
        subentry.data.get(CONF_TARGET_SWITCH)
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id != besides
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_THERMOSTAT
    )
    return {switch for switch, count in targets.items() if count > 1}


def remove_stale_issues(
    hass: HomeAssistant, entry_id: str, subentry_ids: set[str], *, removed: bool
) -> None:
    """
    Delete the Repairs issues whose thermostat, switch or entry is gone.

    An issue of this entry names its thermostat or switch (subentry); a
    shared switch's issue goes once no two thermostats share it.
    """
    shared = shared_switches(hass, besides=entry_id if removed else None)
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain != DOMAIN:
            continue
        issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
        data = (issue.data if issue else None) or {}
        if issue_id.startswith(f"{ISSUE_SWITCH_SHARED}_"):
            stale = issue_id.removeprefix(f"{ISSUE_SWITCH_SHARED}_") not in shared
        else:
            stale = (
                data.get("entry_id") == entry_id
                and data.get("subentry_id") not in subentry_ids
            )
        if stale:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


class BalancingCoordinator:
    """Shares the capacity of a climate entry among its thermostats."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumClimateConfigEntry,
    ) -> None:
        """Initialize the balancing coordinator; call async_load."""
        self._hass = hass
        self._entry = entry
        self._min_working_time = duration_dict_to_timedelta(
            entry.data.get(CONF_MIN_WORKING_TIME),
        )
        self.controls: dict[str, ThermostatControl] = {}
        self._states: dict[str, ControlState] = {}
        self._shared: set[str] = set()
        self._relays: set[str] = set()
        self._unsubscribe: list[CALLBACK_TYPE] = []
        # One cycle at a time; a request during one makes one more after it
        self._cycling = False
        self._pending = False
        self._stopped = False
        self._red_off_at: datetime | None = None
        self._red_timer: CALLBACK_TYPE | None = None
        self._zone_ids: list[str] = []
        self._zone_unsubscribe: CALLBACK_TYPE | None = None
        self._started_at: datetime | None = None
        # In green whether a start may go in this cycle (one at a time)
        self._gate_open = True
        self._start_timer: CALLBACK_TYPE | None = None
        self._start_due: datetime | None = None

    async def async_load(self) -> None:
        """Make the control of each thermostat with what it kept."""
        memory = await async_get_thermostat_memory(self._hass)
        memory.keep_only(
            {
                subentry_id
                for entry in self._hass.config_entries.async_entries(DOMAIN)
                for subentry_id in entry.subentries
            }
        )
        self.controls = {
            subentry.subentry_id: ThermostatControl(
                self._hass, self._entry, subentry, memory
            )
            for subentry in self._entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_THERMOSTAT
        }

    def start(self) -> None:
        """Follow the switches and the zones; balance every minute and on changes."""
        self._report_shared_switches(force=True)
        for control in self.controls.values():
            control.async_start()
            self._states[control.subentry_id] = control.state
            self._unsubscribe.append(
                async_dispatcher_connect(
                    self._hass,
                    SIGNAL_CONTROL_CHANGED.format(control.subentry_id),
                    self._control_changed(control),
                )
            )
        self._follow_zones()
        # Decide at once when a power relay changes (the rotation changes)
        # and when a switch turns on (a virtual switch showing its "on" late,
        # a person: it may not be wanted). A switch's "off" waits for the
        # cycle: a moment's glitch turns nothing off. Changes of the state
        # only, not of its attributes.
        switches = [c.switch for c in self.controls.values()]
        self._unsubscribe.append(
            async_track_state_change_event(
                self._hass, power_relays(self._hass, switches), self._on_changed
            )
        )
        self._unsubscribe.append(
            async_track_state_change_event(self._hass, switches, self._on_switch_on)
        )
        self._unsubscribe.append(
            self._hass.bus.async_listen(
                EVENT_ENTITY_REGISTRY_UPDATED, self._on_registry_updated
            )
        )
        self._unsubscribe.append(
            async_track_time_interval(
                self._hass,
                self.async_request_balance,
                timedelta(seconds=BALANCING_INTERVAL_SECONDS),
            )
        )

    def stop(self) -> None:
        """Stop balancing and following; cancel the commands under way."""
        self._stopped = True
        for control in self.controls.values():
            control.cancel_commands()
        while self._unsubscribe:
            self._unsubscribe.pop()()
        if self._zone_unsubscribe is not None:
            self._zone_unsubscribe()
            self._zone_unsubscribe = None
        self._cancel_red_timer()
        if self._start_timer is not None:
            self._start_timer()
            self._start_timer = self._start_due = None
        for control in self.controls.values():
            control.async_stop()

    def _follow_zones(self) -> None:
        """Follow the zone sensors there are now, by their current entity_ids."""
        if self._zone_unsubscribe is not None:
            self._zone_unsubscribe()
        self._zone_ids = sorted(self._zone_sensors().values())
        self._zone_unsubscribe = async_track_state_change_event(
            self._hass,
            self._zone_ids,
            callback(lambda _event: self.async_request_balance()),
        )

    @callback
    def _on_registry_updated(self, _event: Event) -> None:
        """Follow a zone sensor created, renamed or removed; decide again."""
        if sorted(self._zone_sensors().values()) != self._zone_ids:
            self._follow_zones()
            self.async_request_balance()

    def _report_shared_switches(self, *, force: bool = False) -> None:
        """
        Report the switches the balancer must leave alone, as Repairs issues.

        One switch, one thermostat: a shared switch. One relay, one
        integration: a switch that is a Bestiarium Devices relay (the
        owner's rule, review 18). Their thermostats are left out of the
        balancing (no command, no attempt) until fixed; a configuration made
        before DEV-008 stage B may have them. Read every cycle: another entry
        may share or free a switch.
        """
        shared = shared_switches(self._hass)
        relays = {
            control.switch
            for control in self.controls.values()
            if device_of_relay(self._hass, control.switch)
        }
        if shared == self._shared and relays == self._relays and not force:
            return
        if force:
            remove_stale_issues(
                self._hass,
                self._entry.entry_id,
                set(self._entry.subentries),
                removed=False,
            )
        self._shared, self._relays = shared, relays
        for control in self.controls.values():
            self._issue(
                f"{ISSUE_SWITCH_SHARED}_{control.switch}",
                ISSUE_SWITCH_SHARED,
                control.switch in shared,
                placeholders={"switch": control.switch},
                data={"switch": control.switch},
            )
            device = device_of_relay(self._hass, control.switch)
            self._issue(
                f"{ISSUE_SWITCH_IS_DEVICE}_{control.subentry_id}",
                ISSUE_SWITCH_IS_DEVICE,
                device is not None,
                placeholders={
                    "thermostat": control.name,
                    "switch": control.switch,
                    "device": device.title if device else "",
                },
                data={
                    "entry_id": self._entry.entry_id,
                    "subentry_id": control.subentry_id,
                },
            )

    def _issue(
        self,
        issue_id: str,
        key: str,
        active: bool,  # noqa: FBT001
        *,
        placeholders: dict[str, str],
        data: dict[str, str],
    ) -> None:
        if not active:
            ir.async_delete_issue(self._hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=key,
            translation_placeholders=placeholders,
            data=data,
        )

    def _left_alone(self, control: ThermostatControl) -> bool:
        """Return whether the switch is shared or a device relay: no command."""
        return control.switch in self._shared or control.switch in self._relays

    def _control_changed(self, control: ThermostatControl) -> CALLBACK_TYPE:
        """Return the listener that rebalances when the control's state changes."""

        @callback
        def changed() -> None:
            if control.state is not self._states[control.subentry_id]:
                self._states[control.subentry_id] = control.state
                self.async_request_balance()

        return changed

    @callback
    def _on_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if (old and old.state) != (new and new.state):
            self.async_request_balance()

    @callback
    def _on_switch_on(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if (
            new is not None
            and new.state == STATE_ON
            and (old and old.state) != STATE_ON
        ):
            self.async_request_balance()

    @callback
    def async_request_balance(self, _now: datetime | None = None) -> None:
        """
        Run a cycle now; asked for during one, it runs once more after it.

        A cycle never waits for a switch: each command is its own task, so a
        slow switch (an IR remote) holds back neither the others nor the next
        decisions, and a switch's sequence may call a climate service.
        """
        if self._stopped:
            return
        if self._cycling:
            self._pending = True
            return
        # Not a step of the script that asked (a virtual switch's sequence
        # changing a power): its commands, events and timers carry no script
        # stack, else HA refuses another switch's sequence as a recursion
        contextvars.Context().run(self._cycles)

    def _cycles(self) -> None:
        self._cycling = True
        try:
            self._pending = True
            while self._pending:
                self._pending = False
                self._cycle(dt_util.utcnow())
        finally:
            self._cycling = False

    def _cycle(self, now: datetime) -> None:
        """Allocate the capacity by the zone, then send the switches."""
        self._report_shared_switches()
        self._allocate(self._thermostats(), now, self._zone())
        for control in self.controls.values():
            if self._left_alone(control) or (on := control.next_command(now)) is None:
                continue
            command = control.send(on, now)
            command.task = self._entry.async_create_task(
                self._hass, self._async_send(control, on=on), f"switch {control.switch}"
            )
            command.task.add_done_callback(
                partial(self._command_ended, control, command)
            )

    def _capacity(self) -> int:
        """Read the capacity from its entity, the one source of it."""
        entity_id = er.async_get(self._hass).async_get_entity_id(
            "number", DOMAIN, f"{self._entry.entry_id}_capacity"
        )
        state = self._hass.states.get(entity_id) if entity_id else None
        try:
            value = int(float(state.state)) if state else DEFAULT_CAPACITY
        except ValueError:
            return DEFAULT_CAPACITY
        return max(CAPACITY_MIN, min(CAPACITY_MAX, value))

    def _zone_sensors(self) -> dict[Zone, str]:
        """Return the zone sensors of Bestiarium Mode that exist, by zone."""
        registry = er.async_get(self._hass)
        return {
            zone: entity_id
            for zone in SENSOR_ZONES
            if (
                entity_id := registry.async_get_entity_id(
                    "binary_sensor",
                    DOMAIN_BESTIARIUM_MODE,
                    ZONE_SENSOR_UNIQUE_ID.format(zone=zone),
                )
            )
        }

    def _zone(self) -> Zone:
        """
        Return the load zone by the rule of the devices' balancer.

        The highest zone whose sensor is on; none on: the pause while green is
        available (off), no data when it is not. No zone sensor: no limits.
        """
        sensors = self._zone_sensors()
        if not sensors:
            return Zone.NONE
        states = {zone: self._hass.states.get(e) for zone, e in sensors.items()}
        for zone, state in states.items():
            if state is not None and state.state == STATE_ON:
                return zone
        green = states.get(Zone.GREEN)
        if green is not None and green.state == STATE_OFF:
            return Zone.PAUSE
        return Zone.NO_DATA

    def _thermostats(self) -> dict[str, VirtualThermostat]:
        """Return the thermostats of this entry, by subentry id."""
        component = self._hass.data.get(CLIMATE_DATA_COMPONENT)
        entities = list(component.entities) if component else []
        return {
            entity.subentry_id: entity
            for entity in entities
            if entity.platform.platform_name == DOMAIN
            and entity.subentry_id in self.controls
        }

    def _working_time_coefficient(
        self, thermostat: VirtualThermostat, control: ThermostatControl, now: datetime
    ) -> float:
        """Coefficient for deficit sort (ascending = less time first)."""
        if not control.expected_on:
            return 1
        min_required = thermostat.min_cycle_duration
        if min_required.total_seconds() <= 0:
            min_required = self._min_working_time
        actual = (now - control.expected_changed_at).total_seconds()
        return actual / min_required.total_seconds()

    def _allocate(
        self, thermostats: dict[str, VirtualThermostat], now: datetime, zone: Zone
    ) -> None:
        """
        Share the capacity among the thermostats that want to work, by the zone.

        Those that do not work (idle, off), whose switch is gone, shared or
        does not respond, or that have no temperature get nothing: their
        place goes to the others. A deficit is the normal state of a home
        that needs Bestiarium Climate: the ones that worked least go first.

        The zones of the load, as the devices' balancer: green (or no load
        limits) shares the capacity and rotates; yellow only turns off, by
        the rotation, starting none; the pause and no data change nothing;
        red turns the heaters off one by one, the overload all at once. A
        switch the load protection turned off is no failed attempt: out of
        green the balancer does not want it on again.
        """
        # The power cut: a heater that does not heat waits for it, holding no
        # place, and gets no command; one that heats goes off by its 0 W
        # (turned off by another), so a short glitch of a cloud relay commands
        # nothing
        cut: set[ThermostatControl] = set()
        for subentry_id, thermostat in thermostats.items():
            control = self.controls[subentry_id]
            control.pause = thermostat.min_cycle_duration or DEFAULT_PAUSE
        for subentry_id, control in self.controls.items():
            control.defer_if_exhausted(now)
            if not control.heating(now) and power_cut(self._hass, control.switch):
                control.lost_power(now)
                cut.add(control)
            # "No power" only while its thermostat is not off (decision 32)
            thermostat = thermostats.get(subentry_id)
            control.set_power(
                cut=control in cut
                and thermostat is not None
                and thermostat.hvac_mode != HVACMode.OFF,
                now=now,
            )
        working = [
            (thermostat, control)
            for subentry_id, thermostat in thermostats.items()
            if (control := self.controls[subentry_id]).in_rotation(now)
            and not self._left_alone(control)
            and control not in cut
            and thermostat.hvac_action in (HVACAction.HEATING, HVACAction.COOLING)
        ]
        for control in self.controls.values():
            if not any(control is c for _, c in working):
                control.set_expected(False, now)  # noqa: FBT003
        for _thermostat, control in working:
            if control.turned_off_by_another(now):
                control.set_expected(False, now)  # noqa: FBT003
        if zone is not Zone.RED:
            self._red_off_at = None
            self._cancel_red_timer()
        if zone is Zone.RED:
            self._red_step([p for p in working if self._heating(p[1], now)], now)
            return
        capacity = self._capacity()
        ranked = sorted(working, key=lambda pair: self._rank(*pair, now))
        # In green a start waits for the one under way; no load limits, no wait
        self._gate_open = zone is Zone.NONE or (
            zone is Zone.GREEN and not any(c.starting(now) for _, c in ranked)
        )
        LOGGER.debug(
            "Zone %s, capacity %s: %s",
            zone,
            capacity,
            [
                (c.name, round(self._working_time_coefficient(t, c, now), 2))
                for t, c in ranked
            ],
        )
        self._arrange(ranked, capacity, zone, now)

    def _rank(
        self, thermostat: VirtualThermostat, control: ThermostatControl, now: datetime
    ) -> tuple[float, float]:
        """Return the rank: the less worked first, then the colder room."""
        return (
            self._working_time_coefficient(thermostat, control, now),
            -thermostat.temperature_diff,
        )

    def _arrange(
        self,
        ranked: list[tuple[VirtualThermostat, ThermostatControl]],
        capacity: int,
        zone: Zone,
        now: datetime,
    ) -> None:
        """
        Carry out the arrangement by the zone's rules (the owner, decision 32).

        Who should heat is decided without the zone (rotation.arrange). Green
        (or no load limits): its "off"s at once, even before the new heater
        fits, and its starts one at a time, if they fit with the power of
        the ones turned off freed. Yellow: its "off"s only. The pause and no
        data: no rotation. Overload: all off at once. Out of green a heater
        that does not heat is not wanted on (B1).
        """
        deficit = capacity < len(ranked)
        if zone is Zone.OVERLOAD:
            # Not in the devices' emergency queue: climate turns off all at
            # once; no rotation, so no rotation events
            for pair in ranked:
                self._turn(pair, on=False, now=now, deficit=False)
            return
        arranged, freed = self._arranged(ranked, capacity, zone, now)
        for _thermostat, control in ranked:
            # Decide again the moment a pause ends
            if not control.expected_on and (until := control.paused_until(now)):
                self._start_again(until - now)
        if zone not in STARTING_ZONES:
            for pair in ranked:
                control = pair[1]
                on = self._heating(control, now) and (
                    zone is not Zone.YELLOW or control.subentry_id in arranged
                )
                self._turn(pair, on=on, now=now, deficit=deficit)
            return
        out = [
            pair
            for pair in ranked
            if pair[1].expected_on and pair[1].subentry_id not in arranged
        ]
        for pair in out:
            self._turn(pair, on=False, now=now, deficit=deficit)
        for pair in ranked:
            control = pair[1]
            if (
                control.subentry_id in arranged
                and not control.expected_on
                and self._start(control, now, zone, freed[control.subentry_id])
            ):
                self._turn(pair, on=True, now=now, deficit=deficit)

    def _arranged(
        self,
        ranked: list[tuple[VirtualThermostat, ThermostatControl]],
        capacity: int,
        zone: Zone,
        now: datetime,
    ) -> tuple[set[str], dict[str, float]]:
        """
        Return who should heat, and the power each new one frees by its place.

        A waiting one that does not fit holds no place.

        The owner's decision (SRC-140): a waiting heater that does not fit
        even with the power of the running one it would replace freed (load
        minus that one's measured power plus its own over the red threshold;
        an unknown load or threshold does not fit) takes no place, as one in
        its pause: the running one goes on heating. Without load limits all
        fit. While one does not fit, the next step decides again.
        """
        heaters = {
            control.subentry_id: Heater(
                control.subentry_id,
                self._rank(thermostat, control, now),
                running=control.expected_on,
                starting=control.starting(now),
                paused=control.paused_until(now) is not None,
            )
            for thermostat, control in ranked
        }
        unfit: set[str] = set()
        while True:
            arranged = arrange(
                (h for key, h in heaters.items() if key not in unfit), capacity
            )
            new = [
                c for _, c in ranked if c.subentry_id in arranged and not c.expected_on
            ]
            out = [
                c
                for _, c in reversed(ranked)
                if c.expected_on and c.subentry_id not in arranged
            ]
            # Each new one takes the place of the worst-ranked going out
            freed = {
                control.subentry_id: self._measured_power(out[place])
                if place < len(out)
                else 0.0
                for place, control in enumerate(new)
            }
            if zone is Zone.NONE:
                return arranged, freed
            if (
                no_fit := next(
                    (c for c in new if not self._fits(c, freed[c.subentry_id])),
                    None,
                )
            ) is None:
                if unfit:
                    self._start_again(timedelta(seconds=START_STEP_SECONDS))
                return arranged, freed
            unfit.add(no_fit.subentry_id)

    def _start(
        self,
        control: ThermostatControl,
        now: datetime,
        zone: Zone,
        freed: float,
    ) -> bool:
        """Return whether an arranged heater starts now: one at a time, if it fits."""
        if not self._gate_open:
            self._start_again(timedelta(seconds=START_STEP_SECONDS))
            return False
        on = self._may_start(control, now, zone, freed)
        self._gate_open = not on
        return on

    def _turn(
        self,
        pair: tuple[VirtualThermostat, ThermostatControl],
        *,
        on: bool,
        now: datetime,
        deficit: bool,
    ) -> None:
        """Want a heater on or off; in a deficit, tell the rotation."""
        thermostat, control = pair
        if on == control.expected_on:
            return
        coefficient = self._working_time_coefficient(thermostat, control, now)
        control.set_expected(on, now)
        if deficit:
            self._hass.bus.async_fire(
                EVENT_DEVICE_ROTATED_IN if on else EVENT_DEVICE_ROTATED_OUT,
                {
                    "thermostat": thermostat.entity_id,
                    "switch_entity_id": control.switch,
                    "working_time_coefficient": round(coefficient, 2),
                    "temperature_diff": round(thermostat.temperature_diff, 2),
                },
            )

    def _may_start(
        self,
        control: ThermostatControl,
        now: datetime,
        zone: Zone,
        freed: float,
    ) -> bool:
        """
        Return whether a heater may start now: one at a time, if it fits.

        The next start waits a step, so that the load shows the last one.
        Without load limits (no zone) only the capacity counts.
        """
        step = timedelta(seconds=START_STEP_SECONDS)
        if zone is Zone.NONE:
            return True
        if self._started_at is not None and now - self._started_at < step:
            self._start_again(step - (now - self._started_at))
            return False
        if not self._fits(control, freed):
            self._start_again(step)
            return False
        self._started_at = now
        self._start_again(step)
        return True

    def _fits(self, control: ThermostatControl, freed: float) -> bool:
        """
        Return whether a heater's start fits: the rule of the devices' balancer.

        The load, less the measured power freed by the one it takes the place
        of, plus the heater's power stays at or below the red threshold
        (DEV-006, SRC-140); an unknown load or threshold does not fit.
        """
        if (load_and_threshold := self._load_and_red_threshold()) is None:
            LOGGER.debug("%s does not fit: load or red threshold unknown", control.name)
            return False
        load, threshold = load_and_threshold
        power, source = self._heater_power(control)
        if load - freed + power > threshold:
            LOGGER.debug(
                "%s does not fit: %s W - %s W + %s W (%s) > %s W",
                control.name,
                load,
                freed,
                power,
                source,
                threshold,
            )
            return False
        return True

    def _start_again(self, delay: timedelta) -> None:
        """Run a cycle after a delay (the start step, a pause), the earliest one."""
        due = dt_util.utcnow() + delay
        if self._start_timer is not None and self._start_due is not None:
            if self._start_due <= due:
                return
            self._start_timer()
        self._start_due = due
        self._start_timer = async_call_later(
            self._hass, delay.total_seconds(), self._async_start_timer
        )

    @callback
    def _async_start_timer(self, _now: datetime) -> None:
        self._start_timer = self._start_due = None
        self.async_request_balance()

    def _heater_power(self, control: ThermostatControl) -> tuple[float, str]:
        """
        Return the power of the thermostat's heater, and where it comes from.

        The expected power of the Bestiarium device that powers its virtual
        switch; else the power in the thermostat's form; else a large heater.
        """
        device = power_device(self._hass, control.switch)
        if device is not None and (power := expected_power(self._hass, device)):
            return power, "devices"
        if control.heater_power:
            return float(control.heater_power), "thermostat"
        return LARGE_HEATER_POWER_W, "large heater"

    def _measured_power(self, control: ThermostatControl) -> float:
        """
        Return what a heater draws now: the power of its virtual switch.

        A running air conditioner idling draws less than its expected power,
        and frees only that; a switch without a power sensor, the expected.
        """
        entity_id = power_entity(self._hass, control.switch)
        state = self._hass.states.get(entity_id) if entity_id else None
        try:
            return float(state.state) if state else self._heater_power(control)[0]
        except ValueError:
            return self._heater_power(control)[0]

    def _load_and_red_threshold(self) -> tuple[float, float] | None:
        """Return the load and the red threshold of bestiarium_mode, in W."""
        mode_entry = next(
            iter(self._hass.config_entries.async_entries(DOMAIN_BESTIARIUM_MODE)),
            None,
        )
        sensor = (
            (mode_entry.data.get(MODE_AC_LOAD_LIMITS) or {}).get(MODE_AC_POWER_SENSOR)
            if mode_entry
            else None
        )
        red = self._zone_sensors().get(Zone.RED)
        load = self._hass.states.get(sensor) if sensor else None
        red_state = self._hass.states.get(red) if red else None
        if load is None or red_state is None:
            return None
        try:
            return float(load.state), float(red_state.attributes[MODE_RED_THRESHOLD])
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _heating(control: ThermostatControl, now: datetime) -> bool:
        """Return whether the switch heats (or is starting); else want it off."""
        if control.heating(now):
            return True
        control.set_expected(False, now)  # noqa: FBT003
        return False

    def _red_step(
        self, heating: list[tuple[VirtualThermostat, ThermostatControl]], now: datetime
    ) -> None:
        """Red zone: every 5 s the heater whose room needs it least goes off."""
        if not heating:
            return
        step = timedelta(seconds=RED_STEP_SECONDS)
        if self._red_off_at is None or now - self._red_off_at >= step:
            _thermostat, control = min(heating, key=lambda p: p[0].temperature_diff)
            LOGGER.info("%s: turned off in the red zone", control.name)
            control.set_expected(False, now)  # noqa: FBT003
            self._red_off_at = now
            if len(heating) == 1:
                return
        if self._red_timer is None:
            self._red_timer = async_call_later(
                self._hass, RED_STEP_SECONDS, self._async_red_timer
            )

    @callback
    def _async_red_timer(self, _now: datetime) -> None:
        self._red_timer = None
        self.async_request_balance()

    def _cancel_red_timer(self) -> None:
        if self._red_timer is not None:
            self._red_timer()
            self._red_timer = None

    def _command_ended(
        self,
        control: ThermostatControl,
        command: SwitchCommand,
        task: asyncio.Task[None],
    ) -> None:
        """
        Note the end of a command, whatever ended it; decide what was wanted meanwhile.

        An unexpected error of it is logged; the switch is free for the next one.
        """
        if not task.cancelled() and (err := task.exception()) is not None:
            LOGGER.error("%s failed", task.get_name(), exc_info=err)
        control.ended(command, dt_util.utcnow())
        self.async_request_balance()

    async def _async_send(self, control: ThermostatControl, *, on: bool) -> None:
        """
        Send a switch what the balancer wants; one switch never stops the others.

        A command HA refuses counts as an attempt; the control state tells a
        lasting problem, so the refusal itself is only a debug line.
        """
        try:
            await self._hass.services.async_call(
                "switch",
                "turn_on" if on else "turn_off",
                {"entity_id": control.switch},
                blocking=True,
            )
        except HomeAssistantError as err:
            LOGGER.debug("%s: switch %s refused: %s", control.name, control.switch, err)
