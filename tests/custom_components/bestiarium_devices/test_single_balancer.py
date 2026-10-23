"""All device entries share one balancer, however they are set up."""

# ruff: noqa: S101

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    GREEN_INTERVAL_SECONDS,
)
from custom_components.bestiarium_devices.power_memory import PowerMemory

from .conftest import FakeRelays
from .test_headroom import _setup_mode

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

DEVICES = 5
STUCK = "switch.socket_0"


def _add_entry(hass: HomeAssistant, relays: FakeRelays, switch: str) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: switch.split(".")[1],
            CONF_SWITCH_ENTITY_ID: switch,
            CONF_POWER_SENSOR_ENTITY_ID: f"{switch}_power",
            CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5200},
        },
    ).add_to_hass(hass)
    relays.add(switch, on=switch != STUCK)


async def test_entries_set_up_at_start_run_one_green_job(
    hass: HomeAssistant, relays: FakeRelays
) -> None:
    """Entries set up together at HA start: one turn-on per green tick."""
    await _setup_mode(hass, "1000")
    for number in range(DEVICES):
        _add_entry(hass, relays, f"switch.socket_{number}")
    relays.stuck.add(STUCK)

    # HA sets up all entries of the integration concurrently, and the power
    # memory is read from disk meanwhile: the other entries go on setting up
    load = PowerMemory.async_load

    async def load_from_disk(memory: PowerMemory) -> None:
        await asyncio.sleep(0)
        await load(memory)

    with patch.object(PowerMemory, "async_load", load_from_disk):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    try:
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=GREEN_INTERVAL_SECONDS + 1)
        )
        await hass.async_block_till_done()

        assert relays.calls == [("turn_on", STUCK)]
    finally:
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_remove(entry.entry_id)
