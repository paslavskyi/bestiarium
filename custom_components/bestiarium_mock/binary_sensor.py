"""Binary sensor platform for bestiarium_mock."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory

from .const import DOMAIN, LOGGER
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

    async_add_entities(entities)


class MockDeviceStateSensor(MockSocketEntity, BinarySensorEntity):
    """
    Whether the load of a mock socket draws power, whatever its switch shows.

    The tester's view of the truth: unlike the switch and the power sensor it
    stays available during a dropout.
    """

    _attr_translation_key = "state"
    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        """Return whether the load draws power; it stays known during a dropout."""
        return self._socket.power > 0
