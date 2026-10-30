"""
Custom integration to integrate Bestiarium Mode with Home Assistant.

For more details about this integration, please refer to
https://github.com/paslavskyi/bestiarium
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .binary_sensor import ZONE_ICONS
from .card import async_register_card
from .const import DOMAIN, LOGGER
from .data import power_load_monitoring_configured
from .repairs import async_watch_power_sensor
from .zones import ZoneTracker

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

    from .data import BestiariumModeConfigEntry

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:  # noqa: ARG001
    """Serve the power flow card (DEV-014)."""
    await async_register_card(hass)
    return True


# https://developers.home-assistant.io/docs/config_entries_index/#setting-up-an-entry
async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumModeConfigEntry,
) -> bool:
    """Set up this integration using UI."""
    LOGGER.debug("Setting up Bestiarium Mode integration")
    entry.runtime_data = None
    if not power_load_monitoring_configured(entry):
        _remove_zone_sensors(hass)
    else:
        entry.runtime_data = tracker = ZoneTracker(hass, entry)
        tracker.async_start()
        entry.async_on_unload(tracker.async_stop)
        entry.async_on_unload(async_watch_power_sensor(hass, entry))

    # No update listener: the load limit entity updates the entry at runtime
    # without a reload; the reconfigure flow reloads explicitly.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _remove_zone_sensors(hass: HomeAssistant) -> None:
    """
    Remove the zone sensors of load limits that were removed.

    Orphans would stay unavailable and the balancer would follow them.
    """
    registry = er.async_get(hass)
    for zone in ZONE_ICONS:
        if entity_id := registry.async_get_entity_id(
            Platform.BINARY_SENSOR, DOMAIN, f"power_load_{zone}"
        ):
            registry.async_remove(entity_id)


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: BestiariumModeConfigEntry,
) -> bool:
    """
    Migrate the mode entry to version 1.2.

    1.2: a mode is its name; the description and the icon of each mode go
    (DEV-008 stage B, docs/concepts/necessity.md: nothing read them).
    """
    if entry.minor_version < 2:  # noqa: PLR2004
        options = [{"name": option["name"]} for option in entry.data.get("options", [])]
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "options": options}, minor_version=2
        )
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: BestiariumModeConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
