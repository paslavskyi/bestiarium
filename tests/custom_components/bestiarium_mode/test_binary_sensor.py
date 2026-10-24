"""Tests for the Bestiarium Mode binary sensor entities."""

# ruff: noqa: S101

from datetime import timedelta

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.bestiarium_mode.const import (
    DEFAULT_OVERLOAD_RECOVERY_SECONDS,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

# Test configuration: 5000W limit, 75% yellow (3750W), 90% red (4500W)
AC_LOAD_LIMITS_CONFIG = {
    "ac_power_sensor": "sensor.power_meter",
    "ac_load_limit_w": 5000,
    "ac_yellow_zone_percent": 75,
    "ac_red_zone_percent": 90,
}

# Config with specific recovery seconds for testing
AC_LOAD_LIMITS_CONFIG_WITH_RECOVERY = {
    **AC_LOAD_LIMITS_CONFIG,
    "ac_overload_recovery_seconds": 10,  # 10 seconds for faster tests
}


def _create_config_entry(
    ac_load_limits: dict | None = None,
) -> MockConfigEntry:
    """Create a mock config entry."""
    data = {"options": [{"name": "Mode1"}]}
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


async def test_green_zone_on_after_recovery(hass: HomeAssistant) -> None:
    """Test Green turns ON after recovery timeout when power < yellow_threshold."""
    hass.states.async_set("sensor.power_meter", "3000")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG_WITH_RECOVERY)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    green_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_green"
    )

    # Initially the pause: Green and the others OFF
    state = hass.states.get(green_id)
    assert state.state == STATE_OFF
    assert _on_pause(hass)

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

    # Advance time past recovery timeout (10 seconds + buffer)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()

    # Now Green should be ON
    state = hass.states.get(green_id)
    assert state.state == STATE_ON
    assert not _on_pause(hass)


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

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG_WITH_RECOVERY)
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

    # Initially green should be OFF (recovery pending)
    assert hass.states.get(green_id).state == STATE_OFF
    assert _on_pause(hass)
    assert hass.states.get(yellow_id).state == STATE_OFF

    # Advance time past recovery timeout
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()

    # Now green should be ON
    assert hass.states.get(green_id).state == STATE_ON
    assert hass.states.get(yellow_id).state == STATE_OFF

    # Change to yellow zone - green turns OFF immediately
    hass.states.async_set("sensor.power_meter", "4000")
    await hass.async_block_till_done()

    # Now yellow should be ON, green OFF
    assert hass.states.get(green_id).state == STATE_OFF
    assert hass.states.get(yellow_id).state == STATE_ON


async def test_green_zone_immediate_off_transition(hass: HomeAssistant) -> None:
    """Test that GREEN zone turns OFF immediately when power rises (no delay)."""
    hass.states.async_set("sensor.power_meter", "3000")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG_WITH_RECOVERY)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    green_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_green"
    )

    # Advance time to complete recovery
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()

    # Green should be ON
    assert hass.states.get(green_id).state == STATE_ON

    # Now increase power above yellow threshold - should turn OFF immediately
    hass.states.async_set("sensor.power_meter", "4000")
    await hass.async_block_till_done()

    # Green should be OFF immediately (no delay for OFF)
    assert hass.states.get(green_id).state == STATE_OFF
    assert not _on_pause(hass)


async def test_recovery_cancelled_when_power_rises(hass: HomeAssistant) -> None:
    """Test that recovery timer is cancelled when power rises during recovery period."""
    hass.states.async_set("sensor.power_meter", "3000")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG_WITH_RECOVERY)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    green_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_green"
    )

    # Initially recovery is pending
    state = hass.states.get(green_id)
    assert state.state == STATE_OFF
    assert _on_pause(hass)

    # Advance time partially (5 seconds, less than 10 second recovery)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()

    # Still recovering
    state = hass.states.get(green_id)
    assert state.state == STATE_OFF
    assert _on_pause(hass)

    # Power rises above threshold - should cancel recovery
    hass.states.async_set("sensor.power_meter", "4000")
    await hass.async_block_till_done()

    # Recovery should be cancelled
    state = hass.states.get(green_id)
    assert state.state == STATE_OFF
    assert not _on_pause(hass)

    # Advance time past original recovery timeout
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=15))
    await hass.async_block_till_done()

    # Green should still be OFF (recovery was cancelled)
    assert hass.states.get(green_id).state == STATE_OFF


async def test_recovery_restarts_after_cancellation(hass: HomeAssistant) -> None:
    """Test that recovery restarts when power drops again after cancellation."""
    hass.states.async_set("sensor.power_meter", "3000")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG_WITH_RECOVERY)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    green_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_green"
    )

    # Initially recovery is pending
    assert _on_pause(hass)

    # Power rises - cancels recovery
    hass.states.async_set("sensor.power_meter", "4000")
    await hass.async_block_till_done()
    assert not _on_pause(hass)

    # Power drops again - should start new recovery
    hass.states.async_set("sensor.power_meter", "3000")
    await hass.async_block_till_done()

    state = hass.states.get(green_id)
    assert state.state == STATE_OFF
    assert _on_pause(hass)

    # Advance time past recovery timeout
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()

    # Now green should be ON
    state = hass.states.get(green_id)
    assert state.state == STATE_ON
    assert not _on_pause(hass)


async def test_other_zones_work_immediately(hass: HomeAssistant) -> None:
    """Test that YELLOW, RED, and OVERLOAD zones work immediately (no delay)."""
    # Start in yellow zone
    hass.states.async_set("sensor.power_meter", "4000")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG_WITH_RECOVERY)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    yellow_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_yellow"
    )
    red_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_red"
    )
    overload_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_overload"
    )

    # Yellow should be ON immediately
    assert hass.states.get(yellow_id).state == STATE_ON

    # Change to red zone - should update immediately
    hass.states.async_set("sensor.power_meter", "4600")
    await hass.async_block_till_done()
    assert hass.states.get(yellow_id).state == STATE_OFF
    assert hass.states.get(red_id).state == STATE_ON

    # Change to overload zone - should update immediately
    hass.states.async_set("sensor.power_meter", "5500")
    await hass.async_block_till_done()
    assert hass.states.get(red_id).state == STATE_OFF
    assert hass.states.get(overload_id).state == STATE_ON

    # Change back to yellow - should update immediately
    hass.states.async_set("sensor.power_meter", "4000")
    await hass.async_block_till_done()
    assert hass.states.get(overload_id).state == STATE_OFF
    assert hass.states.get(yellow_id).state == STATE_ON


async def test_only_red_has_an_attribute(hass: HomeAssistant) -> None:
    """Review 11: only the red threshold, which the balancer reads, is shown."""
    hass.states.async_set("sensor.power_meter", "3000")
    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    for zone in ("green", "yellow", "red", "overload"):
        attributes = hass.states.get(_zone_id(hass, zone)).attributes
        own = {"red_threshold"} if zone == "red" else set()
        assert {
            "recovery_pending",
            "yellow_threshold",
            "red_threshold",
        } & attributes.keys() == own


async def test_default_recovery_seconds(hass: HomeAssistant) -> None:
    """Test that default recovery seconds is used when not configured."""
    hass.states.async_set("sensor.power_meter", "3000")

    # Use config without explicit recovery_seconds
    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    green_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_green"
    )

    # Recovery should be pending
    assert hass.states.get(green_id).state == STATE_OFF
    assert _on_pause(hass)

    # Advance time by default recovery seconds + buffer
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=DEFAULT_OVERLOAD_RECOVERY_SECONDS + 1),
    )
    await hass.async_block_till_done()

    # Green should now be ON
    assert hass.states.get(green_id).state == STATE_ON


async def test_lost_data_stops_the_pause(hass: HomeAssistant) -> None:
    """
    No data within the tolerance: the zone stays, the pause does not run out.

    After the tolerance the sensors are unavailable.
    """
    hass.states.async_set("sensor.power_meter", "3000")

    entry = _create_config_entry(AC_LOAD_LIMITS_CONFIG_WITH_RECOVERY)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    green_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "power_load_green"
    )
    assert _on_pause(hass)

    hass.states.async_set("sensor.power_meter", STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=15))
    await hass.async_block_till_done()

    state = hass.states.get(green_id)
    assert state.state == STATE_OFF
    assert _on_pause(hass)

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert hass.states.get(green_id).state == STATE_UNAVAILABLE


def _zone_id(hass: HomeAssistant, zone: str) -> str:
    return er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"power_load_{zone}"
    )


def _on_pause(hass: HomeAssistant) -> bool:
    """Return whether the zone is the pause: all four off and available."""
    return all(
        hass.states.get(_zone_id(hass, zone)).state == STATE_OFF
        for zone in ("green", "yellow", "red", "overload")
    )
