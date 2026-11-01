"""
The second queue of an overload the action raised (FR-031): the owner's rule.

The real zones of bestiarium_mode and the real balancer, on a virtual clock: the
shutdown's own waits move the clock, so its events are seen at their second.
"""

# ruff: noqa: S101

import asyncio
from collections.abc import AsyncGenerator, Callable
from datetime import timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import Event, HomeAssistant, callback
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_OVERLOAD_FIRST_QUEUE,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    EVENT_OVERLOAD_CLEARED,
    EVENT_OVERLOAD_FIRST_QUEUE,
    EVENT_OVERLOAD_SECOND_QUEUE,
)
from custom_components.bestiarium_mode.const import DOMAIN as MODE_DOMAIN
from custom_components.bestiarium_mode.const import SERVICE_TRIGGER_OVERLOAD
from custom_components.bestiarium_mode.zones import Zone

from .conftest import started_coordinator

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

POWER = "sensor.power_meter"
OVERLOAD_SENSOR = "binary_sensor.overload"
_REAL_SLEEP = asyncio.sleep


class Home:
    """A home on the virtual clock: what happens at which second, the events."""

    def __init__(self, hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
        """Keep the clock at second 0."""
        self.hass = hass
        self.freezer = freezer
        self.second = 0
        self.script: dict[int, Callable[[], None]] = {}
        self.events: list[tuple[str, int]] = []
        self.mode_entry: MockConfigEntry
        self.turned_off: list[str] = []

    async def sleep(self, seconds: float) -> None:
        """Move the clock instead of waiting; the script runs on its seconds."""
        for _ in range(int(seconds)):
            await self._settle()  # the events of this second are told at it
            self.second += 1
            self.freezer.tick(timedelta(seconds=1))
            if step := self.script.pop(self.second, None):
                step()
            async_fire_time_changed(self.hass)
        await self._settle()

    @staticmethod
    async def _settle() -> None:
        """Let what the loop has to do at this second run."""
        for _ in range(5):
            await _REAL_SLEEP(0)

    def load(self, watts: str) -> Callable[[], None]:
        """Return the step that reports a load."""
        return lambda: self.hass.states.async_set(POWER, watts)

    def call(self) -> Callable[[], None]:
        """Return the step that calls the action."""
        return lambda: self.hass.async_create_task(
            self.hass.services.async_call(
                MODE_DOMAIN, SERVICE_TRIGGER_OVERLOAD, {"source": "the relay"}
            )
        )

    async def signal(self) -> None:
        """Call the action and let the whole shutdown run."""
        await self.hass.services.async_call(
            MODE_DOMAIN,
            SERVICE_TRIGGER_OVERLOAD,
            {"source": "the relay"},
            blocking=True,
        )
        await self.hass.async_block_till_done()

    def seen(self, event_type: str) -> list[int]:
        """Return the seconds of an event."""
        return [second for kind, second in self.events if kind == event_type]


@pytest.fixture
async def home(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> AsyncGenerator[Home]:
    """Return a home at 1000 W with a device in each queue."""
    the_home = Home(hass, freezer)
    hass.states.async_set(POWER, "1000")
    the_home.mode_entry = MockConfigEntry(
        domain=MODE_DOMAIN,
        data={
            "options": [{"name": "Grid on"}],
            "ac_load_limits": {
                "ac_power_sensor": POWER,
                "ac_load_limit_w": 5000,
                "ac_yellow_zone_percent": 75,
                "ac_red_zone_percent": 90,
            },
        },
    )
    the_home.mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(the_home.mode_entry.entry_id)
    for name, first in (("first", True), ("second", False)):
        MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: name,
                CONF_SWITCH_ENTITY_ID: f"switch.{name}",
                CONF_POWER_SENSOR_ENTITY_ID: f"sensor.{name}_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: first,
            },
        ).add_to_hass(hass)
        hass.states.async_set(f"switch.{name}", STATE_ON)
    calls = async_mock_service(hass, "switch", "turn_off")
    coordinator = await started_coordinator(hass)
    await hass.async_block_till_done()

    @callback
    def seen(event: Event) -> None:
        the_home.events.append((event.event_type, the_home.second))
        the_home.turned_off = [call.data["entity_id"] for call in calls]

    for event_type in (
        EVENT_OVERLOAD_FIRST_QUEUE,
        EVENT_OVERLOAD_SECOND_QUEUE,
        EVENT_OVERLOAD_CLEARED,
    ):
        hass.bus.async_listen(event_type, seen)
    with patch(
        "custom_components.bestiarium_devices.strategies.asyncio.sleep", the_home.sleep
    ):
        yield the_home
    await coordinator.async_stop()
    await hass.config_entries.async_unload(the_home.mode_entry.entry_id)


async def test_the_meter_stays_normal_the_decision_at_the_end_of_the_hold(
    home: Home,
) -> None:
    """The meter stays normal the decision at the end of the hold."""
    await home.signal()

    assert home.seen(EVENT_OVERLOAD_FIRST_QUEUE) == [0]
    assert home.seen(EVENT_OVERLOAD_CLEARED) == [30]
    assert home.seen(EVENT_OVERLOAD_SECOND_QUEUE) == []
    assert home.turned_off == ["switch.first"]


async def test_the_meter_over_the_limit_at_the_signal_after_5_s(
    hass: HomeAssistant, home: Home
) -> None:
    """The signal changes nothing about a shutdown the meter started."""
    home.script[2] = home.call()

    hass.states.async_set(POWER, "6000")
    await hass.async_block_till_done()

    assert home.seen(EVENT_OVERLOAD_FIRST_QUEUE) == [0]
    assert home.seen(EVENT_OVERLOAD_SECOND_QUEUE) == [5]


async def test_a_signal_after_the_shutdown_starts_no_other(
    hass: HomeAssistant, home: Home
) -> None:
    """A signal after the shutdown starts no other."""
    hass.states.async_set(POWER, "6000")
    await hass.async_block_till_done()
    home.events.clear()

    await home.signal()

    assert home.events == []


@pytest.mark.parametrize(
    ("meter_second", "decided"),
    [(3, 5), (5, 5), (8, 8), (29, 29)],
)
async def test_the_meter_shows_it_during_the_hold(
    home: Home, meter_second: int, decided: int
) -> None:
    """At once, not before 5 s; the shutdown looks at the meter every second."""
    home.script[meter_second] = home.load("6000")

    await home.signal()

    assert home.seen(EVENT_OVERLOAD_SECOND_QUEUE) == [decided]
    assert home.seen(EVENT_OVERLOAD_CLEARED) == []
    assert home.turned_off == ["switch.first", "switch.second"]


async def test_the_first_queue_helped_cleared_as_without_the_action(
    hass: HomeAssistant, home: Home
) -> None:
    """Over the limit at the signal, below the margin by the 5th second: cleared."""
    home.script[3] = home.load("3000")
    hass.states.async_set(POWER, "5300")

    await home.signal()

    assert home.seen(EVENT_OVERLOAD_CLEARED) == [5]
    assert home.seen(EVENT_OVERLOAD_SECOND_QUEUE) == []
    assert home.turned_off == ["switch.first"]


async def test_the_meter_showed_it_then_fell_before_the_5th_second(home: Home) -> None:
    """The meter leads once it has shown the overload: cleared, not held."""
    home.script[2] = home.load("6000")
    home.script[3] = home.load("3000")

    await home.signal()

    assert home.seen(EVENT_OVERLOAD_CLEARED) == [5]
    assert home.seen(EVENT_OVERLOAD_SECOND_QUEUE) == []


async def test_a_repeat_after_the_meter_fell_does_not_put_the_decision_off(
    hass: HomeAssistant, home: Home
) -> None:
    """The review's point 4: decided on the 5th second, not 30 s later."""
    home.script[3] = home.load("3000")
    home.script[4] = home.call()
    hass.states.async_set(POWER, "5300")

    await home.signal()

    assert home.seen(EVENT_OVERLOAD_FIRST_QUEUE) == [0]
    assert home.seen(EVENT_OVERLOAD_SECOND_QUEUE) == [5]
    assert home.seen(EVENT_OVERLOAD_CLEARED) == []


async def test_low_on_the_5th_second_over_the_limit_on_the_8th(home: Home) -> None:
    """The case of the plan review: the second queue at once, not on the 31st."""
    home.script[8] = home.load("6000")

    await home.signal()

    assert home.seen(EVENT_OVERLOAD_SECOND_QUEUE) == [8]


@pytest.mark.parametrize("repeat_second", [3, 10, 29])
async def test_a_repeated_signal_does_not_put_the_decision_off(
    home: Home, repeat_second: int
) -> None:
    """Counted from the first call; the hold keeps the zone on: not cleared."""
    home.script[repeat_second] = home.call()

    await home.signal()

    assert home.seen(EVENT_OVERLOAD_FIRST_QUEUE) == [0]
    assert home.seen(EVENT_OVERLOAD_SECOND_QUEUE) == [30]
    assert home.seen(EVENT_OVERLOAD_CLEARED) == []
    assert home.mode_entry.runtime_data.zone is Zone.OVERLOAD


async def test_no_data_the_second_queue_at_the_end_of_the_hold(
    hass: HomeAssistant, home: Home
) -> None:
    """No data for longer than the tolerance: the sensor is unavailable: fail-safe."""
    hass.states.async_set(POWER, STATE_UNAVAILABLE)
    await home.sleep(61)
    await hass.async_block_till_done()
    assert home.mode_entry.runtime_data.zone is Zone.NO_DATA
    home.second = 0

    await home.signal()

    assert home.seen(EVENT_OVERLOAD_FIRST_QUEUE) == [0]
    assert home.seen(EVENT_OVERLOAD_SECOND_QUEUE) == [30]
    assert home.turned_off == ["switch.first", "switch.second"]
    assert hass.states.get(OVERLOAD_SENSOR).state == STATE_UNAVAILABLE


async def test_the_signal_during_a_shutdown_the_meter_started(
    hass: HomeAssistant, home: Home
) -> None:
    """The meter's overload ended, the signal raises it: one shutdown goes on."""
    home.script[2] = home.load("1000")
    home.script[3] = home.call()

    hass.states.async_set(POWER, "6000")
    await hass.async_block_till_done()

    assert home.seen(EVENT_OVERLOAD_FIRST_QUEUE) == [0]
    assert home.seen(EVENT_OVERLOAD_CLEARED) == [33]
    assert home.seen(EVENT_OVERLOAD_SECOND_QUEUE) == []


async def test_an_overload_back_during_the_shutdown_without_the_action(
    hass: HomeAssistant, home: Home
) -> None:
    """The flag: one shutdown where there were two side by side."""
    home.script[2] = home.load("1000")
    home.script[3] = home.load("6000")

    hass.states.async_set(POWER, "6000")
    await hass.async_block_till_done()

    assert home.seen(EVENT_OVERLOAD_FIRST_QUEUE) == [0]
    assert home.seen(EVENT_OVERLOAD_SECOND_QUEUE) == [5]


async def test_a_new_shutdown_after_the_last_one(home: Home) -> None:
    """The flag is down when the shutdown is over."""
    await home.signal()
    await home.sleep(31)
    await home.hass.async_block_till_done()
    home.events.clear()
    home.second = 0

    await home.signal()

    assert home.seen(EVENT_OVERLOAD_FIRST_QUEUE) == [0]
    assert home.seen(EVENT_OVERLOAD_CLEARED) == [30]
