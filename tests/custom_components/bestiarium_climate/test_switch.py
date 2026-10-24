"""Tests for the Bestiarium Climate switch platform (VirtualSwitch)."""

# ruff: noqa: S101

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bestiarium_climate.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_switch_creation_from_subentry(
    hass: HomeAssistant,
    climate_entry_with_switch,
) -> None:
    """Test VirtualSwitch is created from switch subentry (hass, entry, subentry)."""
    hass.states.async_set("sensor.power", "0")
    await hass.config_entries.async_setup(climate_entry_with_switch.entry_id)
    await hass.async_block_till_done()

    states = hass.states.async_all("switch")
    assert len(states) == 1
    assert states[0].attributes.get("friendly_name") or "Virtual Sw" in str(states[0])


async def test_switch_turn_on_executes_heating_action(
    hass: HomeAssistant,
    climate_entry_with_switch,
) -> None:
    """Test async_turn_on runs heating_action when mode is heating."""
    hass.states.async_set("sensor.power", "0")
    await hass.config_entries.async_setup(climate_entry_with_switch.entry_id)
    await hass.async_block_till_done()

    with patch(
        "custom_components.bestiarium_climate.switch._execute_action",
        new_callable=AsyncMock,
    ) as mock_execute:
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": hass.states.async_all("switch")[0].entity_id},
            blocking=True,
        )
        await hass.async_block_till_done()
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args[0][1]
        assert call_args.get("action") == "switch.turn_on"
        assert call_args.get("target", {}).get("entity_id") == "switch.heater"


async def test_switch_turn_off_executes_off_action(
    hass: HomeAssistant,
    climate_entry_with_switch,
) -> None:
    """Test async_turn_off runs off_action."""
    hass.states.async_set("sensor.power", "0")
    await hass.config_entries.async_setup(climate_entry_with_switch.entry_id)
    await hass.async_block_till_done()

    with patch(
        "custom_components.bestiarium_climate.switch._execute_action",
        new_callable=AsyncMock,
    ) as mock_execute:
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": hass.states.async_all("switch")[0].entity_id},
            blocking=True,
        )
        await hass.async_block_till_done()
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args[0][1]
        assert call_args.get("action") == "switch.turn_off"


async def test_switch_power_below_min_is_off(
    hass: HomeAssistant,
    climate_entry_with_switch,
) -> None:
    """Test power sensor below min_power sets is_on to False."""
    hass.states.async_set(
        "sensor.power",
        "30",  # below min_power 50 in fixture
        {"device_class": "power", "unit_of_measurement": "W"},
    )
    hass.states.async_set("sensor.power", "0")
    await hass.config_entries.async_setup(climate_entry_with_switch.entry_id)
    await hass.async_block_till_done()

    state = hass.states.async_all("switch")[0]
    assert state.state == "off"


async def test_switch_power_above_min_is_on(
    hass: HomeAssistant,
    climate_entry_with_switch,
) -> None:
    """Test power sensor above min_power sets is_on to True."""
    hass.states.async_set(
        "sensor.power",
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )
    await hass.config_entries.async_setup(climate_entry_with_switch.entry_id)
    await hass.async_block_till_done()

    state = hass.states.async_all("switch")[0]
    assert state.state == "on"
