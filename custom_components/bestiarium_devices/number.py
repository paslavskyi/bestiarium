"""Number platform for bestiarium_devices."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_MODE_OPTIONS_SNAPSHOT,
    DEVICE_TYPE_EXTENDED,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    LOAD_PRIORITY_DEFAULT,
    LOAD_PRIORITY_MAX,
    LOAD_PRIORITY_MIN,
    LOGGER,
)
from .helpers import get_device_id_from_entry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BestiariumDevicesConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number platform."""
    LOGGER.debug("Setting up number entities for entry %s", entry.entry_id)

    mode_snapshot = entry.data.get(CONF_MODE_OPTIONS_SNAPSHOT, [])

    entities: list[LoadPriorityNumber] = [LoadPriorityNumber(hass, entry, None)]

    # Create per-mode priority entities
    entities.extend(
        LoadPriorityNumber(hass, entry, mode_name)
        for mode_name in mode_snapshot
        if mode_name != CONF_DEFAULT_PRIORITY
    )

    async_add_entities(entities)


class LoadPriorityNumber(NumberEntity, RestoreEntity):
    """Load priority number entity."""

    _attr_native_min_value = LOAD_PRIORITY_MIN
    _attr_native_max_value = LOAD_PRIORITY_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumDevicesConfigEntry,
        mode_name: str | None,
    ) -> None:
        """Initialize the load priority number entity."""
        super().__init__()
        self.hass = hass
        self._entry = entry
        self._mode_name = mode_name  # None for default priority

        # Get initial value from config entry
        priorities = entry.data.get(CONF_LOAD_PRIORITIES, {})
        key = CONF_DEFAULT_PRIORITY if mode_name is None else mode_name
        self._attr_native_value = priorities.get(key, LOAD_PRIORITY_DEFAULT)

        # Set unique ID
        device_id = get_device_id_from_entry(entry)
        suffix = "_default" if mode_name is None else f"_{mode_name}"
        self._attr_unique_id = f"{device_id}_priority{suffix}"

        # Set device info
        self._attr_device_info = self._get_device_info()

    def _get_device_info(self) -> DeviceInfo | None:
        """Get device info for this entity."""
        device_type = self._entry.data.get(CONF_DEVICE_TYPE)

        if device_type == DEVICE_TYPE_VIRTUAL:
            # Virtual device - create new device entry
            return DeviceInfo(
                identifiers={(DOMAIN, self._entry.entry_id)},
                name=self._entry.data.get(CONF_DEVICE_NAME, "Unknown Device"),
                manufacturer="Bestiarium",
                model="Virtual Device",
            )

        if device_type == DEVICE_TYPE_EXTENDED:
            # Extended device - attach to existing device
            device_id = self._entry.data.get(CONF_DEVICE_ID)
            if device_id:
                device_registry = dr.async_get(self.hass)
                device = device_registry.async_get(device_id)
                if device:
                    return DeviceInfo(
                        identifiers=device.identifiers,
                    )

        return None

    @property
    def translation_key(self) -> str:
        """Return the translation key."""
        if self._mode_name is None:
            return "default_priority"
        return "mode_priority"

    @property
    def translation_placeholders(self) -> dict[str, str]:
        """Return translation placeholders."""
        if self._mode_name is None:
            return {}
        return {"mode_name": self._mode_name}

    async def async_set_native_value(self, value: float) -> None:
        """Set the native value."""
        self._attr_native_value = int(value)
        self.async_write_ha_state()

        # Update config entry data
        priorities = self._entry.data.get(CONF_LOAD_PRIORITIES, {}).copy()
        key = CONF_DEFAULT_PRIORITY if self._mode_name is None else self._mode_name
        priorities[key] = int(value)

        # Update config entry
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={**self._entry.data, CONF_LOAD_PRIORITIES: priorities},
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()

        # Restore state if available
        if (
            last_state := await self.async_get_last_state()
        ) and last_state.state not in ("unknown", "unavailable"):
            try:
                value = float(last_state.state)
                if LOAD_PRIORITY_MIN <= value <= LOAD_PRIORITY_MAX:
                    self._attr_native_value = int(value)
            except (ValueError, TypeError):
                pass
