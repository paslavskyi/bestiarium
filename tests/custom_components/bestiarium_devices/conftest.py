"""Fixtures for bestiarium_devices tests."""

import pytest
from homeassistant.core import HomeAssistant

from .mode_entities import register_mode_entities


@pytest.fixture
def mode_entities(hass: HomeAssistant) -> None:
    """Register bestiarium_mode entities as a production install has them."""
    register_mode_entities(hass)
