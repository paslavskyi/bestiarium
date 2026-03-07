"""Tests for the Bestiarium Climate balancing coordinator."""

# ruff: noqa: S101

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.climate import HVACAction
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from custom_components.bestiarium_climate.balancer import BalancingCoordinator
from custom_components.bestiarium_climate.const import (
    DOMAIN,
    EVENT_CAPACITY_EXHAUSTED,
    EVENT_DEVICE_ROTATED_IN,
    EVENT_DEVICE_ROTATED_OUT,
    EVENT_SWITCH_RECOVERED,
)

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

    events: list = []

    def record(event):
        events.append({"event_type": event.event_type, "data": dict(event.data)})

    hass.bus.async_listen(EVENT_SWITCH_RECOVERED, record)

    coordinator = hass.data[DOMAIN][coordinator_entry.entry_id]
    await coordinator._handle_recovery(datetime.now(UTC))
    await hass.async_block_till_done()

    assert t.switch_not_responding is False
    assert t.recovery_time is None
    assert t.switch_state_change_tries == 0
    assert len(events) == 1
    assert events[0]["event_type"] == EVENT_SWITCH_RECOVERED
    assert events[0]["data"]["thermostat"]
    assert events[0]["data"]["switch_entity_id"] == "switch.room"


async def test_capacity_deficit_fires_events(
    hass: HomeAssistant,
    coordinator_entry_two_thermostats,
) -> None:
    """Deficit path fires capacity_exhausted, device_rotated_in, device_rotated_out."""
    hass.states.async_set("sensor.temp", "18", {"device_class": "temperature"})
    hass.states.async_set("sensor.temp2", "19", {"device_class": "temperature"})
    hass.states.async_set("switch.room", STATE_OFF)
    hass.states.async_set("switch.room2", STATE_OFF)
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
    assert len(thermostats) == 2
    t1, t2 = thermostats[0], thermostats[1]
    t1._attr_hvac_action = HVACAction.HEATING
    t2._attr_hvac_action = HVACAction.HEATING
    # t1: expected=False (coeff=1) so when it gets the slot we fire rotated_in
    # t2: expected=True with old changed_at (coeff>1) so it loses slot, we fire rotated_out
    t1.set_expected_switch_state(False)
    t2.set_expected_switch_state(True)
    t2._expected_switch_state_changed_at = datetime.now(UTC) - timedelta(minutes=40)

    events: list = []

    def record(event):
        events.append({"event_type": event.event_type, "data": dict(event.data)})

    hass.bus.async_listen(EVENT_CAPACITY_EXHAUSTED, record)
    hass.bus.async_listen(EVENT_DEVICE_ROTATED_IN, record)
    hass.bus.async_listen(EVENT_DEVICE_ROTATED_OUT, record)

    coordinator = hass.data[DOMAIN][coordinator_entry_two_thermostats.entry_id]
    with patch.object(coordinator, "_get_capacity", return_value=1):
        await coordinator._calculate_expected_state(datetime.now(UTC))
    await hass.async_block_till_done()

    exhausted = [e for e in events if e["event_type"] == EVENT_CAPACITY_EXHAUSTED]
    rotated_in = [e for e in events if e["event_type"] == EVENT_DEVICE_ROTATED_IN]
    rotated_out = [e for e in events if e["event_type"] == EVENT_DEVICE_ROTATED_OUT]

    assert len(exhausted) == 1
    assert exhausted[0]["data"]["capacity"] == 1
    assert exhausted[0]["data"]["demand"] == 2
    assert len(exhausted[0]["data"]["thermostats_on"]) == 1
    assert len(exhausted[0]["data"]["thermostats_waiting"]) == 1

    assert len(rotated_in) == 1
    assert "thermostat" in rotated_in[0]["data"]
    assert "switch_entity_id" in rotated_in[0]["data"]
    assert "working_time_coefficient" in rotated_in[0]["data"]
    assert "temperature_diff" in rotated_in[0]["data"]

    assert len(rotated_out) == 1
    assert "thermostat" in rotated_out[0]["data"]
    assert "switch_entity_id" in rotated_out[0]["data"]
