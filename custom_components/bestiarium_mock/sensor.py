"""Sensor platform for bestiarium_mock."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfPower
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, LOGGER, MOCK_DEVICES, MOCK_GRID, MockDeviceSpec

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import MockDevicesCoordinator
    from .data import BestiariumMockConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumMockConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    LOGGER.debug("Setting up mock device power sensors")

    coordinator: MockDevicesCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    # Power sensors for each mock device
    for device_spec in MOCK_DEVICES:
        sensor = MockDevicePowerSensor(entry, device_spec)
        coordinator.register_power_sensor(device_spec.device_id, sensor)
        entities.append(sensor)

    # Grid power sensor
    grid_sensor = MockGridPowerSensor(entry)
    coordinator.register_grid_power_sensor(grid_sensor)
    entities.append(grid_sensor)

    async_add_entities(entities)


class MockDevicePowerSensor(SensorEntity):
    """Power sensor for a mock device."""

    _attr_has_entity_name = True
    _attr_translation_key = "power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self,
        entry: BestiariumMockConfigEntry,
        device_spec: MockDeviceSpec,
    ) -> None:
        """Initialize the power sensor."""
        super().__init__()
        self._entry = entry
        self._device_spec = device_spec
        self._attr_native_value: float = 0

        # Entity attributes
        self._attr_unique_id = f"mock_{device_spec.device_id}_power"

        # Device info - group under same device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"mock_{device_spec.device_id}")},
        )

    def set_power(self, power: float) -> None:
        """Set the power value (called by coordinator)."""
        self._attr_native_value = power
        self.async_write_ha_state()


class MockGridPowerSensor(SensorEntity):
    """Power sensor for the mock grid (total power consumption)."""

    _attr_has_entity_name = True
    _attr_translation_key = "grid_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, entry: BestiariumMockConfigEntry) -> None:
        """Initialize the grid power sensor."""
        super().__init__()
        self._entry = entry
        self._attr_native_value: float = 0

        # Entity attributes
        self._attr_unique_id = f"mock_{MOCK_GRID.device_id}_power"

        # Device info for grid
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"mock_{MOCK_GRID.device_id}")},
            name=MOCK_GRID.name,
            manufacturer="Bestiarium",
            model="Mock Grid Monitor",
        )

    def set_power(self, power: float) -> None:
        """Set the power value (called by coordinator)."""
        self._attr_native_value = power
        self.async_write_ha_state()
