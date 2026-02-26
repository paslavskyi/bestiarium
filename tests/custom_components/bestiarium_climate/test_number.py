"""Tests for the Bestiarium Climate number platform (CapacityNumber)."""

# ruff: noqa: S101

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_climate.const import (
    CONF_CAPACITY,
    CONF_MIN_WORKING_TIME,
    CONF_RECOVERY_TIMEOUT,
    DEFAULT_CAPACITY,
    DOMAIN,
)
from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_capacity_number_creation(
    hass: HomeAssistant,
    climate_entry,
) -> None:
    """Test CapacityNumber is created from config entry (hass, entry)."""
    await hass.config_entries.async_setup(climate_entry.entry_id)
    await hass.async_block_till_done()

    reg = hass.states.async_all("number")
    assert len(reg) == 1
    state = reg[0]
    assert state.attributes.get("min") == 0
    assert state.attributes.get("max") == 100
    assert float(state.state) == climate_entry.data.get(CONF_CAPACITY, DEFAULT_CAPACITY)


async def test_capacity_number_set_value(
    hass: HomeAssistant,
    climate_entry,
) -> None:
    """Test async_set_native_value updates value and writes state."""
    await hass.config_entries.async_setup(climate_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.async_all("number")[0]
    entity_id = state.entity_id
    assert float(state.state) == 2  # from fixture

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id, "value": 5},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert float(state.state) == 5


async def test_capacity_number_restore_fallback_to_config(hass: HomeAssistant) -> None:
    """Test when no restored state, value comes from config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CAPACITY: 7,
            CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 20, "seconds": 0},
            CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
        },
    )
    entry.add_to_hass(hass)
    if not hasattr(entry, "subentries"):
        entry.subentries = {}

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.async_all("number")[0]
    assert float(state.state) == 7

    await hass.config_entries.async_unload(entry.entry_id)
