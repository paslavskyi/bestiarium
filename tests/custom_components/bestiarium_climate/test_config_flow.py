"""Tests for the Bestiarium Climate config flow."""

# ruff: noqa: S101

import json
from dataclasses import fields
from pathlib import Path

import pytest
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.bestiarium_climate as climate
from custom_components.bestiarium_climate.const import (
    ControlSettings,
    ControlState,
    CONF_CAPACITY,
    CONF_MIN_WORKING_TIME,
    CONF_RECOVERY_TIMEOUT,
    DEFAULT_CAPACITY,
    DEFAULT_POWER_DELAY_SECONDS,
    DEFAULT_POWER_HYSTERESIS_PERCENT,
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
            CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 30, "seconds": 0},
            CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 30, "seconds": 0},
        },
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    updated = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated is not None
    # The capacity is its entity's: the form keeps the initial value
    assert updated.data[CONF_CAPACITY] == 1
    assert updated.data[CONF_MIN_WORKING_TIME]["minutes"] == 30
    assert updated.data[CONF_RECOVERY_TIMEOUT]["minutes"] == 30

    # Wait for the reload to finish, then unload so the BalancingCoordinator
    # timer is stopped (no lingering timer at teardown)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    await hass.config_entries.async_unload(entry.entry_id)


async def test_switch_subentry_flow_create(
    hass: HomeAssistant, climate_entry: MockConfigEntry
) -> None:
    """A switch has no mode of its own; hysteresis and delay get defaults."""
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
        result["flow_id"], SWITCH_INPUT
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Heating Switch"

    sub = next(iter(climate_entry.subentries.values()))
    assert sub.subentry_type == SUBENTRY_TYPE_SWITCH
    assert "mode" not in sub.data
    assert sub.data["power_hysteresis_percent"] == DEFAULT_POWER_HYSTERESIS_PERCENT
    assert sub.data["power_delay_seconds"] == DEFAULT_POWER_DELAY_SECONDS
    assert "heating_action" in sub.data
    assert "cooling_action" not in sub.data


async def test_switch_needs_heating_or_cooling(
    hass: HomeAssistant, climate_entry: MockConfigEntry
) -> None:
    """Without a heating and a cooling action the form refuses."""
    result = await hass.config_entries.subentries.async_init(
        (climate_entry.entry_id, SUBENTRY_TYPE_SWITCH),
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {key: value for key, value in SWITCH_INPUT.items() if key != "heating_action"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "heating_or_cooling_required"}


async def test_switch_action_is_validated(
    hass: HomeAssistant, climate_entry: MockConfigEntry
) -> None:
    """An action HA cannot run as a script is refused."""
    result = await hass.config_entries.subentries.async_init(
        (climate_entry.entry_id, SUBENTRY_TYPE_SWITCH),
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**SWITCH_INPUT, "heating_action": [{"nonsense": 1}]}
    )
    assert result["errors"] == {"heating_action": "invalid_action"}


async def _add(
    hass: HomeAssistant, entry: MockConfigEntry, kind: str, *steps: dict
) -> dict:
    flow = hass.config_entries.subentries
    result = await flow.async_init(
        (entry.entry_id, kind), context={"source": SOURCE_USER}
    )
    for step in steps:
        result = await flow.async_configure(result["flow_id"], step)
    await hass.async_block_till_done()
    return result


async def _virtual_switch(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Add the switch of SWITCH_INPUT (heating only); return its entity id."""
    hass.states.async_set("sensor.power_test", "0")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await _add(hass, entry, SUBENTRY_TYPE_SWITCH, SWITCH_INPUT)
    return er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, next(iter(entry.subentries))
    )


async def test_thermostat_needs_the_action_of_its_mode(
    hass: HomeAssistant, climate_entry: MockConfigEntry
) -> None:
    """A cooling thermostat cannot use a virtual switch that only heats."""
    switch = await _virtual_switch(hass, climate_entry)

    result = await _add(
        hass,
        climate_entry,
        SUBENTRY_TYPE_THERMOSTAT,
        {
            **THERMOSTAT_INPUT,
            "target_switch": switch,
            "thermostat_mode": ClimateMode.COOLING,
        },
    )

    assert result["errors"] == {"target_switch": "switch_has_no_action"}


async def test_one_switch_serves_one_mode(
    hass: HomeAssistant, climate_entry: MockConfigEntry
) -> None:
    """A second thermostat of the other mode on the same switch is refused."""
    hass.states.async_set("switch.room_heat", "off")
    await _add(hass, climate_entry, SUBENTRY_TYPE_THERMOSTAT, THERMOSTAT_INPUT, {})

    result = await _add(
        hass,
        climate_entry,
        SUBENTRY_TYPE_THERMOSTAT,
        {
            **THERMOSTAT_INPUT,
            "name": "Second",
            "thermostat_mode": ClimateMode.COOLING,
        },
    )

    assert result["errors"] == {"thermostat_mode": "switch_serves_other_mode"}


async def test_switch_keeps_the_action_its_thermostat_needs(
    hass: HomeAssistant, climate_entry: MockConfigEntry
) -> None:
    """Reconfiguring away the heating action of a heating thermostat's switch fails."""
    switch = await _virtual_switch(hass, climate_entry)
    await _add(
        hass,
        climate_entry,
        SUBENTRY_TYPE_THERMOSTAT,
        {**THERMOSTAT_INPUT, "target_switch": switch},
        {},
    )
    subentry_id = next(
        sub.subentry_id
        for sub in climate_entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_SWITCH
    )

    flow = hass.config_entries.subentries
    result = await flow.async_init(
        (climate_entry.entry_id, SUBENTRY_TYPE_SWITCH),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    result = await flow.async_configure(
        result["flow_id"],
        {
            **{k: v for k, v in SWITCH_INPUT.items() if k != "heating_action"},
            "cooling_action": [{"action": "switch.turn_on"}],
        },
    )

    assert result["errors"] == {"heating_action": "needed_by_thermostat"}


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


SWITCH_INPUT = {
    "name": "Heating Switch",
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
}
THERMOSTAT_INPUT = {
    "name": "Room Thermostat",
    "temperature_sensor": "sensor.temp_room",
    "target_switch": "switch.room_heat",
    "thermostat_mode": ClimateMode.HEATING,
    "cold_tolerance": 0.3,
    "hot_tolerance": 0.3,
}


async def _run_subentry_flow(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    subentry_type: str,
    context: dict,
    steps: list[dict],
) -> dict:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, subentry_type), context=context
    )
    for user_input in steps:
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], user_input
        )
    return result


async def _create_and_reconfigure_subentry(
    hass: HomeAssistant, entry: MockConfigEntry, subentry_type: str, steps: list[dict]
) -> dict:
    """Create a subentry, then reconfigure it with the same input."""
    await _run_subentry_flow(hass, entry, subentry_type, {"source": SOURCE_USER}, steps)
    context = {
        "source": SOURCE_RECONFIGURE,
        "subentry_id": next(iter(entry.subentries)),
    }
    return await _run_subentry_flow(hass, entry, subentry_type, context, steps)


async def test_reconfigure_entry_message_from_integration_translations(
    hass: HomeAssistant,
) -> None:
    """Reconfigure success text comes from this integration (it has uk text)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CAPACITY: 1,
            CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 20, "seconds": 0},
            CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 20, "seconds": 0},
            CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
        },
    )

    assert result["reason"] == "reconfigure_successful"
    assert result.get("translation_domain") is None


async def test_reconfigure_switch_subentry_message_from_integration_translations(
    hass: HomeAssistant, climate_entry: MockConfigEntry
) -> None:
    """Switch subentry reconfigure: success text from this integration."""
    hass.states.async_set(
        "sensor.power_test",
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    result = await _create_and_reconfigure_subentry(
        hass, climate_entry, SUBENTRY_TYPE_SWITCH, [SWITCH_INPUT]
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert result.get("translation_domain") is None


async def test_reconfigure_thermostat_subentry_message_from_integration_translations(
    hass: HomeAssistant, climate_entry: MockConfigEntry
) -> None:
    """Thermostat subentry reconfigure: success text from this integration."""
    hass.states.async_set(
        "sensor.temp_room",
        "20",
        {"device_class": "temperature", "unit_of_measurement": "°C"},
    )
    hass.states.async_set("switch.room_heat", "off")

    result = await _create_and_reconfigure_subentry(
        hass, climate_entry, SUBENTRY_TYPE_THERMOSTAT, [THERMOSTAT_INPUT, {}]
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert result.get("translation_domain") is None


async def test_control_settings_in_the_options(hass: HomeAssistant) -> None:
    """Stage B: attempts, tolerance and hysteresis in the entry's options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CAPACITY: 1,
            CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 20, "seconds": 0},
            CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    defaults = {key.schema: key.default() for key in result["data_schema"].schema}
    assert defaults == {
        "attempts": 7,
        "tolerance_seconds": 60,
        "hysteresis_seconds": 60,
    }

    await hass.config_entries.options.async_configure(
        result["flow_id"], {**defaults, "attempts": 3}
    )
    assert entry.options["attempts"] == 3


@pytest.mark.parametrize("language", ["en", "uk"])
def test_control_texts_are_translated(language: str) -> None:
    """Every control state and setting has a text in both languages."""
    path = Path(climate.__file__).parent / "translations" / f"{language}.json"
    texts = json.loads(path.read_text(encoding="utf-8"))
    states = texts["entity"]["sensor"]["control_state"]["state"]
    assert set(states) == {state.value for state in ControlState}
    assert set(texts["options"]["step"]["init"]["data"]) == {
        f.name for f in fields(ControlSettings)
    }
