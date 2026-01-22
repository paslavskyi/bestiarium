"""Tests for the Bestiarium Mode config flow."""

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_mode.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_config_flow_create_entry(hass) -> None:
    """Test creating a config entry via the flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    assert result["type"] == FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "add"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "add"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Eco",
            "description": "Energy saving",
            "icon": "mdi:leaf",
        },
    )
    assert result["type"] == FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "complete"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data["options"] == [
        {"name": "Eco", "description": "Energy saving", "icon": "mdi:leaf"}
    ]


async def test_config_flow_reconfigure_updates_entry(hass) -> None:
    """Test reconfigure flow updates existing entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="bestiarium_mode",
        data={
            "options": [
                {"name": "Eco", "description": "Energy saving", "icon": "mdi:leaf"}
            ]
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "add"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "add"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Boost",
            "description": "High priority",
            "icon": "mdi:flash",
        },
    )
    assert result["type"] == FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "complete"},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry is not None
    assert updated_entry.data["options"] == [
        {"name": "Eco", "description": "Energy saving", "icon": "mdi:leaf"},
        {"name": "Boost", "description": "High priority", "icon": "mdi:flash"},
    ]
