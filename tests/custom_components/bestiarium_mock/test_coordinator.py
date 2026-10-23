"""Tests for the Bestiarium Mock grid."""

# ruff: noqa: S101

from collections.abc import Callable

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from custom_components.bestiarium_mock.const import (
    GRID_ALARM_THRESHOLD_SECONDS,
    GRID_RANDOM_MAX_W,
    GRID_RANDOM_MIN_W,
    FaultProfile,
)
from custom_components.bestiarium_mock.coordinator import MockDevicesCoordinator

EntityId = Callable[[str, str], str]


def _grid(hass: HomeAssistant, entity_id: EntityId) -> float:
    return float(hass.states.get(entity_id("sensor", "mock_grid_power")).state)


async def _switch(hass: HomeAssistant, entity_id: EntityId, device: str) -> None:
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id("switch", f"mock_{device}")}
    )


async def test_grid_sums_the_loads(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,  # noqa: ARG001
    entity_id: EntityId,
    advance: Callable,
) -> None:
    """The grid is the loads plus 40-100 W of background."""
    await _switch(hass, entity_id, "boiler")
    await _switch(hass, entity_id, "fridge")
    await advance(1)

    assert _grid(hass, entity_id) >= 2100 + GRID_RANDOM_MIN_W
    assert _grid(hass, entity_id) <= 2100 + GRID_RANDOM_MAX_W


async def test_grid_sees_a_stuck_socket(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,
    entity_id: EntityId,
    advance: Callable,
) -> None:
    """A socket whose switch still reports off still loads the grid."""
    stand.sockets["boiler"].set_profile(FaultProfile.STUCK_STATE)
    await _switch(hass, entity_id, "boiler")
    await advance(3)

    assert hass.states.get(entity_id("switch", "mock_boiler")).state == STATE_OFF
    assert _grid(hass, entity_id) >= 2000 + GRID_RANDOM_MIN_W


@pytest.mark.parametrize(
    ("seconds", "alarm"),
    [
        (GRID_ALARM_THRESHOLD_SECONDS - 5, STATE_OFF),
        (GRID_ALARM_THRESHOLD_SECONDS + 5, STATE_ON),
    ],
)
async def test_alarm_after_sustained_overload(  # noqa: PLR0913
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,  # noqa: ARG001
    entity_id: EntityId,
    advance: Callable,
    seconds: int,
    alarm: str,
) -> None:
    """Over 5000 W the alarm goes on only after the threshold."""
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id("number", "mock_cooker_mock_power"), "value": 4200},
        blocking=True,
    )
    for device in ("cooker", "boiler"):
        await _switch(hass, entity_id, device)
    await advance(1)
    # The grid is recalculated every 5 s
    for _ in range(seconds // 5):
        await advance(5)

    state = hass.states.get(entity_id("binary_sensor", "mock_grid_alarm")).state
    assert state == alarm


async def test_alarm_resets_below_the_limit(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,  # noqa: ARG001
    entity_id: EntityId,
    advance: Callable,
) -> None:
    """When the load drops, the alarm goes off."""
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id("number", "mock_cooker_mock_power"), "value": 4200},
        blocking=True,
    )
    for device in ("cooker", "boiler"):
        await _switch(hass, entity_id, device)
    for _ in range(4):
        await advance(5)
    alarm = entity_id("binary_sensor", "mock_grid_alarm")
    assert hass.states.get(alarm).state == STATE_ON

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id("switch", "mock_boiler")}
    )
    await advance(1)

    assert hass.states.get(alarm).state == STATE_OFF
