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
from dataclasses import dataclass
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


def switch_power(hass: HomeAssistant, switch: str) -> float | None:
    """Return what a virtual switch's power sensor reads, if it has one."""
    entity_id = power_entity(hass, switch)
    state = hass.states.get(entity_id) if entity_id else None
    try:
        return float(state.state) if state else None
    except ValueError:
        return None


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


def expected_power(hass: HomeAssistant, device: str) -> float | None:
    """
    Return the expected power of a Bestiarium device (its entry), if known.

    The devices' default for a large device is no knowledge: the
    thermostat's own heater power counts then.
    """
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor",
        DOMAIN_BESTIARIUM_DEVICES,
        DEVICES_EXPECTED_POWER_UNIQUE_ID.format(entry_id=device),
    )
    state = hass.states.get(entity_id) if entity_id else None
    if state is None or state.attributes.get(DEVICES_ESTIMATE_SOURCE) == (
        DEVICES_LARGE_DEFAULT
    ):
        return None
    with contextlib.suppress(ValueError):
        return float(state.state)
    return None


@dataclass(frozen=True)
class PowerSupply:
    """What powers a thermostat's switch, found once and again on a change."""

    switch: str
    # The Bestiarium device that powers its virtual switch, and its relay
    device: str | None = None
    relay: str | None = None
    # The virtual switch's own socket
    socket: str | None = None

    @property
    def relays(self) -> list[str]:
        """Return the relay and the socket there are."""
        return [r for r in (self.relay, self.socket) if r]


def power_supply(hass: HomeAssistant, switch: str) -> PowerSupply:
    """Find what powers a switch: a virtual switch's device and socket, if any."""
    device = power_device(hass, switch)
    return PowerSupply(
        switch,
        device=device.entry_id if device else None,
        relay=device.data.get(DEVICES_SWITCH) if device else None,
        socket=power_socket(hass, switch),
    )


def power_cut(hass: HomeAssistant, supply: PowerSupply) -> bool:
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
        for relay in supply.relays
    )
