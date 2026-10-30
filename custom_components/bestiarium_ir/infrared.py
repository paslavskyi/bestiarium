"""
Layer 1: a Zigbee TS1201 blaster as an infrared emitter of HA.

A command's timings go to the blaster in the Tuya/Zosung code format through
ZHA's cluster command, as the home's scripts send them. ZHA reports a command
to a sleeping blaster as sent at once: there is no confirmation, and no
retry here (a thermostat confirms by the power drawn). A temporary bridge:
once HA's core shows the TS1201 as an emitter, this subentry type goes and
the air conditioners on it stay as they are.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.infrared import InfraredEmitterEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_device_registry_updated_event

from .const import (
    CONF_ZHA_DEVICE,
    DOMAIN,
    SUBENTRY_TYPE_BLASTER,
    ZHA_DOMAIN,
    ZHA_SERVICE,
    ZOSUNG_CLUSTER,
    ZOSUNG_ENDPOINT,
    ZOSUNG_SEND,
)
from .tuya import encode_code

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, ConfigSubentry
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from infrared_protocols.commands import Command


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the emitter of each blaster."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_BLASTER:
            async_add_entities(
                [ZigbeeBlasterEmitter(hass, subentry)],
                config_subentry_id=subentry.subentry_id,
            )


def _ieee(hass: HomeAssistant, device_id: str) -> str | None:
    """Return the IEEE address of a ZHA device, None if it is gone."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    return next(
        (value for domain, value in device.identifiers if domain == ZHA_DOMAIN),
        None,
    )


class ZigbeeBlasterEmitter(InfraredEmitterEntity):
    """The emitter of a TS1201; unavailable while its ZHA device is gone."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, subentry: ConfigSubentry) -> None:
        """Place the emitter on the blaster's device, behind the ZHA device."""
        self._device_id: str = subentry.data[CONF_ZHA_DEVICE]
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Bestiarium",
            model="TS1201 IR bridge",
        )
        if dr.async_get(hass).async_get(self._device_id):
            self._attr_device_info["via_device_id"] = self._device_id

    async def async_added_to_hass(self) -> None:
        """Follow the ZHA device."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_device_registry_updated_event(
                self.hass, self._device_id, self._device_changed
            )
        )

    @callback
    def _device_changed(self, _event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return whether the ZHA device is there."""
        return _ieee(self.hass, self._device_id) is not None

    async def async_send_command(self, command: Command) -> None:
        """Send the command's timings, repeated as it asks, in one code."""
        try:
            code = encode_code(command.get_raw_timings() * (command.repeat_count + 1))
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="timing_too_long",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.hass.services.async_call(
            ZHA_DOMAIN,
            ZHA_SERVICE,
            {
                "ieee": _ieee(self.hass, self._device_id),
                "endpoint_id": ZOSUNG_ENDPOINT,
                "cluster_id": ZOSUNG_CLUSTER,
                "cluster_type": "in",
                "command": ZOSUNG_SEND,
                "command_type": "server",
                "params": {"code": code},
            },
            blocking=True,
        )
