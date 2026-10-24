"""Balancing coordinator for bestiarium_climate."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.climate import (
    DATA_COMPONENT as CLIMATE_DATA_COMPONENT,
)
from homeassistant.components.climate import HVACAction
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    BALANCING_INTERVAL_SECONDS,
    CAPACITY_MAX,
    CAPACITY_MIN,
    CONF_MIN_WORKING_TIME,
    DEFAULT_CAPACITY,
    DOMAIN,
    EVENT_DEVICE_ROTATED_IN,
    EVENT_DEVICE_ROTATED_OUT,
    LOGGER,
    SUBENTRY_TYPE_THERMOSTAT,
)
from .control import ThermostatControl, async_get_thermostat_memory
from .util import duration_dict_to_timedelta

if TYPE_CHECKING:
    from homeassistant.core import CALLBACK_TYPE, HomeAssistant

    from .climate import VirtualThermostat
    from .data import BestiariumClimateConfigEntry


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
        self._unsub: CALLBACK_TYPE | None = None

    async def async_load(self) -> None:
        """Make the control of each thermostat with what it kept."""
        memory = await async_get_thermostat_memory(self._hass)
        self.controls = {
            subentry.subentry_id: ThermostatControl(
                self._hass, self._entry, subentry, memory
            )
            for subentry in self._entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_THERMOSTAT
        }

    def start(self) -> None:
        """Follow the switches and balance every minute."""
        for control in self.controls.values():
            control.async_start()
        self._unsub = async_track_time_interval(
            self._hass,
            self.async_balance,
            timedelta(seconds=BALANCING_INTERVAL_SECONDS),
        )

    def stop(self) -> None:
        """Stop balancing and following."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        for control in self.controls.values():
            control.async_stop()

    async def async_balance(self, now: datetime | None = None) -> None:
        """Run one cycle: allocate the capacity, then send the switches."""
        now = now or dt_util.utcnow()
        thermostats = self._thermostats()
        self._allocate(thermostats, now)
        for control in self.controls.values():
            await self._async_sync(control, now)

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
        self, thermostats: dict[str, VirtualThermostat], now: datetime
    ) -> None:
        """
        Share the capacity among the thermostats that want to work.

        Those that do not work (idle, off), whose switch is gone or does not
        respond, or that have no temperature get nothing: their place goes
        to the others. A deficit is the normal state of a home that needs
        Bestiarium Climate: the ones that worked least go first.
        """
        working = [
            (thermostat, control)
            for subentry_id, thermostat in thermostats.items()
            if (control := self.controls[subentry_id]).in_rotation(now)
            and thermostat.hvac_action in (HVACAction.HEATING, HVACAction.COOLING)
        ]
        for control in self.controls.values():
            if not any(control is c for _, c in working):
                control.set_expected(False, now)  # noqa: FBT003
        capacity = self._capacity()
        ranked = sorted(
            working,
            key=lambda pair: (
                self._working_time_coefficient(*pair, now),
                -pair[0].temperature_diff,
            ),
        )
        deficit = capacity < len(ranked)
        for place, (thermostat, control) in enumerate(ranked):
            on = place < capacity
            if on == control.expected_on:
                continue
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

    async def _async_sync(self, control: ThermostatControl, now: datetime) -> None:
        """
        Send a switch what the balancer wants; one switch never stops the others.

        A command HA refuses counts as an attempt; the control state tells a
        lasting problem, so the refusal itself is only a debug line.
        """
        if (on := control.to_command(now)) is None:
            return
        try:
            await self._hass.services.async_call(
                "switch",
                "turn_on" if on else "turn_off",
                {"entity_id": control.switch},
                blocking=True,
            )
        except HomeAssistantError as err:
            LOGGER.debug("%s: switch %s refused: %s", control.name, control.switch, err)
