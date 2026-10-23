"""The balancer turns a device on only when it fits below the red zone."""

# ruff: noqa: S101

from datetime import timedelta

import pytest
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.bestiarium_devices.balancer import (
    ActiveZone,
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
    DOMAIN_BESTIARIUM_MODE,
    LOAD_PRIORITY_ALWAYS_ON,
)
from custom_components.bestiarium_devices.consumption import SOURCE_RUN, PowerRecord
from custom_components.bestiarium_devices.power_memory import async_get_power_memory

from .conftest import FakeRelays, started_coordinator

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

GRID = "sensor.grid_main_load_em0_power"
BOILER = "switch.boiler"
GARLAND = "switch.garland"


async def _setup_mode(hass: HomeAssistant, load: str) -> None:
    """Set up the real bestiarium_mode: limit 5000 W, red zone from 4250 W."""
    hass.states.async_set(GRID, load)
    entry = MockConfigEntry(
        domain=DOMAIN_BESTIARIUM_MODE,
        unique_id="bestiarium_mode",
        data={
            "options": [
                {"name": "Grid on", "icon": "mdi:home"},
                {"name": "Always", "icon": "mdi:home"},
            ],
            "ac_load_limits": {
                "ac_power_sensor": GRID,
                "ac_load_limit_w": 5000,
                "ac_yellow_zone_percent": 60,
                "ac_red_zone_percent": 85,
                "ac_overload_recovery_seconds": 30,
            },
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()


async def _add_device(
    hass: HomeAssistant,
    relays: FakeRelays,
    switch: str,
    watts: float,
    priorities: dict[str, int],
) -> MockConfigEntry:
    """Add an OFF device whose memory holds 3 real runs at the given power."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: switch.split(".")[1].title(),
            CONF_SWITCH_ENTITY_ID: switch,
            CONF_POWER_SENSOR_ENTITY_ID: f"{switch}_power",
            CONF_LOAD_PRIORITIES: priorities,
        },
    )
    entry.add_to_hass(hass)
    relays.add(switch, on=False)
    memory = await async_get_power_memory(hass)
    for minute in range(3):
        memory.add_run(
            entry.entry_id,
            PowerRecord(
                start=dt_util.utcnow() - timedelta(minutes=minute),
                duration_s=600,
                power_w=watts,
                source=SOURCE_RUN,
            ),
        )
    return entry


async def _green_cycle(hass: HomeAssistant) -> None:
    coordinator = await started_coordinator(hass)
    try:
        assert coordinator.active_zone == ActiveZone.GREEN
        await coordinator._strategies.async_green()  # noqa: SLF001
    finally:
        await coordinator.async_stop()


async def test_big_device_does_not_fit(hass: HomeAssistant, relays: FakeRelays) -> None:
    """Criterion 1: 2900 W + 2400 W > 4250 W -> the boiler stays off."""
    await _setup_mode(hass, "2900")
    await _add_device(hass, relays, BOILER, 2400, {CONF_DEFAULT_PRIORITY: 7000})

    await _green_cycle(hass)

    assert relays.calls == []


async def test_smaller_device_turns_on_instead(
    hass: HomeAssistant, relays: FakeRelays
) -> None:
    """Criterion 2: the 300 W garland below in priority fits and turns on."""
    await _setup_mode(hass, "2900")
    await _add_device(hass, relays, BOILER, 2400, {CONF_DEFAULT_PRIORITY: 7000})
    await _add_device(hass, relays, GARLAND, 300, {CONF_DEFAULT_PRIORITY: 100})

    await _green_cycle(hass)

    assert relays.calls == [("turn_on", GARLAND)]


async def test_big_device_fits_at_lower_load(
    hass: HomeAssistant, relays: FakeRelays
) -> None:
    """Criterion 3: 1500 W + 2400 W <= 4250 W -> the boiler turns on."""
    await _setup_mode(hass, "1500")
    await _add_device(hass, relays, BOILER, 2400, {CONF_DEFAULT_PRIORITY: 7000})

    await _green_cycle(hass)

    assert relays.calls == [("turn_on", BOILER)]


async def test_unknown_load_turns_nothing_on(
    hass: HomeAssistant, relays: FakeRelays
) -> None:
    """An unreadable grid load: no device is turned on."""
    await _setup_mode(hass, "1500")
    await _add_device(hass, relays, GARLAND, 300, {CONF_DEFAULT_PRIORITY: 100})
    coordinator = await started_coordinator(hass)
    try:
        hass.states.async_set(GRID, STATE_UNAVAILABLE)
        await coordinator._strategies.async_green()  # noqa: SLF001
    finally:
        await coordinator.async_stop()

    assert relays.calls == []


async def test_always_on_mode_is_not_limited(
    hass: HomeAssistant, relays: FakeRelays
) -> None:
    """Criterion 4: switching to an 'always on' mode turns the boiler on anyway."""
    await _setup_mode(hass, "2900")
    await _add_device(
        hass,
        relays,
        BOILER,
        2400,
        {CONF_DEFAULT_PRIORITY: 7000, "Always": LOAD_PRIORITY_ALWAYS_ON},
    )
    select = er.async_get(hass).async_get_entity_id(
        "select", DOMAIN_BESTIARIUM_MODE, "select"
    )
    coordinator = await started_coordinator(hass)
    try:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": select, "option": "Always"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert relays.calls == [("turn_on", BOILER)]

        # The green strategy does not hold an 'always on' device back either
        hass.states.async_set(BOILER, STATE_OFF)
        await coordinator._strategies.async_green()  # noqa: SLF001
        assert relays.calls == [("turn_on", BOILER), ("turn_on", BOILER)]
    finally:
        await coordinator.async_stop()
