"""Tests for the Bestiarium Mock Devices config flow."""

# ruff: noqa: S101

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_mock.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_config_flow_shows_form(hass: HomeAssistant) -> None:
    """Test that config flow shows confirmation form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["description_placeholders"]["device_count"] == "10"


async def test_config_flow_creates_entry(hass: HomeAssistant) -> None:
    """Test that config flow creates entry on confirmation."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM

    # Confirm creation (empty user_input triggers entry creation)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Mock Devices"
    assert result["data"] == {}

    # Verify entry was created
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].unique_id == DOMAIN


async def test_config_flow_already_configured(hass: HomeAssistant) -> None:
    """Test that config flow aborts if already configured."""
    # Create existing entry
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={},
    )
    entry.add_to_hass(hass)

    # Try to create another
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_config_flow_single_instance_only(hass: HomeAssistant) -> None:
    """Test that only one instance of mock devices can be configured."""
    # First instance should succeed
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    # Second instance should abort
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
