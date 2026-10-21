"""'Allowed' switch for an entry whose device was split by the HA 2026.8 migration."""

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
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_EXTENDED,
    DOMAIN,
)

from .registry_2026_2 import COMPOSITE_ID, POWER_SENSOR, RELAY, async_load_split_device

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.mark.parametrize("load_registries", [False])
async def test_allowed_switch_on_the_split_holding_the_relay(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """The switch sits on the split with the relay; no device is added."""
    relay_device_id = await async_load_split_device(hass, hass_storage)
    device_registry = dr.async_get(hass)
    assert relay_device_id != COMPOSITE_ID
    devices_before = len(list(device_registry.devices))

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
            CONF_DEVICE_ID: COMPOSITE_ID,
            CONF_DEVICE_NAME: "Heating floor hallway",
            CONF_SWITCH_ENTITY_ID: RELAY,
            CONF_POWER_SENSOR_ENTITY_ID: POWER_SENSOR,
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
