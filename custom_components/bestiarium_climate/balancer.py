"""Balancing coordinator for bestiarium_climate."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import (
    DATA_COMPONENT as CLIMATE_DATA_COMPONENT,
)
from homeassistant.components.climate import (
    HVACAction,
)
from homeassistant.const import STATE_ON
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    BALANCING_INTERVAL_SECONDS,
    CAPACITY_MAX,
    CAPACITY_MIN,
    CONF_MIN_WORKING_TIME,
    CONF_RECOVERY_TIMEOUT,
    CONF_TARGET_SWITCH,
    DEFAULT_CAPACITY,
    DOMAIN,
    EVENT_SWITCH_FAILED,
    LOGGER,
    MAX_SWITCH_RETRIES,
)
from .util import duration_dict_to_timedelta

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .climate import VirtualThermostat
    from .data import BestiariumClimateConfigEntry


class BalancingCoordinator:
    """Coordinator for thermostat switch balancing."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumClimateConfigEntry,
    ) -> None:
        """Initialize the balancing coordinator."""
        self._hass = hass
        self._entry = entry
        data = entry.data or {}
        self._min_working_time = duration_dict_to_timedelta(
            data.get(CONF_MIN_WORKING_TIME),
        )
        self._recovery_timeout = duration_dict_to_timedelta(
            data.get(CONF_RECOVERY_TIMEOUT),
        )
        self._unsub: Any = None

    def start(self) -> None:
        """Register periodic balancing (60s interval)."""
        if self._unsub is not None:
            LOGGER.warning(
                "BalancingCoordinator already started for entry %s",
                self._entry.entry_id,
            )
            return
        self._unsub = async_track_time_interval(
            self._hass,
            self._async_balance,
            timedelta(seconds=BALANCING_INTERVAL_SECONDS),
        )
        LOGGER.debug("BalancingCoordinator started for entry %s", self._entry.entry_id)

    def stop(self) -> None:
        """Cancel the periodic balancing."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
            LOGGER.debug(
                "BalancingCoordinator stopped for entry %s",
                self._entry.entry_id,
            )

    def _thermostat_label(self, thermostat: VirtualThermostat) -> str:
        """Return a label for the thermostat (entity_id or subentry_id) for logging."""
        if hasattr(thermostat, "entity_id") and thermostat.entity_id:
            return thermostat.entity_id
        return getattr(thermostat, "_attr_unique_id", "unknown")

    async def _async_balance(self, now: datetime) -> None:
        """Run one cycle: recovery, turn off idle, calculate expected, sync."""
        LOGGER.debug("Balance cycle started for entry %s", self._entry.entry_id)
        await self._handle_recovery(now)
        await self._turn_off_idle()
        await self._calculate_expected_state(now)
        await self._sync_switches(now)

    def _get_capacity(self) -> int:
        """Read current capacity from number entity."""
        entity_id = f"number.{DOMAIN}_capacity"
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            registry = er.async_get(self._hass)
            capacity_unique_id = f"{self._entry.entry_id}_capacity"
            for ent in registry.entities.values():
                if (
                    ent.domain == "number"
                    and ent.platform == DOMAIN
                    and ent.unique_id == capacity_unique_id
                ):
                    entity_id = ent.entity_id
                    state = self._hass.states.get(entity_id)
                    break
        if state is None or state.state in ("unknown", "unavailable", ""):
            return DEFAULT_CAPACITY
        try:
            value = int(float(state.state))
            return max(CAPACITY_MIN, min(CAPACITY_MAX, value))
        except (TypeError, ValueError):
            return DEFAULT_CAPACITY

    def _get_thermostats(self) -> list[VirtualThermostat]:
        """Collect VirtualThermostat entities for this entry."""
        from .climate import VirtualThermostat  # noqa: PLC0415

        comp = self._hass.data.get(CLIMATE_DATA_COMPONENT)
        if comp is None:
            return []
        # Copy to avoid mutation during iteration (EntityComponent doc)
        entities = list(getattr(comp, "entities", []))
        entry_id = self._entry.entry_id
        return [
            e
            for e in entities
            if isinstance(e, VirtualThermostat)
            and (ent := getattr(e, "_entry", None)) is not None
            and ent.entry_id == entry_id
        ]

    def _get_switch_entity_id(self, thermostat: VirtualThermostat) -> str | None:
        """Return target_switch entity_id for this thermostat, or None."""
        subentry = getattr(thermostat, "_subentry", None)
        if subentry is None:
            return None
        return subentry.data.get(CONF_TARGET_SWITCH) or None

    def _event_switch_failed_payload(
        self, thermostat: VirtualThermostat
    ) -> dict[str, Any]:
        """Payload for EVENT_SWITCH_FAILED: entity_id, thermostat, switch_entity_id."""
        label = self._thermostat_label(thermostat)
        return {
            "entity_id": label,
            "thermostat": label,
            "switch_entity_id": self._get_switch_entity_id(thermostat),
        }

    def _get_switch_is_on(self, thermostat: VirtualThermostat) -> bool | None:
        """Read target_switch state. True/False for on/off, None if unknown."""
        entity_id = self._get_switch_entity_id(thermostat)
        if not entity_id:
            return None
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return None
        return state.state == STATE_ON

    async def _async_turn_switch(
        self,
        thermostat: VirtualThermostat,
        turn_on: bool,  # noqa: FBT001
    ) -> None:
        """Call switch.turn_on or switch.turn_off for thermostat's target_switch."""
        entity_id = self._get_switch_entity_id(thermostat)
        if not entity_id:
            return
        service = "turn_on" if turn_on else "turn_off"
        await self._hass.services.async_call(
            "switch",
            service,
            {"entity_id": entity_id},
            blocking=True,
        )

    async def _handle_recovery(self, now: datetime) -> None:
        """Recovery retry for non-responding switches."""
        thermostats = self._get_thermostats()
        for t in thermostats:
            recovery_time = t.recovery_time
            if recovery_time is None or now < recovery_time:
                continue
            label = self._thermostat_label(t)
            switch_on = self._get_switch_is_on(t)
            if switch_on is True:
                tries = t.switch_state_change_tries
                if tries >= MAX_SWITCH_RETRIES:
                    LOGGER.warning(
                        "Switch still on after %d tries for %s",
                        tries,
                        label,
                    )
                    self._hass.bus.async_fire(
                        EVENT_SWITCH_FAILED, self._event_switch_failed_payload(t)
                    )
                await self._async_turn_switch(t, turn_on=False)
                t.set_switch_state_change_tries(tries + 1)
                LOGGER.debug(
                    "Recovery: turned off switch for %s (tries=%d)", label, tries + 1
                )
            else:
                t.set_switch_not_responding(False)
                t.set_recovery_time(None)
                t.set_switch_state_change_tries(0)
                LOGGER.debug("Recovery: cleared for %s (switch is off)", label)

    async def _turn_off_idle(self) -> None:
        """Free capacity from idle/off thermostats (no actual switch check)."""
        thermostats = self._get_thermostats()
        for t in thermostats:
            if t.hvac_action not in (HVACAction.IDLE, HVACAction.OFF):
                continue
            if not t.expected_switch_state:
                continue
            label = self._thermostat_label(t)
            t.set_expected_switch_state(False)
            t.set_switch_state_change_tries(0)
            LOGGER.debug("Turn off idle: cleared expected state for %s", label)

    def _working_time_coefficient(
        self, thermostat: VirtualThermostat, now: datetime
    ) -> float:
        """Coefficient for deficit sort (ascending = less time first)."""
        if not thermostat.expected_switch_state:
            return 1
        min_required = getattr(thermostat, "_min_cycle_duration", None)
        if not min_required or min_required.total_seconds() <= 0:
            min_required = self._min_working_time
        changed_at = thermostat.expected_switch_state_changed_at
        actual = (now - changed_at).total_seconds()
        return actual / min_required.total_seconds()

    def _temperature_diff(self, thermostat: VirtualThermostat) -> float:
        """Temperature difference for sorting (higher = more demand)."""
        target = thermostat.target_temperature
        current = thermostat.current_temperature
        if target is None or current is None:
            return 0.0
        return abs(target - current)

    async def _calculate_expected_state(self, now: datetime) -> None:
        """Allocate capacity to thermostats (three strategies)."""
        working = [
            t
            for t in self._get_thermostats()
            if t.hvac_action in (HVACAction.HEATING, HVACAction.COOLING)
            and not t.switch_not_responding
        ]
        capacity = self._get_capacity()
        n = len(working)

        if capacity <= 0:
            LOGGER.debug("Calculate expected: capacity=0, turning off %d working", n)
            for t in working:
                if t.expected_switch_state:
                    t.set_expected_switch_state(False)
                    t.set_switch_state_change_tries(0)
                    LOGGER.debug("  off: %s", self._thermostat_label(t))
            return

        if capacity >= n:
            LOGGER.debug(
                "Calculate expected: capacity=%d >= %d, turning on all", capacity, n
            )
            for t in working:
                if not t.expected_switch_state:
                    t.set_expected_switch_state(True)
                    t.set_switch_state_change_tries(0)
                    LOGGER.debug("  on: %s", self._thermostat_label(t))
            return

        # Deficit: sort by coefficient ASC, then temp_diff DESC
        LOGGER.debug("Calculate expected: deficit capacity=%d < %d", capacity, n)
        with_coeff = [
            (t, self._working_time_coefficient(t, now), self._temperature_diff(t))
            for t in working
        ]
        with_coeff.sort(key=lambda x: (x[1], -x[2]))
        for i, (t, _coeff, _diff) in enumerate(with_coeff):
            label = self._thermostat_label(t)
            if i < capacity:
                if not t.expected_switch_state:
                    t.set_expected_switch_state(True)
                    t.set_switch_state_change_tries(0)
                    LOGGER.debug(
                        "  on: %s (coeff=%.2f diff=%.2f)", label, _coeff, _diff
                    )
            elif t.expected_switch_state:
                t.set_expected_switch_state(False)
                t.set_switch_state_change_tries(0)
                LOGGER.debug("  off: %s (coeff=%.2f diff=%.2f)", label, _coeff, _diff)

    async def _sync_switches(self, now: datetime) -> None:
        """Sync actual switch state with expected, retries, not_responding."""
        thermostats = self._get_thermostats()
        for t in thermostats:
            if t.switch_not_responding:
                continue
            label = self._thermostat_label(t)
            actual = self._get_switch_is_on(t)
            expected = t.expected_switch_state
            if actual == expected:
                t.set_switch_state_change_tries(0)
                LOGGER.debug("Sync: %s already matches expected", label)
                continue
            tries = t.switch_state_change_tries
            if tries >= MAX_SWITCH_RETRIES:
                LOGGER.warning(
                    "Switch not responding after %d tries for %s",
                    tries,
                    label,
                )
                self._hass.bus.async_fire(
                    EVENT_SWITCH_FAILED, self._event_switch_failed_payload(t)
                )
                t.set_switch_state_change_tries(0)
                t.set_switch_not_responding(True)
                t.set_recovery_time(now + self._recovery_timeout)
                t.set_expected_switch_state(False)
                LOGGER.debug("Sync: marked not_responding, recovery at %s", label)
                continue
            await self._async_turn_switch(t, turn_on=expected)
            t.set_switch_state_change_tries(tries + 1)
            LOGGER.debug(
                "Sync: sent %s for %s (tries=%d)",
                "on" if expected else "off",
                label,
                tries + 1,
            )
