"""Switch platform for bestiarium_devices: the per-device 'Allowed' switch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    CONF_ALLOWED,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_SWITCH_ENTITY_ID,
    DATA_COORDINATOR,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
)
from .helpers import is_allowed

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BestiariumDevicesConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the 'Allowed' switch of a device entry."""
    async_add_entities([AllowedSwitch(hass, entry)])


class AllowedSwitch(SwitchEntity):
    """
    Whether the balancer may turn the device on.

    The state lives in the config entry options, so it survives restarts and
    the balancer reads it synchronously with the rest of the entry.
    """

    _attr_has_entity_name = True

    def __init__(
        self, hass: HomeAssistant, entry: BestiariumDevicesConfigEntry
    ) -> None:
        """Place the switch on the device the relay belongs to."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_allowed"
        self._attr_translation_key = "allowed"
        if entry.data[CONF_DEVICE_TYPE] == DEVICE_TYPE_VIRTUAL:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)},
                name=entry.data[CONF_DEVICE_NAME],
                manufacturer="Bestiarium",
                model="Virtual Device",
            )
        # An extended device belongs to another integration; link the switch to
        # it instead of owning it, as core helper integrations do.
        elif device := async_entity_id_to_device(
            hass, entry.data[CONF_SWITCH_ENTITY_ID]
        ):
            self.device_entry = device
        else:
            self._attr_translation_key = "allowed_standalone"
            self._attr_translation_placeholders = {
                "device_name": entry.data[CONF_DEVICE_NAME]
            }

    @property
    def is_on(self) -> bool:
        """Return whether the balancer may turn the device on."""
        return is_allowed(self._entry)

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Allow the balancer to turn the device on."""
        await self._async_set_allowed(allowed=True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Forbid the balancer to turn the device on; turn it off now."""
        await self._async_set_allowed(allowed=False)

    async def _async_set_allowed(self, *, allowed: bool) -> None:
        if allowed == self.is_on:
            return
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_ALLOWED: allowed}
        )
        self.async_write_ha_state()
        await self.hass.data[DOMAIN][DATA_COORDINATOR].async_allowed_changed(
            self._entry
        )
