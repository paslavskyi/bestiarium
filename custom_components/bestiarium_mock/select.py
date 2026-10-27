"""Select platform for bestiarium_mock: the fault profile of each mock socket."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, FaultProfile, ReadingProfile
from .entity import MockSocketEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import MockDevicesCoordinator
    from .data import BestiariumMockConfigEntry
    from .readings import MockReading


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
    async_add_entities(
        MockReadingProfileSelect(device_id, reading)
        for device_id, reading in coordinator.readings.items()
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


class MockReadingProfileSelect(SelectEntity, RestoreEntity):
    """The fault profile of a thermometer or of the grid (a test knob)."""

    _attr_has_entity_name = True
    _attr_translation_key = "reading_profile"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, device_id: str, reading: MockReading) -> None:
        """Choose the profile of the device's reading."""
        self._reading = reading
        self._attr_unique_id = f"mock_{device_id}_reading_profile"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"mock_{device_id}")})
        self._attr_options = [profile.value for profile in ReadingProfile]

    @property
    def current_option(self) -> str:
        """Return the profile in effect."""
        return self._reading.profile

    async def async_select_option(self, option: str) -> None:
        """Switch the reading to the profile."""
        sensor = self._sensor_value()
        self._reading.set_profile(ReadingProfile(option), sensor)
        self.async_write_ha_state()

    def _sensor_value(self) -> float:
        """Return the true value of the device's sensor, to freeze it."""
        unique_id = self._attr_unique_id.removesuffix("_reading_profile")
        for suffix in ("_temperature", "_power"):
            entity_id = er.async_get(self.hass).async_get_entity_id(
                "sensor", DOMAIN, unique_id + suffix
            )
            if entity_id and (state := self.hass.states.get(entity_id)):
                try:
                    return float(state.state)
                except ValueError:
                    break
        return 0.0

    async def async_added_to_hass(self) -> None:
        """Restore the profile."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None and (
            last_state.state in self.options
        ):
            self._reading.set_profile(
                ReadingProfile(last_state.state), self._sensor_value()
            )
