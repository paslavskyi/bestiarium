"""The manual mode of the universal thermostat (FR-028) and the red zone (FR-029)."""

# ruff: noqa: S101

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import STATE_OFF
from homeassistant.core import Event, HomeAssistant, callback

from custom_components.bestiarium_climate.const import (
    EVENT_MANUAL_TURNED_OFF,
    ManualCause,
    ManualTurnedOffData,
)

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


def _manual_events(floors: Floors) -> list[dict[str, Any]]:
    """Record the payloads of the manual thermostats turned off by the zone."""
    payloads: list[dict[str, Any]] = []

    @callback
    def record(event: Event) -> None:
        payloads.append(dict(event.data))

    floors.hass.bus.async_listen(EVENT_MANUAL_TURNED_OFF, record)
    return payloads


def _mode(floors: Floors, floor: str) -> str:
    return floors.hass.states.get(floors.climate(floor)).state


async def _manual_running_in_green(floors: Floors) -> list[dict[str, Any]]:
    """Floor 1 manual and running, the old ones heating, the zone green."""
    events = _manual_events(floors)
    await _manual(floors, capacity=2, zones=True)
    await _manual_heats(floors)
    assert floors.on() == {"floor_1", "floor_2", "floor_3"}
    floors.calls.clear()
    return events


@pytest.mark.parametrize("zone", ["red", "overload"])
async def test_entering_the_zone_turns_manual_off_first(
    floors: Floors, zone: str
) -> None:
    """The entry: the manual one goes off first, its mode off, the event."""
    events = await _manual_running_in_green(floors)
    floors.zone(zone)
    await floors.tick(5, step=5)
    assert floors.calls[0] == ("turn_off", "floor_1")
    assert _mode(floors, "floor_1") == "off"
    assert events == [
        {
            "thermostat": floors.climate("floor_1"),
            "switch_entity_id": "switch.floor_1",
            "zone": zone,
            "cause": "zone_entered",
        }
    ]


async def test_overload_turns_the_others_off_too(floors: Floors) -> None:
    """The overload: the manual one first, then all at once."""
    await _manual_running_in_green(floors)
    floors.zone("overload")
    await floors.tick(5, step=5)
    assert floors.on() == set()


async def test_manual_stays_off_after_red(floors: Floors) -> None:
    """Back to green the manual thermostat stays off until a person turns it on."""
    events = await _manual_running_in_green(floors)
    floors.zone("red")
    await floors.tick(MINUTE)
    floors.zone("green")
    await floors.tick(10 * MINUTE)
    assert _mode(floors, "floor_1") == "off"
    assert "floor_1" not in floors.on()
    assert len(events) == 1


async def test_manual_turned_on_in_red_goes_off_at_once(floors: Floors) -> None:
    """Heat set in red: off at once, no "on" sent, the event says so."""
    events = _manual_events(floors)
    await floors.setup(universal=UNIVERSAL, zones=True)
    await _hvac(floors, "floor_1", "off")
    await floors.tick(MINUTE)
    floors.zone("red")
    await floors.tick(MINUTE)
    floors.calls.clear()
    await _hvac(floors, "floor_1", "heat")
    assert _mode(floors, "floor_1") == "off"
    assert ("turn_on", "floor_1") not in floors.calls
    assert events[-1]["cause"] == "turned_on"
    assert events[-1]["zone"] == "red"


async def test_manual_in_the_pause_between_red_and_overload(floors: Floors) -> None:
    """Red, the pause: heat goes on; the overload is no new entry: cause turned_on."""
    events = _manual_events(floors)
    await floors.setup(universal=UNIVERSAL, zones=True)
    await _hvac(floors, "floor_1", "off")
    floors.zone("red")
    await floors.tick(MINUTE)
    floors.zone("pause")
    await floors.tick(5, step=5)
    await _hvac(floors, "floor_1", "heat")
    await floors.tick(5, step=5)
    assert ("turn_on", "floor_1") in floors.calls
    offs = floors.calls.count(("turn_off", "floor_2"))
    floors.zone("overload")
    await floors.tick(5, step=5)
    assert _mode(floors, "floor_1") == "off"
    assert events[-1]["cause"] == "turned_on"
    assert events[-1]["zone"] == "overload"
    # No entry: the "off"s outside the attempts are not sent again
    assert floors.calls.count(("turn_off", "floor_2")) == offs


async def test_manual_restored_in_red_is_turned_off(
    hass: HomeAssistant, floors: Floors
) -> None:
    """HA starts again in red with a manual thermostat: the entry turns it off."""
    events = await _manual_running_in_green(floors)
    assert await hass.config_entries.async_unload(floors.entry.entry_id)
    floors.zone("red")
    assert await hass.config_entries.async_setup(floors.entry.entry_id)
    await hass.async_block_till_done()
    # The first cycle, at the latest the periodic one
    await floors.tick(MINUTE)
    assert _mode(floors, "floor_1") == "off"
    assert [e["cause"] for e in events] == ["zone_entered"]


async def test_manual_shed_switch_still_on_is_turned_off_stepwise(
    floors: Floors,
) -> None:
    """Its switch does not turn off: again a cycle apart in red, "Does not turn off"."""
    await _manual_running_in_green(floors)
    floors.stuck.add("floor_1")
    floors.zone("red")
    await floors.tick(4 * MINUTE)
    assert floors.calls.count(("turn_off", "floor_1")) >= 2
    assert floors.state("floor_1") == "not_turning_off"


def test_manual_event_payload_is_typed() -> None:
    """The payload's fields are the contract for the owner's automations."""
    assert set(ManualTurnedOffData.__annotations__) == {
        "thermostat",
        "switch_entity_id",
        "zone",
        "cause",
    }
    assert {cause.value for cause in ManualCause} == {"zone_entered", "turned_on"}


async def test_manual_to_auto_near_the_red_threshold_keeps_running(
    floors: Floors,
) -> None:
    """Green near red: its own power is in the load already, it is no new start."""
    await _manual(floors, capacity=3, zones=True, heater_power=2000)
    await _manual_heats(floors)
    floors.load(3000)
    await floors.tick(MINUTE)
    await _hvac(floors, "floor_1", "auto")
    await floors.tick(MINUTE)
    assert ("turn_off", "floor_1") not in floors.calls
    assert "floor_1" in floors.on()


async def test_manual_to_auto_in_yellow_keeps_running(floors: Floors) -> None:
    """Yellow starts nothing, but a running switch with a place goes on."""
    await _manual(floors, capacity=3, zones=True)
    await _manual_heats(floors)
    floors.zone("yellow")
    await floors.tick(MINUTE)
    await _hvac(floors, "floor_1", "auto")
    await floors.tick(MINUTE)
    assert ("turn_off", "floor_1") not in floors.calls
    assert "floor_1" in floors.on()
