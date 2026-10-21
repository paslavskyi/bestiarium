"""Sensor platform for bestiarium_devices: the device's expected power."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import CONF_ESTIMATED_POWER, SIGNAL_EXPECTED_POWER_CHANGED
from .entity import BestiariumDeviceEntity
from .power_memory import PowerMemory, async_get_power_memory

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BestiariumDevicesConfigEntry

# Translated attribute value explaining when the manual value stops counting
MANUAL_POWER_NOTE = "counts_until_real_runs"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the expected power sensor of a device entry."""
    memory = await async_get_power_memory(hass)
    async_add_entities([ExpectedPowerSensor(hass, entry, memory)])


class ExpectedPowerSensor(BestiariumDeviceEntity, SensorEntity):
    """The power the balancer expects the device to draw when turned on."""

    _key = "expected_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumDevicesConfigEntry,
        memory: PowerMemory,
    ) -> None:
        """Follow the device's power memory."""
        super().__init__(hass, entry)
        self._memory = memory

    async def async_added_to_hass(self) -> None:
        """Update on memory changes and on a new manual value."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_EXPECTED_POWER_CHANGED.format(self._entry.entry_id),
                self.async_write_ha_state,
            )
        )

    @property
    def native_value(self) -> float:
        """Return the expected power."""
        return self._memory.expected_power(self._entry)[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return what the expectation is based on."""
        return {
            "estimate_source": self._memory.expected_power(self._entry)[1],
            "runs": [
                record.as_dict()
                for record in self._memory.records(self._entry.entry_id)
            ],
            "manual_power_w": self._entry.options.get(CONF_ESTIMATED_POWER) or None,
            "manual_power_note": MANUAL_POWER_NOTE,
        }
