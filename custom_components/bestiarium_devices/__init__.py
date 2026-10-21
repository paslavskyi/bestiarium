"""
Custom integration to integrate Bestiarium Devices with Home Assistant.

For more details about this integration, please refer to
https://github.com/paslavsky/bestiarium
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .balancer import BalancingCoordinator
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_SWITCH_ENTITY_ID,
    DATA_COORDINATOR,
    DEVICE_TYPE_EXTENDED,
    DOMAIN,
    LOGGER,
)
from .helpers import get_device_id_from_entry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import BestiariumDevicesConfigEntry

PLATFORMS: list[Platform] = [Platform.SWITCH]


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
) -> bool:
    """
    Migrate a device entry to version 1.2.

    1.2: an extended device whose device_id names no device any more (a
    pre-2026.8 composite id, or a deleted device) points to its relay's device.
    """
    if entry.minor_version < 2:  # noqa: PLR2004
        data = dict(entry.data)
        if relay_device_id := _relay_device_to_migrate_to(hass, entry):
            data[CONF_DEVICE_ID] = relay_device_id
        hass.config_entries.async_update_entry(entry, data=data, minor_version=2)
    return True


def _relay_device_to_migrate_to(
    hass: HomeAssistant, entry: BestiariumDevicesConfigEntry
) -> str | None:
    """Return the relay's device for a stale device_id, if it can take its place."""
    if entry.data.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_EXTENDED:
        return None
    device_id = entry.data.get(CONF_DEVICE_ID)
    if dr.async_get(hass).async_get(device_id, include_composite_devices=False):
        return None
    relay = er.async_get(hass).async_get(entry.data[CONF_SWITCH_ENTITY_ID])
    if relay is None or relay.device_id is None:
        return None
    if taken_by := next(
        (
            other.title
            for other in hass.config_entries.async_entries(DOMAIN)
            if other.entry_id != entry.entry_id
            and get_device_id_from_entry(other) == relay.device_id
        ),
        None,
    ):
        LOGGER.warning(
            "Device %s keeps its stale device id: the device of its relay is "
            "already used by %s; reconfigure it to pick the device again",
            entry.data.get(CONF_DEVICE_NAME, entry.title),
            taken_by,
        )
        return None
    return relay.device_id


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

    # No update listener: the 'Allowed' switch updates the entry options at
    # runtime without a reload; the reconfigure flow reloads explicitly.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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
