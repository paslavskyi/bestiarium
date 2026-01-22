"""
Custom integration to integrate Bestiarium Mode with Home Assistant.

For more details about this integration, please refer to
https://github.com/paslavsky/bestiarium
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .const import LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import BestiariumModeConfigEntry

PLATFORMS: list[Platform] = [
    Platform.SELECT,
]


# https://developers.home-assistant.io/docs/config_entries_index/#setting-up-an-entry
async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumModeConfigEntry,
) -> bool:
    """Set up this integration using UI."""
    LOGGER.debug("Setting up Bestiarium Mode integration")

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: BestiariumModeConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(
    hass: HomeAssistant,
    entry: BestiariumModeConfigEntry,
) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
