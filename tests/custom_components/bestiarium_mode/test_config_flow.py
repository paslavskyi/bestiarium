"""Tests for the Bestiarium Mode config flow."""

# ruff: noqa: S101

import pytest
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_mode.const import (
    CONF_AC_LOAD_LIMIT_W,
    CONF_AC_LOAD_LIMITS,
    CONF_AC_OVERLOAD_RECOVERY_SECONDS,
    CONF_AC_POWER_SENSOR,
    CONF_AC_POWER_TOLERANCE_SECONDS,
    CONF_AC_RED_ZONE_PERCENT,
    CONF_AC_YELLOW_ZONE_PERCENT,
    CONF_AC_ZONE_HYSTERESIS_PERCENT,
    DEFAULT_OVERLOAD_RECOVERY_SECONDS,
    DEFAULT_POWER_TOLERANCE_SECONDS,
    DEFAULT_ZONE_HYSTERESIS_PERCENT,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_config_flow_create_entry(hass: HomeAssistant) -> None:
    """Test creating a config entry via the flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "add"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "add"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Eco"},
    )
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "complete"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_ac_load_limits"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_AC_POWER_SENSOR: "sensor.ac_power",
            CONF_AC_LOAD_LIMIT_W: 3500,
            CONF_AC_YELLOW_ZONE_PERCENT: 75,
            CONF_AC_RED_ZONE_PERCENT: 90,
        },
    )
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "complete"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data["options"] == [{"name": "Eco"}]
    assert entries[0].data[CONF_AC_LOAD_LIMITS] == {
        CONF_AC_POWER_SENSOR: "sensor.ac_power",
        CONF_AC_LOAD_LIMIT_W: 3500,
        CONF_AC_YELLOW_ZONE_PERCENT: 75,
        CONF_AC_RED_ZONE_PERCENT: 90,
        CONF_AC_ZONE_HYSTERESIS_PERCENT: DEFAULT_ZONE_HYSTERESIS_PERCENT,
        CONF_AC_POWER_TOLERANCE_SECONDS: DEFAULT_POWER_TOLERANCE_SECONDS,
        CONF_AC_OVERLOAD_RECOVERY_SECONDS: DEFAULT_OVERLOAD_RECOVERY_SECONDS,
    }


async def test_config_flow_ac_load_limits_validation(
    hass: HomeAssistant,
) -> None:
    """Test AC load limits validation rules."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    assert result["type"] == FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "configure_ac_load_limits"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_ac_load_limits"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_AC_POWER_SENSOR: "sensor.ac_power",
            CONF_AC_LOAD_LIMIT_W: 2500,
            CONF_AC_YELLOW_ZONE_PERCENT: 40,
            CONF_AC_RED_ZONE_PERCENT: 40,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_AC_YELLOW_ZONE_PERCENT] == "yellow_zone_min"
    assert result["errors"][CONF_AC_RED_ZONE_PERCENT] == "red_zone_must_exceed_yellow"


async def test_config_flow_reconfigure_updates_entry(hass: HomeAssistant) -> None:
    """Test reconfigure flow updates existing entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="bestiarium_mode",
        data={
            "options": [{"name": "Eco"}],
            CONF_AC_LOAD_LIMITS: {
                CONF_AC_POWER_SENSOR: "sensor.ac_power",
                CONF_AC_LOAD_LIMIT_W: 3000,
                CONF_AC_YELLOW_ZONE_PERCENT: 70,
                CONF_AC_RED_ZONE_PERCENT: 85,
            },
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

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
        {"name": "Boost"},
    )
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "configure_ac_load_limits"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_ac_load_limits"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_AC_POWER_SENSOR: "sensor.ac_power",
            CONF_AC_LOAD_LIMIT_W: 3200,
            CONF_AC_YELLOW_ZONE_PERCENT: 75,
            CONF_AC_RED_ZONE_PERCENT: 90,
        },
    )
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "complete"},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry is not None
    assert updated_entry.data["options"] == [{"name": "Eco"}, {"name": "Boost"}]
    assert updated_entry.data[CONF_AC_LOAD_LIMITS] == {
        CONF_AC_POWER_SENSOR: "sensor.ac_power",
        CONF_AC_LOAD_LIMIT_W: 3200,
        CONF_AC_YELLOW_ZONE_PERCENT: 75,
        CONF_AC_RED_ZONE_PERCENT: 90,
        CONF_AC_ZONE_HYSTERESIS_PERCENT: DEFAULT_ZONE_HYSTERESIS_PERCENT,
        CONF_AC_POWER_TOLERANCE_SECONDS: DEFAULT_POWER_TOLERANCE_SECONDS,
        CONF_AC_OVERLOAD_RECOVERY_SECONDS: DEFAULT_OVERLOAD_RECOVERY_SECONDS,
    }


async def test_reconfigure_message_from_integration_translations(
    hass: HomeAssistant,
) -> None:
    """Reconfigure success text comes from this integration (it has uk text)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="bestiarium_mode",
        data={"options": [{"name": "Eco"}]},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "configure_ac_load_limits"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_AC_POWER_SENSOR: "sensor.ac_power",
            CONF_AC_LOAD_LIMIT_W: 3000,
            CONF_AC_YELLOW_ZONE_PERCENT: 70,
            CONF_AC_RED_ZONE_PERCENT: 85,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "complete"}
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert result.get("translation_domain") is None
