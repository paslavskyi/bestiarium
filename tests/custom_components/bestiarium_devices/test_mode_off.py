"""
"Off" by mode (DEV-008 A5): repeated like any command, honest when it fails.

The mode "Always" says off for the devices of these tests; "Grid on" says 5000.
"""

# ruff: noqa: S101

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    DOMAIN_BESTIARIUM_MODE,
    LOAD_PRIORITY_OFF,
)
from custom_components.bestiarium_devices.control import ControlState

from .conftest import FakeRelays
from .control_home import Home

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

MINUTE = 60
HEATER = "switch.heater"
DRYER = "switch.dryer"
KETTLE = "switch.kettle"
OFF_IN_ALWAYS = {CONF_DEFAULT_PRIORITY: 5000, "Always": LOAD_PRIORITY_OFF}


@pytest.fixture
def home(
    hass: HomeAssistant, relays: FakeRelays, freezer: FrozenDateTimeFactory
) -> Home:
    """Return a home with no devices yet."""
    return Home(hass, relays, freezer)


async def _select(home: Home, mode: str) -> None:
    select = er.async_get(home.hass).async_get_entity_id(
        "select", DOMAIN_BESTIARIUM_MODE, "select"
    )
    await home.hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": mode}, blocking=True
    )
    await home.hass.async_block_till_done()


async def test_turned_on_by_a_person_stays_on_in_green(home: Home) -> None:
    """Criterion 8: off by the mode, turned on by a person: green leaves it on."""
    await home.mode("1000")
    heater = await home.device(HEATER, 5000, on=True, priorities=OFF_IN_ALWAYS)
    await _select(home, "Always")
    assert home.relays.is_on(HEATER) is False

    home.set(HEATER, "on")
    await home.tick(5 * MINUTE, step=5)

    assert home.relays.is_on(HEATER) is True
    assert home.commands(HEATER) == ["turn_off"]
    assert home.state(heater) == ControlState.NORMAL


async def test_red_turns_off_every_device_off_by_mode_first(home: Home) -> None:
    """Criterion 9: all devices off by mode at once in the first red step."""
    await home.mode("1000")
    await home.device(HEATER, 5000, priorities=OFF_IN_ALWAYS)
    await home.device(DRYER, 5000, priorities=OFF_IN_ALWAYS)
    await home.device(KETTLE, 100, on=True)
    await _select(home, "Always")
    home.set(HEATER, "on")
    home.set(DRYER, "on")
    await home.tick(1)
    home.relays.calls.clear()

    home.load("4400")
    await home.tick(1)
    assert sorted(home.relays.calls) == [("turn_off", DRYER), ("turn_off", HEATER)]

    await home.tick(5, step=5)
    assert home.commands(KETTLE) == ["turn_off"]


async def test_a_dead_device_off_by_mode_does_not_hold_the_red_queue(
    home: Home,
) -> None:
    """Criterion 9: the queue goes on while the device off by mode does not answer."""
    await home.mode("1000")
    await home.device(HEATER, 5000, priorities=OFF_IN_ALWAYS)
    await home.device(KETTLE, 100, on=True)
    await _select(home, "Always")
    home.set(HEATER, "on")
    home.relays.stuck.add(HEATER)
    await home.tick(1)

    home.load("4400")
    await home.tick(10, step=5)

    assert home.commands(KETTLE) == ["turn_off"]


async def test_mode_off_failed_once_then_recovered(home: Home) -> None:
    """Criterion 10, 11: the state outranks 'not responding'; one event."""
    await home.mode("1000")
    heater = await home.device(HEATER, 5000, on=True, priorities=OFF_IN_ALWAYS)
    home.relays.stuck.add(HEATER)
    await _select(home, "Always")

    await home.tick(2 * MINUTE, step=5)
    assert home.commands(HEATER) == ["turn_off"] * 3
    assert home.state(heater) == ControlState.MODE_OFF_FAILED
    assert home.events[heater.entry_id] == ["mode_off_failed"]

    # The deferral ends: tried again, no second "failed"
    await home.tick(16 * MINUTE, step=5)
    assert home.commands(HEATER)[3:] == ["turn_off"] * 3
    assert home.events[heater.entry_id] == ["mode_off_failed", "retrying", "deferred"]
    assert home.state(heater) == ControlState.MODE_OFF_FAILED

    home.relays.stuck.discard(HEATER)
    home.set(HEATER, "off")
    await home.tick(MINUTE + 5, step=5)
    assert home.state(heater) == ControlState.NORMAL
    assert home.events[heater.entry_id][-1] == "recovered"


async def test_another_mode_ends_the_failure(home: Home) -> None:
    """The mode no longer says off: no 'does not turn off by mode', no repeats."""
    await home.mode("1000")
    heater = await home.device(HEATER, 5000, on=True, priorities=OFF_IN_ALWAYS)
    home.relays.stuck.add(HEATER)
    await _select(home, "Always")
    await home.tick(2 * MINUTE, step=5)
    assert home.state(heater) == ControlState.MODE_OFF_FAILED

    await _select(home, "Grid on")
    await home.tick(5, step=5)

    assert home.state(heater) == ControlState.UNRESPONSIVE
    commands = len(home.commands(HEATER))
    await home.tick(20 * MINUTE, step=MINUTE)
    assert len(home.commands(HEATER)) == commands


async def test_reload_of_the_modes_is_no_change_of_mode(home: Home) -> None:
    """Review 06, 1: the mode entry reloads; a device a person turned on stays on."""
    await home.mode("1000")
    await home.device(HEATER, 5000, on=True, priorities=OFF_IN_ALWAYS)
    await _select(home, "Always")
    home.set(HEATER, "on")
    await home.tick(1)

    mode_entry = home.hass.config_entries.async_entries(DOMAIN_BESTIARIUM_MODE)[0]
    await home.hass.config_entries.async_reload(mode_entry.entry_id)
    await home.tick(MINUTE, step=5)

    assert home.relays.is_on(HEATER) is True
    assert home.commands(HEATER) == ["turn_off"]


async def test_red_turn_off_of_a_device_off_by_mode_is_the_mode_duty(
    home: Home,
) -> None:
    """Review 06, 8: red opens the duty; it fails as the mode; repeats in green."""
    await home.mode("1000")
    heater = await home.device(HEATER, 5000, priorities=OFF_IN_ALWAYS)
    await _select(home, "Always")
    home.set(HEATER, "on")
    home.relays.stuck.add(HEATER)
    await home.tick(1)

    home.load("4400")
    await home.tick(MINUTE, step=5)
    assert home.state(heater) == ControlState.MODE_OFF_FAILED
    assert home.events[heater.entry_id] == ["mode_off_failed"]

    home.load("1000")
    await home.tick(16 * MINUTE, step=5)
    commands = home.commands(HEATER)
    assert len(commands) > 3  # noqa: PLR2004 - more than the first series
    assert set(commands) == {"turn_off"}
    assert home.state(heater) == ControlState.MODE_OFF_FAILED


async def test_a_reloaded_device_gets_the_mode_again(home: Home) -> None:
    """The owner's decision: a reloaded device entry, as after a restart."""
    await home.mode("1000")
    heater = await home.device(HEATER, 5000, priorities=OFF_IN_ALWAYS)
    await _select(home, "Always")
    home.set(HEATER, "on")
    await home.tick(1)

    await home.hass.config_entries.async_reload(heater.entry_id)
    await home.tick(1)

    assert home.relays.is_on(HEATER) is False
    assert home.commands(HEATER) == ["turn_off", "turn_off"]
