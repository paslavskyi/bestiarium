"""Fixtures for bestiarium_mock tests."""

from collections.abc import Callable
from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.bestiarium_mock.const import DOMAIN
from custom_components.bestiarium_mock.coordinator import MockDevicesCoordinator

SEED = 7


def mock_entry() -> MockConfigEntry:
    """Return a Bestiarium Mock entry of the current version."""
    return MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={}, version=2)


@pytest.fixture
async def stand(
    hass: HomeAssistant,
    enable_custom_integrations: None,  # noqa: ARG001
) -> MockDevicesCoordinator:
    """Set up the test stand with a seeded random generator."""
    entry = mock_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)  # noqa: S101
    await hass.async_block_till_done()
    coordinator: MockDevicesCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.rng.seed(SEED)
    for socket in coordinator.sockets.values():
        socket.rng.seed(SEED)
    return coordinator


@pytest.fixture
def entity_id(hass: HomeAssistant) -> Callable[[str, str], str]:
    """Return the entity_id of a mock entity by its domain and unique_id."""

    def find(domain: str, unique_id: str) -> str:
        found = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
        assert found is not None, unique_id  # noqa: S101
        return found

    return find


@pytest.fixture
def advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> Callable[[float], object]:
    """Move the clock forward and run what became due."""

    async def move(seconds: float) -> None:
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    return move
