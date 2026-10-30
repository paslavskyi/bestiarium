"""A Hisense air conditioner as a climate device on any infrared emitter (layer 2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.bestiarium_ir.hisense import ACTIONS, Change
from custom_components.bestiarium_ir.kelon168 import (
    Kelon168Action,
    Kelon168Fan,
    Kelon168Mode,
)

from .conftest import (
    RecordingEmitter,
    call,
    hisense_subentry,
    ir_entry,
    set_up,
    subentry_entity,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

AC = tuple["MockConfigEntry", str]


def _state(frame: object) -> tuple:
    """Return the state a frame carries, without its button."""
    return (frame.power, frame.mode, frame.temperature, frame.fan)


async def _heat_24(hass: HomeAssistant, climate: str) -> None:
    await call(
        hass,
        "climate",
        "set_temperature",
        climate,
        temperature=24,
        hvac_mode=HVACMode.HEAT,
    )


@pytest.mark.parametrize(
    ("change", "button"),
    [
        (Change.TURN_ON, Kelon168Action.TEMP),
        (Change.MODE, Kelon168Action.MODE),
        (Change.TEMPERATURE, Kelon168Action.TEMP),
        (Change.FAN, Kelon168Action.FAN),
        (Change.OWN_AUTO, Kelon168Action.IFEEL),
        (Change.OFF, Kelon168Action.POWER),
        (Change.DISPLAY, Kelon168Action.LIGHT),
    ],
)
def test_the_button_of_each_change(change: Change, button: Kelon168Action) -> None:
    """The one table of buttons: only TEMP was checked at home (DEV-012 review, U1)."""
    assert ACTIONS[change] is button


async def test_heat_from_off_is_one_frame(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """Off -> heat 24 °C: one frame, on, heat, 24, fan auto."""
    _, climate = ac
    await _heat_24(hass, climate)
    assert [_state(f) for f in emitter.frames] == [
        (True, Kelon168Mode.HEAT, 24, Kelon168Fan.AUTO)
    ]
    assert emitter.frames[0].action is ACTIONS[Change.TURN_ON]
    state = hass.states.get(climate)
    assert (state.state, state.attributes[ATTR_TEMPERATURE]) == (HVACMode.HEAT, 24)


@pytest.mark.parametrize(
    ("hvac", "mode"),
    [
        (HVACMode.COOL, Kelon168Mode.COOL),
        (HVACMode.DRY, Kelon168Mode.DRY),
        (HVACMode.FAN_ONLY, Kelon168Mode.FAN),
    ],
)
async def test_mode_change_while_on(
    hass: HomeAssistant,
    ac: AC,
    emitter: RecordingEmitter,
    hvac: HVACMode,
    mode: Kelon168Mode,
) -> None:
    """A mode of a running unit: one frame with the mode and the target."""
    _, climate = ac
    await _heat_24(hass, climate)
    await call(hass, "climate", "set_hvac_mode", climate, hvac_mode=hvac)
    assert len(emitter.frames) == 2
    assert _state(emitter.frames[-1]) == (True, mode, 24, Kelon168Fan.AUTO)
    assert emitter.frames[-1].action is ACTIONS[Change.MODE]


async def test_temperature_while_on(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """A new target of a running unit: one frame."""
    _, climate = ac
    await _heat_24(hass, climate)
    await call(hass, "climate", "set_temperature", climate, temperature=26)
    assert _state(emitter.frames[-1]) == (True, Kelon168Mode.HEAT, 26, Kelon168Fan.AUTO)
    assert emitter.frames[-1].action is ACTIONS[Change.TEMPERATURE]


async def test_fan_speed_while_on(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """A fan speed of a running unit: one frame; every speed of the protocol."""
    _, climate = ac
    assert hass.states.get(climate).attributes["fan_modes"] == [
        "auto",
        "min",
        "low",
        "medium",
        "high",
        "max",
    ]
    await _heat_24(hass, climate)
    await call(hass, "climate", "set_fan_mode", climate, fan_mode="high")
    assert _state(emitter.frames[-1]) == (True, Kelon168Mode.HEAT, 24, Kelon168Fan.HIGH)
    assert emitter.frames[-1].action is ACTIONS[Change.FAN]
    assert hass.states.get(climate).attributes[ATTR_FAN_MODE] == "high"


async def test_auto_is_the_units_own(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """The unit's auto: 23 °C, fan auto; the target is kept for the other modes."""
    _, climate = ac
    await _heat_24(hass, climate)
    await call(hass, "climate", "set_hvac_mode", climate, hvac_mode=HVACMode.AUTO)
    assert _state(emitter.frames[-1]) == (True, Kelon168Mode.AUTO, 23, Kelon168Fan.AUTO)
    assert emitter.frames[-1].action is ACTIONS[Change.OWN_AUTO]
    assert hass.states.get(climate).attributes[ATTR_TEMPERATURE] == 24
    # Target and fan in auto: kept, nothing sent
    await call(hass, "climate", "set_temperature", climate, temperature=25)
    await call(hass, "climate", "set_fan_mode", climate, fan_mode="low")
    assert len(emitter.frames) == 2
    await call(hass, "climate", "set_hvac_mode", climate, hvac_mode=HVACMode.HEAT)
    assert _state(emitter.frames[-1]) == (True, Kelon168Mode.HEAT, 25, Kelon168Fan.LOW)


async def test_off_is_one_power_frame(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """Off: one frame, off, with the last mode and target."""
    _, climate = ac
    await _heat_24(hass, climate)
    await call(hass, "climate", "turn_off", climate)
    assert _state(emitter.frames[-1]) == (
        False,
        Kelon168Mode.HEAT,
        24,
        Kelon168Fan.AUTO,
    )
    assert emitter.frames[-1].action is ACTIONS[Change.OFF]
    assert hass.states.get(climate).state == HVACMode.OFF


async def test_temperature_while_off_sends_nothing(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """A target of a unit that is off is kept for the next start."""
    _, climate = ac
    await call(hass, "climate", "set_temperature", climate, temperature=27)
    assert emitter.frames == []
    await call(hass, "climate", "set_hvac_mode", climate, hvac_mode=HVACMode.COOL)
    assert _state(emitter.frames[-1]) == (True, Kelon168Mode.COOL, 27, Kelon168Fan.AUTO)


async def test_assumed_state_is_restored(
    hass: HomeAssistant, emitter: RecordingEmitter
) -> None:
    """After a restart: the mode, target, fan, and the last mode for "on"."""
    entry = ir_entry(hisense_subentry())
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(
                    "climate.stasi_ac",
                    HVACMode.OFF,
                    {ATTR_TEMPERATURE: 22, ATTR_FAN_MODE: "low"},
                ),
                {"last_active_hvac_mode": HVACMode.COOL},
            )
        ],
    )
    await set_up(hass, entry)
    climate = subentry_entity(hass, entry, "climate")
    assert climate == "climate.stasi_ac"
    await call(hass, "climate", "turn_on", climate)
    assert _state(emitter.frames[-1]) == (True, Kelon168Mode.COOL, 22, Kelon168Fan.LOW)
    assert hass.states.get(climate).state == HVACMode.COOL


async def test_unavailable_with_its_emitter(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """The emitter unavailable: the air conditioner too."""
    _, climate = ac
    emitter._attr_available = False
    emitter.async_write_ha_state()
    await hass.async_block_till_done()
    assert hass.states.get(climate).state == STATE_UNAVAILABLE


async def test_modes_of_the_device(hass: HomeAssistant, ac: AC) -> None:
    """Off, heat, cool, dry, fan only and the unit's auto; 16-30 °C by 1."""
    _, climate = ac
    attributes = hass.states.get(climate).attributes
    assert attributes["hvac_modes"] == [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.AUTO,
    ]
    assert (
        attributes["min_temp"],
        attributes["max_temp"],
        attributes["target_temp_step"],
    ) == (16, 30, 1)
