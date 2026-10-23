"""One balancer for all device entries: every step runs exactly once."""

# ruff: noqa: S101

import asyncio
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
)
from custom_components.bestiarium_devices.power_memory import PowerMemory

from .conftest import FakeRelays
from .control_home import Home

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

DEVICES = 5
STUCK = "switch.socket_0"
MINUTE = 60


def _entry(home: Home, number: int, priorities: dict[str, int]) -> MockConfigEntry:
    switch = f"switch.socket_{number}"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: f"Socket {number}",
            CONF_SWITCH_ENTITY_ID: switch,
            CONF_POWER_SENSOR_ENTITY_ID: f"{switch}_power",
            CONF_LOAD_PRIORITIES: priorities,
        },
    )
    entry.add_to_hass(home.hass)
    home.relays.add(switch, on=switch != STUCK)
    home.set(f"{switch}_power", "0")
    return entry


@pytest.fixture
async def home(
    hass: HomeAssistant, relays: FakeRelays, freezer: FrozenDateTimeFactory
) -> Home:
    """
    Five device entries set up as at HA start, in the green zone.

    HA sets up all entries of an integration concurrently, and the power
    memory is read from disk meanwhile: the race that once started a balancer
    per entry.
    """
    home = Home(hass, relays, freezer)
    await home.mode("1000")
    priorities = {CONF_DEFAULT_PRIORITY: 5200, "Always": 0}
    home.entries = [_entry(home, number, priorities) for number in range(DEVICES)]
    relays.stuck.add(STUCK)
    load = PowerMemory.async_load

    async def load_from_disk(memory: PowerMemory) -> None:
        await asyncio.sleep(0)
        await load(memory)

    with patch.object(PowerMemory, "async_load", load_from_disk):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    return home


async def test_one_command_a_step_after_start(home: Home) -> None:
    """Entries set up together: one turn-on per green step, not one per entry."""
    await home.tick(MINUTE, step=MINUTE)

    assert home.commands(STUCK) == ["turn_on"]


async def test_one_command_after_a_zone_change(home: Home) -> None:
    """Green, yellow, green again: still one balancer."""
    home.load("3500")
    await home.tick(5)
    home.load("1000")
    await home.tick(31)

    await home.tick(MINUTE, step=MINUTE)

    assert home.commands(STUCK) == ["turn_on"]


async def test_one_command_after_reload_add_and_remove(home: Home) -> None:
    """Reloading, adding and removing a device keep one balancer."""
    await home.hass.config_entries.async_reload(home.entries[1].entry_id)
    added = _entry(home, DEVICES, {CONF_DEFAULT_PRIORITY: 100})
    assert await home.hass.config_entries.async_setup(added.entry_id)
    await home.hass.config_entries.async_remove(home.entries[2].entry_id)
    await home.hass.async_block_till_done()

    await home.tick(MINUTE, step=MINUTE)

    assert home.commands(STUCK) == ["turn_on"]


async def test_mode_is_applied_once(home: Home) -> None:
    """A mode change sends each device one command."""
    await home.hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.bestiarium_mode", "option": "Always"},
        blocking=True,
    )
    await home.hass.async_block_till_done()

    for number in range(DEVICES):
        assert home.commands(f"switch.socket_{number}") == ["turn_off"]
