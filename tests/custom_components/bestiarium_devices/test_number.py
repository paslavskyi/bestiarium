"""Tests for the Bestiarium Devices number entities."""

# ruff: noqa: S101

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_MODE_OPTIONS_SNAPSHOT,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_EXTENDED,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    LOAD_PRIORITY_DEFAULT,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_number_entities_virtual_device(
    hass: HomeAssistant,
) -> None:
    """Test number entities for virtual device."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: "Test Virtual Device",
            CONF_SWITCH_ENTITY_ID: "switch.test",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.test",
            CONF_LOAD_PRIORITIES: {
                CONF_DEFAULT_PRIORITY: 5000,
                "Eco": 1000,
                "Boost": 9999,
            },
            CONF_MODE_OPTIONS_SNAPSHOT: [CONF_DEFAULT_PRIORITY, "Eco", "Boost"],
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    # Check default priority entity
    default_entity_id = entity_registry.async_get_entity_id(
        "number",
        DOMAIN,
        f"{entry.entry_id}_priority_default",
    )
    assert default_entity_id is not None

    state = hass.states.get(default_entity_id)
    assert state is not None
    assert state.state == "5000"
    assert state.attributes["min"] == 0
    assert state.attributes["max"] == 9999  # noqa: PLR2004

    # Check mode priority entities
    eco_entity_id = entity_registry.async_get_entity_id(
        "number",
        DOMAIN,
        f"{entry.entry_id}_priority_Eco",
    )
    assert eco_entity_id is not None
    assert hass.states.get(eco_entity_id).state == "1000"

    boost_entity_id = entity_registry.async_get_entity_id(
        "number",
        DOMAIN,
        f"{entry.entry_id}_priority_Boost",
    )
    assert boost_entity_id is not None
    assert hass.states.get(boost_entity_id).state == "9999"

    # Check device registry
    device = device_registry.async_get_device(
        identifiers={(DOMAIN, entry.entry_id)},
    )
    assert device is not None
    assert device.name == "Test Virtual Device"
    assert device.manufacturer == "Bestiarium"
    assert device.model == "Virtual Device"


async def test_number_entities_extended_device(
    hass: HomeAssistant,
) -> None:
    """Test number entities for extended device."""
    # Create a mock config entry for the device
    other_entry = MockConfigEntry(domain="other", entry_id="other_entry")
    other_entry.add_to_hass(hass)

    # Create existing device
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=other_entry.entry_id,
        identifiers={("other", "device1")},
        name="Existing Device",
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
            CONF_DEVICE_ID: device.id,
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.power",
            CONF_LOAD_PRIORITIES: {
                CONF_DEFAULT_PRIORITY: 6000,
                "Eco": 2000,
            },
            CONF_MODE_OPTIONS_SNAPSHOT: [CONF_DEFAULT_PRIORITY, "Eco"],
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)

    # Check entities are linked to existing device
    default_entity_id = entity_registry.async_get_entity_id(
        "number",
        DOMAIN,
        f"{device.id}_priority_default",
    )
    assert default_entity_id is not None

    entity = entity_registry.async_get(default_entity_id)
    assert entity is not None
    assert entity.device_id == device.id

    # Verify entity is linked to device
    state = hass.states.get(default_entity_id)
    assert state is not None
    assert state.state == "6000"


async def test_number_entity_update_value(
    hass: HomeAssistant,
) -> None:
    """Test updating number entity value."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: "Test Device",
            CONF_SWITCH_ENTITY_ID: "switch.test",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.test",
            CONF_LOAD_PRIORITIES: {
                CONF_DEFAULT_PRIORITY: 5000,
            },
            CONF_MODE_OPTIONS_SNAPSHOT: [CONF_DEFAULT_PRIORITY],
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id(
        "number",
        DOMAIN,
        f"{entry.entry_id}_priority_default",
    )
    assert entity_id is not None

    # Update value via service call
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id, "value": 7500},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "7500"

    # Verify config entry was updated
    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry is not None
    assert (
        updated_entry.data[CONF_LOAD_PRIORITIES][CONF_DEFAULT_PRIORITY] == 7500  # noqa: PLR2004
    )


async def test_number_entity_restore_state(
    hass: HomeAssistant,
) -> None:
    """Test number entity restores state."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: "Test Device",
            CONF_SWITCH_ENTITY_ID: "switch.test",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.test",
            CONF_LOAD_PRIORITIES: {
                CONF_DEFAULT_PRIORITY: LOAD_PRIORITY_DEFAULT,
            },
            CONF_MODE_OPTIONS_SNAPSHOT: [CONF_DEFAULT_PRIORITY],
        },
    )
    entry.add_to_hass(hass)

    # Set up entity
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id(
        "number",
        DOMAIN,
        f"{entry.entry_id}_priority_default",
    )

    # Set a value
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id, "value": 8000},
        blocking=True,
    )
    await hass.async_block_till_done()

    # Unload and reload
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # State should be restored from config entry (not from restore_state)
    # since config entry is the source of truth
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "8000"
