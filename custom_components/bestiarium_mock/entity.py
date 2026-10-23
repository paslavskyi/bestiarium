"""Base entity for the entities of a mock socket."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN

if TYPE_CHECKING:
    from .faults import MockSocket


class MockSocketEntity(Entity):
    """An entity of a mock socket, updated on every change of the socket."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, socket: MockSocket, key: str | None) -> None:
        """Place the entity on the socket's device; key None is the socket itself."""
        self._socket = socket
        device_id = socket.spec.device_id
        self._attr_unique_id = f"mock_{device_id}_{key}" if key else f"mock_{device_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"mock_{device_id}")},
            name=socket.spec.name,
            manufacturer="Bestiarium",
            model="Mock Smart Socket",
        )

    async def async_added_to_hass(self) -> None:
        """Follow the socket."""
        await super().async_added_to_hass()
        self.async_on_remove(self._socket.add_listener(self.async_write_ha_state))
