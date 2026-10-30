"""
The manual modes of the air conditioner thermostat (FR-028, DEV-013).

Heat, cool, dry and fan only set the air conditioner to the person's mode
and target once, outside the capacity, without attempts; red and the
overload turn them off first.
"""

# ruff: noqa: S101

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .test_ac_thermostat import HEAT_22, MINUTE, OFF, Units, units

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

__all__ = ["units"]

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

HEAT_21 = ("set_temperature", {"hvac_mode": "heat", "temperature": 21})


async def test_each_manual_mode_goes_once_with_the_persons_target(
    units: Units,
) -> None:
    """Heat, cool, dry with the target as it is; fan only without; no repeats."""
    await units.setup()
    for mode in ("heat", "cool", "dry"):
        await units.hvac("ac_1", mode)
        assert units.frames("ac_1")[-1] == (
            "set_temperature",
            {"hvac_mode": mode, "temperature": 21},
        )
    await units.hvac("ac_1", "fan_only")
    assert units.frames("ac_1")[-1] == ("set_hvac_mode", {"hvac_mode": "fan_only"})
    sent = len(units.frames("ac_1"))
    await units.tick(10 * MINUTE)
    assert len(units.frames("ac_1")) == sent


async def test_manual_holds_no_place_and_has_no_attempts(units: Units) -> None:
    """Capacity 0, an AC that hears nothing: one frame; an hour later normal."""
    units.deaf.add("ac_1")
    await units.setup(capacity=0)
    await units.hvac("ac_1", "heat")
    await units.tick(60 * MINUTE)
    assert units.frames("ac_1") == [HEAT_21]
    assert units.state("ac_1") == "normal"


async def test_manual_leaves_a_change_past_the_thermostat_alone(units: Units) -> None:
    """In heat, the AC turned off from a dashboard: the thermostat does nothing."""
    await units.setup()
    await units.hvac("ac_1", "heat")
    await units.change_past_the_thermostat("ac_1", "off")
    sent = len(units.calls)
    await units.tick(10 * MINUTE)
    assert len(units.calls) == sent


async def test_manual_setting_change_goes_at_once(units: Units) -> None:
    """A new target in heat: the new setpoint at once."""
    await units.setup()
    await units.hvac("ac_1", "heat")
    await units.target("ac_1", 24)
    assert units.frames("ac_1")[-1] == (
        "set_temperature",
        {"hvac_mode": "heat", "temperature": 24},
    )


async def test_manual_entered_with_the_ac_already_so_sends_nothing(
    units: Units,
) -> None:
    """Margin 0: auto set heat 21; heat with target 21 sends nothing more."""
    await units.setup(margin=0)
    assert units.frames("ac_1") == [HEAT_21]
    await units.hvac("ac_1", "heat")
    await units.tick(5 * MINUTE)
    assert units.frames("ac_1") == [HEAT_21]


async def test_manual_to_auto_keeps_the_running_ac_with_the_margin(
    units: Units,
) -> None:
    """A place free: no new start, one frame with the margin."""
    await units.setup(capacity=1)
    await units.hvac("ac_1", "heat")
    await units.hvac("ac_1", "auto")
    assert units.frames("ac_1")[-2:] == [HEAT_21, HEAT_22]
    await units.tick(5 * MINUTE)
    assert units.frames("ac_1")[-1] == HEAT_22
    assert units.state("ac_1") == "normal"


async def test_manual_to_auto_without_a_place_turns_off_stepwise(
    units: Units,
) -> None:
    """Capacity 0: back to auto, the running AC is turned off."""
    await units.setup(capacity=0)
    await units.hvac("ac_1", "heat")
    await units.tick(MINUTE)
    await units.hvac("ac_1", "auto")
    await units.tick(15, step=5)
    assert units.frames("ac_1") == [HEAT_21, OFF]


async def test_red_turns_the_manual_ac_off_first(
    hass: HomeAssistant, units: Units
) -> None:
    """Red: the manual one off first, its mode off, the event; then the rest."""
    await units.setup(units=("ac_1", "ac_2"), zones=True)
    await units.hvac("ac_1", "heat")
    await units.tick(MINUTE)
    first = len(units.calls)
    units.zone("red")
    await units.tick(10, step=5)
    assert units.calls[first][:2] == ("set_hvac_mode", "ac_1")
    assert hass.states.get(units.climate("ac_1")).state == "off"
    assert units.payloads["bestiarium_climate_manual_turned_off"] == [
        {
            "thermostat": units.climate("ac_1"),
            "switch_entity_id": "climate.ac_1",
            "zone": "red",
            "cause": "zone_entered",
        }
    ]


async def test_manual_ac_turned_on_in_red_goes_off_at_once(units: Units) -> None:
    """Heat chosen in red: off at once, the event; no frame of heat."""
    await units.setup(zones=True)
    units.zone("red")
    await units.tick(MINUTE)
    sent = len(units.frames("ac_1"))
    await units.hvac("ac_1", "heat")
    assert HEAT_21 not in units.frames("ac_1")[sent:]
    assert units.payloads["bestiarium_climate_manual_turned_off"][-1]["cause"] == (
        "turned_on"
    )
