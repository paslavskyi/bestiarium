"""The per-device power entities: expected power, estimated power, reset."""

# ruff: noqa: S101, PLR2004

from datetime import timedelta

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_ESTIMATED_POWER,
    CONF_LOAD_PRIORITIES,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
)
from custom_components.bestiarium_devices.consumption import (
    SOURCE_HISTORY,
    SOURCE_RUN,
    PowerRecord,
)
from custom_components.bestiarium_devices.power_memory import DATA_POWER_MEMORY

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a virtual boiler."""
    hass.states.async_set("switch.boiler", "off")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: "Boiler",
            CONF_SWITCH_ENTITY_ID: "switch.boiler",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.boiler_power",
            CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)


def _entity_id(
    hass: HomeAssistant, domain: str, entry: MockConfigEntry, key: str
) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None
    return entity_id


def _run(watts: float, source: str = SOURCE_RUN) -> PowerRecord:
    return PowerRecord(
        start=dt_util.utcnow() - timedelta(hours=2),
        duration_s=312,
        power_w=watts,
        source=source,
    )


async def test_entities_on_the_device_with_their_categories(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The three entities sit on the device's own device, out of dashboards."""
    registry = er.async_get(hass)
    allowed = registry.async_get(_entity_id(hass, "switch", entry, "allowed"))
    for domain, key, category in (
        ("sensor", "expected_power", EntityCategory.DIAGNOSTIC),
        ("number", "estimated_power", EntityCategory.CONFIG),
        ("button", "reset_power_memory", EntityCategory.CONFIG),
    ):
        registry_entry = registry.async_get(_entity_id(hass, domain, entry, key))
        assert registry_entry.device_id == allowed.device_id
        assert registry_entry.entity_category == category


async def test_expected_power_shows_what_it_is_based_on(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Criterion 11: the sensor's details show the runs and their source."""
    sensor = _entity_id(hass, "sensor", entry, "expected_power")
    state = hass.states.get(sensor)
    assert state.state == "2000"
    assert state.attributes["unit_of_measurement"] == "W"
    assert state.attributes["estimate_source"] == "large_device_default"
    assert state.attributes["runs"] == []
    assert state.attributes["manual_power_w"] is None
    assert state.attributes["manual_power_note"] == "counts_until_real_runs"

    memory = hass.data[DATA_POWER_MEMORY]
    memory.set_imported(entry.entry_id, [_run(2600, SOURCE_HISTORY)])
    memory.add_run(entry.entry_id, _run(2380))
    await hass.async_block_till_done()

    state = hass.states.get(sensor)
    assert state.state == "2600"
    assert state.attributes["estimate_source"] == "history"
    assert [(r["power_w"], r["source"]) for r in state.attributes["runs"]] == [
        (2600, "history"),
        (2380, "run"),
    ]
    assert state.attributes["runs"][1]["duration_s"] == 312


async def test_estimated_power_number(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Setting the estimated power stores it and updates the expectation."""
    number = _entity_id(hass, "number", entry, "estimated_power")
    sensor = _entity_id(hass, "sensor", entry, "expected_power")
    assert hass.states.get(number).state == "0"

    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 1000}, blocking=True
    )
    await hass.async_block_till_done()

    assert entry.options[CONF_ESTIMATED_POWER] == 1000
    assert hass.states.get(number).state == "1000"
    state = hass.states.get(sensor)
    assert state.state == "1000"
    assert state.attributes["estimate_source"] == "manual"
    assert state.attributes["manual_power_w"] == 1000


async def test_reset_button_clears_the_memory(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Criterion 9: after a reset the expectation falls back to the manual value."""
    hass.config_entries.async_update_entry(entry, options={CONF_ESTIMATED_POWER: 700})
    memory = hass.data[DATA_POWER_MEMORY]
    for _ in range(3):
        memory.add_run(entry.entry_id, _run(300))
    await hass.async_block_till_done()
    sensor = _entity_id(hass, "sensor", entry, "expected_power")
    assert hass.states.get(sensor).state == "300"

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": _entity_id(hass, "button", entry, "reset_power_memory")},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(sensor)
    assert state.state == "700"
    assert state.attributes["runs"] == []
    assert state.attributes["estimate_source"] == "manual"
