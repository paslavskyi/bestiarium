"""
What Bestiarium Climate reads of Bestiarium Devices: the whole contract.

One relay lives in one integration (the owner's rule, DEV-008 review 18): a
thermostat's switch is never a device relay. A virtual switch's power,
though, is often a device: the relay that powers an air conditioner, whose
power sensor the virtual switch reads. Its expected power is the heater's
power, and while its relay is off (the load protection cut it) the heater
cannot start.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_POWER_ENTITY,
    DEVICES_EXPECTED_POWER_UNIQUE_ID,
    DEVICES_POWER_SENSOR,
    DEVICES_SWITCH,
    DOMAIN,
    DOMAIN_BESTIARIUM_DEVICES,
    SUBENTRY_TYPE_SWITCH,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def device_of_relay(hass: HomeAssistant, switch: str) -> ConfigEntry | None:
    """Return the Bestiarium device whose relay is the switch, if any."""
    return next(
        (
            entry
            for entry in hass.config_entries.async_entries(DOMAIN_BESTIARIUM_DEVICES)
            if entry.data.get(DEVICES_SWITCH) == switch
        ),
        None,
    )


def power_device(hass: HomeAssistant, switch: str) -> ConfigEntry | None:
    """Return the Bestiarium device that powers a virtual switch of Climate."""
    entity = er.async_get(hass).async_get(switch)
    if entity is None or entity.platform != DOMAIN:
        return None
    power = next(
        (
            subentry.data.get(CONF_POWER_ENTITY)
            for entry in hass.config_entries.async_entries(DOMAIN)
            for subentry in entry.subentries.values()
            if subentry.subentry_id == entity.unique_id
            and subentry.subentry_type == SUBENTRY_TYPE_SWITCH
        ),
        None,
    )
    return next(
        (
            entry
            for entry in hass.config_entries.async_entries(DOMAIN_BESTIARIUM_DEVICES)
            if power and entry.data.get(DEVICES_POWER_SENSOR) == power
        ),
        None,
    )


def expected_power(hass: HomeAssistant, device: ConfigEntry) -> float | None:
    """Return the expected power of a Bestiarium device, if it is known."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor",
        DOMAIN_BESTIARIUM_DEVICES,
        DEVICES_EXPECTED_POWER_UNIQUE_ID.format(entry_id=device.entry_id),
    )
    state = hass.states.get(entity_id) if entity_id else None
    if state is not None:
        with contextlib.suppress(ValueError):
            return float(state.state)
    return None


def relay_off(hass: HomeAssistant, device: ConfigEntry) -> bool:
    """Return whether the device's relay is off (the power is cut)."""
    state = hass.states.get(device.data.get(DEVICES_SWITCH, ""))
    return state is not None and state.state == STATE_OFF
