"""Switch platform for bestiarium_mock: the switch of each mock socket."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
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
    """Set up the switch platform."""
    coordinator: MockDevicesCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MockDeviceSwitch(socket, None) for socket in coordinator.sockets.values()
    )


class MockDeviceSwitch(MockSocketEntity, SwitchEntity, RestoreEntity):
    """The socket's switch: shows the reported state, passes commands to it."""

    _attr_translation_key = "device_switch"

    @property
    def available(self) -> bool:
        """Return whether the socket is reachable."""
        return self._socket.available

    @property
    def is_on(self) -> bool:
        """Return the state the socket reports."""
        return self._socket.reported_on

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Send the socket a turn on command."""
        self._socket.command(on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Send the socket a turn off command."""
        self._socket.command(on=False)

    async def async_added_to_hass(self) -> None:
        """Restore the socket's state."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._socket.restore(on=last_state.state == STATE_ON)
