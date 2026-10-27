"""Tests for the Bestiarium Climate climate platform (VirtualThermostat)."""

# ruff: noqa: S101

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.bestiarium_climate.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_thermostat_creation_from_subentry(
    hass: HomeAssistant,
    climate_entry_with_thermostat,
) -> None:
    """Test VirtualThermostat is created from thermostat subentry (hass, entry, subentry)."""
    hass.states.async_set(
        "sensor.temp",
        "20",
        {"device_class": "temperature", "unit_of_measurement": "°C"},
    )
    hass.states.async_set("switch.room", "off")

    await hass.config_entries.async_setup(climate_entry_with_thermostat.entry_id)
    await hass.async_block_till_done()

    states = hass.states.async_all("climate")
    assert len(states) == 1
    state = states[0]
    assert state.attributes.get("hvac_modes") == ["heat", "off"]


async def test_thermostat_temperature_tracking_updates_cur_temp(
    hass: HomeAssistant,
    climate_entry_with_thermostat,
) -> None:
    """Test temperature sensor updates current temp and hvac_action."""
    hass.states.async_set(
        "sensor.temp",
        "19",
        {"device_class": "temperature", "unit_of_measurement": "°C"},
    )
    hass.states.async_set("switch.room", "off")

    await hass.config_entries.async_setup(climate_entry_with_thermostat.entry_id)
    await hass.async_block_till_done()

    state = hass.states.async_all("climate")[0]
    assert float(state.attributes.get("current_temperature", 0)) == 19.0

    # Set target above current -> heating demand -> hvac_action heating (after mode heat)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {
            "entity_id": state.entity_id,
            "temperature": 22,
        },
        blocking=True,
    )
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": state.entity_id, "hvac_mode": HVACMode.HEAT},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(state.entity_id)
    assert state.attributes.get("hvac_action") == HVACAction.HEATING


async def test_thermostat_hvac_action_idle_when_temp_met(
    hass: HomeAssistant,
    climate_entry_with_thermostat,
) -> None:
    """Test hvac_action is IDLE when current temp meets target (no demand)."""
    hass.states.async_set(
        "sensor.temp",
        "21",
        {"device_class": "temperature", "unit_of_measurement": "°C"},
    )
    hass.states.async_set("switch.room", "off")

    await hass.config_entries.async_setup(climate_entry_with_thermostat.entry_id)
    await hass.async_block_till_done()

    state = hass.states.async_all("climate")[0]
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": state.entity_id, "temperature": 20},
        blocking=True,
    )
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": state.entity_id, "hvac_mode": HVACMode.HEAT},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(state.entity_id)
    # 21 >= 20 + tolerance -> no heating demand -> idle
    assert state.attributes.get("hvac_action") == HVACAction.IDLE


async def test_thermostat_has_no_internal_attributes(
    hass: HomeAssistant,
    climate_entry_with_thermostat,
) -> None:
    """The balancer's internals are in a Store, not in the climate's attributes."""
    hass.states.async_set(
        "sensor.temp",
        "20",
        {"device_class": "temperature", "unit_of_measurement": "°C"},
    )
    hass.states.async_set("switch.room", "off")

    await hass.config_entries.async_setup(climate_entry_with_thermostat.entry_id)
    await hass.async_block_till_done()

    attrs = hass.states.async_all("climate")[0].attributes
    assert (
        not {
            "expected_switch_state",
            "switch_state_change_tries",
            "switch_not_responding",
            "expected_switch_state_changed_at",
            "recovery_time",
        }
        & attrs.keys()
    )


async def test_thermostat_set_hvac_mode(
    hass: HomeAssistant,
    climate_entry_with_thermostat,
) -> None:
    """Test async_set_hvac_mode updates mode."""
    hass.states.async_set(
        "sensor.temp",
        "20",
        {"device_class": "temperature", "unit_of_measurement": "°C"},
    )
    hass.states.async_set("switch.room", "off")

    await hass.config_entries.async_setup(climate_entry_with_thermostat.entry_id)
    await hass.async_block_till_done()

    state = hass.states.async_all("climate")[0]
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": state.entity_id, "hvac_mode": HVACMode.OFF},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(state.entity_id)
    assert state.state == HVACMode.OFF
    assert state.attributes.get("hvac_action") == HVACAction.OFF


async def test_target_before_a_preset_survives_a_restart(
    hass: HomeAssistant, climate_entry_with_thermostat
) -> None:
    """The target to go back to after a preset is kept across a reload (audit)."""
    climate_entry_with_thermostat.subentries["thermo_01"].data["eco_temp"] = 16.0
    hass.states.async_set("sensor.temp", "20")
    hass.states.async_set("switch.room", "off")
    await hass.config_entries.async_setup(climate_entry_with_thermostat.entry_id)
    await hass.async_block_till_done()
    entity_id = hass.states.async_all("climate")[0].entity_id
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_id, "temperature": 22},
        blocking=True,
    )
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": entity_id, "preset_mode": "eco"},
        blocking=True,
    )

    await hass.config_entries.async_reload(climate_entry_with_thermostat.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": entity_id, "preset_mode": "none"},
        blocking=True,
    )

    assert hass.states.get(entity_id).attributes["temperature"] == 22


async def test_mode_and_target_survive_a_restart_without_temperature(
    hass: HomeAssistant, climate_entry_with_thermostat
) -> None:
    """Unavailable at shutdown (no temperature): heat and 23 °C come back."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("climate.room_thermostat", STATE_UNAVAILABLE),
                {"hvac_mode": "heat", "temperature": 23.0, "preset_mode": None},
            )
        ],
    )
    await hass.config_entries.async_setup(climate_entry_with_thermostat.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("climate.room_thermostat")
    assert state.state == "heat"
    assert state.attributes["temperature"] == 23.0
