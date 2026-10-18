"""Tests for the Bestiarium Climate config flow."""

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

from custom_components.bestiarium_climate.const import (
    CONF_CAPACITY,
    CONF_MIN_WORKING_TIME,
    CONF_RECOVERY_TIMEOUT,
    DEFAULT_CAPACITY,
    DOMAIN,
    SUBENTRY_TYPE_SWITCH,
    SUBENTRY_TYPE_THERMOSTAT,
    ClimateMode,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_config_flow_create_entry(hass: HomeAssistant) -> None:
    """Test creating the main config entry via the flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CAPACITY: 3,
            CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 25, "seconds": 0},
            CONF_RECOVERY_TIMEOUT: {"hours": 2, "minutes": 0, "seconds": 0},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bestiarium Climate"
    assert result["data"][CONF_CAPACITY] == 3
    assert result["data"][CONF_MIN_WORKING_TIME] == {
        "hours": 0,
        "minutes": 25,
        "seconds": 0,
    }
    assert result["data"][CONF_RECOVERY_TIMEOUT] == {
        "hours": 2,
        "minutes": 0,
        "seconds": 0,
    }

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data[CONF_CAPACITY] == 3


async def test_config_flow_create_entry_defaults(hass: HomeAssistant) -> None:
    """Test create entry uses defaults when omitted."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CAPACITY: DEFAULT_CAPACITY,
            CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 20, "seconds": 0},
            CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CAPACITY] == DEFAULT_CAPACITY


async def test_config_flow_reconfigure(hass: HomeAssistant) -> None:
    """Test reconfigure flow updates the main entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CAPACITY: 1,
            CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 20, "seconds": 0},
            CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CAPACITY: 5,
            CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 30, "seconds": 0},
            CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 30, "seconds": 0},
        },
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    updated = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated is not None
    assert updated.data[CONF_CAPACITY] == 5
    assert updated.data[CONF_MIN_WORKING_TIME]["minutes"] == 30
    assert updated.data[CONF_RECOVERY_TIMEOUT]["minutes"] == 30

    # Wait for the reload to finish, then unload so the BalancingCoordinator
    # timer is stopped (no lingering timer at teardown)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    await hass.config_entries.async_unload(entry.entry_id)


async def test_switch_subentry_flow_create(
    hass: HomeAssistant,
    climate_entry: MockConfigEntry,
) -> None:
    """Test creating a switch subentry (heating mode, heating_action required)."""
    hass.states.async_set(
        "sensor.power_test",
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    result = await hass.config_entries.subentries.async_init(
        (climate_entry.entry_id, SUBENTRY_TYPE_SWITCH),
        context={"source": SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Heating Switch",
            "mode": ClimateMode.HEATING,
            "power_entity": "sensor.power_test",
            "min_power": 40.0,
            "heating_action": {
                "action": "switch.turn_on",
                "target": {"entity_id": "switch.heat"},
            },
            "off_action": {
                "action": "switch.turn_off",
                "target": {"entity_id": "switch.heat"},
            },
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Heating Switch"

    entry = hass.config_entries.async_get_entry(climate_entry.entry_id)
    assert entry is not None
    assert hasattr(entry, "subentries") and len(entry.subentries) == 1
    sub = next(iter(entry.subentries.values()))
    assert sub.subentry_type == SUBENTRY_TYPE_SWITCH
    assert sub.title == "Heating Switch"
    assert sub.data.get("mode") == ClimateMode.HEATING
    assert "heating_action" in sub.data
    assert sub.data.get("off_action") is not None


async def test_switch_subentry_flow_heating_requires_heating_action(
    hass: HomeAssistant, climate_entry: MockConfigEntry
) -> None:
    """Test switch subentry in heating mode requires heating_action."""
    hass.states.async_set(
        "sensor.power_test",
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    result = await hass.config_entries.subentries.async_init(
        (climate_entry.entry_id, SUBENTRY_TYPE_SWITCH),
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Heating Switch",
            "mode": ClimateMode.HEATING,
            "power_entity": "sensor.power_test",
            "min_power": 40.0,
            # heating_action missing
            "off_action": {
                "action": "switch.turn_off",
                "target": {"entity_id": "switch.heat"},
            },
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"].get("heating_action") == "required"


async def test_switch_subentry_flow_cooling_requires_cooling_action(
    hass: HomeAssistant, climate_entry: MockConfigEntry
) -> None:
    """Test switch subentry in cooling mode requires cooling_action."""
    hass.states.async_set(
        "sensor.power_test",
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    result = await hass.config_entries.subentries.async_init(
        (climate_entry.entry_id, SUBENTRY_TYPE_SWITCH),
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Cooling Switch",
            "mode": ClimateMode.COOLING,
            "power_entity": "sensor.power_test",
            "min_power": 50.0,
            # cooling_action missing
            "off_action": {
                "action": "switch.turn_off",
                "target": {"entity_id": "switch.cool"},
            },
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"].get("cooling_action") == "required"


async def test_thermostat_subentry_flow_two_steps(
    hass: HomeAssistant,
    climate_entry: MockConfigEntry,
) -> None:
    """Test creating a thermostat subentry (two-step: basic then presets)."""
    hass.states.async_set(
        "sensor.temp_room",
        "20",
        {"device_class": "temperature", "unit_of_measurement": "°C"},
    )
    hass.states.async_set("switch.room_heat", "off")

    result = await hass.config_entries.subentries.async_init(
        (climate_entry.entry_id, SUBENTRY_TYPE_THERMOSTAT),
        context={"source": SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "basic"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Room Thermostat",
            "temperature_sensor": "sensor.temp_room",
            "target_switch": "switch.room_heat",
            "thermostat_mode": ClimateMode.HEATING,
            "cold_tolerance": 0.3,
            "hot_tolerance": 0.3,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "presets"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Room Thermostat"

    entry = hass.config_entries.async_get_entry(climate_entry.entry_id)
    assert entry is not None
    assert hasattr(entry, "subentries") and len(entry.subentries) == 1
    sub = next(iter(entry.subentries.values()))
    assert sub.subentry_type == SUBENTRY_TYPE_THERMOSTAT
    assert sub.data.get("temperature_sensor") == "sensor.temp_room"
    assert sub.data.get("target_switch") == "switch.room_heat"
