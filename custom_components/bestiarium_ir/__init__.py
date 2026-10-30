"""
Bestiarium IR: IR emitters and IR air conditioners as devices of HA.

One entry; its subentries are Zigbee TS1201 blasters (an infrared emitter of
HA over ZHA) and Hisense air conditioners (a climate device on any infrared
emitter).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .const import CONF_EMITTER, SUBENTRY_TYPE_HISENSE
from .hisense import HisenseLink

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import BestiariumIrConfigEntry

PLATFORMS: list[Platform] = [Platform.INFRARED, Platform.CLIMATE, Platform.SWITCH]


async def async_setup_entry(
    hass: HomeAssistant, entry: BestiariumIrConfigEntry
) -> bool:
    """Set up the subentries' devices."""
    entry.runtime_data = {
        subentry.subentry_id: HisenseLink(hass, subentry.data[CONF_EMITTER])
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_HISENSE
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: BestiariumIrConfigEntry) -> None:
    """Reload when a subentry is added, changed or removed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: BestiariumIrConfigEntry
) -> bool:
    """Unload the subentries' devices."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
