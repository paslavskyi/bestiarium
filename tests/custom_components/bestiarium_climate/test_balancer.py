"""Tests for the Bestiarium Climate balancing coordinator."""

# ruff: noqa: S101

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.climate import HVACAction
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from custom_components.bestiarium_climate.balancer import BalancingCoordinator
from custom_components.bestiarium_climate.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def coordinator_entry(hass, climate_entry_with_thermostat):
    """Entry with one thermostat; integration set up so coordinator and entities exist."""
    await hass.config_entries.async_setup(climate_entry_with_thermostat.entry_id)
    await hass.async_block_till_done()
    return climate_entry_with_thermostat


async def test_coordinator_starts_and_stops(
    hass: HomeAssistant,
    climate_entry,
) -> None:
    """Test coordinator start/stop registers and cancels interval."""
    coordinator = BalancingCoordinator(hass, climate_entry)
    coordinator.start()
    assert coordinator._unsub is not None
    coordinator.stop()
    assert coordinator._unsub is None


async def test_turn_off_idle_clears_expected_when_hvac_idle(
    hass: HomeAssistant,
    coordinator_entry,
) -> None:
    """Test _turn_off_idle sets expected_switch_state False when hvac_action is IDLE."""
    hass.states.async_set("sensor.temp", "21", {"device_class": "temperature"})
    hass.states.async_set("switch.room", STATE_OFF)
    await hass.async_block_till_done()

    # Get the thermostat entity and set it to "idle" with expected=True
    from homeassistant.components.climate import (
        DATA_COMPONENT as CLIMATE_DATA_COMPONENT,
    )
    from custom_components.bestiarium_climate.climate import VirtualThermostat

    comp = hass.data.get(CLIMATE_DATA_COMPONENT)
    thermostats = [
        e
        for e in (getattr(comp, "entities", []) or [])
        if isinstance(e, VirtualThermostat)
    ]
    assert len(thermostats) == 1
    t = thermostats[0]
    t._attr_hvac_action = HVACAction.IDLE
    t.set_expected_switch_state(True)

    coordinator = hass.data[DOMAIN][coordinator_entry.entry_id]
    await coordinator._turn_off_idle()

    assert t.expected_switch_state is False
    assert t.switch_state_change_tries == 0


async def test_calculate_expected_state_capacity_zero_turns_off(
    hass: HomeAssistant,
    coordinator_entry,
) -> None:
    """Test capacity 0 sets expected_switch_state False for virtually working thermostats."""
    from homeassistant.components.climate import (
        DATA_COMPONENT as CLIMATE_DATA_COMPONENT,
    )
    from custom_components.bestiarium_climate.climate import VirtualThermostat

    hass.states.async_set("sensor.temp", "18", {"device_class": "temperature"})
    hass.states.async_set("switch.room", STATE_OFF)
    await hass.async_block_till_done()

    comp = hass.data.get(CLIMATE_DATA_COMPONENT)
    thermostats = [
        e
        for e in (getattr(comp, "entities", []) or [])
        if isinstance(e, VirtualThermostat)
    ]
    assert len(thermostats) == 1
    t = thermostats[0]
    t._attr_hvac_action = HVACAction.HEATING
    t.set_expected_switch_state(True)

    coordinator = hass.data[DOMAIN][coordinator_entry.entry_id]
    with patch.object(coordinator, "_get_capacity", return_value=0):
        await coordinator._calculate_expected_state(datetime.now(UTC))

    assert t.expected_switch_state is False


async def test_sync_switches_resets_tries_when_match(
    hass: HomeAssistant,
    coordinator_entry,
) -> None:
    """Test _sync_switches sets switch_state_change_tries to 0 when actual matches expected."""
    hass.states.async_set("sensor.temp", "20", {"device_class": "temperature"})
    hass.states.async_set("switch.room", STATE_OFF)
    await hass.async_block_till_done()

    from homeassistant.components.climate import (
        DATA_COMPONENT as CLIMATE_DATA_COMPONENT,
    )
    from custom_components.bestiarium_climate.climate import VirtualThermostat

    comp = hass.data.get(CLIMATE_DATA_COMPONENT)
    thermostats = [
        e
        for e in (getattr(comp, "entities", []) or [])
        if isinstance(e, VirtualThermostat)
    ]
    assert len(thermostats) == 1
    t = thermostats[0]
    t.set_expected_switch_state(False)  # match switch.room off
    t.set_switch_state_change_tries(3)

    coordinator = hass.data[DOMAIN][coordinator_entry.entry_id]
    await coordinator._sync_switches(datetime.now(UTC))

    assert t.switch_state_change_tries == 0


async def test_sync_switches_calls_turn_on_when_mismatch(
    hass: HomeAssistant,
    coordinator_entry,
) -> None:
    """Test _sync_switches calls switch.turn_on when expected True and switch is off."""
    hass.states.async_set("sensor.temp", "20", {"device_class": "temperature"})
    hass.states.async_set("switch.room", STATE_OFF)
    await hass.async_block_till_done()

    from homeassistant.components.climate import (
        DATA_COMPONENT as CLIMATE_DATA_COMPONENT,
    )
    from custom_components.bestiarium_climate.climate import VirtualThermostat

    comp = hass.data.get(CLIMATE_DATA_COMPONENT)
    thermostats = [
        e
        for e in (getattr(comp, "entities", []) or [])
        if isinstance(e, VirtualThermostat)
    ]
    assert len(thermostats) == 1
    t = thermostats[0]
    t.set_expected_switch_state(True)
    t.set_switch_state_change_tries(0)

    coordinator = hass.data[DOMAIN][coordinator_entry.entry_id]
    with patch.object(
        coordinator, "_async_turn_switch", new_callable=AsyncMock
    ) as mock_turn:
        await coordinator._sync_switches(datetime.now(UTC))
        mock_turn.assert_called_once()
        assert mock_turn.call_args.kwargs.get("turn_on") is True


async def test_handle_recovery_clears_when_switch_off(
    hass: HomeAssistant,
    coordinator_entry,
) -> None:
    """Test _handle_recovery clears not_responding and recovery_time when switch is off."""
    hass.states.async_set("sensor.temp", "20", {"device_class": "temperature"})
    hass.states.async_set("switch.room", STATE_OFF)
    await hass.async_block_till_done()

    from homeassistant.components.climate import (
        DATA_COMPONENT as CLIMATE_DATA_COMPONENT,
    )
    from custom_components.bestiarium_climate.climate import VirtualThermostat

    comp = hass.data.get(CLIMATE_DATA_COMPONENT)
    thermostats = [
        e
        for e in (getattr(comp, "entities", []) or [])
        if isinstance(e, VirtualThermostat)
    ]
    assert len(thermostats) == 1
    t = thermostats[0]
    t.set_switch_not_responding(True)
    t.set_recovery_time(datetime.now(UTC) - timedelta(minutes=1))  # in the past

    coordinator = hass.data[DOMAIN][coordinator_entry.entry_id]
    await coordinator._handle_recovery(datetime.now(UTC))

    assert t.switch_not_responding is False
    assert t.recovery_time is None
    assert t.switch_state_change_tries == 0
