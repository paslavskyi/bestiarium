"""Number platform for bestiarium_devices: the device's estimated power."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import CONF_ESTIMATED_POWER, SIGNAL_EXPECTED_POWER_CHANGED
from .entity import BestiariumDeviceEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BestiariumDevicesConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the estimated power number of a device entry."""
    async_add_entities([EstimatedPowerNumber(hass, entry)])


class EstimatedPowerNumber(BestiariumDeviceEntity, NumberEntity):
    """
    The power the owner expects the device to draw; 0 means not set.

    It counts until the device has 3 real runs. The value lives in the config
    entry options, like the 'Allowed' switch.
    """

    _key = "estimated_power"
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_min_value = 0
    _attr_native_max_value = 100_000
    _attr_native_step = 10
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def native_value(self) -> float:
        """Return the estimated power, 0 if not set."""
        return self._entry.options.get(CONF_ESTIMATED_POWER, 0)

    async def async_set_native_value(self, value: float) -> None:
        """Store a new estimated power."""
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_ESTIMATED_POWER: int(value)},
        )
        self.async_write_ha_state()
        async_dispatcher_send(
            self.hass, SIGNAL_EXPECTED_POWER_CHANGED.format(self._entry.entry_id)
        )
