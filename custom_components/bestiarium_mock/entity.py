"""Base entity for the entities of a mock socket."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN

if TYPE_CHECKING:
    from .faults import MockSocket
    from .home_power import MockHomePower
    from .ir_ac import MockIrAc


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
            translation_key=device_id,
            manufacturer="Bestiarium",
            model="Mock Smart Socket",
        )

    async def async_added_to_hass(self) -> None:
        """Follow the socket."""
        await super().async_added_to_hass()
        self.async_on_remove(self._socket.add_listener(self.async_write_ha_state))


class MockIrAcEntity(Entity):
    """An entity of the stand's IR air conditioner, updated on its every change."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, ac: MockIrAc, key: str) -> None:
        """Place the entity on the air conditioner's device."""
        self._ac = ac
        device_id = ac.spec.device_id
        self._attr_unique_id = f"mock_{device_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"mock_{device_id}")},
            translation_key=device_id,
            manufacturer="Bestiarium",
            model="Mock IR Air Conditioner",
        )

    async def async_added_to_hass(self) -> None:
        """Follow the air conditioner."""
        await super().async_added_to_hass()
        self.async_on_remove(self._ac.add_listener(self.async_write_ha_state))


class MockHomePowerEntity(Entity):
    """An entity of the stand's home power, updated on its every change."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, home_power: MockHomePower, key: str) -> None:
        """Place the entity on the home power's device."""
        self._home_power = home_power
        self._attr_unique_id = f"mock_home_power_{key}"
        self._attr_translation_key = f"home_power_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "mock_home_power")},
            translation_key="home_power",
            manufacturer="Bestiarium",
            model="Mock Home Power",
        )

    async def async_added_to_hass(self) -> None:
        """Follow the home power."""
        await super().async_added_to_hass()
        self.async_on_remove(self._home_power.add_listener(self.async_write_ha_state))
