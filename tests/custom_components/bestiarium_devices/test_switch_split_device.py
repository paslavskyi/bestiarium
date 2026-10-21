"""'Allowed' switch for an entry whose device was split by the HA 2026.8 migration."""

# ruff: noqa: S101

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_EXTENDED,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

# Id of a device shared by two config entries before HA 2026.8 (now a composite id)
COMPOSITE_ID = "ad76d39c00000000000000000000aaaa"
TIMESTAMP = "2025-01-01T00:00:00+00:00"


def _pre_2026_8_registries() -> dict:
    """Device and entity registries as HA 2026.2 stored them."""
    device = {
        "area_id": "bathroom",
        "config_entries": ["shellyentry", "mikrotikentry"],
        "config_entries_subentries": {"shellyentry": [None], "mikrotikentry": [None]},
        "configuration_url": None,
        "connections": [["mac", "aa:bb:cc:dd:ee:ff"]],
        "created_at": TIMESTAMP,
        "disabled_by": None,
        "entry_type": None,
        "hw_version": None,
        "id": COMPOSITE_ID,
        "identifiers": [["shelly", "shelly-123"]],
        "labels": [],
        "manufacturer": "Shelly",
        "model": "1PM",
        "model_id": None,
        "modified_at": TIMESTAMP,
        "name_by_user": "Heating floor hallway",
        "name": "shelly1pm-123",
        "primary_config_entry": "shellyentry",
        "serial_number": None,
        "sw_version": None,
        "via_device_id": None,
    }
    relay = {
        "aliases": [],
        "area_id": None,
        "categories": {},
        "capabilities": None,
        "config_entry_id": "shellyentry",
        "config_subentry_id": None,
        "created_at": TIMESTAMP,
        "device_class": None,
        "device_id": COMPOSITE_ID,
        "disabled_by": None,
        "entity_category": None,
        "entity_id": "switch.floor_hallway",
        "hidden_by": None,
        "icon": None,
        "id": "relay",
        "has_entity_name": True,
        "labels": [],
        "modified_at": TIMESTAMP,
        "name": None,
        "options": {},
        "original_device_class": None,
        "original_icon": None,
        "original_name": None,
        "platform": "shelly",
        "suggested_object_id": None,
        "supported_features": 0,
        "translation_key": None,
        "unique_id": "relay",
        "previous_unique_id": None,
        "unit_of_measurement": None,
    }
    area = {
        "aliases": [],
        "floor_id": None,
        "humidity_entity_id": None,
        "icon": None,
        "id": "bathroom",
        "labels": [],
        "name": "Bathroom",
        "picture": None,
        "temperature_entity_id": None,
        "created_at": TIMESTAMP,
        "modified_at": TIMESTAMP,
    }
    return {
        "core.device_registry": {
            "version": 1,
            "minor_version": 12,
            "key": "core.device_registry",
            "data": {"devices": [device], "deleted_devices": []},
        },
        "core.entity_registry": {
            "version": 1,
            "minor_version": 20,
            "key": "core.entity_registry",
            "data": {"entities": [relay], "deleted_entities": []},
        },
        "core.area_registry": {
            "version": 1,
            "minor_version": 9,
            "key": "core.area_registry",
            "data": {"areas": [area]},
        },
    }


@pytest.mark.parametrize("load_registries", [False])
async def test_allowed_switch_on_the_split_holding_the_relay(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """The switch sits on the split with the relay; no device is added."""
    for domain, entry_id in (("shelly", "shellyentry"), ("mikrotik", "mikrotikentry")):
        MockConfigEntry(domain=domain, entry_id=entry_id).add_to_hass(hass)
    hass_storage.update(_pre_2026_8_registries())
    await ar.async_load(hass)
    await fr.async_load(hass)
    await lr.async_load(hass)
    dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    relay_device_id = er.async_get(hass).async_get("switch.floor_hallway").device_id
    assert relay_device_id != COMPOSITE_ID
    devices_before = len(list(device_registry.devices))

    hass.states.async_set("switch.floor_hallway", "off")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
            CONF_DEVICE_ID: COMPOSITE_ID,
            CONF_DEVICE_NAME: "Heating floor hallway",
            CONF_SWITCH_ENTITY_ID: "switch.floor_hallway",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.floor_hallway_power",
            CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 9000},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    allowed = er.async_get(hass).async_get(
        er.async_get(hass).async_get_entity_id(
            "switch", DOMAIN, f"{entry.entry_id}_allowed"
        )
    )
    assert allowed.device_id == relay_device_id
    assert device_registry.async_get(relay_device_id).area_id == "bathroom"
    assert allowed.area_id is None
    assert len(list(device_registry.devices)) == devices_before
