"""Base entity for the entities of a device entry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import (
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import BestiariumDevicesConfigEntry


class BestiariumDeviceEntity(Entity):
    """An entity of a device entry, placed on the device its relay belongs to."""

    _attr_has_entity_name = True
    _key: str  # unique_id suffix and translation key

    def __init__(
        self, hass: HomeAssistant, entry: BestiariumDevicesConfigEntry
    ) -> None:
        """Place the entity on the device of the entry's relay."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self._attr_translation_key = self._key
        if entry.data[CONF_DEVICE_TYPE] == DEVICE_TYPE_VIRTUAL:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)},
                name=entry.data[CONF_DEVICE_NAME],
                manufacturer="Bestiarium",
                model="Virtual Device",
            )
        # An extended device belongs to another integration; link the entity to
        # it instead of owning it, as core helper integrations do.
        elif device := async_entity_id_to_device(
            hass, entry.data[CONF_SWITCH_ENTITY_ID]
        ):
            self.device_entry = device
        else:
            self._attr_translation_key = f"{self._key}_standalone"
            self._attr_translation_placeholders = {
                "device_name": entry.data[CONF_DEVICE_NAME]
            }
