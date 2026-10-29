"""
What Bestiarium Climate reads of Bestiarium Devices: the whole contract.

One relay lives in one integration (the owner's rule, DEV-008 review 18): a
thermostat's switch is never a device relay. A virtual switch's power,
though, is often a device: the relay that powers an air conditioner, whose
power sensor the virtual switch reads. Its expected power is the heater's
power, and while its relay is off (the load protection cut it) a heater that
does not heat leaves the rotation until the power is back. So does one whose
own socket (a field of its virtual switch) is off: the stepwise "off" cut it,
or a person.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_POWER_ENTITY,
    CONF_POWER_SOCKET,
    DEVICES_ESTIMATE_SOURCE,
    DEVICES_EXPECTED_POWER_UNIQUE_ID,
    DEVICES_LARGE_DEFAULT,
    DEVICES_POWER_SENSOR,
    DEVICES_SWITCH,
    DOMAIN_BESTIARIUM_DEVICES,
)
from .switch import virtual_switch

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


def power_entity(hass: HomeAssistant, switch: str) -> str | None:
    """Return the power sensor a virtual switch of Climate reads, if it is one."""
    found = virtual_switch(hass, switch)
    return found[1].data.get(CONF_POWER_ENTITY) if found else None


def power_socket(hass: HomeAssistant, switch: str) -> str | None:
    """Return the socket of a virtual switch of Climate, if it has one."""
    found = virtual_switch(hass, switch)
    return found[1].data.get(CONF_POWER_SOCKET) if found else None


def power_device(hass: HomeAssistant, switch: str) -> ConfigEntry | None:
    """Return the Bestiarium device that powers a virtual switch of Climate."""
    power = power_entity(hass, switch)
    devices = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN_BESTIARIUM_DEVICES)
        if power and entry.data.get(DEVICES_POWER_SENSOR) == power
    ]
    # Two devices on one power sensor: none is surely the power
    return devices[0] if len(devices) == 1 else None


def expected_power(hass: HomeAssistant, device: ConfigEntry) -> float | None:
    """
    Return the expected power of a Bestiarium device, if it is known.

    The devices' default for a large device is no knowledge: the
    thermostat's own heater power counts then.
    """
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor",
        DOMAIN_BESTIARIUM_DEVICES,
        DEVICES_EXPECTED_POWER_UNIQUE_ID.format(entry_id=device.entry_id),
    )
    state = hass.states.get(entity_id) if entity_id else None
    if state is None or state.attributes.get(DEVICES_ESTIMATE_SOURCE) == (
        DEVICES_LARGE_DEFAULT
    ):
        return None
    with contextlib.suppress(ValueError):
        return float(state.state)
    return None


def power_cut(hass: HomeAssistant, switch: str) -> bool:
    """
    Return whether the power of a virtual switch's device is off.

    The relay of the device powering it, or its own socket: the load
    protection, the stepwise "off" (or a person) cut the power. A heater
    that does not heat (not on, no start under way) leaves the rotation,
    holding no place of the capacity, until the power is back. One that heats
    stays: it goes off by its 0 W, so a moment "off" of a cloud relay
    commands nothing.
    """
    return any(
        (state := hass.states.get(relay)) is not None and state.state == STATE_OFF
        for relay in power_relays(hass, [switch])
    )


def power_relays(hass: HomeAssistant, switches: list[str]) -> list[str]:
    """Return the relays and sockets that power these virtual switches."""
    relays = []
    for switch in switches:
        device = power_device(hass, switch)
        relays += [
            relay
            for relay in (
                device.data.get(DEVICES_SWITCH) if device else None,
                power_socket(hass, switch),
            )
            if relay
        ]
    return relays
