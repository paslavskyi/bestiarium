"""The expected power of a device, its estimated power and reset in the form."""

# ruff: noqa: S101, PLR2004

from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
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
    """The expected power sits on the device's own device, out of dashboards."""
    registry = er.async_get(hass)
    allowed = registry.async_get(_entity_id(hass, "switch", entry, "allowed"))
    registry_entry = registry.async_get(
        _entity_id(hass, "sensor", entry, "expected_power")
    )
    assert registry_entry.device_id == allowed.device_id
    assert registry_entry.entity_category == EntityCategory.DIAGNOSTIC
    for domain, key in (
        ("number", "estimated_power"),
        ("button", "reset_power_memory"),
    ):
        unique_id = f"{entry.entry_id}_{key}"
        assert registry.async_get_entity_id(domain, DOMAIN, unique_id) is None


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


async def _settings(
    hass: HomeAssistant, entry: MockConfigEntry, **changes: object
) -> None:
    """Save the device's settings form with some fields changed."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    form = {
        key.schema: key.default()
        for key in result["data_schema"].schema
        if key.schema != "advanced"
    }
    advanced = result["data_schema"].schema["advanced"].schema.schema
    form["advanced"] = {key.schema: key.default() for key in advanced}
    await hass.config_entries.options.async_configure(
        result["flow_id"], {**form, **changes}
    )
    await hass.async_block_till_done()


async def test_estimated_power_in_the_settings(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Criterion 7: the estimated power is a setting; it applies at once."""
    sensor = _entity_id(hass, "sensor", entry, "expected_power")

    await _settings(hass, entry, estimated_power_w=1000)

    assert entry.options[CONF_ESTIMATED_POWER] == 1000
    state = hass.states.get(sensor)
    assert state.state == "1000"
    assert state.attributes["estimate_source"] == "manual"
    assert state.attributes["manual_power_w"] == 1000
    assert entry.state is ConfigEntryState.LOADED


async def test_reset_in_the_settings_clears_the_memory(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """After a reset the expectation falls back to the manual value."""
    hass.config_entries.async_update_entry(entry, options={CONF_ESTIMATED_POWER: 700})
    memory = hass.data[DATA_POWER_MEMORY]
    for _ in range(3):
        memory.add_run(entry.entry_id, _run(300))
    await hass.async_block_till_done()
    sensor = _entity_id(hass, "sensor", entry, "expected_power")
    assert hass.states.get(sensor).state == "300"

    await _settings(hass, entry, reset_power_memory=True)

    state = hass.states.get(sensor)
    assert state.state == "700"
    assert state.attributes["runs"] == []
    assert state.attributes["estimate_source"] == "manual"
    assert "reset_power_memory" not in entry.options


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"allowed": False, CONF_ESTIMATED_POWER: 900}, id="dev-006"),
        pytest.param(
            {"allowed": True, CONF_ESTIMATED_POWER: 900, "attempts": 5}, id="dev-007"
        ),
        pytest.param({}, id="nothing-set"),
    ],
)
async def test_migration_removes_the_setting_entities(
    hass: HomeAssistant, options: dict
) -> None:
    """Criterion 7, 12: 1.2 to 1.3 keeps the options and drops number and button."""
    hass.states.async_set("switch.boiler", "off")
    entry = MockConfigEntry(
        domain=DOMAIN,
        minor_version=2,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: "Boiler",
            CONF_SWITCH_ENTITY_ID: "switch.boiler",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.boiler_power",
            CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
        },
        options=options,
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for domain, key in (
        ("number", "estimated_power"),
        ("button", "reset_power_memory"),
    ):
        registry.async_get_or_create(
            domain, DOMAIN, f"{entry.entry_id}_{key}", config_entry=entry
        )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 3
    assert dict(entry.options) == options
    assert not [
        e
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain in ("number", "button")
    ]
