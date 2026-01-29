"""Binary sensor platform for bestiarium_mock."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    """Set up the binary sensor platform."""
    LOGGER.debug("Setting up mock device binary sensors")

    coordinator: MockDevicesCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []

    # State sensor for each mock device
    for device_spec in MOCK_DEVICES:
        sensor = MockDeviceStateSensor(entry, device_spec)
        coordinator.register_state_sensor(device_spec.device_id, sensor)
        entities.append(sensor)

    # Grid alarm sensor
    alarm_sensor = MockGridAlarmSensor(entry)
    coordinator.register_grid_alarm_sensor(alarm_sensor)
    entities.append(alarm_sensor)

    async_add_entities(entities)


class MockDeviceStateSensor(BinarySensorEntity):
    """Binary sensor showing on/off state of a mock device."""

    _attr_has_entity_name = True
    _attr_translation_key = "state"
    _attr_device_class = BinarySensorDeviceClass.POWER

    def __init__(
        self,
        entry: BestiariumMockConfigEntry,
        device_spec: MockDeviceSpec,
    ) -> None:
        """Initialize the state sensor."""
        super().__init__()
        self._entry = entry
        self._device_spec = device_spec
        self._attr_is_on: bool = False

        # Entity attributes
        self._attr_unique_id = f"mock_{device_spec.device_id}_state"

        # Device info - group under same device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"mock_{device_spec.device_id}")},
        )

    def set_state(self, *, is_on: bool) -> None:
        """Set the state (called by coordinator)."""
        self._attr_is_on = is_on
        self.async_write_ha_state()


class MockGridAlarmSensor(BinarySensorEntity):
    """Binary sensor for grid overload alarm."""

    _attr_has_entity_name = True
    _attr_translation_key = "grid_alarm"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:flash-alert"

    def __init__(self, entry: BestiariumMockConfigEntry) -> None:
        """Initialize the grid alarm sensor."""
        super().__init__()
        self._entry = entry
        self._attr_is_on: bool = False

        # Entity attributes
        self._attr_unique_id = f"mock_{MOCK_GRID.device_id}_alarm"

        # Device info for grid
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"mock_{MOCK_GRID.device_id}")},
        )

    def set_alarm(self, *, is_alarm: bool) -> None:
        """Set the alarm state (called by coordinator)."""
        self._attr_is_on = is_alarm
        self.async_write_ha_state()
