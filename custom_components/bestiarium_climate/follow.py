"""
Follow a renamed entity of the entry's thermostats and virtual switches.

A subentry keeps the entity_ids of a thermostat's switch and temperature
sensor, and of a virtual switch's power sensor and socket. A rename in HA
writes the new one into the subentry, and the entry reloads (its
configuration changed), as Bestiarium Devices does with its relay and power
sensor. A sequence of a virtual switch running then is cut short; the next
cycle sends what is wanted again (the owner, DEV-008 stage C).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_entity_registry_updated_event

from .const import (
    CONF_AIR_CONDITIONER,
    CONF_POWER_ENTITY,
    CONF_POWER_SOCKET,
    CONF_TARGET_SWITCH,
    CONF_TEMPERATURE_SENSOR,
    LOGGER,
)

if TYPE_CHECKING:
    from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant
    from homeassistant.helpers.entity_registry import EventEntityRegistryUpdatedData

    from .data import BestiariumClimateConfigEntry

# The fields of the subentries that name an entity
FOLLOWED = (
    CONF_TARGET_SWITCH,
    CONF_AIR_CONDITIONER,
    CONF_TEMPERATURE_SENSOR,
    CONF_POWER_ENTITY,
    CONF_POWER_SOCKET,
)


@callback
def async_follow_renames(
    hass: HomeAssistant, entry: BestiariumClimateConfigEntry
) -> CALLBACK_TYPE:
    """Write a renamed entity's new entity_id into the subentries that name it."""
    entity_ids = {
        entity_id
        for subentry in entry.subentries.values()
        for key in FOLLOWED
        if (entity_id := subentry.data.get(key))
    }

    @callback
    def renamed(event: Event[EventEntityRegistryUpdatedData]) -> None:
        if event.data["action"] != "update" or "old_entity_id" not in event.data:
            return
        old, new = event.data["old_entity_id"], event.data["entity_id"]
        for subentry in list(entry.subentries.values()):
            keys = [key for key in FOLLOWED if subentry.data.get(key) == old]
            if not keys:
                continue
            LOGGER.info("%s: follows %s renamed to %s", subentry.title, old, new)
            hass.config_entries.async_update_subentry(
                entry, subentry, data={**subentry.data, **dict.fromkeys(keys, new)}
            )

    return async_track_entity_registry_updated_event(hass, entity_ids, renamed)
