"""Tests for the Bestiarium Mock Devices entity platforms."""

# ruff: noqa: S101, PLR2004, PLC0415

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_mock.const import DOMAIN, MOCK_DEVICES

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _create_config_entry() -> MockConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={},
    )


async def test_all_entities_created(hass: HomeAssistant) -> None:
    """Test that all expected entities are created."""
    entry = _create_config_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # For each device, check expected entities exist
    for device_spec in MOCK_DEVICES:
        device_id = device_spec.device_id

        # Main switch
        switch_id = entity_registry.async_get_entity_id(
            "switch", DOMAIN, f"mock_{device_id}"
        )
        assert switch_id is not None, f"Switch for {device_id} not found"

        # Mock enabled switch
        mock_enabled_id = entity_registry.async_get_entity_id(
            "switch", DOMAIN, f"mock_{device_id}_mock_enabled"
        )
        assert mock_enabled_id is not None, f"Mock enabled for {device_id} not found"

        # Power sensor
        power_id = entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"mock_{device_id}_power"
        )
        assert power_id is not None, f"Power sensor for {device_id} not found"

        # State sensor
        state_id = entity_registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"mock_{device_id}_state"
        )
        assert state_id is not None, f"State sensor for {device_id} not found"

        # Number entity (only for variable power devices)
        if device_spec.has_variable_power:
            number_id = entity_registry.async_get_entity_id(
                "number", DOMAIN, f"mock_{device_id}_mock_power"
            )
            assert number_id is not None, f"Number for {device_id} not found"

    # Grid entities
    grid_power_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "mock_grid_power"
    )
    assert grid_power_id is not None, "Grid power sensor not found"

    grid_alarm_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "mock_grid_alarm"
    )
    assert grid_alarm_id is not None, "Grid alarm sensor not found"


async def test_switch_initial_state_off(hass: HomeAssistant) -> None:
    """Test that switches start in OFF state."""
    entry = _create_config_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # Check boiler switch is off
    switch_id = entity_registry.async_get_entity_id("switch", DOMAIN, "mock_boiler")
    state = hass.states.get(switch_id)
    assert state is not None
    assert state.state == STATE_OFF


async def test_mock_enabled_initial_state_on(hass: HomeAssistant) -> None:
    """Test that mock_enabled switches start in ON state (enabled by default)."""
    entry = _create_config_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # Check boiler mock_enabled is on
    mock_enabled_id = entity_registry.async_get_entity_id(
        "switch", DOMAIN, "mock_boiler_mock_enabled"
    )
    state = hass.states.get(mock_enabled_id)
    assert state is not None
    assert state.state == STATE_ON


async def test_power_sensor_initial_value_zero(hass: HomeAssistant) -> None:
    """Test that power sensors start at 0."""
    entry = _create_config_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # Check boiler power is 0
    power_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "mock_boiler_power"
    )
    state = hass.states.get(power_id)
    assert state is not None
    assert float(state.state) == 0


async def test_state_sensor_initial_state_off(hass: HomeAssistant) -> None:
    """Test that state sensors start in OFF state."""
    entry = _create_config_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # Check boiler state is off
    state_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "mock_boiler_state"
    )
    state = hass.states.get(state_id)
    assert state is not None
    assert state.state == STATE_OFF


async def test_number_entity_has_correct_range(hass: HomeAssistant) -> None:
    """Test that number entities have correct min/max values."""
    entry = _create_config_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # Check cooker number entity (400-4200W)
    number_id = entity_registry.async_get_entity_id(
        "number", DOMAIN, "mock_cooker_mock_power"
    )
    state = hass.states.get(number_id)
    assert state is not None
    assert state.attributes["min"] == 400
    assert state.attributes["max"] == 4200


async def test_grid_alarm_initial_state_off(hass: HomeAssistant) -> None:
    """Test that grid alarm starts in OFF state."""
    entry = _create_config_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # Check grid alarm is off
    alarm_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "mock_grid_alarm"
    )
    state = hass.states.get(alarm_id)
    assert state is not None
    assert state.state == STATE_OFF


async def test_switch_turn_on_updates_power(hass: HomeAssistant) -> None:
    """Test that turning on switch updates power sensor."""
    entry = _create_config_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    switch_id = entity_registry.async_get_entity_id("switch", DOMAIN, "mock_boiler")
    power_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "mock_boiler_power"
    )

    # Turn on the switch
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": switch_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    # Power should now be 1800W (boiler spec)
    state = hass.states.get(power_id)
    assert float(state.state) == 1800


async def test_switch_turn_off_sets_power_zero(hass: HomeAssistant) -> None:
    """Test that turning off switch sets power to 0."""
    entry = _create_config_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    switch_id = entity_registry.async_get_entity_id("switch", DOMAIN, "mock_boiler")
    power_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "mock_boiler_power"
    )

    # Turn on then off
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch_id}, blocking=True
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch_id}, blocking=True
    )
    await hass.async_block_till_done()

    # Power should be 0
    state = hass.states.get(power_id)
    assert float(state.state) == 0


async def test_mock_disabled_sets_power_zero(hass: HomeAssistant) -> None:
    """Test that disabling mock behavior sets power to 0."""
    entry = _create_config_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    switch_id = entity_registry.async_get_entity_id("switch", DOMAIN, "mock_boiler")
    mock_enabled_id = entity_registry.async_get_entity_id(
        "switch", DOMAIN, "mock_boiler_mock_enabled"
    )
    power_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, "mock_boiler_power"
    )

    # Turn on switch
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch_id}, blocking=True
    )
    await hass.async_block_till_done()

    # Verify power is on
    assert float(hass.states.get(power_id).state) == 1800

    # Disable mock behavior
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": mock_enabled_id}, blocking=True
    )
    await hass.async_block_till_done()

    # Power should be 0
    assert float(hass.states.get(power_id).state) == 0


async def test_state_sensor_reflects_power(hass: HomeAssistant) -> None:
    """Test that state sensor reflects whether device has power."""
    entry = _create_config_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    switch_id = entity_registry.async_get_entity_id("switch", DOMAIN, "mock_boiler")
    state_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "mock_boiler_state"
    )

    # Initially off
    assert hass.states.get(state_id).state == STATE_OFF

    # Turn on switch
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch_id}, blocking=True
    )
    await hass.async_block_till_done()

    # State should be on
    assert hass.states.get(state_id).state == STATE_ON

    # Turn off switch
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch_id}, blocking=True
    )
    await hass.async_block_till_done()

    # State should be off
    assert hass.states.get(state_id).state == STATE_OFF


async def test_unload_entry(hass: HomeAssistant) -> None:
    """Test that unloading entry works correctly."""
    from homeassistant.config_entries import ConfigEntryState

    entry = _create_config_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Verify loaded
    assert entry.state == ConfigEntryState.LOADED

    # Unload
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    # Verify unloaded (not removed, just unloaded)
    assert entry.state == ConfigEntryState.NOT_LOADED
