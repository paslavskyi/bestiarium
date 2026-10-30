"""
Select platform for bestiarium_climate: the auto mode of an air conditioner thermostat.

What auto does, heat or cool: an automation of the home switches it by the
season (FEAT-057), so it is an entity, not a field of the form. Its value is
kept with what the thermostats keep across restarts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, SIGNAL_AUTO_MODE_CHANGED, SUBENTRY_TYPE_AC, ClimateMode
from .control import ThermostatMemory, async_get_thermostat_memory

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .data import BestiariumClimateConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the auto mode of each air conditioner thermostat, on its device."""
    memory = await async_get_thermostat_memory(hass)
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_TYPE_AC:
            async_add_entities(
                [AutoModeSelect(subentry_id, memory)], config_subentry_id=subentry_id
            )


class AutoModeSelect(SelectEntity):
    """What auto of an air conditioner thermostat does: heat or cool."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "auto_mode"
    _attr_options: ClassVar[list[str]] = [ClimateMode.HEATING, ClimateMode.COOLING]

    def __init__(self, subentry_id: str, memory: ThermostatMemory) -> None:
        """Show the auto mode of one thermostat."""
        self._subentry_id = subentry_id
        self._memory = memory
        self._attr_unique_id = f"{subentry_id}_auto_mode"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, subentry_id)})

    @property
    def current_option(self) -> str:
        """Return the auto mode; heating until chosen."""
        return self._memory.get(self._subentry_id).get("auto_mode", ClimateMode.HEATING)

    async def async_select_option(self, option: str) -> None:
        """Choose the auto mode; its thermostat decides again."""
        self._memory.update(self._subentry_id, auto_mode=option)
        self.async_write_ha_state()
        async_dispatcher_send(
            self.hass, SIGNAL_AUTO_MODE_CHANGED.format(self._subentry_id)
        )
