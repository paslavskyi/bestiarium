"""Binary sensor platform for bestiarium_mock."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, LOGGER, MOCK_GRID
from .entity import MockSocketEntity

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
    entities: list[BinarySensorEntity] = [
        MockDeviceStateSensor(socket, "state")
        for socket in coordinator.sockets.values()
    ]

    # Grid alarm sensor
    alarm_sensor = MockGridAlarmSensor(entry)
    coordinator.register_grid_alarm_sensor(alarm_sensor)
    entities.append(alarm_sensor)

    async_add_entities(entities)


class MockDeviceStateSensor(MockSocketEntity, BinarySensorEntity):
    """
    Whether the load of a mock socket draws power, whatever its switch shows.

    The tester's view of the truth: unlike the switch and the power sensor it
    stays available during a dropout.
    """

    _attr_translation_key = "state"
    _attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def is_on(self) -> bool:
        """Return whether the load draws power; it stays known during a dropout."""
        return self._socket.power > 0


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
