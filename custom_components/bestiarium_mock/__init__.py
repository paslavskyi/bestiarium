"""
Custom integration to simulate mock devices for Bestiarium testing.

For more details about this integration, please refer to
https://github.com/paslavskyi/bestiarium
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, LOGGER, MOCK_DEVICES, MOCK_GRID, MOCK_THERMOMETERS
from .coordinator import MockDevicesCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import BestiariumMockConfigEntry

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
]


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: BestiariumMockConfigEntry,
) -> bool:
    """
    Migrate the entry to version 2: the device set of the DEV-007 test stand.

    Devices no longer in the set go with their entities, and the 'Mock Enabled'
    switches of the remaining ones (the fault profile replaced them).
    """
    if entry.version < 2:  # noqa: PLR2004
        kept = {
            (DOMAIN, f"mock_{spec.device_id}")
            for spec in (*MOCK_DEVICES, *MOCK_THERMOMETERS, MOCK_GRID)
        }
        device_registry = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(
            device_registry, entry.entry_id
        ):
            if not device.identifiers & kept:
                device_registry.async_remove_device(device.id)
        entity_registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        ):
            if entity.unique_id.endswith("_mock_enabled"):
                entity_registry.async_remove(entity.entity_id)
        hass.config_entries.async_update_entry(entry, version=2)
    return True


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

    _remove_grid_alarm(hass)
    # Forward to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start coordinator after all entities are set up
    await coordinator.async_start()

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


def _remove_grid_alarm(hass: HomeAssistant) -> None:
    """Remove the grid's overload alarm, gone in DEV-008 stage B (an orphan)."""
    registry = er.async_get(hass)
    if entity_id := registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"mock_{MOCK_GRID.device_id}_alarm"
    ):
        registry.async_remove(entity_id)


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
