"""
Custom integration to integrate Bestiarium Climate with Home Assistant.

For more details about this integration, please refer to
https://github.com/paslavskyi/bestiarium
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .balancer import BalancingCoordinator
from .const import DOMAIN, LOGGER, SUBENTRY_TYPE_SWITCH

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import BestiariumClimateConfigEntry

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
) -> bool:
    """Set up Bestiarium Climate from a config entry."""
    LOGGER.debug("Setting up Bestiarium Climate for entry %s", entry.entry_id)

    coordinator = BalancingCoordinator(hass, entry)
    await coordinator.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
) -> bool:
    """
    Migrate the entry to version 1.2.

    1.2: a virtual switch takes heating or cooling from its thermostats; its
    own mode field goes (DEV-008 stage B).
    """
    if entry.minor_version < 2:  # noqa: PLR2004
        for subentry in entry.subentries.values():
            if (
                subentry.subentry_type == SUBENTRY_TYPE_SWITCH
                and "mode" in subentry.data
            ):
                data = {k: v for k, v in subentry.data.items() if k != "mode"}
                hass.config_entries.async_update_subentry(entry, subentry, data=data)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


async def _async_update_listener(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
) -> None:
    """Reload entry when config (including subentries) changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
) -> bool:
    """Unload a config entry."""
    LOGGER.debug("Unloading Bestiarium Climate entry %s", entry.entry_id)

    hass.data[DOMAIN][entry.entry_id].stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
