"""The reading of a switch by its power: threshold, hysteresis, delay, tolerance."""

# ruff: noqa: S101

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.bestiarium_climate.power_reading import (
    PowerReading,
    PowerThresholds,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

POWER = "sensor.heater_power"


class Watched:
    """A reading started on the power sensor, and its changes counted."""

    def __init__(self, hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
        """Start a reading: on at 100 W, hysteresis 20 %, delay 10 s, tolerance 60 s."""
        self.hass = hass
        self.freezer = freezer
        self.changes = 0
        self.reading = PowerReading(
            hass,
            "Heater",
            POWER,
            PowerThresholds(
                min_power=100.0,
                hysteresis_percent=20,
                delay_seconds=10,
                tolerance_seconds=60,
            ),
            self._changed,
        )

    def _changed(self) -> None:
        self.changes += 1

    async def power(self, state: str) -> None:
        """Show the power sensor's state."""
        self.hass.states.async_set(POWER, state)
        await self.hass.async_block_till_done()

    async def tick(self, seconds: float) -> None:
        """Let time pass."""
        self.freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(self.hass)
        await self.hass.async_block_till_done()


@pytest.fixture
async def watched(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> Watched:
    """Return a reading started on a sensor that reads 0 W."""
    hass.states.async_set(POWER, "0")
    watched = Watched(hass, freezer)
    watched.reading.start()
    return watched


async def test_on_at_min_power_off_below_hysteresis(watched: Watched) -> None:
    """On at 100 W after the delay; 85 W keeps it on; 79 W turns it off."""
    assert watched.reading.available
    assert not watched.reading.is_on
    await watched.power("100")
    await watched.tick(11)
    assert watched.reading.is_on
    await watched.power("85")
    await watched.tick(11)
    assert watched.reading.is_on
    await watched.power("79")
    await watched.tick(11)
    assert not watched.reading.is_on
    assert watched.changes == 2


async def test_a_change_shorter_than_the_delay_is_not_shown(watched: Watched) -> None:
    """On; 0 W for 5 s and back: still on, nothing told."""
    await watched.power("100")
    await watched.tick(11)
    changes = watched.changes
    await watched.power("0")
    await watched.tick(5)
    await watched.power("100")
    await watched.tick(11)
    assert watched.reading.is_on
    assert watched.changes == changes


async def test_no_data_after_the_tolerance_back_at_once(watched: Watched) -> None:
    """Unavailable 30 s: still available; 61 s: not; a number again: at once."""
    await watched.power(STATE_UNAVAILABLE)
    await watched.tick(30)
    assert watched.reading.available
    await watched.tick(31)
    assert not watched.reading.available
    await watched.power("100")
    assert watched.reading.available
    assert watched.reading.is_on


async def test_last_changed_is_when_the_shown_state_changed(watched: Watched) -> None:
    """The moment the shown state changed, not the moment of the power."""
    await watched.power("100")
    await watched.tick(10)
    shown_at = dt_util.utcnow()
    await watched.tick(30)
    assert watched.reading.is_on
    assert watched.reading.last_changed == shown_at


async def test_stopped_reading_follows_nothing(watched: Watched) -> None:
    """After stop the power changes nothing."""
    watched.reading.stop()
    await watched.power("100")
    await watched.tick(11)
    assert not watched.reading.is_on
    assert watched.changes == 0
