"""
Follow a renamed emitter of the entry's air conditioners.

A rename in HA writes the new entity_id into the subentry, and the entry
reloads (its configuration changed), as Bestiarium Climate does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_entity_registry_updated_event

from .const import CONF_EMITTER, LOGGER

if TYPE_CHECKING:
    from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant
    from homeassistant.helpers.entity_registry import EventEntityRegistryUpdatedData

    from .data import BestiariumIrConfigEntry


@callback
def async_follow_renames(
    hass: HomeAssistant, entry: BestiariumIrConfigEntry
) -> CALLBACK_TYPE:
    """Write a renamed emitter's new entity_id into the subentries that name it."""
    emitters = {
        emitter
        for subentry in entry.subentries.values()
        if (emitter := subentry.data.get(CONF_EMITTER))
    }

    @callback
    def renamed(event: Event[EventEntityRegistryUpdatedData]) -> None:
        if event.data["action"] != "update" or "old_entity_id" not in event.data:
            return
        old, new = event.data["old_entity_id"], event.data["entity_id"]
        for subentry in list(entry.subentries.values()):
            if subentry.data.get(CONF_EMITTER) == old:
                LOGGER.info("%s: follows %s renamed to %s", subentry.title, old, new)
                hass.config_entries.async_update_subentry(
                    entry, subentry, data={**subentry.data, CONF_EMITTER: new}
                )

    return async_track_entity_registry_updated_event(hass, emitters, renamed)
