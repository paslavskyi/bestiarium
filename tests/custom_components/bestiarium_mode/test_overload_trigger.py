"""
The action that raises the overload ahead of the meter (FR-031).

Limit 5000 W, margin 150 W, pause 30 s, tolerance 60 s, hold 30 s by default:
the entry of the zone tests has no hold in its data.
"""

# ruff: noqa: S101

import pytest
import voluptuous as vol
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_mode.const import (
    ATTR_AWAITING_METER,
    DOMAIN,
    EVENT_LOAD_LIMIT_CHANGED,
    SERVICE_TRIGGER_OVERLOAD,
)
from custom_components.bestiarium_mode.zones import Zone

from .test_zones import Grid

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

OVERLOAD_SENSOR = "binary_sensor.overload"


@pytest.fixture
def grid(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> Grid:
    """Return the grid of a test."""
    return Grid(hass, freezer)


async def trigger(hass: HomeAssistant, source: str = "the relay") -> None:
    """Call the action."""
    await hass.services.async_call(
        DOMAIN, SERVICE_TRIGGER_OVERLOAD, {"source": source}, blocking=True
    )
    await hass.async_block_till_done()


async def test_overload_at_once_below_the_limit(
    hass: HomeAssistant, grid: Grid
) -> None:
    """Overload at once below the limit."""
    await grid.setup("1000")
    await grid.tick(31)
    assert grid.zone is Zone.GREEN

    await trigger(hass)

    assert grid.zone is Zone.OVERLOAD
    state = hass.states.get(OVERLOAD_SENSOR)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_AWAITING_METER] is True
    green = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_green"
    )
    assert hass.states.get(green).state == STATE_OFF


async def test_old_reading_below_the_limit_does_not_lower_it(
    hass: HomeAssistant, grid: Grid
) -> None:
    """Old reading below the limit does not lower it."""
    await grid.setup("1000")
    await trigger(hass)

    await grid.tick(10)
    await grid.load("1100")
    await grid.tick(19)

    assert grid.zone is Zone.OVERLOAD
    assert ATTR_AWAITING_METER in hass.states.get(OVERLOAD_SENSOR).attributes


async def test_back_by_the_usual_rules_after_the_hold(
    hass: HomeAssistant, grid: Grid
) -> None:
    """Back by the usual rules after the hold."""
    await grid.setup("1000")
    await trigger(hass)

    await grid.tick(30)
    assert grid.zone is Zone.PAUSE
    assert ATTR_AWAITING_METER not in hass.states.get(OVERLOAD_SENSOR).attributes

    # The pause counts from the end of the hold
    await grid.tick(29)
    assert grid.zone is Zone.PAUSE
    await grid.tick(1)
    assert grid.zone is Zone.GREEN


@pytest.mark.parametrize(
    ("watts", "zone"),
    [("4000", Zone.YELLOW), ("4600", Zone.RED), ("4900", Zone.OVERLOAD)],
)
async def test_after_the_hold_the_zone_of_the_load(
    hass: HomeAssistant, grid: Grid, watts: str, zone: Zone
) -> None:
    """After the hold the zone of the load."""
    await grid.setup("1000")
    await trigger(hass)
    await grid.load(watts)

    await grid.tick(30)

    assert grid.zone is zone


async def test_the_pause_ends_at_the_call(hass: HomeAssistant, grid: Grid) -> None:
    """The pause ends at the call."""
    await grid.setup("1000")
    await grid.tick(20)
    assert grid.zone is Zone.PAUSE

    await trigger(hass)
    await grid.tick(10)

    assert grid.zone is Zone.OVERLOAD


async def test_a_meter_over_the_limit_needs_no_waiting(
    hass: HomeAssistant, grid: Grid
) -> None:
    """A meter over the limit needs no waiting."""
    await grid.setup("6000")

    await trigger(hass)

    assert grid.zone is Zone.OVERLOAD
    assert ATTR_AWAITING_METER not in hass.states.get(OVERLOAD_SENSOR).attributes


async def test_the_meter_over_the_limit_ends_the_hold(
    hass: HomeAssistant, grid: Grid
) -> None:
    """Once the meter has shown the overload, it leads the zone at once."""
    await grid.setup("1000")
    await trigger(hass)

    await grid.load("6000")
    assert grid.zone is Zone.OVERLOAD
    assert ATTR_AWAITING_METER not in hass.states.get(OVERLOAD_SENSOR).attributes

    await grid.load("1000")
    assert grid.zone is Zone.PAUSE


async def test_the_meter_over_the_limit_at_the_call_is_no_hold(
    hass: HomeAssistant, grid: Grid, caplog: pytest.LogCaptureFixture
) -> None:
    """The load falls after the first queue: down at once, as without the action."""
    await grid.setup("6000")
    await trigger(hass)
    assert "Overload triggered by the relay" in caplog.text

    await grid.load("1000")

    assert grid.zone is Zone.PAUSE


async def test_a_repeat_after_the_meter_showed_it_awaits_nothing(
    hass: HomeAssistant, grid: Grid, caplog: pytest.LogCaptureFixture
) -> None:
    """The zone goes up and is held again, but the meter's answer is not due again."""
    await grid.setup("6000")
    await trigger(hass)
    await grid.load("1000")
    assert grid.zone is Zone.PAUSE

    await grid.tick(10)
    await trigger(hass, "the relay again")

    assert grid.zone is Zone.OVERLOAD
    assert ATTR_AWAITING_METER not in hass.states.get(OVERLOAD_SENSOR).attributes
    assert "Overload triggered by the relay again" in caplog.text

    await grid.tick(29)
    assert grid.zone is Zone.OVERLOAD
    await grid.tick(1)
    assert grid.zone is Zone.PAUSE


async def test_a_repeat_that_changes_no_zone_is_no_new_line(
    hass: HomeAssistant, grid: Grid, caplog: pytest.LogCaptureFixture
) -> None:
    """The meter leads the overload and a repeat comes: nothing new has started."""
    await grid.setup("6000")
    await trigger(hass)
    await trigger(hass, "the relay again")

    assert caplog.text.count("Overload triggered") == 1


async def test_a_call_after_the_first_hold_awaits_the_meter_again(
    hass: HomeAssistant, grid: Grid, caplog: pytest.LogCaptureFixture
) -> None:
    """A signal later than the first call's hold is a new one."""
    await grid.setup("6000")
    await trigger(hass)
    await grid.load("1000")

    await grid.tick(30)
    await trigger(hass)

    assert ATTR_AWAITING_METER in hass.states.get(OVERLOAD_SENSOR).attributes
    assert caplog.text.count("Overload triggered") == 2  # noqa: PLR2004


async def test_a_repeated_call_counts_the_hold_again(
    hass: HomeAssistant, grid: Grid, caplog: pytest.LogCaptureFixture
) -> None:
    """A repeated call counts the hold again."""
    await grid.setup("1000")
    await trigger(hass)
    await grid.tick(20)
    await trigger(hass, "the relay again")

    # The meter's answer is due at the end of the first call's hold
    await grid.tick(10)
    assert grid.zone is Zone.OVERLOAD
    assert ATTR_AWAITING_METER not in hass.states.get(OVERLOAD_SENSOR).attributes

    await grid.tick(19)
    assert grid.zone is Zone.OVERLOAD
    await grid.tick(1)
    assert grid.zone is Zone.PAUSE

    assert "Overload triggered by the relay" in caplog.text
    assert caplog.text.count("Overload triggered") == 1


async def test_the_zone_changes_once_for_repeated_calls(
    hass: HomeAssistant, grid: Grid
) -> None:
    """The zone changes once for repeated calls."""
    await grid.setup("1000")
    await trigger(hass)
    last_changed = hass.states.get(OVERLOAD_SENSOR).last_changed
    await grid.tick(5)
    await trigger(hass)

    assert hass.states.get(OVERLOAD_SENSOR).last_changed == last_changed


async def test_a_new_call_after_the_hold_is_a_new_line(
    hass: HomeAssistant, grid: Grid, caplog: pytest.LogCaptureFixture
) -> None:
    """A new call after the hold is a new line."""
    await grid.setup("1000")
    await trigger(hass)
    await grid.tick(30)
    await trigger(hass)

    assert caplog.text.count("Overload triggered") == 2  # noqa: PLR2004
    assert ATTR_AWAITING_METER in hass.states.get(OVERLOAD_SENSOR).attributes


async def test_no_data_before_the_call(hass: HomeAssistant, grid: Grid) -> None:
    """No data before the call."""
    await grid.setup("1000")
    await grid.load(STATE_UNAVAILABLE)
    await grid.tick(60)
    assert grid.zone is Zone.NO_DATA

    await trigger(hass)
    assert grid.zone is Zone.OVERLOAD
    assert hass.states.get(OVERLOAD_SENSOR).state == STATE_ON

    await grid.tick(30)
    assert grid.zone is Zone.NO_DATA
    assert hass.states.get(OVERLOAD_SENSOR).state == STATE_UNAVAILABLE


async def test_data_gone_during_the_hold_for_longer_than_the_tolerance(
    hass: HomeAssistant, grid: Grid
) -> None:
    """The tolerance ends during a hold of 90 s: the hold goes on, then no data."""
    await grid.setup_without_sensor()
    grid.hass.config_entries.async_update_entry(
        grid.entry,
        data={
            **grid.entry.data,
            "ac_load_limits": {
                **grid.entry.data["ac_load_limits"],
                "ac_overload_hold_seconds": 90,
            },
        },
    )
    hass.bus.async_fire(EVENT_LOAD_LIMIT_CHANGED)
    await trigger(hass)

    await grid.tick(60)
    assert grid.zone is Zone.OVERLOAD

    await grid.tick(30)
    assert grid.zone is Zone.NO_DATA


async def test_after_the_hold_the_tolerance_goes_on(
    hass: HomeAssistant, grid: Grid
) -> None:
    """After the hold the tolerance goes on."""
    await grid.setup("1000")
    await trigger(hass)
    await grid.tick(20)
    await grid.load(STATE_UNAVAILABLE)

    await grid.tick(10)
    assert grid.zone is Zone.OVERLOAD

    await grid.tick(50)
    assert grid.zone is Zone.NO_DATA


async def test_data_back_during_the_hold(
    hass: HomeAssistant, grid: Grid, caplog: pytest.LogCaptureFixture
) -> None:
    """Data back during the hold."""
    await grid.setup("1000")
    await grid.load(STATE_UNAVAILABLE)
    await grid.tick(60)
    await trigger(hass)

    await grid.load("1000")
    assert grid.zone is Zone.OVERLOAD
    assert "Power load data is back" in caplog.text

    await grid.tick(30)
    assert grid.zone is Zone.PAUSE


async def test_a_limit_change_during_the_hold(hass: HomeAssistant, grid: Grid) -> None:
    """A limit change during the hold."""
    await grid.setup("3000")
    await trigger(hass)
    hass.config_entries.async_update_entry(
        grid.entry,
        data={
            **grid.entry.data,
            "ac_load_limits": {
                **grid.entry.data["ac_load_limits"],
                "ac_load_limit_w": 3500,
            },
        },
    )
    hass.bus.async_fire(EVENT_LOAD_LIMIT_CHANGED)
    await hass.async_block_till_done()
    assert grid.zone is Zone.OVERLOAD

    # 3000 W against the new limit: the yellow zone starts at 2625 W
    await grid.tick(30)
    assert grid.zone is Zone.YELLOW


async def test_a_reload_ends_the_hold(hass: HomeAssistant, grid: Grid) -> None:
    """A reload ends the hold."""
    await grid.setup("1000")
    await trigger(hass)

    assert await hass.config_entries.async_reload(grid.entry.entry_id)
    await hass.async_block_till_done()

    assert grid.entry.runtime_data.zone is not Zone.OVERLOAD
    assert ATTR_AWAITING_METER not in hass.states.get(OVERLOAD_SENSOR).attributes


async def test_nothing_to_wait_for_without_the_action(
    hass: HomeAssistant, grid: Grid
) -> None:
    """Nothing to wait for without the action."""
    await grid.setup("6000")
    assert ATTR_AWAITING_METER not in hass.states.get(OVERLOAD_SENSOR).attributes
    await grid.load("1000")
    assert grid.zone is Zone.PAUSE


async def test_error_without_an_entry(hass: HomeAssistant) -> None:
    """Error without an entry."""
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError) as err:
        await trigger(hass)
    assert err.value.translation_key == "not_loaded"


async def test_error_with_an_entry_not_loaded(hass: HomeAssistant, grid: Grid) -> None:
    """Error with an entry not loaded."""
    await grid.setup("1000")
    assert await hass.config_entries.async_unload(grid.entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await trigger(hass)
    assert err.value.translation_key == "not_loaded"


async def test_error_without_load_limits(hass: HomeAssistant) -> None:
    """Error without load limits."""
    entry = MockConfigEntry(domain=DOMAIN, data={"options": [{"name": "Grid on"}]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await trigger(hass)
    assert err.value.translation_key == "no_load_limits"


async def test_a_source_is_required(hass: HomeAssistant, grid: Grid) -> None:
    """A source is required."""
    await grid.setup("1000")
    with pytest.raises(vol.Invalid, match="source"):
        await hass.services.async_call(
            DOMAIN, SERVICE_TRIGGER_OVERLOAD, {}, blocking=True
        )
