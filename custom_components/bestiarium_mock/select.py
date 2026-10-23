"""Select platform for bestiarium_mock: the fault profile of each mock socket."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, FaultProfile
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
    """Set up the select platform."""
    coordinator: MockDevicesCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MockFaultProfileSelect(socket, "fault_profile")
        for socket in coordinator.sockets.values()
    )


class MockFaultProfileSelect(MockSocketEntity, SelectEntity, RestoreEntity):
    """The fault profile; every profile but 'custom' sets all fault parameters."""

    _attr_translation_key = "fault_profile"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def options(self) -> list[str]:
        """Return the fault profiles."""
        return [profile.value for profile in FaultProfile]

    @property
    def current_option(self) -> str:
        """Return the profile in effect."""
        return self._socket.profile

    async def async_select_option(self, option: str) -> None:
        """Switch the socket to the profile."""
        self._socket.set_profile(FaultProfile(option))

    async def async_added_to_hass(self) -> None:
        """Restore the profile."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None and (
            last_state.state in self.options
        ):
            self._socket.restore_profile(FaultProfile(last_state.state))
