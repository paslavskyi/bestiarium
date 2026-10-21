"""Button platform for bestiarium_devices: reset the device's power memory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import BestiariumDeviceEntity
from .power_memory import PowerMemory, async_get_power_memory

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BestiariumDevicesConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the reset button of a device entry."""
    memory = await async_get_power_memory(hass)
    async_add_entities([ResetPowerMemoryButton(hass, entry, memory)])


class ResetPowerMemoryButton(BestiariumDeviceEntity, ButtonEntity):
    """Forget the device's runs; its history is not imported again."""

    _key = "reset_power_memory"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumDevicesConfigEntry,
        memory: PowerMemory,
    ) -> None:
        """Reset the given memory."""
        super().__init__(hass, entry)
        self._memory = memory

    async def async_press(self) -> None:
        """Forget the device's runs."""
        self._memory.reset(self._entry.entry_id)
