"""
Bestiarium IR: IR emitters and IR air conditioners as devices of HA.

One entry; its subentries are Zigbee TS1201 blasters (an infrared emitter of
HA over ZHA) and Hisense air conditioners (a climate device on any infrared
emitter).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS: list[Platform] = [Platform.INFRARED]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the subentries' devices."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when a subentry is added, changed or removed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the subentries' devices."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
