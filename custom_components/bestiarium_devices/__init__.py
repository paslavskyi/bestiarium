"""
Custom integration to integrate Bestiarium Devices with Home Assistant.

For more details about this integration, please refer to
https://github.com/paslavsky/bestiarium
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .const import LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import device_registry as dr

    from .data import BestiariumDevicesConfigEntry

PLATFORMS: list[Platform] = [
    Platform.NUMBER,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
) -> bool:
    """Set up this integration using UI."""
    LOGGER.debug("Setting up Bestiarium Devices for entry %s", entry.entry_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    LOGGER.debug("Unloading Bestiarium Devices entry %s", entry.entry_id)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant,  # noqa: ARG001
    config_entry: BestiariumDevicesConfigEntry,  # noqa: ARG001
    device_entry: dr.DeviceEntry,  # noqa: ARG001
) -> bool:
    """
    Allow removal of config entry from device.

    Home Assistant automatically removes entities associated with a config entry
    when the entry is removed, so no explicit entity cleanup is needed here.
    """
    LOGGER.debug("Removing device entry from Bestiarium Devices")
    return True
