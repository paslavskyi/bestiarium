"""
The zone model approved by the owner (DEV-008 review 03).

Limit 5000 W: yellow 3750 W, red 4500 W, margin 3 % = 150 W, pause 30 s,
power data tolerance 60 s.
"""

# ruff: noqa: S101

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.bestiarium_mode.const import DOMAIN, SIGNAL_ZONE_CHANGED
from custom_components.bestiarium_mode.zones import Zone

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

POWER = "sensor.power_meter"


class Grid:
    """The power meter, the clock and the zones seen."""

    def __init__(self, hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
        """Drive the mode entry's zone."""
        self.hass = hass
        self.freezer = freezer
        self.entry: MockConfigEntry
        self.zones: list[Zone] = []

    async def setup(self, watts: str) -> None:
        """Set up the mode entry at the given load."""
        self.hass.states.async_set(POWER, watts)
        await self.setup_without_sensor()

    async def setup_without_sensor(self) -> None:
        """Set up the mode entry; the power sensor as it is."""
        self.entry = MockConfigEntry(
            domain=DOMAIN,
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
        self.entry.add_to_hass(self.hass)
        assert await self.hass.config_entries.async_setup(self.entry.entry_id)
        await self.hass.async_block_till_done()
        async_dispatcher_connect(
            self.hass,
            SIGNAL_ZONE_CHANGED.format(self.entry.entry_id),
            callback(lambda: self.zones.append(self.zone)),
        )

    @property
    def zone(self) -> Zone:
        """Return the current zone."""
        return self.entry.runtime_data.zone

    async def load(self, watts: str) -> None:
        """Report a new load."""
        self.hass.states.async_set(POWER, watts)
        await self.hass.async_block_till_done()

    async def tick(self, seconds: float) -> None:
        """Let time pass."""
        self.freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(self.hass)
        await self.hass.async_block_till_done()


@pytest.fixture
def grid(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> Grid:
    """Return the grid of a test."""
    return Grid(hass, freezer)


async def test_load_around_yellow_stays_yellow(grid: Grid) -> None:
    """±100 W around the yellow threshold for 2 min: only yellow."""
    await grid.setup("3800")
    assert grid.zone is Zone.YELLOW
    for _ in range(12):
        await grid.load("3650")
        await grid.tick(5)
        await grid.load("3850")
        await grid.tick(5)
    assert grid.zones == []
    assert grid.zone is Zone.YELLOW


async def test_green_after_a_pause_below_the_margin(grid: Grid) -> None:
    """Below 3600 W: pause; 30 s later green."""
    await grid.setup("3800")
    await grid.load("3550")
    assert grid.zone is Zone.PAUSE
    await grid.tick(29)
    assert grid.zone is Zone.PAUSE
    await grid.tick(2)
    assert grid.zones == [Zone.PAUSE, Zone.GREEN]


async def test_pause_waits_while_load_is_within_the_margin(grid: Grid) -> None:
    """In the pause the load rises to 3650 W: the 30 s start again below 3600 W."""
    await grid.setup("3800")
    await grid.load("3500")
    await grid.tick(20)
    await grid.load("3650")
    await grid.tick(40)
    assert grid.zone is Zone.PAUSE
    await grid.load("3500")
    await grid.tick(29)
    assert grid.zone is Zone.PAUSE
    await grid.tick(2)
    assert grid.zone is Zone.GREEN


@pytest.mark.parametrize(
    ("watts", "zone"),
    [("3750", Zone.YELLOW), ("4500", Zone.RED), ("5000", Zone.OVERLOAD)],
)
async def test_up_from_the_pause_at_once(grid: Grid, watts: str, zone: Zone) -> None:
    """A jump in the pause: the zone at once; the next pause starts from zero."""
    await grid.setup("3800")
    await grid.load("3500")
    await grid.tick(20)
    await grid.load(watts)
    assert grid.zone is zone
    await grid.load("3500")
    await grid.tick(20)
    assert grid.zone is Zone.PAUSE
    await grid.tick(11)
    assert grid.zone is Zone.GREEN


async def test_up_from_green_at_once(grid: Grid) -> None:
    """Green to overload in one step."""
    await grid.setup("1000")
    await grid.tick(31)
    await grid.load("5200")
    assert grid.zones == [Zone.GREEN, Zone.OVERLOAD]


async def test_red_to_yellow_at_once_below_the_margin(grid: Grid) -> None:
    """Red stays red at 4400 W; 4340 W is yellow at once."""
    await grid.setup("4600")
    await grid.load("4400")
    assert grid.zone is Zone.RED
    await grid.load("4340")
    assert grid.zone is Zone.YELLOW


async def test_overload_down_at_once(grid: Grid) -> None:
    """Overload stays at 4900 W; 4840 W is red, 2000 W from overload is the pause."""
    await grid.setup("5100")
    await grid.load("4900")
    assert grid.zone is Zone.OVERLOAD
    await grid.load("4840")
    assert grid.zone is Zone.RED
    await grid.load("5100")
    await grid.load("2000")
    assert grid.zones == [Zone.RED, Zone.OVERLOAD, Zone.PAUSE]


async def test_no_data_after_the_tolerance(
    grid: Grid, caplog: pytest.LogCaptureFixture
) -> None:
    """Under 60 s the zone stays; then no data, one warning; back at once."""
    await grid.setup("4000")
    await grid.load(STATE_UNAVAILABLE)
    await grid.tick(59)
    assert grid.zone is Zone.YELLOW
    await grid.tick(2)
    assert grid.zone is Zone.NO_DATA
    await grid.load("not a number")
    await grid.tick(120)
    assert caplog.text.count("No power load data") == 1

    await grid.load("4600")
    assert grid.zones == [Zone.NO_DATA, Zone.RED]


async def test_green_after_no_data_through_the_pause(grid: Grid) -> None:
    """Data back below yellow: the pause first."""
    await grid.setup("1000")
    await grid.load(STATE_UNAVAILABLE)
    await grid.tick(61)
    await grid.load("1000")
    assert grid.zone is Zone.PAUSE
    await grid.tick(31)
    assert grid.zone is Zone.GREEN


async def test_short_dropout_is_silent(grid: Grid) -> None:
    """Data gone for 30 s in yellow: nothing changes."""
    await grid.setup("4000")
    await grid.load(STATE_UNAVAILABLE)
    await grid.tick(30)
    await grid.load("4000")
    await grid.tick(60)
    assert grid.zones == []


async def test_gone_power_sensor_is_a_repair(grid: Grid) -> None:
    """A3: the zones' sensor has no state after start: an issue; picked: gone."""
    grid.hass.states.async_remove(POWER)
    await grid.setup_without_sensor()
    issues = ir.async_get(grid.hass)
    assert issues.async_get_issue(DOMAIN, "missing_power_sensor") is not None

    assert await async_setup_component(grid.hass, "repairs", {})
    manager = repairs_flow_manager(grid.hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": "missing_power_sensor"})
    grid.hass.states.async_set("sensor.new_meter", "1000")
    await manager.async_configure(
        result["flow_id"], {"ac_power_sensor": "sensor.new_meter"}
    )
    await grid.hass.async_block_till_done()

    assert issues.async_get_issue(DOMAIN, "missing_power_sensor") is None
    assert grid.entry.data["ac_load_limits"]["ac_power_sensor"] == "sensor.new_meter"
    assert grid.zone is Zone.PAUSE
