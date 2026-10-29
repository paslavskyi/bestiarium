"""The universal thermostat: any switch, auto balanced with the old thermostats."""

# ruff: noqa: S101

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_climate.const import (
    DOMAIN,
    EVENT_POWER_CUT,
    EVENT_SWITCH_OUT_OF_CONTROL,
)
from custom_components.bestiarium_climate.control import Reading, ThermostatControl
from custom_components.bestiarium_climate.repairs import ThermostatSwitchRepairFlow

from .test_balancer import MINUTE, Floors, _hvac, _temperatures, floors

__all__ = ["floors"]

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

UNIVERSAL = frozenset({"floor_1"})
POWER = {"floor_1": "sensor.floor_1_power"}


def _payloads(hass: HomeAssistant, event_type: str) -> list[dict[str, Any]]:
    """Record the payloads of an event type."""
    payloads: list[dict[str, Any]] = []

    @callback
    def record(event: Event) -> None:
        payloads.append(dict(event.data))

    hass.bus.async_listen(event_type, record)
    return payloads


async def test_universal_auto_heats_and_stops(floors: Floors) -> None:
    """Auto: on below the target, off once the room is warm."""
    await floors.setup(universal=UNIVERSAL)
    assert "floor_1" in floors.on()
    await _temperatures(floors, floor_1="22")
    await floors.tick(MINUTE)
    assert "floor_1" not in floors.on()


async def test_universal_modes(hass: HomeAssistant, floors: Floors) -> None:
    """Auto, the manual mode of its heating, off; the old one keeps heat and off."""
    await floors.setup(universal=UNIVERSAL)
    universal = hass.states.get(floors.climate("floor_1"))
    assert universal.state == "auto"
    assert universal.attributes["hvac_modes"] == ["auto", "heat", "off"]
    old = hass.states.get(floors.climate("floor_2"))
    assert old.attributes["hvac_modes"] == ["heat", "off"]


async def test_universal_device_model(hass: HomeAssistant, floors: Floors) -> None:
    """The universal thermostat's device says what it is."""
    await floors.setup(universal=UNIVERSAL)
    entity = er.async_get(hass).async_get(floors.climate("floor_1"))
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device.model == "Universal Thermostat"


async def test_old_and_universal_share_the_capacity(floors: Floors) -> None:
    """Capacity 2 taken by the old ones: the universal waits, the rotation brings it in."""
    await floors.setup(universal=UNIVERSAL)
    await _temperatures(floors, floor_1="22")
    await floors.tick(6 * MINUTE)
    assert floors.on() == {"floor_2", "floor_3"}
    await _temperatures(floors, floor_1="15")
    await floors.tick(MINUTE)
    assert floors.on() == {"floor_2", "floor_3"}
    await floors.tick(11 * MINUTE)
    assert "floor_1" in floors.on()
    assert len(floors.on()) == 2


async def test_universal_off_frees_its_place(floors: Floors) -> None:
    """Off: its switch goes off and its place goes to an old one."""
    await floors.setup(capacity=1, universal=UNIVERSAL)
    await _temperatures(floors, floor_2="22", floor_3="22")
    await floors.tick(6 * MINUTE)
    assert floors.on() == {"floor_1"}
    await _temperatures(floors, floor_2="18")
    await floors.tick(MINUTE)
    assert floors.on() == {"floor_1"}
    await _hvac(floors, "floor_1", "off")
    await floors.tick(6 * MINUTE)
    assert floors.on() == {"floor_2"}


async def test_universal_stepwise_off_has_no_socket_step(floors: Floors) -> None:
    """A stuck switch: three "off"s, then the event with its power; no socket cut."""
    out = _payloads(floors.hass, EVENT_SWITCH_OUT_OF_CONTROL)
    await _running(floors)
    floors.stuck.add("floor_1")
    await _temperatures(floors, floor_1="25")
    await floors.tick(25 * MINUTE)
    assert floors.calls.count(("turn_off", "floor_1")) == 3
    assert len(out) == 1
    assert out[0]["switch_entity_id"] == "switch.floor_1"
    assert out[0]["power"] == 1500.0
    assert EVENT_POWER_CUT not in floors.events


async def test_universal_power_device_counts_for_no_power(
    hass: HomeAssistant, floors: Floors
) -> None:
    """The Bestiarium device whose power sensor is the universal's: relay off -> no power."""
    hass.states.async_set("switch.ac_relay", STATE_OFF)
    hass.states.async_set("sensor.floor_1_power", "0")
    MockConfigEntry(
        domain="bestiarium_devices",
        title="AC power",
        data={
            "switch_entity_id": "switch.ac_relay",
            "power_sensor_entity_id": "sensor.floor_1_power",
        },
    ).add_to_hass(hass)
    await floors.setup(universal=UNIVERSAL, power=POWER)
    await floors.tick(2 * MINUTE)
    assert floors.state("floor_1") == "no_power"
    assert "floor_1" not in floors.on()
    hass.states.async_set("switch.ac_relay", STATE_ON)
    await floors.tick(MINUTE)
    assert floors.state("floor_1") == "normal"


async def test_universal_restores_auto(hass: HomeAssistant, floors: Floors) -> None:
    """After a reload the universal thermostat is in auto again."""
    await floors.setup(universal=UNIVERSAL)
    await hass.config_entries.async_reload(floors.entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(floors.climate("floor_1")).state == "auto"


async def test_device_relay_repair_offers_no_virtual_switch(
    hass: HomeAssistant, floors: Floors
) -> None:
    """A universal on a device relay: the fix offers no virtual switch of Climate."""
    MockConfigEntry(
        domain="bestiarium_devices",
        title="Floor 1",
        data={"switch_entity_id": "switch.floor_1"},
        disabled_by=ConfigEntryDisabler.USER,
    ).add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "switch", DOMAIN, "virtual", suggested_object_id="virtual_one"
    )
    await floors.setup(universal=UNIVERSAL)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "switch_is_device_floor_1")
    assert issue is not None
    flow = ThermostatSwitchRepairFlow(issue.data)
    flow.hass = hass
    result = await flow.async_step_init()
    field = next(k for k in result["data_schema"].schema if k.schema == "target_switch")
    selector = result["data_schema"].schema[field]
    assert selector.config["exclude_entities"] == ["switch.virtual_one"]


def _control(floors: Floors, floor: str) -> ThermostatControl:
    return floors.hass.data[DOMAIN][floors.entry.entry_id].controls[floor]


def _reading(floors: Floors, floor: str) -> Reading | None:
    return _control(floors, floor).reading(dt_util.utcnow())


def _attributes(floors: Floors, floor: str) -> dict[str, Any]:
    entity_id = floors.entity_id("sensor", f"{floor}_control_state")
    return dict(floors.hass.states.get(entity_id).attributes)


async def _power(floors: Floors, watts: str, *, after: float = 11) -> None:
    """Show floor 1's power, and let it last."""
    floors.hass.states.async_set("sensor.floor_1_power", watts)
    await floors.tick(after, step=1)


def _power_follows_the_switch(floors: Floors) -> None:
    """Floor 1's heater draws 1500 W while its switch is on, 0 W while off."""

    @callback
    def follow(event: Event) -> None:
        if event.data["entity_id"] == "switch.floor_1" and event.data["new_state"]:
            on = event.data["new_state"].state == STATE_ON
            floors.hass.states.async_set("sensor.floor_1_power", "1500" if on else "0")

    floors.hass.bus.async_listen("state_changed", follow)


async def _running(floors: Floors) -> None:
    """Floor 1 universal, read by its power: running at 1500 W."""
    floors.hass.states.async_set("sensor.floor_1_power", "0")
    _power_follows_the_switch(floors)
    await floors.setup(capacity=3, universal=UNIVERSAL, power=POWER)
    assert ("turn_on", "floor_1") in floors.calls
    await _power(floors, "1500")
    assert _reading(floors, "floor_1") is Reading.ON


async def test_reading_follows_power_not_the_switch(floors: Floors) -> None:
    """Its switch shows on at once, but it runs only once the power shows it."""
    floors.hass.states.async_set("sensor.floor_1_power", "0")
    await floors.setup(capacity=3, universal=UNIVERSAL, power=POWER)
    assert floors.hass.states.get("switch.floor_1").state == STATE_ON
    assert _reading(floors, "floor_1") is Reading.STARTING
    await _power(floors, "1500", after=5)
    assert _reading(floors, "floor_1") is Reading.STARTING
    await _power(floors, "1500", after=6)
    assert _reading(floors, "floor_1") is Reading.ON


async def test_power_dip_shorter_than_the_delay_changes_nothing(
    floors: Floors,
) -> None:
    """Running, 0 W for 5 s: no command, still running."""
    await _running(floors)
    calls = list(floors.calls)
    await _power(floors, "0", after=5)
    await _power(floors, "1500", after=MINUTE)
    assert floors.calls == calls
    assert _reading(floors, "floor_1") is Reading.ON


async def test_no_power_data_is_switch_unavailable(floors: Floors) -> None:
    """No power data past both tolerances: "Switch unavailable", out of the rotation."""
    await _running(floors)
    await _power(floors, STATE_UNAVAILABLE, after=3 * MINUTE)
    assert floors.state("floor_1") == "switch_unavailable"
    assert _reading(floors, "floor_1") is None
    await _power(floors, "1500", after=3 * MINUTE)
    assert floors.state("floor_1") == "normal"
    await floors.tick(5 * MINUTE)
    assert "floor_1" in floors.on()


async def test_off_by_another_read_from_power(floors: Floors) -> None:
    """Its power gone for good, the switch still on: off by another, no attempt."""
    await _running(floors)
    calls = list(floors.calls)
    await _power(floors, "0", after=3 * MINUTE)
    assert _reading(floors, "floor_1") is Reading.OFF
    assert floors.calls == calls
    attributes = _attributes(floors, "floor_1")
    assert attributes["failed_attempts"] == 0
    assert attributes["pause_until"] is not None
    assert floors.state("floor_1") == "normal"
