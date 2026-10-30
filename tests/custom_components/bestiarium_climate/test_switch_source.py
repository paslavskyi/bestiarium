"""The sources of a thermostat's switch: its own state, or read by its power."""

# ruff: noqa: S101

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, ServiceCall, callback
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.bestiarium_climate.const import (
    SUBENTRY_TYPE_THERMOSTAT,
    SUBENTRY_TYPE_UNIVERSAL,
)
from custom_components.bestiarium_climate.switch_source import (
    EntitySwitch,
    PoweredSwitch,
    SwitchSource,
    SwitchState,
    switch_source,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory

SWITCH = "switch.heater"
POWER = "sensor.heater_power"


def _subentry(subentry_type: str, **data: Any) -> ConfigSubentry:
    return ConfigSubentry(
        data={"target_switch": SWITCH, **data},
        subentry_type=subentry_type,
        title="Heater",
        unique_id=None,
    )


class Watched:
    """A source started, its changes recorded (was_on of each)."""

    def __init__(self, hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
        """Follow nothing yet."""
        self.hass = hass
        self.freezer = freezer
        self.changes: list[bool] = []
        self.source: SwitchSource

    def start(self, subentry: ConfigSubentry) -> SwitchSource:
        """Make the source of a subentry and start it."""
        self.source = switch_source(self.hass, subentry, "Heater", setting=lambda: None)
        self.source.start(self.changes.append)
        return self.source

    async def set(self, entity_id: str, state: str, **attributes: Any) -> None:
        """Show an entity's state."""
        self.hass.states.async_set(entity_id, state, attributes)
        await self.hass.async_block_till_done()

    async def tick(self, seconds: float) -> None:
        """Let time pass."""
        self.freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(self.hass)
        await self.hass.async_block_till_done()


@pytest.fixture
def watched(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> Watched:
    """Return a watcher of sources."""
    return Watched(hass, freezer)


async def test_entity_switch_reads_the_switch(
    hass: HomeAssistant, watched: Watched
) -> None:
    """On, off, and gone while missing, unavailable or unknown."""
    source = watched.start(_subentry(SUBENTRY_TYPE_THERMOSTAT))
    assert source.state() is None
    await watched.set(SWITCH, STATE_ON)
    assert source.state() == SwitchState(
        on=True, changed_at=hass.states.get(SWITCH).last_changed
    )
    await watched.set(SWITCH, STATE_OFF)
    assert source.state() == SwitchState(
        on=False, changed_at=hass.states.get(SWITCH).last_changed
    )
    for gone in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        await watched.set(SWITCH, gone)
        assert source.state() is None


async def test_powered_switch_reads_the_power(watched: Watched) -> None:
    """An optimistic switch on, its power 5 W: off; 100 W for the delay: on."""
    await watched.set(SWITCH, STATE_ON)
    await watched.set(POWER, "5")
    source = watched.start(
        _subentry(SUBENTRY_TYPE_UNIVERSAL, power_entity=POWER, min_power=20)
    )
    assert isinstance(source, PoweredSwitch)
    assert source.state() is not None
    assert source.state().on is False
    await watched.set(POWER, "100")
    await watched.tick(10)
    on_at = source.state().changed_at
    assert source.state() == SwitchState(on=True, changed_at=on_at)
    await watched.tick(30)
    assert source.state().changed_at == on_at


async def test_powered_switch_unknown_switch_is_not_gone(watched: Watched) -> None:
    """A template switch over scenes is unknown until its first command."""
    await watched.set(SWITCH, STATE_UNKNOWN)
    await watched.set(POWER, "0")
    source = watched.start(_subentry(SUBENTRY_TYPE_UNIVERSAL, power_entity=POWER))
    assert source.state() is not None


async def test_powered_switch_gone_without_power_data_or_switch(
    watched: Watched,
) -> None:
    """Gone: no power data for longer than the tolerance, or the switch unavailable."""
    await watched.set(SWITCH, STATE_OFF)
    await watched.set(POWER, "0")
    source = watched.start(_subentry(SUBENTRY_TYPE_UNIVERSAL, power_entity=POWER))
    await watched.set(POWER, STATE_UNAVAILABLE)
    await watched.tick(61)
    assert source.state() is None
    await watched.set(POWER, "0")
    assert source.state() is not None
    await watched.set(SWITCH, STATE_UNAVAILABLE)
    assert source.state() is None


async def test_on_change_tells_was_on_once_per_change(watched: Watched) -> None:
    """Off, on, off: two changes; a change of attributes only tells nothing."""
    await watched.set(SWITCH, STATE_OFF)
    watched.start(_subentry(SUBENTRY_TYPE_THERMOSTAT))
    await watched.set(SWITCH, STATE_ON)
    await watched.set(SWITCH, STATE_ON, friendly_name="Heater")
    await watched.set(SWITCH, STATE_OFF)
    assert watched.changes == [False, True]


async def test_powered_switch_tells_a_change_of_the_power(watched: Watched) -> None:
    """The power on after the delay: one change, was off."""
    await watched.set(SWITCH, STATE_ON)
    await watched.set(POWER, "0")
    watched.start(_subentry(SUBENTRY_TYPE_UNIVERSAL, power_entity=POWER))
    await watched.set(POWER, "100")
    await watched.tick(10)
    assert watched.changes == [False]


async def test_turn_calls_the_switch_service(
    hass: HomeAssistant, watched: Watched
) -> None:
    """On and off are the switch's services."""
    calls: list[tuple[str, str]] = []

    @callback
    def record(call: ServiceCall) -> None:
        calls.append((call.service, call.data["entity_id"]))

    hass.services.async_register("switch", "turn_on", record)
    hass.services.async_register("switch", "turn_off", record)
    await watched.set(SWITCH, STATE_OFF)
    source = watched.start(_subentry(SUBENTRY_TYPE_THERMOSTAT))
    await source.async_turn(on=True)
    await source.async_turn(on=False)
    assert calls == [("turn_on", SWITCH), ("turn_off", SWITCH)]


async def test_factory_picks_the_source(hass: HomeAssistant) -> None:
    """The old thermostat and a universal without power: the switch; with: power."""
    for subentry, kind in (
        (_subentry(SUBENTRY_TYPE_THERMOSTAT), EntitySwitch),
        (_subentry(SUBENTRY_TYPE_UNIVERSAL), EntitySwitch),
        (_subentry(SUBENTRY_TYPE_UNIVERSAL, power_entity=POWER), PoweredSwitch),
    ):
        assert (
            type(switch_source(hass, subentry, "Heater", setting=lambda: None)) is kind
        )
