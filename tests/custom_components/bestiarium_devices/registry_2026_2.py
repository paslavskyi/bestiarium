"""
Registries as HA 2026.2 stored them, with one device shared by two entries.

Loading them on HA 2026.10 runs the 2026.8 device registry migration, which
splits the shared device into one device per config entry.
"""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Id of a device shared by two config entries before HA 2026.8 (now a composite id)
COMPOSITE_ID = "ad76d39c00000000000000000000aaaa"
RELAY = "switch.floor_hallway"
POWER_SENSOR = "sensor.floor_hallway_power"
TIMESTAMP = "2025-01-01T00:00:00+00:00"

_DEVICE = {
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
_AREA = {
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


def _entity(entity_id: str, device_class: str | None, unit: str | None) -> dict:
    return {
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
        "entity_id": entity_id,
        "hidden_by": None,
        "icon": None,
        "id": entity_id,
        "has_entity_name": True,
        "labels": [],
        "modified_at": TIMESTAMP,
        "name": None,
        "options": {},
        "original_device_class": device_class,
        "original_icon": None,
        "original_name": None,
        "platform": "shelly",
        "suggested_object_id": None,
        "supported_features": 0,
        "translation_key": None,
        "unique_id": entity_id,
        "previous_unique_id": None,
        "unit_of_measurement": unit,
    }


async def async_load_split_device(
    hass: HomeAssistant, hass_storage: dict, *, extra_switches: tuple[str, ...] = ()
) -> str:
    """
    Load registries with a shared Shelly + MikroTik device and return the relay's split.

    Needs `load_registries` parametrized to False.
    """
    for domain, entry_id in (("shelly", "shellyentry"), ("mikrotik", "mikrotikentry")):
        MockConfigEntry(domain=domain, entry_id=entry_id).add_to_hass(hass)
    entities = [
        _entity(RELAY, None, None),
        _entity(POWER_SENSOR, "power", "W"),
        *(_entity(switch, None, None) for switch in extra_switches),
    ]
    hass_storage.update(
        {
            "core.device_registry": {
                "version": 1,
                "minor_version": 12,
                "key": "core.device_registry",
                "data": {"devices": [_DEVICE], "deleted_devices": []},
            },
            "core.entity_registry": {
                "version": 1,
                "minor_version": 20,
                "key": "core.entity_registry",
                "data": {"entities": entities, "deleted_entities": []},
            },
            "core.area_registry": {
                "version": 1,
                "minor_version": 9,
                "key": "core.area_registry",
                "data": {"areas": [_AREA]},
            },
        }
    )
    await ar.async_load(hass)
    await fr.async_load(hass)
    await lr.async_load(hass)
    dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    await hass.async_block_till_done()

    for switch in (RELAY, *extra_switches):
        hass.states.async_set(switch, "off")
    hass.states.async_set(
        POWER_SENSOR, "50", {"device_class": "power", "unit_of_measurement": "W"}
    )
    return er.async_get(hass).async_get(RELAY).device_id
