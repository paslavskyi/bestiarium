"""The display switch of a Hisense and the frame sent twice while it is off."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.bestiarium_ir.kelon168 import Kelon168Action

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


def _display(hass: HomeAssistant, ac: AC) -> str:
    entry, _ = ac
    return subentry_entity(hass, entry, "switch", "_display")


async def _heat_24(hass: HomeAssistant, climate: str) -> None:
    await call(
        hass,
        "climate",
        "set_temperature",
        climate,
        temperature=24,
        hvac_mode=HVACMode.HEAT,
    )


async def test_display_off_is_one_light_frame(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """The display of a running unit off: one display frame with its state."""
    _, climate = ac
    display = _display(hass, ac)
    assert hass.states.get(display).state == STATE_ON
    await _heat_24(hass, climate)
    await call(hass, "switch", "turn_off", display)
    frame = emitter.frames[-1]
    assert (frame.action, frame.power, frame.temperature) == (
        Kelon168Action.LIGHT,
        True,
        24,
    )
    assert len(emitter.frames) == 2
    assert hass.states.get(display).state == STATE_OFF


async def test_command_with_display_off_goes_twice(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter, clock: Clock
) -> None:
    """The first frame only lights the display: the same frame again, 3 s later."""
    _, climate = ac
    display = _display(hass, ac)
    await _heat_24(hass, climate)
    await call(hass, "switch", "turn_off", display)
    await call(hass, "climate", "set_temperature", climate, temperature=26)
    assert [(f.action, f.temperature) for f in emitter.frames[-2:]] == [
        (Kelon168Action.TEMP, 26)
    ] * 2
    assert emitter.times[-1] - emitter.times[-2] >= 3
    assert clock.sleeps
    assert hass.states.get(display).state == STATE_ON


async def test_off_with_display_off_goes_once(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """Turning off works with the display off (the home, 2026-10-28): once."""
    _, climate = ac
    display = _display(hass, ac)
    await _heat_24(hass, climate)
    await call(hass, "switch", "turn_off", display)
    sent = len(emitter.frames)
    await call(hass, "climate", "turn_off", climate)
    assert len(emitter.frames) == sent + 1
    assert emitter.frames[-1].power is False
    assert hass.states.get(display).state == STATE_OFF


async def test_display_off_unknown_to_ha_only_lights_it(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """
    The display turned off with the remote: HA believes it on.

    The frame goes once (the unit only lights its display); HA shows the new
    state, assumed (design 10); the next command goes through.
    """
    _, climate = ac
    await _heat_24(hass, climate)
    await call(hass, "climate", "set_temperature", climate, temperature=26)
    assert len(emitter.frames) == 2
    assert hass.states.get(climate).attributes["temperature"] == 26


async def test_display_of_a_unit_that_is_off_sends_nothing(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """The display of a unit that is off: assumed only (DEV-012 review, U3)."""
    display = _display(hass, ac)
    await call(hass, "switch", "turn_off", display)
    assert emitter.frames == []
    assert hass.states.get(display).state == STATE_OFF
    await call(hass, "switch", "turn_on", display)
    assert emitter.frames == []
    assert hass.states.get(display).state == STATE_ON


async def test_display_on_again_is_one_frame(
    hass: HomeAssistant, ac: AC, emitter: RecordingEmitter
) -> None:
    """The display on again: one display frame (it lights the display)."""
    _, climate = ac
    display = _display(hass, ac)
    await _heat_24(hass, climate)
    await call(hass, "switch", "turn_off", display)
    await call(hass, "switch", "turn_on", display)
    assert [f.action for f in emitter.frames[-2:]] == [Kelon168Action.LIGHT] * 2
    assert len(emitter.frames) == 3
    assert hass.states.get(display).state == STATE_ON


async def test_display_is_restored(
    hass: HomeAssistant, emitter: RecordingEmitter
) -> None:
    """The display off before a restart: the next state frame goes twice."""
    mock_restore_cache_with_extra_data(
        hass, [(State("switch.stasi_ac_display", STATE_OFF), {"display_on": False})]
    )
    entry = ir_entry(hisense_subentry())
    await set_up(hass, entry)
    display = subentry_entity(hass, entry, "switch", "_display")
    assert display == "switch.stasi_ac_display"
    assert hass.states.get(display).state == STATE_OFF
    climate = subentry_entity(hass, entry, "climate")
    await _heat_24(hass, climate)
    assert len(emitter.frames) == 2
