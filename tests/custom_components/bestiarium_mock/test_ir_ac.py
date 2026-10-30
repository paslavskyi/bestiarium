"""The stand's IR emitter and Hisense air conditioner."""

# ruff: noqa: S101, PLR2004, PLR0913

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from homeassistant.components.infrared import async_send_command
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, State
from infrared_protocols.commands.gree_ac import GreeAcCommand, GreeAcMode
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.bestiarium_ir.kelon168 import (
    Kelon168Action,
    Kelon168Command,
    Kelon168Fan,
    Kelon168Mode,
)
from custom_components.bestiarium_mock.const import IrFrameOutcome

if TYPE_CHECKING:
    from custom_components.bestiarium_mock.coordinator import MockDevicesCoordinator
    from custom_components.bestiarium_mock.ir_ac import MockIrAc

EntityId = Callable[[str, str], str]
Advance = Callable[[float], object]

EMITTER = "mock_hisense_emitter"


@pytest.fixture
async def ac(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,
    entity_id: EntityId,
    advance: Advance,
) -> MockIrAc:
    """Return the stand's air conditioner, its socket on."""
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": entity_id("switch", "mock_hisense_power")},
        blocking=True,
    )
    await advance(2)
    return stand.ir_acs["hisense"]


async def _send(hass: HomeAssistant, entity_id: EntityId, command: object) -> None:
    await async_send_command(hass, entity_id("infrared", EMITTER), command)
    await hass.async_block_till_done()


def _heat(temperature: int = 24, **kwargs: object) -> Kelon168Command:
    return Kelon168Command(mode=Kelon168Mode.HEAT, temperature=temperature, **kwargs)


def _power(stand: MockDevicesCoordinator) -> float:
    return stand.sockets["hisense_power"].power


@pytest.mark.parametrize(
    ("mode", "state", "watts"),
    [
        (Kelon168Mode.HEAT, "heat", 1200),
        (Kelon168Mode.COOL, "cool", 1200),
        (Kelon168Mode.AUTO, "auto", 1200),
        (Kelon168Mode.DRY, "dry", 300),
        (Kelon168Mode.FAN, "fan_only", 40),
    ],
)
async def test_each_mode_draws_its_power(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,
    ac: MockIrAc,  # noqa: ARG001
    entity_id: EntityId,
    mode: Kelon168Mode,
    state: str,
    watts: int,
) -> None:
    """A frame sets the unit; it draws the power of its mode."""
    await _send(hass, entity_id, Kelon168Command(mode=mode, temperature=24))
    assert _power(stand) == watts
    ac_state = hass.states.get(entity_id("sensor", "mock_hisense_ac_state"))
    assert ac_state.state == state
    assert ac_state.attributes["temperature"] == 24
    assert ac_state.attributes["fan"] == "auto"
    assert ac_state.attributes["display"] is True
    last = hass.states.get(entity_id("sensor", "mock_hisense_last_frame"))
    assert last.state == IrFrameOutcome.APPLIED
    assert last.attributes["action"] == "temp"


async def test_off_draws_standby(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,
    ac: MockIrAc,
    entity_id: EntityId,
) -> None:
    """Off: 5 W."""
    assert _power(stand) == 5
    await _send(hass, entity_id, _heat())
    await _send(hass, entity_id, _heat(power=False, action=Kelon168Action.POWER))
    assert (ac.power, _power(stand)) == (False, 5)
    assert hass.states.get(entity_id("sensor", "mock_hisense_ac_state")).state == "off"


async def test_display_off_first_frame_only_lights(
    hass: HomeAssistant, ac: MockIrAc, entity_id: EntityId
) -> None:
    """With the display off, a frame only lights it; the same again sets the unit."""
    await _send(hass, entity_id, _heat())
    await _send(hass, entity_id, _heat(action=Kelon168Action.LIGHT))
    assert ac.display_on is False
    await _send(hass, entity_id, _heat(26))
    assert (ac.display_on, ac.temperature, ac.last_outcome) == (
        True,
        24,
        IrFrameOutcome.DISPLAY_LIT,
    )
    await _send(hass, entity_id, _heat(26))
    assert (ac.temperature, ac.last_outcome) == (26, IrFrameOutcome.APPLIED)


async def test_off_with_display_off_is_applied(
    hass: HomeAssistant, ac: MockIrAc, entity_id: EntityId
) -> None:
    """Off works with the display off (the home, 2026-10-28)."""
    await _send(hass, entity_id, _heat())
    await _send(hass, entity_id, _heat(action=Kelon168Action.LIGHT))
    await _send(hass, entity_id, _heat(power=False, action=Kelon168Action.POWER))
    assert (ac.power, ac.display_on) == (False, False)


async def test_ir_lost_hears_nothing(
    hass: HomeAssistant, ac: MockIrAc, entity_id: EntityId
) -> None:
    """The knob "IR lost": no frame reaches the unit."""
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": entity_id("switch", "mock_hisense_ir_lost")},
        blocking=True,
    )
    await _send(hass, entity_id, _heat())
    assert (ac.power, ac.last_outcome) == (False, None)


async def test_ignores_off(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,
    ac: MockIrAc,
    entity_id: EntityId,
) -> None:
    """The knob "Ignores off": the unit runs on."""
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": entity_id("switch", "mock_hisense_ignores_off")},
        blocking=True,
    )
    await _send(hass, entity_id, _heat())
    await _send(hass, entity_id, _heat(power=False, action=Kelon168Action.POWER))
    assert (ac.power, ac.last_outcome, _power(stand)) == (
        True,
        IrFrameOutcome.OFF_IGNORED,
        1200,
    )


async def test_unknown_signal_changes_nothing(
    hass: HomeAssistant, ac: MockIrAc, entity_id: EntityId
) -> None:
    """Another protocol's frame: not recognised, nothing changes."""
    await _send(hass, entity_id, _heat())
    await _send(hass, entity_id, GreeAcCommand(mode=GreeAcMode.COOL, temperature=18))
    assert (ac.mode, ac.temperature, ac.last_outcome) == (
        Kelon168Mode.HEAT,
        24,
        IrFrameOutcome.NOT_RECOGNISED,
    )


async def test_socket_off_turns_the_ac_off(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,
    ac: MockIrAc,
    entity_id: EntityId,
    advance: Advance,
) -> None:
    """Its power cut: the unit is off and hears nothing until the power is back."""
    await _send(hass, entity_id, _heat())
    socket = entity_id("switch", "mock_hisense_power")
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": socket}, blocking=True
    )
    await advance(2)
    assert ac.power is False
    await _send(hass, entity_id, _heat())
    assert ac.power is False
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": socket}, blocking=True
    )
    await advance(2)
    assert (ac.power, _power(stand)) == (False, 5)


async def test_fan_speed_is_shown(
    hass: HomeAssistant, ac: MockIrAc, entity_id: EntityId
) -> None:
    """A fan speed frame: the unit's speed."""
    await _send(hass, entity_id, _heat(fan=Kelon168Fan.HIGH, action=Kelon168Action.FAN))
    assert ac.fan is Kelon168Fan.HIGH
    last = hass.states.get(entity_id("sensor", "mock_hisense_last_frame"))
    assert (last.attributes["action"], last.attributes["fan"]) == ("fan", "high")


async def test_state_and_knobs_are_restored(
    hass: HomeAssistant,
    enable_custom_integrations: None,  # noqa: ARG001
) -> None:
    """After a restart: the unit's state, its display and the knobs."""
    mock_restore_cache(
        hass,
        [
            State(
                "sensor.mock_hisense_air_conditioner_state",
                "cool",
                {"temperature": 22, "fan": "low", "display": False},
            ),
            State("switch.mock_hisense_ignores_off", STATE_ON),
            State("switch.mock_hisense_ir_lost", STATE_OFF),
            State("switch.mock_hisense_power_switch", STATE_ON),
        ],
    )
    entry = MockConfigEntry(
        domain="bestiarium_mock", unique_id="bestiarium_mock", data={}, version=2
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ac = hass.data["bestiarium_mock"][entry.entry_id].ir_acs["hisense"]
    assert (ac.power, ac.mode, ac.temperature, ac.fan, ac.display_on) == (
        True,
        Kelon168Mode.COOL,
        22,
        Kelon168Fan.LOW,
        False,
    )
    assert (ac.ignores_off, ac.ir_lost) == (True, False)


async def test_hisense_on_the_stand_emitter_end_to_end(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,
    ac: MockIrAc,
    entity_id: EntityId,
) -> None:
    """Bestiarium IR's Hisense on the stand's emitter: heat 24 runs the unit."""
    entry = MockConfigEntry(
        domain="bestiarium_ir",
        unique_id="bestiarium_ir",
        data={},
        subentries_data=(
            ConfigSubentryData(
                data={"emitter": entity_id("infrared", EMITTER)},
                subentry_type="hisense",
                title="Stand Hisense",
                unique_id=None,
            ),
        ),
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.stand_hisense", "temperature": 24, "hvac_mode": "heat"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert (ac.power, ac.mode, ac.temperature, _power(stand)) == (
        True,
        Kelon168Mode.HEAT,
        24,
        1200,
    )
