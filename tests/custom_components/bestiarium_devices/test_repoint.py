"""
Re-pointing a device entry to another device (DEV-008 A1).

The owner moves a device from SmartThings to Matter: the same appliance, a new
device in HA. The entry keeps what it learned; the response time starts anew.
"""

# ruff: noqa: S101, PLR2004

from datetime import timedelta

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_ESTIMATED_POWER,
    CONF_LOAD_PRIORITIES,
    CONF_MODE_OPTIONS_SNAPSHOT,
    CONF_OVERLOAD_FIRST_QUEUE,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_RESET_POWER_MEMORY,
    CONF_SWITCH_ENTITY_ID,
    CONF_THERMOSTAT_DRIVEN,
    DEVICE_TYPE_EXTENDED,
    DOMAIN,
)
from custom_components.bestiarium_devices.consumption import SOURCE_RUN, PowerRecord
from custom_components.bestiarium_devices.control import async_get_response_memory
from custom_components.bestiarium_devices.power_memory import async_get_power_memory

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

OPTIONS = {"allowed": False, CONF_ESTIMATED_POWER: 900, "attempts": 5}
PRIORITIES = {CONF_DEFAULT_PRIORITY: 7000, "Eco": 1000}


def _device(hass: HomeAssistant, key: str, name: str) -> tuple[str, str, str]:
    """Create a device of another integration with a relay and a power sensor."""
    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("test", key)}, name=name
    )
    registry = er.async_get(hass)
    relay = registry.async_get_or_create(
        "switch", "test", f"{key}_relay", device_id=device.id
    ).entity_id
    power = registry.async_get_or_create(
        "sensor",
        "test",
        f"{key}_power",
        device_id=device.id,
        original_device_class="power",
        unit_of_measurement="W",
    ).entity_id
    hass.states.async_set(relay, "off")
    hass.states.async_set(
        power, "0", {"device_class": "power", "unit_of_measurement": "W"}
    )
    return device.id, relay, power


@pytest.fixture
async def boiler(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> MockConfigEntry:
    """Set up a boiler on its SmartThings device, with learned power and answers."""
    device_id, relay, power = _device(hass, "smartthings_boiler", "Boiler (ST)")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Boiler",
        minor_version=3,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
            CONF_DEVICE_ID: device_id,
            CONF_DEVICE_NAME: "Boiler",
            CONF_SWITCH_ENTITY_ID: relay,
            CONF_POWER_SENSOR_ENTITY_ID: power,
            CONF_LOAD_PRIORITIES: PRIORITIES,
            CONF_MODE_OPTIONS_SNAPSHOT: list(PRIORITIES),
            CONF_OVERLOAD_FIRST_QUEUE: True,
            CONF_THERMOSTAT_DRIVEN: False,
        },
        options=OPTIONS,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    memory = await async_get_power_memory(hass)
    for hours in range(3):
        memory.add_run(
            entry.entry_id,
            PowerRecord(
                start=dt_util.utcnow() - timedelta(hours=hours + 1),
                duration_s=900,
                power_w=2400,
                source=SOURCE_RUN,
            ),
        )
    (await async_get_response_memory(hass)).add(entry.entry_id, 12.0)
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)


def _entities(
    hass: HomeAssistant, entry: MockConfigEntry
) -> dict[str, er.RegistryEntry]:
    """Return the entry's entities by unique_id."""
    return {
        e.unique_id: e
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }


async def _reconfigure(
    hass: HomeAssistant, entry: MockConfigEntry, device_id: str, *, reset: bool
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["step_id"] == "pick_device"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: device_id, CONF_RESET_POWER_MEMORY: reset}
    )
    assert result["step_id"] == "configure_priorities"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 7000,
            "priority_Eco": 1000,
            CONF_OVERLOAD_FIRST_QUEUE: True,
            CONF_THERMOSTAT_DRIVEN: False,
        },
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()


async def test_repointed_device_keeps_what_it_learned(
    hass: HomeAssistant, boiler: MockConfigEntry
) -> None:
    """Criterion 1: settings, consumption and entities stay; the device is new."""
    before = _entities(hass, boiler)
    matter_id, matter_relay, matter_power = _device(hass, "matter_boiler", "Plug")

    await _reconfigure(hass, boiler, matter_id, reset=False)

    assert boiler.state is ConfigEntryState.LOADED
    assert boiler.data[CONF_DEVICE_ID] == matter_id
    assert boiler.data[CONF_SWITCH_ENTITY_ID] == matter_relay
    assert boiler.data[CONF_POWER_SENSOR_ENTITY_ID] == matter_power
    assert boiler.data[CONF_DEVICE_NAME] == "Boiler"
    assert boiler.data[CONF_LOAD_PRIORITIES] == PRIORITIES
    assert boiler.data[CONF_OVERLOAD_FIRST_QUEUE] is True
    assert dict(boiler.options) == OPTIONS
    memory = await async_get_power_memory(hass)
    assert [run.power_w for run in memory.records(boiler.entry_id)] == [2400] * 3
    # The response time belongs to the old connection
    assert (await async_get_response_memory(hass)).longest(boiler.entry_id) is None

    after = _entities(hass, boiler)
    assert after.keys() == before.keys()
    for unique_id, entity in after.items():
        assert entity.entity_id == before[unique_id].entity_id
        assert entity.device_id == matter_id


async def test_repointing_with_reset_forgets_the_consumption(
    hass: HomeAssistant, boiler: MockConfigEntry
) -> None:
    """Criterion 2: the checkbox empties the consumption memory, the rest stays."""
    matter_id, _relay, _power = _device(hass, "matter_boiler", "Plug")

    await _reconfigure(hass, boiler, matter_id, reset=True)

    memory = await async_get_power_memory(hass)
    assert memory.records(boiler.entry_id) == []
    assert dict(boiler.options) == OPTIONS
    assert boiler.data[CONF_LOAD_PRIORITIES] == PRIORITIES


async def test_same_device_keeps_the_response_time(
    hass: HomeAssistant, boiler: MockConfigEntry
) -> None:
    """Reconfiguring on the same device changes nothing of the memory."""
    await _reconfigure(hass, boiler, boiler.data[CONF_DEVICE_ID], reset=False)

    assert (await async_get_response_memory(hass)).longest(boiler.entry_id) == 12.0
    memory = await async_get_power_memory(hass)
    assert len(memory.records(boiler.entry_id)) == 3


async def test_device_without_a_relay_is_refused(
    hass: HomeAssistant, boiler: MockConfigEntry
) -> None:
    """A device with no switch: an error, the entry unchanged."""
    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    empty = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("test", "empty")}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": boiler.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: empty.id, CONF_RESET_POWER_MEMORY: False}
    )
    assert result["step_id"] == "pick_device"
    assert result["errors"] == {CONF_DEVICE_ID: "no_switch"}
