"""
Custom integration to integrate Bestiarium Mode with Home Assistant.

For more details about this integration, please refer to
https://github.com/paslavskyi/bestiarium
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .const import LOGGER
from .data import power_load_monitoring_configured
from .repairs import async_watch_power_sensor
from .zones import ZoneTracker

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import BestiariumModeConfigEntry

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
]


# https://developers.home-assistant.io/docs/config_entries_index/#setting-up-an-entry
async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumModeConfigEntry,
) -> bool:
    """Set up this integration using UI."""
    LOGGER.debug("Setting up Bestiarium Mode integration")
    entry.runtime_data = None
    if power_load_monitoring_configured(entry):
        entry.runtime_data = tracker = ZoneTracker(hass, entry)
        tracker.async_start()
        entry.async_on_unload(tracker.async_stop)
        entry.async_on_unload(async_watch_power_sensor(hass, entry))

    # No update listener: the load limit entity updates the entry at runtime
    # without a reload; the reconfigure flow reloads explicitly.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: BestiariumModeConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
