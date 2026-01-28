"""Tests for the Bestiarium Mode binary sensor entities."""

# ruff: noqa: S101

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_mode.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

# Test configuration: 5000W limit, 75% yellow (3750W), 90% red (4500W)
AC_LOAD_LIMITS_CONFIG = {
    "ac_power_sensor": "sensor.power_meter",
    "ac_load_limit_w": 5000,
    "ac_yellow_zone_percent": 75,
    "ac_red_zone_percent": 90,
}


def _create_config_entry(
    ac_load_limits: dict | None = None,
) -> MockConfigEntry:
    """Create a mock config entry."""
    data = {"options": [{"name": "Mode1", "icon": "mdi:home"}]}
    if ac_load_limits is not None:
        data["ac_load_limits"] = ac_load_limits
    return MockConfigEntry(domain=DOMAIN, data=data)


async def test_binary_sensors_created_with_config(hass: HomeAssistant) -> None:
    """Test that 4 binary sensors are created when ac_load_limits is configured."""
    # Set up initial power sensor state
    hass.states.async_set("sensor.power_meter", "1000")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # Verify all 4 binary sensors are created
    zones = ["green", "yellow", "red", "overload"]
    for zone in zones:
        entity_id = entity_registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"power_load_{zone}"
        )
        assert entity_id is not None, f"Binary sensor for {zone} zone not found"

        state = hass.states.get(entity_id)
        assert state is not None, f"State for {zone} zone not found"


async def test_no_binary_sensors_without_config(hass: HomeAssistant) -> None:
    """Test that no binary sensors are created when ac_load_limits is missing."""
    entry = _create_config_entry(ac_load_limits=None)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # Verify no binary sensors are created
    zones = ["green", "yellow", "red", "overload"]
    for zone in zones:
        entity_id = entity_registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"power_load_{zone}"
        )
        assert entity_id is None, f"Binary sensor for {zone} should not exist"


async def test_green_zone_on_below_yellow_threshold(hass: HomeAssistant) -> None:
    """Test Green is ON when power < yellow_threshold (3750W)."""
    hass.states.async_set("sensor.power_meter", "3000")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # Green should be ON
    green_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_green"
    )
    assert hass.states.get(green_id).state == STATE_ON

    # Yellow, Red, Overload should be OFF
    yellow_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_yellow"
    )
    assert hass.states.get(yellow_id).state == STATE_OFF

    red_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_red"
    )
    assert hass.states.get(red_id).state == STATE_OFF

    overload_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_overload"
    )
    assert hass.states.get(overload_id).state == STATE_OFF


async def test_yellow_zone_at_boundary(hass: HomeAssistant) -> None:
    """Test Yellow is ON when power is exactly at yellow_threshold (3750W)."""
    hass.states.async_set("sensor.power_meter", "3750")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # Green should be OFF (power >= yellow_threshold)
    green_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_green"
    )
    assert hass.states.get(green_id).state == STATE_OFF

    # Yellow should be ON
    yellow_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_yellow"
    )
    assert hass.states.get(yellow_id).state == STATE_ON

    # Red and Overload should be OFF
    red_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_red"
    )
    assert hass.states.get(red_id).state == STATE_OFF

    overload_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_overload"
    )
    assert hass.states.get(overload_id).state == STATE_OFF


async def test_red_zone_at_boundary(hass: HomeAssistant) -> None:
    """Test Red is ON when power is exactly at red_threshold (4500W)."""
    hass.states.async_set("sensor.power_meter", "4500")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # Green and Yellow should be OFF
    green_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_green"
    )
    assert hass.states.get(green_id).state == STATE_OFF

    yellow_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_yellow"
    )
    assert hass.states.get(yellow_id).state == STATE_OFF

    # Red should be ON
    red_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_red"
    )
    assert hass.states.get(red_id).state == STATE_ON

    # Overload should be OFF
    overload_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_overload"
    )
    assert hass.states.get(overload_id).state == STATE_OFF


async def test_overload_zone_at_limit(hass: HomeAssistant) -> None:
    """Test Overload is ON when power is exactly at limit (5000W)."""
    hass.states.async_set("sensor.power_meter", "5000")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # Green, Yellow, Red should be OFF
    green_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_green"
    )
    assert hass.states.get(green_id).state == STATE_OFF

    yellow_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_yellow"
    )
    assert hass.states.get(yellow_id).state == STATE_OFF

    red_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_red"
    )
    assert hass.states.get(red_id).state == STATE_OFF

    # Overload should be ON
    overload_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_overload"
    )
    assert hass.states.get(overload_id).state == STATE_ON


async def test_overload_zone_above_limit(hass: HomeAssistant) -> None:
    """Test Overload is ON when power exceeds limit."""
    hass.states.async_set("sensor.power_meter", "6000")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    overload_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_overload"
    )
    assert hass.states.get(overload_id).state == STATE_ON


async def test_sensor_unavailable_makes_binary_sensors_unavailable(
    hass: HomeAssistant,
) -> None:
    """Test that unavailable power sensor makes binary sensors unavailable."""
    hass.states.async_set("sensor.power_meter", STATE_UNAVAILABLE)

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # All binary sensors should be unavailable
    zones = ["green", "yellow", "red", "overload"]
    for zone in zones:
        entity_id = entity_registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"power_load_{zone}"
        )
        state = hass.states.get(entity_id)
        assert state.state == STATE_UNAVAILABLE, f"{zone} should be unavailable"


async def test_sensor_unknown_makes_binary_sensors_unavailable(
    hass: HomeAssistant,
) -> None:
    """Test that unknown power sensor makes binary sensors unavailable."""
    hass.states.async_set("sensor.power_meter", STATE_UNKNOWN)

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # All binary sensors should be unavailable
    zones = ["green", "yellow", "red", "overload"]
    for zone in zones:
        entity_id = entity_registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"power_load_{zone}"
        )
        state = hass.states.get(entity_id)
        assert state.state == STATE_UNAVAILABLE, f"{zone} should be unavailable"


async def test_invalid_sensor_value_makes_binary_sensors_unavailable(
    hass: HomeAssistant,
) -> None:
    """Test that invalid (non-numeric) sensor value makes binary sensors unavailable."""
    hass.states.async_set("sensor.power_meter", "invalid_value")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # All binary sensors should be unavailable
    zones = ["green", "yellow", "red", "overload"]
    for zone in zones:
        entity_id = entity_registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"power_load_{zone}"
        )
        state = hass.states.get(entity_id)
        assert state.state == STATE_UNAVAILABLE, f"{zone} should be unavailable"


async def test_zone_transition_on_state_change(hass: HomeAssistant) -> None:
    """Test that binary sensors update when power sensor value changes."""
    # Start in green zone
    hass.states.async_set("sensor.power_meter", "1000")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    green_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_green"
    )
    yellow_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_yellow"
    )

    # Initially green should be ON
    assert hass.states.get(green_id).state == STATE_ON
    assert hass.states.get(yellow_id).state == STATE_OFF

    # Change to yellow zone
    hass.states.async_set("sensor.power_meter", "4000")
    await hass.async_block_till_done()

    # Now yellow should be ON, green OFF
    assert hass.states.get(green_id).state == STATE_OFF
    assert hass.states.get(yellow_id).state == STATE_ON


async def test_extra_state_attributes(hass: HomeAssistant) -> None:
    """Test that binary sensors have correct extra state attributes."""
    hass.states.async_set("sensor.power_meter", "1000")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    green_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_green"
    )

    # Expected thresholds based on config: 5000W limit, 75% yellow, 90% red
    limit_w = AC_LOAD_LIMITS_CONFIG["ac_load_limit_w"]
    expected_yellow = limit_w * AC_LOAD_LIMITS_CONFIG["ac_yellow_zone_percent"] / 100
    expected_red = limit_w * AC_LOAD_LIMITS_CONFIG["ac_red_zone_percent"] / 100

    state = hass.states.get(green_id)
    assert state.attributes["zone"] == "green"
    assert state.attributes["yellow_threshold"] == expected_yellow
    assert state.attributes["red_threshold"] == expected_red
    assert state.attributes["limit_w"] == limit_w
