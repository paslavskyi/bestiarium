"""
Custom integration to integrate Bestiarium Climate with Home Assistant.

For more details about this integration, please refer to
https://github.com/paslavskyi/bestiarium
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .balancer import BalancingCoordinator
from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import BestiariumClimateConfigEntry

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SWITCH,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
) -> bool:
    """Set up Bestiarium Climate from a config entry."""
    LOGGER.debug("Setting up Bestiarium Climate for entry %s", entry.entry_id)

    hass.data.setdefault(DOMAIN, {})
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    coordinator = BalancingCoordinator(hass, entry)
    coordinator.start()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
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

    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if isinstance(coordinator, BalancingCoordinator):
        coordinator.stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
