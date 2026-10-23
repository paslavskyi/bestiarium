"""A device that does not turn on does not hold back the devices after it."""

# ruff: noqa: S101, SLF001

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from custom_components.bestiarium_devices.balancer import BalancingCoordinator
from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    GREEN_INTERVAL_SECONDS,
    TURN_ON_ATTEMPTS,
    TURN_ON_RETRY_MINUTES,
)

from .conftest import FakeRelays
from .test_headroom import _add_device, _setup_mode

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

# The owner's two bedroom sockets of the same priority (2026-10-23)
COMMODE = "switch.spalnia_rozetka_spalnia_za_komodom_1"
FENIX = "switch.spalnia_rozetka_bilia_lizhka_fenix"
PRIORITIES = {CONF_DEFAULT_PRIORITY: 5200}


async def _green_ticks(
    coordinator: BalancingCoordinator, freezer: FrozenDateTimeFactory, ticks: int
) -> None:
    for _ in range(ticks):
        freezer.tick(timedelta(seconds=GREEN_INTERVAL_SECONDS))
        await coordinator._execute_green_strategy()


async def test_stuck_device_lets_the_next_one_turn_on(
    hass: HomeAssistant, relays: FakeRelays, freezer: FrozenDateTimeFactory
) -> None:
    """After the attempts a stale 'off' socket waits; the next socket turns on."""
    await _setup_mode(hass, "1000")
    await _add_device(hass, relays, COMMODE, 100, PRIORITIES)
    await _add_device(hass, relays, FENIX, 100, PRIORITIES)
    relays.stuck.add(COMMODE)
    coordinator = BalancingCoordinator(hass)
    await coordinator.async_start()
    try:
        await _green_ticks(coordinator, freezer, TURN_ON_ATTEMPTS + 2)

        assert relays.calls == [("turn_on", COMMODE)] * TURN_ON_ATTEMPTS + [
            ("turn_on", FENIX)
        ]

        # Later it gets its attempts again
        relays.calls.clear()
        freezer.tick(timedelta(minutes=TURN_ON_RETRY_MINUTES))
        await _green_ticks(coordinator, freezer, 1)

        assert relays.calls == [("turn_on", COMMODE)]
    finally:
        await coordinator.async_stop()


async def test_device_that_answered_starts_counting_over(
    hass: HomeAssistant, relays: FakeRelays, freezer: FrozenDateTimeFactory
) -> None:
    """A relay that switched after an attempt gets all its attempts back."""
    await _setup_mode(hass, "1000")
    await _add_device(hass, relays, COMMODE, 100, PRIORITIES)
    relays.stuck.add(COMMODE)
    coordinator = BalancingCoordinator(hass)
    await coordinator.async_start()
    try:
        await _green_ticks(coordinator, freezer, TURN_ON_ATTEMPTS - 1)
        # It went on and was turned off again by hand
        hass.states.async_set(COMMODE, "on")
        freezer.tick(timedelta(seconds=1))
        hass.states.async_set(COMMODE, "off")
        relays.calls.clear()

        await _green_ticks(coordinator, freezer, TURN_ON_ATTEMPTS)

        assert relays.calls == [("turn_on", COMMODE)] * TURN_ON_ATTEMPTS
    finally:
        await coordinator.async_stop()


async def test_off_relay_drawing_power_is_on(
    hass: HomeAssistant, relays: FakeRelays, freezer: FrozenDateTimeFactory
) -> None:
    """An 'off' socket whose meter reports 7.5 W is on: the next one turns on."""
    await _setup_mode(hass, "1000")
    await _add_device(hass, relays, COMMODE, 100, PRIORITIES)
    await _add_device(hass, relays, FENIX, 100, PRIORITIES)
    freezer.tick(timedelta(seconds=1))
    hass.states.async_set(f"{COMMODE}_power", "7.5")
    coordinator = BalancingCoordinator(hass)
    await coordinator.async_start()
    try:
        await _green_ticks(coordinator, freezer, 1)
    finally:
        await coordinator.async_stop()

    assert relays.calls == [("turn_on", FENIX)]


async def test_meter_reading_older_than_the_relay_does_not_count(
    hass: HomeAssistant, relays: FakeRelays, freezer: FrozenDateTimeFactory
) -> None:
    """A meter that kept its reading since before the relay went off: not on."""
    await _setup_mode(hass, "1000")
    hass.states.async_set(f"{COMMODE}_power", "7.5")
    freezer.tick(timedelta(seconds=1))
    await _add_device(hass, relays, COMMODE, 100, PRIORITIES)
    coordinator = BalancingCoordinator(hass)
    await coordinator.async_start()
    try:
        await _green_ticks(coordinator, freezer, 1)
    finally:
        await coordinator.async_stop()

    assert relays.calls == [("turn_on", COMMODE)]
