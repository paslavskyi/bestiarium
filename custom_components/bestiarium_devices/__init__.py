"""
Custom integration to integrate Bestiarium Devices with Home Assistant.

For more details about this integration, please refer to
https://github.com/paslavsky/bestiarium
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform  # noqa: TC002
from homeassistant.helpers import device_registry as dr

from .balancer import BalancingCoordinator
from .const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    LOGGER,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import BestiariumDevicesConfigEntry

PLATFORMS: list[Platform] = []

# Key for storing the balancing coordinator in hass.data[DOMAIN]
DATA_COORDINATOR = "balancing_coordinator"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
) -> bool:
    """Set up this integration using UI."""
    LOGGER.debug("Setting up Bestiarium Devices for entry %s", entry.entry_id)

    # Initialize domain data storage if needed
    hass.data.setdefault(DOMAIN, {})

    # Start the balancing coordinator if not already running
    # The coordinator is shared across all device entries
    if DATA_COORDINATOR not in hass.data[DOMAIN]:
        LOGGER.debug("Creating and starting BalancingCoordinator")
        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        hass.data[DOMAIN][DATA_COORDINATOR] = coordinator

    # Virtual devices: ensure device registry entry exists (no platform entities)
    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_VIRTUAL:
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_DEVICE_NAME, "Bestiarium Device"),
            manufacturer="Bestiarium",
            model="Virtual Device",
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    LOGGER.debug("Unloading Bestiarium Devices entry %s", entry.entry_id)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Check if this is the last entry being unloaded
    # If so, stop the balancing coordinator
    remaining_entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]

    if not remaining_entries and DOMAIN in hass.data:
        coordinator = hass.data[DOMAIN].get(DATA_COORDINATOR)
        if coordinator:
            LOGGER.debug("Stopping BalancingCoordinator (last entry unloaded)")
            await coordinator.async_stop()
            del hass.data[DOMAIN][DATA_COORDINATOR]

        # Clean up domain data if empty
        if not hass.data[DOMAIN]:
            del hass.data[DOMAIN]

    return unload_ok


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
