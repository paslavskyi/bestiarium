"""The control state of a device: attempts, deferral, tolerance, hysteresis."""

# ruff: noqa: S101, PLR2004

import logging

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    DATA_COORDINATOR,
    DOMAIN,
)
from custom_components.bestiarium_devices.control import (
    ControlState,
    async_get_response_memory,
)
from custom_components.bestiarium_devices.sensor import ControlStateSensor

from .conftest import FakeRelays
from .control_home import Home

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

LOGGER = "custom_components.bestiarium_devices"

# The owner's two bedroom sockets of the same priority (2026-10-23)
COMMODE = "switch.commode"
BEDSIDE = "switch.bedside"
DEAD = "switch.dead"
NIGHT = "switch.night"
MINUTE = 60


@pytest.fixture
def home(
    hass: HomeAssistant, relays: FakeRelays, freezer: FrozenDateTimeFactory
) -> Home:
    """Return the test home."""
    return Home(hass, relays, freezer)


def _logged(caplog: pytest.LogCaptureFixture, level: int, text: str = "") -> list[str]:
    """Return the integration's log lines of a level that contain the text."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith(LOGGER)
        and record.levelno == level
        and text in record.getMessage()
    ]


async def _night_case(home: Home) -> tuple:
    """Green zone; the commode socket does not hear its commands."""
    await home.mode("1000")
    commode = await home.device(COMMODE, 5200)
    bedside = await home.device(BEDSIDE, 5200)
    home.relays.stuck.add(COMMODE)
    return commode, bedside


async def test_unresponsive_device_is_deferred_and_the_next_turns_on(
    home: Home,
) -> None:
    """Criterion 1: three failed steps, then the next device of the queue."""
    commode, bedside = await _night_case(home)

    await home.tick(4 * MINUTE, step=MINUTE)

    assert home.commands(COMMODE) == ["turn_on"] * 3
    assert home.commands(BEDSIDE) == ["turn_on"]
    assert home.state(commode) == ControlState.UNRESPONSIVE
    assert home.state(bedside) == ControlState.NORMAL
    assert home.events[commode.entry_id] == ["deferred"]


async def test_deferred_device_is_tried_again(home: Home) -> None:
    """Criterion 2: after the deferral a new series; the state stays the same."""
    commode, _ = await _night_case(home)
    await home.tick(4 * MINUTE, step=MINUTE)

    await home.tick(14 * MINUTE, step=MINUTE)
    assert home.commands(COMMODE) == ["turn_on"] * 3

    await home.tick(MINUTE, step=MINUTE)
    assert home.commands(COMMODE) == ["turn_on"] * 4
    assert home.events[commode.entry_id] == ["deferred", "retrying"]
    assert home.state(commode) == ControlState.UNRESPONSIVE


async def test_answer_recovers_after_hysteresis(home: Home) -> None:
    """Criterion 4: the device turned on at last; normal after 60 s of stability."""
    commode, _ = await _night_case(home)
    await home.tick(4 * MINUTE, step=MINUTE)

    home.set(COMMODE, STATE_ON)
    await home.tick(59)
    assert home.state(commode) == ControlState.UNRESPONSIVE

    await home.tick(2)
    assert home.state(commode) == ControlState.NORMAL
    assert home.events[commode.entry_id] == ["deferred", "recovered"]
    attributes = home.hass.states.get(
        home.entity_id(commode, "sensor", "control_state")
    ).attributes
    assert attributes["failed_attempts"] == 0


async def test_manual_switch_recovers_after_hysteresis(home: Home) -> None:
    """Criterion 4: a person switched the device; its attempts start over."""
    await home.mode("4400")
    dead = await home.device(DEAD, 1000, on=True)
    await home.device(NIGHT, 3000, on=True)
    home.relays.stuck.add(DEAD)
    await home.tick(35, step=5)
    assert home.state(dead) == ControlState.UNRESPONSIVE
    # Out of the red zone, so the balancer does not try it again at once
    home.load("3500")
    await home.tick(5)

    home.set(DEAD, STATE_OFF)
    home.set(DEAD, STATE_ON)
    await home.tick(61)

    assert home.state(dead) == ControlState.NORMAL
    assert home.events[dead.entry_id] == ["deferred", "recovered"]


async def test_red_zone_moves_on_within_its_hard_limit(home: Home) -> None:
    """Criterion 3: the dead socket gets at most 30 s, then the next goes off."""
    await home.mode("4400")
    dead = await home.device(DEAD, 1000, on=True)
    await home.device(NIGHT, 3000, on=True)
    home.relays.stuck.add(DEAD)

    await home.tick(30, step=5)
    assert home.commands(DEAD) == ["turn_off", "turn_off"]
    assert home.commands(NIGHT) == []

    await home.tick(5, step=5)
    assert home.commands(NIGHT) == ["turn_off"]
    assert home.state(dead) == ControlState.UNRESPONSIVE


async def test_slow_device_gets_its_response_time(home: Home) -> None:
    """Criterion 12: a reply after 12 s is no failure; it is remembered."""
    await home.mode("4400")
    slow = await home.device(DEAD, 1000, on=True)
    await home.device(NIGHT, 3000, on=True)
    home.relays.stuck.add(DEAD)

    await home.tick(5, step=5)
    await home.tick(12)
    home.set(DEAD, STATE_OFF)
    await home.tick(10, step=5)

    assert home.commands(DEAD) == ["turn_off"]
    assert home.commands(NIGHT) == ["turn_off"]
    coordinator = home.hass.data[DOMAIN][DATA_COORDINATOR]
    assert coordinator.control(slow).attributes["response_time"] == pytest.approx(12)
    assert home.state(slow) == ControlState.NORMAL


async def test_off_relay_drawing_power_is_still_commanded(home: Home) -> None:
    """Criterion 11: the state is not guessed from the power."""
    await home.mode("1000")
    await home.device(COMMODE, 5200, power="7.5")

    await home.tick(MINUTE, step=MINUTE)

    assert home.commands(COMMODE) == ["turn_on"]


async def test_short_dropout_is_no_problem(
    home: Home, caplog: pytest.LogCaptureFixture
) -> None:
    """Criterion 13: unavailable for less than the tolerance changes nothing."""
    await home.mode("1000")
    device = await home.device(COMMODE, 5200, on=True)

    home.set(COMMODE, STATE_UNAVAILABLE)
    await home.tick(30)
    home.set(COMMODE, STATE_ON)
    await home.tick(MINUTE)

    assert home.state(device) == ControlState.NORMAL
    assert not _logged(caplog, logging.WARNING)


async def test_switch_unavailable_with_tolerance_and_hysteresis(home: Home) -> None:
    """Criteria 5, 14: confirmed after 60 s; normal after 60 s without a glitch."""
    await home.mode("1000")
    device = await home.device(COMMODE, 5200, on=True)

    home.set(COMMODE, STATE_UNAVAILABLE)
    await home.tick(59)
    assert home.state(device) == ControlState.NORMAL
    await home.tick(2)
    assert home.state(device) == ControlState.SWITCH_UNAVAILABLE

    home.set(COMMODE, STATE_ON)
    await home.tick(30)
    home.set(COMMODE, STATE_UNAVAILABLE)
    await home.tick(1)
    home.set(COMMODE, STATE_ON)
    await home.tick(59)
    assert home.state(device) == ControlState.SWITCH_UNAVAILABLE
    await home.tick(2)
    assert home.state(device) == ControlState.NORMAL


async def test_power_data_and_priority_of_causes(home: Home) -> None:
    """Criterion 5: no power data; a gone switch matters more."""
    await home.mode("1000")
    device = await home.device(COMMODE, 5200, on=True)

    home.set(f"{COMMODE}_power", STATE_UNAVAILABLE)
    await home.tick(61)
    assert home.state(device) == ControlState.NO_POWER_DATA

    home.set(COMMODE, STATE_UNAVAILABLE)
    await home.tick(61)
    assert home.state(device) == ControlState.SWITCH_UNAVAILABLE


async def test_unstable_connection(
    home: Home, caplog: pytest.LogCaptureFixture
) -> None:
    """Criterion 15: three short dropouts in an hour; one warning; it passes."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    await home.mode("1000")
    device = await home.device(COMMODE, 5200, on=True)

    async def drop_out() -> None:
        # The whole device drops out: its relay and its power sensor
        home.set(COMMODE, STATE_UNAVAILABLE)
        home.set(f"{COMMODE}_power", STATE_UNAVAILABLE)
        await home.tick(10)
        home.set(COMMODE, STATE_ON)
        home.set(f"{COMMODE}_power", "100")
        await home.tick(5 * MINUTE, step=10)

    for _ in range(2):
        await drop_out()
    assert home.state(device) == ControlState.NORMAL
    for _ in range(2):
        await drop_out()
    assert home.state(device) == ControlState.UNSTABLE
    assert len(_logged(caplog, logging.WARNING)) == 1

    await home.tick(70 * MINUTE, step=MINUTE)
    assert home.state(device) == ControlState.NORMAL


async def test_an_hour_of_a_stuck_device_logs_little(
    home: Home, caplog: pytest.LogCaptureFixture
) -> None:
    """Criterion 6: one warning; a turn-on line per series, not per minute."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    await _night_case(home)
    home.relays.stuck.add(BEDSIDE)

    await home.tick(60 * MINUTE, step=MINUTE)

    assert len(_logged(caplog, logging.WARNING, "Commode")) == 1
    assert len(_logged(caplog, logging.INFO, "Turning on device Commode")) == 4
    assert len(home.commands(COMMODE)) == 12


async def test_overload_and_mode_ignore_the_deferral(home: Home) -> None:
    """Criteria 18, 19: emergency and mode commands reach a deferred device."""
    commode, _ = await _night_case(home)
    await home.tick(4 * MINUTE, step=MINUTE)
    home.relays.calls.clear()

    await home.hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.bestiarium_mode", "option": "Always"},
        blocking=True,
    )
    await home.tick(1)
    home.load("6000")
    await home.tick(10)

    assert "turn_off" in home.commands(COMMODE)
    assert home.state(commode) == ControlState.UNRESPONSIVE


async def test_state_after_restart_is_computed_once_started(
    hass: HomeAssistant, home: Home
) -> None:
    """Criterion 5: availability counts only from HA's start, then as usual."""
    await home.mode("1000")
    hass.set_state(CoreState.starting)
    device = await home.device(COMMODE, 5200, on=True)
    hass.states.async_remove(f"{COMMODE}_power")

    await home.tick(2 * MINUTE)
    assert home.state(device) == ControlState.NORMAL

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await home.tick(61)
    assert home.state(device) == ControlState.NO_POWER_DATA


async def test_frequent_details_are_not_recorded(home: Home) -> None:
    """Criterion 17: the details are attributes the recorder skips."""
    await home.mode("1000")
    device = await home.device(COMMODE, 5200)
    attributes = home.hass.states.get(
        home.entity_id(device, "sensor", "control_state")
    ).attributes

    details = {
        "direction",
        "failed_attempts",
        "last_attempt",
        "next_attempt",
        "response_time",
        "dropouts_last_hour",
    }
    assert details <= set(attributes)
    assert details <= ControlStateSensor._unrecorded_attributes  # noqa: SLF001


async def test_control_entities_are_diagnostic(home: Home) -> None:
    """The control state and events are diagnostic: not on family dashboards."""
    await home.mode("1000")
    device = await home.device(COMMODE, 5200, priorities={CONF_DEFAULT_PRIORITY: 1})
    registry = er.async_get(home.hass)

    for domain, key in (("sensor", "control_state"), ("event", "control_events")):
        entity = registry.async_get(home.entity_id(device, domain, key))
        assert entity.entity_category == "diagnostic"


async def test_deferral_survives_a_red_episode(home: Home) -> None:
    """Review 14, 1: the red zone's questions do not wipe a turn-on deferral."""
    commode, _ = await _night_case(home)
    await home.tick(4 * MINUTE, step=MINUTE)

    home.load("4400")
    await home.tick(30, step=5)
    home.load("1000")
    await home.tick(31)
    await home.tick(2 * MINUTE, step=MINUTE)

    assert home.commands(COMMODE) == ["turn_on"] * 3
    assert home.events[commode.entry_id] == ["deferred"]
    assert home.state(commode) == ControlState.UNRESPONSIVE


async def test_answer_after_red_began_is_remembered(home: Home) -> None:
    """Review 14, 1: a turn-on in flight keeps its series through the red zone."""
    await home.mode("1000")
    slow = await home.device(COMMODE, 5200)
    home.relays.stuck.add(COMMODE)
    await home.tick(MINUTE, step=MINUTE)
    assert home.commands(COMMODE) == ["turn_on"]

    home.load("4400")
    await home.tick(12)
    home.set(COMMODE, STATE_ON)
    await home.tick(1)

    coordinator = home.hass.data[DOMAIN][DATA_COORDINATOR]
    assert coordinator.control(slow).attributes["response_time"] == pytest.approx(12)


async def test_response_time_counts_from_the_first_command(home: Home) -> None:
    """Review 14, 2: an answer after a repeat counts from the series' start."""
    await home.mode("4400")
    slow = await home.device(DEAD, 1000, on=True)
    await home.device(NIGHT, 3000, on=True)
    home.relays.stuck.add(DEAD)

    # First command at 5 s; it fails at 30 s and is repeated
    await home.tick(30, step=5)
    assert home.commands(DEAD) == ["turn_off", "turn_off"]
    await home.tick(3)
    home.set(DEAD, STATE_OFF)
    await home.tick(1)

    coordinator = home.hass.data[DOMAIN][DATA_COORDINATOR]
    assert coordinator.control(slow).attributes["response_time"] == pytest.approx(28)


async def test_entries_not_set_up_have_no_control(
    hass: HomeAssistant, home: Home
) -> None:
    """Review 14, 3: a disabled or removed entry is left alone and not stored."""
    await home.mode("1000")
    removed = await home.device(BEDSIDE, 5200)
    disabled = MockConfigEntry(
        domain=DOMAIN,
        data=dict(removed.data, switch_entity_id=COMMODE),
        disabled_by=ConfigEntryDisabler.USER,
    )
    disabled.add_to_hass(hass)
    home.relays.add(COMMODE, on=False)
    await hass.config_entries.async_remove(removed.entry_id)
    await hass.async_block_till_done()

    await home.tick(2 * MINUTE, step=MINUTE)

    coordinator = hass.data[DOMAIN][DATA_COORDINATOR]
    assert coordinator.control(disabled) is None
    assert coordinator.control(removed) is None
    assert home.commands(COMMODE) == []
    memory = await async_get_response_memory(hass)
    assert memory.longest(removed.entry_id) is None


async def test_dead_device_dropping_out_does_not_recover(home: Home) -> None:
    """Review 14, 4: unavailable and back as it was is no answer."""
    commode, _ = await _night_case(home)
    await home.tick(4 * MINUTE, step=MINUTE)

    home.set(COMMODE, STATE_UNAVAILABLE)
    await home.tick(30)
    home.set(COMMODE, STATE_OFF)
    await home.tick(2 * MINUTE)

    assert "recovered" not in home.events[commode.entry_id]
    assert home.state(commode) == ControlState.UNRESPONSIVE


async def test_change_back_restarts_the_hysteresis(home: Home) -> None:
    """Review 14, 4: switched on, then back off: the minute starts again."""
    commode, _ = await _night_case(home)
    await home.tick(4 * MINUTE, step=MINUTE)

    home.set(COMMODE, STATE_ON)
    await home.tick(40)
    home.set(COMMODE, STATE_OFF)
    await home.tick(40)
    assert home.state(commode) == ControlState.UNRESPONSIVE

    await home.tick(21)
    assert home.state(commode) == ControlState.NORMAL


async def test_retrying_tells_the_series_it_ends(home: Home) -> None:
    """Review 14, 5: the retrying event carries the attempts and the time."""
    commode, _ = await _night_case(home)
    await home.tick(19 * MINUTE, step=MINUTE)

    assert home.events[commode.entry_id] == ["deferred", "retrying"]
    retrying = home.payloads[commode.entry_id][1]
    assert retrying["direction"] == "turn_on"
    assert retrying["failed_attempts"] == 3
    assert retrying["next_attempt"] is not None


async def test_device_appearing_after_start_is_no_dropout(
    hass: HomeAssistant, home: Home
) -> None:
    """Review 14, 6: a sensor still loading at HA's start is not a dropout."""
    await home.mode("1000")
    hass.set_state(CoreState.starting)
    device = await home.device(COMMODE, 5200, on=True)
    hass.states.async_remove(f"{COMMODE}_power")
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await home.tick(10)

    home.set(f"{COMMODE}_power", "100")
    await home.tick(MINUTE)

    attributes = hass.states.get(
        home.entity_id(device, "sensor", "control_state")
    ).attributes
    assert attributes["dropouts_last_hour"] == 0
    assert home.state(device) == ControlState.NORMAL
