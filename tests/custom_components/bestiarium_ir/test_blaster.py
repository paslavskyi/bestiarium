"""A Zigbee TS1201 as an infrared emitter of HA (layer 1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.components.infrared import async_send_command
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from infrared_protocols.commands.gree_ac import GreeAcCommand, GreeAcMode

from custom_components.bestiarium_ir.kelon168 import Kelon168Command, Kelon168Mode

from .conftest import IEEE, RawCommand
from .tuya_codes import decode_code, load_vectors

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall
    from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_home_code_goes_to_zha(
    hass: HomeAssistant,
    blaster: tuple[MockConfigEntry, str],
    zha_calls: list[ServiceCall],
) -> None:
    """A Kelon168 frame reaches ZHA as the home's scripts send it."""
    _, emitter = blaster
    await async_send_command(
        hass, emitter, Kelon168Command(mode=Kelon168Mode.HEAT, temperature=24)
    )
    assert zha_calls[-1].data == {
        "ieee": IEEE,
        "endpoint_id": 1,
        "cluster_id": 57348,
        "cluster_type": "in",
        "command": 2,
        "command_type": "server",
        "params": {"code": load_vectors()["home"]["heat"]["24"]},
    }


async def test_any_protocol_is_sent(
    hass: HomeAssistant,
    blaster: tuple[MockConfigEntry, str],
    zha_calls: list[ServiceCall],
) -> None:
    """The bridge knows timings, not protocols."""
    _, emitter = blaster
    command = GreeAcCommand(mode=GreeAcMode.COOL, temperature=24)
    await async_send_command(hass, emitter, command)
    assert decode_code(zha_calls[-1].data["params"]["code"]) == [
        abs(t) for t in command.get_raw_timings()
    ]


async def test_repeats_are_sent_in_one_code(
    hass: HomeAssistant,
    blaster: tuple[MockConfigEntry, str],
    zha_calls: list[ServiceCall],
) -> None:
    """A command repeated once: its timings twice in one code."""
    _, emitter = blaster
    await async_send_command(
        hass, emitter, RawCommand([9000, -4500, 560, -20000], repeat_count=1)
    )
    assert len(zha_calls) == 1
    assert (
        decode_code(zha_calls[-1].data["params"]["code"])
        == [
            9000,
            4500,
            560,
            20000,
        ]
        * 2
    )


async def test_timing_over_16_bits_fails_the_send(
    hass: HomeAssistant,
    blaster: tuple[MockConfigEntry, str],
    zha_calls: list[ServiceCall],
) -> None:
    """A duration the format cannot hold fails the action; nothing is sent."""
    _, emitter = blaster
    with pytest.raises(HomeAssistantError) as err:
        await async_send_command(hass, emitter, RawCommand([560, -70000, 560]))
    assert err.value.translation_key == "timing_too_long"
    assert zha_calls == []


async def test_blaster_without_its_zha_device_is_unavailable(
    hass: HomeAssistant,
    blaster: tuple[MockConfigEntry, str],
    zha_device: dr.DeviceEntry,
) -> None:
    """The TS1201 removed from ZHA: the emitter is unavailable."""
    _, emitter = blaster
    assert hass.states.get(emitter).state != STATE_UNAVAILABLE
    dr.async_get(hass).async_remove_device(zha_device.id)
    await hass.async_block_till_done()
    assert hass.states.get(emitter).state == STATE_UNAVAILABLE
