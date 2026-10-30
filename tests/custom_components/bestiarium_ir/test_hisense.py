"""A Hisense air conditioner as a climate device on any infrared emitter (layer 2)."""

from __future__ import annotations

import asyncio
from itertools import pairwise
from typing import TYPE_CHECKING

import pytest
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    HVACMode,
)
from homeassistant.config_entries import ConfigSubentry, ConfigSubentryData
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.bestiarium_ir.const import (
    CONF_EMITTER,
    CONF_ZHA_DEVICE,
    SUBENTRY_TYPE_BLASTER,
    SUBENTRY_TYPE_HISENSE,
)
from custom_components.bestiarium_ir.hisense import ACTIONS, Change
from custom_components.bestiarium_ir.kelon168 import (
    Kelon168Action,
    Kelon168Fan,
    Kelon168Mode,
)

from .conftest import (
    Clock,
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
                State("climate.stasi_ac", STATE_UNAVAILABLE),
                {
                    "hvac_mode": HVACMode.OFF,
                    "temperature": 22,
                    "fan_mode": "low",
                    "last_active_hvac_mode": HVACMode.COOL,
                },
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


async def test_frames_are_three_seconds_apart(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter, clock: Clock
) -> None:
    """Changes at once: their frames go one by one, at least 3 s apart."""
    _, climate = ac
    await _heat_24(hass, climate)
    await asyncio.gather(
        call(hass, "climate", "set_temperature", climate, temperature=25),
        call(hass, "climate", "set_fan_mode", climate, fan_mode="high"),
    )
    assert len(emitter.times) == 3
    assert all(b - a >= 3 for a, b in pairwise(emitter.times))
    # The first frame went at once; each of the next waited out the pause
    assert clock.sleeps == [3, 3]


async def test_last_change_is_the_last_frame(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """
    Targets one after another: the last one goes, none twice.

    A queued frame is built in its turn from the latest target; one already
    carried by a frame sent meanwhile is not sent again (DEV-012 review, U2).
    """
    _, climate = ac
    await _heat_24(hass, climate)
    await asyncio.gather(
        *(
            call(hass, "climate", "set_temperature", climate, temperature=t)
            for t in (25, 26, 27)
        )
    )
    assert [f.temperature for f in emitter.frames[1:]] == [25, 27]
    assert hass.states.get(climate).attributes[ATTR_TEMPERATURE] == 27


async def test_failed_send_keeps_the_assumed_state(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """The emitter fails: the action fails, the assumed state stays."""
    _, climate = ac
    await _heat_24(hass, climate)
    emitter.fail = True
    with pytest.raises(HomeAssistantError):
        await call(hass, "climate", "set_hvac_mode", climate, hvac_mode=HVACMode.COOL)
    assert hass.states.get(climate).state == HVACMode.HEAT


async def test_a_mode_right_after_a_target_keeps_both(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """A mode queued behind a target: its frame carries the new target too."""
    _, climate = ac
    await call(
        hass,
        "climate",
        "set_temperature",
        climate,
        temperature=22,
        hvac_mode=HVACMode.HEAT,
    )
    await asyncio.gather(
        call(hass, "climate", "set_temperature", climate, temperature=25),
        call(hass, "climate", "set_hvac_mode", climate, hvac_mode=HVACMode.COOL),
    )
    assert _state(emitter.frames[-1]) == (True, Kelon168Mode.COOL, 25, Kelon168Fan.AUTO)
    state = hass.states.get(climate)
    assert (state.state, state.attributes[ATTR_TEMPERATURE]) == (HVACMode.COOL, 25)


async def test_a_speed_right_after_a_target_keeps_both(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """A speed queued behind a target: the last frame has the target and the speed."""
    _, climate = ac
    await call(
        hass,
        "climate",
        "set_temperature",
        climate,
        temperature=22,
        hvac_mode=HVACMode.HEAT,
    )
    await asyncio.gather(
        call(hass, "climate", "set_temperature", climate, temperature=25),
        call(hass, "climate", "set_fan_mode", climate, fan_mode="high"),
    )
    assert _state(emitter.frames[-1]) == (True, Kelon168Mode.HEAT, 25, Kelon168Fan.HIGH)
    state = hass.states.get(climate)
    assert (state.attributes[ATTR_TEMPERATURE], state.attributes[ATTR_FAN_MODE]) == (
        25,
        "high",
    )


async def test_a_reload_keeps_the_assumed_state_on_its_own_blaster(
    hass: HomeAssistant,
    zha_device: object,
    zha_calls: object,  # noqa: ARG001
) -> None:
    """
    A Hisense on the entry's own blaster: a reload keeps its state and display.

    The emitter is unloaded first and the air conditioner goes unavailable
    before it is removed; a subentry added later reloads the entry.
    """
    entry = ir_entry(
        ConfigSubentryData(
            data={CONF_ZHA_DEVICE: zha_device.id},
            subentry_type=SUBENTRY_TYPE_BLASTER,
            title="IR black",
            unique_id=zha_device.id,
        )
    )
    await set_up(hass, entry)
    emitter_id = next(
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.domain == "infrared"
    )
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data={CONF_EMITTER: emitter_id},
            subentry_type=SUBENTRY_TYPE_HISENSE,
            title="Stasi AC",
            unique_id=None,
        ),
    )
    await hass.async_block_till_done()
    await call(
        hass,
        "climate",
        "set_temperature",
        "climate.stasi_ac",
        temperature=24,
        hvac_mode=HVACMode.HEAT,
    )
    await call(hass, "climate", "set_fan_mode", "climate.stasi_ac", fan_mode="low")

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("climate.stasi_ac")
    assert (
        state.state,
        state.attributes[ATTR_TEMPERATURE],
        state.attributes[ATTR_FAN_MODE],
    ) == (
        HVACMode.HEAT,
        24,
        "low",
    )
    assert hass.states.get("switch.stasi_ac_display").state == "on"


def _button_fits(frame: object) -> bool:
    """Return whether the power button goes with "off" and only with it."""
    return (frame.action is Kelon168Action.POWER) is (not frame.power)


async def test_off_then_heat_within_the_pause(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """Off queued, heat right after: no power button on a running unit."""
    _, climate = ac
    await _heat_24(hass, climate)
    await asyncio.gather(
        call(hass, "climate", "set_temperature", climate, temperature=25),
        call(hass, "climate", "turn_off", climate),
        call(hass, "climate", "set_hvac_mode", climate, hvac_mode=HVACMode.HEAT),
    )
    assert all(_button_fits(f) for f in emitter.frames)
    assert _state(emitter.frames[-1]) == (True, Kelon168Mode.HEAT, 25, Kelon168Fan.AUTO)
    assert hass.states.get(climate).state == HVACMode.HEAT


async def test_heat_then_off_within_the_pause(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """A mode queued, off right after: no state button turns the unit off."""
    _, climate = ac
    await _heat_24(hass, climate)
    await asyncio.gather(
        call(hass, "climate", "set_temperature", climate, temperature=25),
        call(hass, "climate", "set_hvac_mode", climate, hvac_mode=HVACMode.COOL),
        call(hass, "climate", "turn_off", climate),
    )
    assert all(_button_fits(f) for f in emitter.frames)
    assert emitter.frames[-1].power is False
    assert hass.states.get(climate).state == HVACMode.OFF


async def test_target_with_the_same_mode_is_the_temperature_button(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """A target sent with the mode the unit is in: the button checked at home."""
    _, climate = ac
    await _heat_24(hass, climate)
    await call(
        hass,
        "climate",
        "set_temperature",
        climate,
        temperature=26,
        hvac_mode=HVACMode.HEAT,
    )
    assert emitter.frames[-1].action is ACTIONS[Change.TEMPERATURE]
    assert emitter.frames[-1].temperature == 26
