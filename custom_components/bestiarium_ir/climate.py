"""
Layer 2: a Hisense air conditioner (Kelon168) as a climate device.

Modelled on HA's gree_infrared: the state is assumed (IR has no answer) and
restored after a restart; every frame carries the whole state. The emitter
is any infrared entity of HA.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.infrared import InfraredEmitterConsumerEntity
from homeassistant.const import (
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .const import CONF_EMITTER, DOMAIN, SUBENTRY_TYPE_HISENSE
from .hisense import ACTIONS, Change
from .kelon168 import (
    AUTO_TEMP,
    MAX_TEMP,
    MIN_TEMP,
    Kelon168Command,
    Kelon168Fan,
    Kelon168Mode,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .data import BestiariumIrConfigEntry
    from .hisense import HisenseLink

# Every mode but off; power is a field of its own
_MODES: dict[HVACMode, Kelon168Mode] = {
    HVACMode.HEAT: Kelon168Mode.HEAT,
    HVACMode.COOL: Kelon168Mode.COOL,
    HVACMode.DRY: Kelon168Mode.DRY,
    HVACMode.FAN_ONLY: Kelon168Mode.FAN,
    HVACMode.AUTO: Kelon168Mode.AUTO,
}


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: BestiariumIrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the climate entity of each air conditioner."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_HISENSE:
            async_add_entities(
                [HisenseClimate(subentry, entry.runtime_data[subentry.subentry_id])],
                config_subentry_id=subentry.subentry_id,
            )


def hisense_device(subentry: ConfigSubentry) -> DeviceInfo:
    """Return the device of an air conditioner."""
    return DeviceInfo(
        identifiers={(DOMAIN, subentry.subentry_id)},
        name=subentry.title,
        manufacturer="Hisense",
        model="Kelon168 (DG11R2-01)",
    )


@dataclass
class _HisenseExtraStoredData(ExtraStoredData):
    """The mode the unit was last on in: "on" goes back to it after a restart."""

    last_active_hvac_mode: str

    @override
    def as_dict(self) -> dict[str, Any]:
        return {"last_active_hvac_mode": self.last_active_hvac_mode}


class HisenseClimate(InfraredEmitterConsumerEntity, ClimateEntity, RestoreEntity):
    """
    The air conditioner.

    A target or a fan speed of a unit that is off, or in its own auto (23 °C,
    fan auto), is kept for later and sends nothing.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "hisense"
    _attr_assumed_state = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_min_temp = float(MIN_TEMP)
    _attr_max_temp = float(MAX_TEMP)
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, subentry: ConfigSubentry, link: HisenseLink) -> None:
        """Start off, heat being the mode "on" goes to."""
        self._link = link
        self._infrared_emitter_entity_id = subentry.data[CONF_EMITTER]
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = hisense_device(subentry)
        self._attr_hvac_modes = [HVACMode.OFF, *_MODES]
        self._attr_fan_modes = [fan.value for fan in Kelon168Fan]
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_target_temperature = float(AUTO_TEMP)
        self._attr_fan_mode = Kelon168Fan.AUTO.value
        self._last_active = HVACMode.HEAT

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the assumed state; give the display its frames."""
        await super().async_added_to_hass()
        self._link.display_frame = self._display_frame
        last = await self.async_get_last_state()
        if last is not None and last.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            if last.state in self._attr_hvac_modes:
                self._attr_hvac_mode = HVACMode(last.state)
            if last.attributes.get(ATTR_FAN_MODE) in self._attr_fan_modes:
                self._attr_fan_mode = last.attributes[ATTR_FAN_MODE]
            if (temperature := last.attributes.get(ATTR_TEMPERATURE)) is not None:
                self._attr_target_temperature = float(round(float(temperature)))
        if self._attr_hvac_mode is not HVACMode.OFF:
            self._last_active = self._attr_hvac_mode
        elif (extra := await self.async_get_last_extra_data()) is not None and (
            mode := extra.as_dict().get("last_active_hvac_mode")
        ) in _MODES:
            self._last_active = HVACMode(mode)

    @property
    @override
    def extra_restore_state_data(self) -> ExtraStoredData:
        return _HisenseExtraStoredData(self._last_active.value)

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Turn off, on in a mode, or change the mode; always sends its frame."""
        await self._apply(self._mode_change(hvac_mode), hvac_mode=hvac_mode)

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target, with a mode when one is given."""
        temperature = float(round(kwargs[ATTR_TEMPERATURE]))
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self._apply(
                self._mode_change(hvac_mode),
                hvac_mode=hvac_mode,
                temperature=temperature,
            )
        else:
            await self._apply(Change.TEMPERATURE, temperature=temperature)

    @override
    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan speed."""
        await self._apply(Change.FAN, fan_mode=fan_mode)

    @override
    async def async_turn_on(self) -> None:
        """Turn on in the last mode."""
        await self.async_set_hvac_mode(self._last_active)

    @override
    async def async_turn_off(self) -> None:
        """Turn off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    def _mode_change(self, hvac_mode: HVACMode) -> Change:
        if hvac_mode is HVACMode.OFF:
            return Change.OFF
        if hvac_mode is HVACMode.AUTO:
            return Change.OWN_AUTO
        if self._attr_hvac_mode is HVACMode.OFF:
            return Change.TURN_ON
        return Change.MODE

    async def _apply(
        self,
        change: Change,
        *,
        hvac_mode: HVACMode | None = None,
        temperature: float | None = None,
        fan_mode: str | None = None,
    ) -> None:
        """Send the frame of the change, then show the new state."""
        hvac_mode = hvac_mode or self._attr_hvac_mode or HVACMode.OFF
        temperature = temperature or self._attr_target_temperature or AUTO_TEMP
        fan_mode = fan_mode or self._attr_fan_mode or Kelon168Fan.AUTO.value
        # A target or a speed of a unit off or in its own auto waits for later
        if change not in (Change.TEMPERATURE, Change.FAN) or hvac_mode not in (
            HVACMode.OFF,
            HVACMode.AUTO,
        ):
            await self._link.send(
                self._command(hvac_mode, int(temperature), fan_mode, change),
                self._context,
            )
        self._attr_hvac_mode = hvac_mode
        self._attr_target_temperature = temperature
        self._attr_fan_mode = fan_mode
        if hvac_mode is not HVACMode.OFF:
            self._last_active = hvac_mode
        self.async_write_ha_state()

    def _display_frame(self) -> Kelon168Command | None:
        """Return the display frame of the state, None while the unit is off."""
        if self._attr_hvac_mode in (None, HVACMode.OFF):
            return None
        return self._command(
            self._attr_hvac_mode,
            int(self._attr_target_temperature or AUTO_TEMP),
            self._attr_fan_mode or Kelon168Fan.AUTO.value,
            Change.DISPLAY,
        )

    def _command(
        self, hvac_mode: HVACMode, temperature: int, fan_mode: str, change: Change
    ) -> Kelon168Command:
        """Return the frame of a state; the unit's auto runs 23 °C, fan auto."""
        mode = _MODES[self._last_active if hvac_mode is HVACMode.OFF else hvac_mode]
        own_auto = mode is Kelon168Mode.AUTO
        return Kelon168Command(
            mode=mode,
            temperature=AUTO_TEMP if own_auto else temperature,
            fan=Kelon168Fan.AUTO if own_auto else Kelon168Fan(fan_mode),
            power=hvac_mode is not HVACMode.OFF,
            action=ACTIONS[change],
        )
