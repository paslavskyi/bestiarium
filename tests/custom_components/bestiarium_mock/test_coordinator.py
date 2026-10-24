"""Tests for the Bestiarium Mock grid."""

# ruff: noqa: S101

from collections.abc import Callable

from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant

from custom_components.bestiarium_mock.const import (
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
