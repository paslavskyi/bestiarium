"""Tests for the Bestiarium Climate climate platform (VirtualThermostat)."""

# ruff: noqa: S101

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.core import HomeAssistant

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


async def test_thermostat_extra_attributes(
    hass: HomeAssistant,
    climate_entry_with_thermostat,
) -> None:
    """Test extra state attributes (expected_switch_state, switch_state_change_tries, etc.)."""
    hass.states.async_set(
        "sensor.temp",
        "20",
        {"device_class": "temperature", "unit_of_measurement": "°C"},
    )
    hass.states.async_set("switch.room", "off")

    await hass.config_entries.async_setup(climate_entry_with_thermostat.entry_id)
    await hass.async_block_till_done()

    state = hass.states.async_all("climate")[0]
    attrs = state.attributes
    assert "expected_switch_state" in attrs
    assert "switch_state_change_tries" in attrs
    assert "switch_not_responding" in attrs
    assert "expected_switch_state_changed_at" in attrs


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
