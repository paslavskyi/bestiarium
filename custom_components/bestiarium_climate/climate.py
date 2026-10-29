"""Climate platform for bestiarium_climate."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import (
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    UnitOfTemperature,
)
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoredExtraData, RestoreEntity

from .const import (
    CONF_COLD_TOLERANCE,
    CONF_HOT_TOLERANCE,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE_DURATION,
    CONF_MIN_TEMP,
    CONF_PRESETS,
    CONF_TEMPERATURE_SENSOR,
    CONF_THERMOSTAT_MODE,
    DEFAULT_TOLERANCE,
    DOMAIN,
    PRESET_KEYS,
    SIGNAL_CONTROL_CHANGED,
    SUBENTRY_TYPE_UNIVERSAL,
    ClimateMode,
)
from .control import ThermostatMemory, async_get_thermostat_memory
from .util import duration_dict_to_timedelta

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .balancer import BalancingCoordinator
    from .control import ThermostatControl
    from .data import BestiariumClimateConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up climate entities from thermostat subentries."""
    coordinator: BalancingCoordinator = hass.data[DOMAIN][entry.entry_id]
    memory = await async_get_thermostat_memory(hass)
    for subentry_id, control in coordinator.controls.items():
        subentry = entry.subentries[subentry_id]
        thermostat = (
            UniversalThermostat
            if subentry.subentry_type == SUBENTRY_TYPE_UNIVERSAL
            else VirtualThermostat
        )
        async_add_entities(
            [thermostat(hass, entry, subentry, control, memory)],
            config_subentry_id=subentry_id,
        )


class VirtualThermostat(ClimateEntity, RestoreEntity):
    """Virtual thermostat with hysteresis; the balancer controls its switch."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    _model = "Virtual Thermostat"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumClimateConfigEntry,
        subentry: ConfigSubentry,
        control: ThermostatControl,
        memory: ThermostatMemory,
    ) -> None:
        """Initialize the virtual thermostat from subentry data."""
        self._hass = hass
        self._control = control
        self._memory = memory
        self._entry = entry
        self._subentry = subentry

        self._attr_unique_id = subentry.subentry_id
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Bestiarium",
            model=self._model,
            entry_type=DeviceEntryType.SERVICE,
        )

        data = subentry.data
        self._temperature_sensor = data.get(CONF_TEMPERATURE_SENSOR) or ""
        self._ac_mode = data.get(CONF_THERMOSTAT_MODE, ClimateMode.HEATING) == (
            ClimateMode.COOLING
        )
        self._cold_tolerance = float(data.get(CONF_COLD_TOLERANCE, DEFAULT_TOLERANCE))
        self._hot_tolerance = float(data.get(CONF_HOT_TOLERANCE, DEFAULT_TOLERANCE))
        self._min_temp = data.get(CONF_MIN_TEMP)
        self._max_temp = data.get(CONF_MAX_TEMP)
        min_cycle = data.get(CONF_MIN_CYCLE_DURATION)
        self._min_cycle_duration = duration_dict_to_timedelta(min_cycle)

        self._attr_hvac_modes = (
            [HVACMode.COOL, HVACMode.OFF]
            if self._ac_mode
            else [HVACMode.HEAT, HVACMode.OFF]
        )

        if self._min_temp is not None:
            self._attr_min_temp = float(self._min_temp)
        if self._max_temp is not None:
            self._attr_max_temp = float(self._max_temp)

        self._preset_temps: dict[str, float] = {}
        for key in PRESET_KEYS:
            conf_key = CONF_PRESETS.get(key)
            if conf_key and data.get(conf_key) is not None:
                self._preset_temps[key.lower()] = float(data[conf_key])
        # "none" leaves a preset: back to the target set before it
        self._attr_preset_modes = (
            [PRESET_NONE, *self._preset_temps] if self._preset_temps else None
        )

        self._cur_temp: float | None = None
        self._target_temp: float | None = None
        self._saved_target_temp: float | None = None
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_preset_mode = None

        self._unsub: Callable | None = None
        self._attr_hvac_action: HVACAction | None = HVACAction.OFF

    def _update_hvac_action(self) -> bool:
        """Recalculate and store hvac_action from temperature demand."""
        current_hvac_action = self._attr_hvac_action
        if self._attr_hvac_mode == HVACMode.OFF:
            self._attr_hvac_action = HVACAction.OFF
        elif self._ac_mode:
            self._attr_hvac_action = (
                HVACAction.COOLING
                if self._temperature_demand_cool()
                else HVACAction.IDLE
            )
        else:
            self._attr_hvac_action = (
                HVACAction.HEATING
                if self._temperature_demand_heat()
                else HVACAction.IDLE
            )
        return current_hvac_action != self._attr_hvac_action

    def _write_state_and_should_rebalance(self) -> bool:
        """Update hvac_action and state; True if rebalance needed (idle/off)."""
        if not self._update_hvac_action():
            return False
        self.async_write_ha_state()
        return self.hvac_action in (HVACAction.OFF, HVACAction.IDLE)

    @property
    def current_temperature(self) -> float | None:
        """Return current temperature from sensor."""
        return self._cur_temp

    @property
    def target_temperature(self) -> float | None:
        """Return target temperature."""
        return self._target_temp

    def _temperature_demand_heat(self) -> bool:
        """Return whether heating is needed (current temp below target - cold_tol)."""
        if self._target_temp is None or self._cur_temp is None:
            return False
        return self._cur_temp < self._target_temp - self._cold_tolerance

    def _temperature_demand_cool(self) -> bool:
        """Return whether cooling is needed (current temp above target + hot_tol)."""
        if self._target_temp is None or self._cur_temp is None:
            return False
        return self._cur_temp > self._target_temp + self._hot_tolerance

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return stored hvac_action (updated on temperature/mode changes)."""
        return self._attr_hvac_action

    @property
    def subentry_id(self) -> str:
        """Return the thermostat's subentry id."""
        return self._subentry.subentry_id

    @property
    def min_cycle_duration(self) -> timedelta:
        """Return the thermostat's own minimum working time (0: the entry's)."""
        return self._min_cycle_duration

    @property
    def temperature_diff(self) -> float:
        """Return how far the temperature is from the target (higher: more demand)."""
        if self._target_temp is None or self._cur_temp is None:
            return 0.0
        return abs(self._target_temp - self._cur_temp)

    @property
    def manual(self) -> bool:
        """Return whether a person runs it: the balancer leaves it alone."""
        return False

    @callback
    def turn_off_by_zone(self) -> None:
        """Turn the thermostat off for the load zone; the cycle goes on."""
        self._attr_hvac_mode = HVACMode.OFF
        self._update_hvac_action()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return False while the thermostat has had no temperature too long."""
        return not self._control.no_temperature

    def _temp_from_preset(self, preset: str | None) -> float | None:
        """Target temperature for preset, or None."""
        if not preset:
            return None
        return self._preset_temps.get(preset.lower())

    @callback
    def _async_temp_changed(self, event: Any) -> None:
        """Handle temperature sensor state change."""
        new_state = event.data.get("new_state")
        # Without a reading the last temperature counts; the control decides
        # when it is gone too long (docs/concepts/stability.md)
        with contextlib.suppress(AttributeError, TypeError, ValueError):
            self._cur_temp = float(new_state.state)
        if self._write_state_and_should_rebalance():
            self._request_balance()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode (heat/cool/off)."""
        self._attr_hvac_mode = hvac_mode
        self._update_hvac_action()
        self.async_write_ha_state()
        self._request_balance()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._target_temp = float(temp)
            if self._attr_preset_mode:
                self._save_target_temp(self._target_temp)
        if self._write_state_and_should_rebalance():
            self._request_balance()

    async def async_set_preset_mode(self, preset_mode: str | None) -> None:
        """Set preset mode; target temp comes from preset or saved value."""
        if not preset_mode or preset_mode == PRESET_NONE:
            self._attr_preset_mode = None
            if self._saved_target_temp is not None:
                self._target_temp = self._saved_target_temp
            if self._write_state_and_should_rebalance():
                self._request_balance()
            return
        if preset_mode not in (self._preset_temps or {}):
            return
        self._attr_preset_mode = preset_mode
        preset_temp = self._temp_from_preset(preset_mode)
        if preset_temp is not None:
            self._save_target_temp(self._target_temp)
            self._target_temp = preset_temp
        if self._write_state_and_should_rebalance():
            self._request_balance()

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Keep the mode, the target and the preset, also while unavailable."""
        return RestoredExtraData(
            {
                "hvac_mode": self._attr_hvac_mode,
                "temperature": self._target_temp,
                "preset_mode": self._attr_preset_mode,
            }
        )

    async def _async_restore(self) -> None:
        """
        Restore the mode, the target and the preset.

        An unavailable state has no attributes (a thermostat without
        temperature at shutdown), so they are kept as extra data; a state
        saved before DEV-008 stage B has only the attributes.
        """
        if extra := await self.async_get_last_extra_data():
            kept = extra.as_dict()
        elif last_state := await self.async_get_last_state():
            kept = {"hvac_mode": last_state.state, **last_state.attributes}
        else:
            kept = {}
        if kept.get("hvac_mode") in self._restorable_modes():
            self._attr_hvac_mode = HVACMode(kept["hvac_mode"])
        with contextlib.suppress(TypeError, ValueError):
            if kept.get("temperature") is not None:
                self._target_temp = float(kept["temperature"])
        if kept.get("preset_mode"):
            self._attr_preset_mode = kept["preset_mode"]
        self._saved_target_temp = self._memory.get(self.subentry_id).get(
            "saved_temperature"
        )

    def _restorable_modes(self) -> tuple[HVACMode, ...]:
        """Return the modes a restored state may set."""
        return (HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF)

    def _save_target_temp(self, value: float | None) -> None:
        """Remember the target to go back to when the preset ends."""
        self._saved_target_temp = value
        self._memory.update(self.subentry_id, saved_temperature=value)

    def _apply_default_target_temp(self) -> None:
        """Set default target temp if not restored."""
        if self._target_temp is None and self._preset_temps:
            first = next(iter(self._preset_temps.values()), None)
            if first is not None:
                self._target_temp = first
        if self._target_temp is None:
            self._target_temp = 20.0

    def _subscribe_temperature(self) -> None:
        """Subscribe to temperature sensor and set initial cur_temp."""
        if not self._temperature_sensor:
            return
        state = self._hass.states.get(self._temperature_sensor)
        if state and state.state not in ("unknown", "unavailable", ""):
            with contextlib.suppress(TypeError, ValueError):
                self._cur_temp = float(state.state)
        self._unsub = async_track_state_change_event(
            self._hass,
            [self._temperature_sensor],
            self._async_temp_changed,
        )

    def _request_balance(self) -> None:
        """Ask the balancer for a cycle at once; it does not wait for it."""
        self._hass.data[DOMAIN][self._entry.entry_id].async_request_balance()

    async def async_added_to_hass(self) -> None:
        """Restore state and subscribe to temperature sensor."""
        await super().async_added_to_hass()

        await self._async_restore()
        self._apply_default_target_temp()
        self._subscribe_temperature()
        self._update_hvac_action()
        self.async_write_ha_state()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CONTROL_CHANGED.format(self._control.subentry_id),
                self._async_control_changed,
            )
        )

    @callback
    def _async_control_changed(self) -> None:
        """Show the availability as soon as the control confirms it."""
        state = self.hass.states.get(self.entity_id)
        if state is not None and (state.state != STATE_UNAVAILABLE) != self.available:
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from temperature sensor."""
        if self._unsub:
            self._unsub()
            self._unsub = None


class UniversalThermostat(VirtualThermostat):
    """
    Universal thermostat: auto balanced, off, or manual (heat or cool).

    Auto heats or cools, by its setting, as the balancer decides; manual
    runs its switch by the temperature alone, outside the capacity, until
    the red zone or the overload turns it off.
    """

    _model = "Universal Thermostat"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumClimateConfigEntry,
        subentry: ConfigSubentry,
        control: ThermostatControl,
        memory: ThermostatMemory,
    ) -> None:
        """Initialize the thermostat; its manual mode is its heating or cooling."""
        super().__init__(hass, entry, subentry, control, memory)
        self._manual_mode = HVACMode.COOL if self._ac_mode else HVACMode.HEAT
        self._attr_hvac_modes = [HVACMode.AUTO, self._manual_mode, HVACMode.OFF]

    @property
    def manual(self) -> bool:
        """Return whether it is in its manual mode."""
        return self._attr_hvac_mode == self._manual_mode

    def _restorable_modes(self) -> tuple[HVACMode, ...]:
        """Return its own modes."""
        return tuple(self._attr_hvac_modes)
