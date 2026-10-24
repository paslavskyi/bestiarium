"""Tests for the Bestiarium Mode select entity."""

# ruff: noqa: S101

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_mode.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_select_entity_state_and_attributes(hass: HomeAssistant) -> None:
    """Test select entity state, options, and attributes."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "options": [
                {
                    "name": "Eco",
                    "description": "Energy saving",
                    "icon": "mdi:leaf",
                },
                {
                    "name": "Boost",
                    "description": "High priority",
                    "icon": "mdi:flash",
                },
            ]
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id("select", DOMAIN, "select")
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "Eco"
    assert state.attributes["options"] == ["Eco", "Boost"]
    assert state.attributes["icon"] == "mdi:leaf"
    # The form's metadata is not repeated in the state
    assert not {"options_metadata", "description"} & state.attributes.keys()
