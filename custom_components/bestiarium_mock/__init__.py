"""
Custom integration to simulate mock devices for Bestiarium testing.

For more details about this integration, please refer to
https://github.com/paslavskyi/bestiarium
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .const import DOMAIN, LOGGER
from .coordinator import MockDevicesCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import BestiariumMockConfigEntry

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumMockConfigEntry,
) -> bool:
    """Set up this integration using UI."""
    LOGGER.debug("Setting up Bestiarium Mock Devices for entry %s", entry.entry_id)

    # Create coordinator
    coordinator = MockDevicesCoordinator(hass, entry)

    # Store coordinator in hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start coordinator after all entities are set up
    await coordinator.async_start()

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: BestiariumMockConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    LOGGER.debug("Unloading Bestiarium Mock Devices entry %s", entry.entry_id)

    # Stop coordinator
    coordinator: MockDevicesCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_stop()

    # Remove from hass.data
    hass.data[DOMAIN].pop(entry.entry_id)

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(
    hass: HomeAssistant,
    entry: BestiariumMockConfigEntry,
) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
