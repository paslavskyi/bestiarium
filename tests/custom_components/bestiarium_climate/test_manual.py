"""The manual mode of the universal thermostat (FR-028) and the red zone (FR-029)."""

# ruff: noqa: S101

from __future__ import annotations

import pytest
from homeassistant.const import STATE_OFF

from .test_balancer import MINUTE, Floors, _hvac, _temperatures, floors

__all__ = ["floors"]

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

UNIVERSAL = frozenset({"floor_1"})


async def _manual(floors: Floors, **setup: object) -> None:
    """Floor 1 universal in manual (heat), idle at 22 °C; the others heat."""
    await floors.setup(universal=UNIVERSAL, **setup)
    await _temperatures(floors, floor_1="22")
    await floors.tick(MINUTE)
    await _hvac(floors, "floor_1", "heat")
    await floors.tick(MINUTE)
    floors.calls.clear()


async def _manual_heats(floors: Floors) -> None:
    """Floor 1 cold: its manual mode turns it on."""
    await _temperatures(floors, floor_1="18")
    await floors.tick(MINUTE)
    assert "floor_1" in floors.on()


async def test_manual_heats_by_temperature_alone(floors: Floors) -> None:
    """On below the target, off above it: one command each."""
    await _manual(floors)
    assert "floor_1" not in floors.on()
    await _manual_heats(floors)
    await _temperatures(floors, floor_1="22")
    await floors.tick(MINUTE)
    assert "floor_1" not in floors.on()
    assert [c for c in floors.calls if c[1] == "floor_1"] == [
        ("turn_on", "floor_1"),
        ("turn_off", "floor_1"),
    ]


async def test_manual_holds_no_place(floors: Floors) -> None:
    """Capacity 1 taken by an old one: the manual one heats as well."""
    await _manual(floors, capacity=1)
    assert len(floors.on()) == 1
    await _manual_heats(floors)
    assert len(floors.on()) == 2


@pytest.mark.parametrize("zone", ["yellow", "pause", "no_data"])
async def test_manual_is_left_alone_out_of_red(floors: Floors, zone: str) -> None:
    """Yellow, the pause, no data: the manual one starts and stops by itself."""
    await _manual(floors, zones=True)
    floors.zone(zone)
    await floors.tick(MINUTE)
    await _manual_heats(floors)
    await floors.tick(5 * MINUTE)
    assert "floor_1" in floors.on()
    await _temperatures(floors, floor_1="22")
    await floors.tick(MINUTE)
    assert "floor_1" not in floors.on()


async def test_manual_command_goes_once(floors: Floors) -> None:
    """A person turns its switch off: no "on" again while the demand is the same."""
    await _manual(floors)
    await _manual_heats(floors)
    floors.hass.states.async_set("switch.floor_1", STATE_OFF)
    await floors.tick(10 * MINUTE)
    assert floors.calls.count(("turn_on", "floor_1")) == 1
    assert "floor_1" not in floors.on()


async def test_manual_never_unresponsive(floors: Floors) -> None:
    """A switch that does not answer: one "on", no "Not responding", no deferral."""
    await _manual(floors)
    floors.stuck.add("floor_1")
    await _temperatures(floors, floor_1="18")
    await floors.tick(60 * MINUTE)
    assert floors.calls.count(("turn_on", "floor_1")) == 1
    assert floors.state("floor_1") == "normal"


async def test_manual_respects_the_minimum_cycle(floors: Floors) -> None:
    """With a minimum cycle of 10 minutes the switch changes no sooner."""
    await floors.setup(universal=UNIVERSAL)
    subentry = floors.entry.subentries["floor_1"]
    floors.hass.config_entries.async_update_subentry(
        floors.entry,
        subentry,
        data={
            **subentry.data,
            "min_cycle_duration": {"hours": 0, "minutes": 10, "seconds": 0},
        },
    )
    await floors.hass.async_block_till_done()
    await _temperatures(floors, floor_1="18")
    await _hvac(floors, "floor_1", "heat")
    await floors.tick(MINUTE)
    assert "floor_1" in floors.on()
    on_at = floors.calls.count(("turn_on", "floor_1"))
    await _temperatures(floors, floor_1="22")
    await floors.tick(5 * MINUTE)
    assert "floor_1" in floors.on()
    await floors.tick(6 * MINUTE)
    assert "floor_1" not in floors.on()
    assert floors.calls.count(("turn_on", "floor_1")) == on_at


async def test_manual_to_auto_keeps_a_running_switch(floors: Floors) -> None:
    """Manual running, a place free: auto takes it as it is, no command."""
    await _manual(floors, capacity=3)
    await _manual_heats(floors)
    calls = list(floors.calls)
    await _hvac(floors, "floor_1", "auto")
    await floors.tick(MINUTE)
    assert floors.calls == calls
    assert "floor_1" in floors.on()


async def test_manual_to_auto_without_a_place_turns_off_stepwise(
    floors: Floors,
) -> None:
    """Manual running, no place in the capacity: auto turns it off."""
    await _manual(floors, capacity=1)
    await _manual_heats(floors)
    await _hvac(floors, "floor_1", "auto")
    await floors.tick(MINUTE)
    assert ("turn_off", "floor_1") in floors.calls
    assert len(floors.on()) == 1
