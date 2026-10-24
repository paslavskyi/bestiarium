"""Number platform for bestiarium_climate."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CAPACITY_MAX,
    CAPACITY_MIN,
    CAPACITY_STEP,
    CONF_CAPACITY,
    DEFAULT_CAPACITY,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .data import BestiariumClimateConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the capacity number entity for the config entry."""
    async_add_entities([CapacityNumber(hass, entry)])


class CapacityNumber(NumberEntity, RestoreEntity):
    """
    How many thermostats may work at once (0-100): the one source of it.

    A power automation changes it; the entry's form gives only its initial
    value. It is operated, so it has no entity category.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "capacity"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = float(CAPACITY_MIN)
    _attr_native_max_value = float(CAPACITY_MAX)
    _attr_native_step = float(CAPACITY_STEP)

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumClimateConfigEntry,
    ) -> None:
        """Initialize the capacity number entity."""
        self._hass = hass
        self._entry = entry
        self._attr_native_value = float(entry.data.get(CONF_CAPACITY, DEFAULT_CAPACITY))
        self._attr_unique_id = f"{entry.entry_id}_capacity"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Bestiarium Climate",
            manufacturer="Bestiarium",
            model="Capacity",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set the capacity and share it again at once."""
        self._attr_native_value = value
        self.async_write_ha_state()
        await self._hass.data[DOMAIN][self._entry.entry_id].async_balance()

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass; fallback to config entry."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (
            "unknown",
            "unavailable",
        ):
            try:
                value = float(last_state.state)
                if self._attr_native_min_value <= value <= self._attr_native_max_value:
                    self._attr_native_value = value
            except (ValueError, TypeError):
                pass
        else:
            # No restored state: use config entry initial value
            self._attr_native_value = float(
                self._entry.data.get(CONF_CAPACITY, DEFAULT_CAPACITY)
            )
