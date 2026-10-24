"""Config entry migration 1.1 -> 1.2: device_id follows the relay's device."""

# ruff: noqa: S101

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_devices.const import (
    CONF_ALLOWED,
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_MODE_OPTIONS_SNAPSHOT,
    CONF_OVERLOAD_FIRST_QUEUE,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    CONF_THERMOSTAT_DRIVEN,
    DEVICE_TYPE_EXTENDED,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    DOMAIN_BESTIARIUM_MODE,
)

from .conftest import keep_device
from .registry_2026_2 import COMPOSITE_ID, POWER_SENSOR, RELAY, async_load_split_device

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _owner_entry(device_id: str, **data: object) -> MockConfigEntry:
    """Entry shaped like the owner's, at version 1.1."""
    return MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=1,
        title="Heating floor hallway",
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
            CONF_DEVICE_ID: device_id,
            CONF_DEVICE_NAME: "Heating floor hallway",
            CONF_SWITCH_ENTITY_ID: RELAY,
            CONF_POWER_SENSOR_ENTITY_ID: POWER_SENSOR,
            CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 9000, "Grid off": 0},
            CONF_MODE_OPTIONS_SNAPSHOT: [CONF_DEFAULT_PRIORITY, "Grid off"],
            CONF_OVERLOAD_FIRST_QUEUE: True,
            CONF_THERMOSTAT_DRIVEN: True,
            **data,
        },
        options={CONF_ALLOWED: False},
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _bestiarium_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelname in ("WARNING", "ERROR")
        and (
            record.name.startswith("custom_components.bestiarium")
            or (
                record.name.startswith("homeassistant")
                and "bestiarium" in record.getMessage()
            )
        )
        and "We found a custom integration" not in record.getMessage()
        # The balancer runs without bestiarium_mode zone sensors in these tests
        and not record.getMessage().startswith("Power load binary sensors not found")
    ]


@pytest.fixture
def mode_entry(hass: HomeAssistant) -> None:
    """Add the bestiarium_mode entry the device config flow needs."""
    MockConfigEntry(
        domain=DOMAIN_BESTIARIUM_MODE,
        unique_id="bestiarium_mode",
        data={"options": [{"name": "Grid off", "icon": "mdi:home"}]},
    ).add_to_hass(hass)


@pytest.mark.parametrize("load_registries", [False])
async def test_composite_device_id_moves_to_the_relay_device(
    hass: HomeAssistant, hass_storage: dict, caplog: pytest.LogCaptureFixture
) -> None:
    """Criteria 1 and 5: only device_id changes; nothing else; no warnings."""
    relay_device_id = await async_load_split_device(hass, hass_storage)
    entry = _owner_entry(COMPOSITE_ID)
    data_before = dict(entry.data)
    options_before = dict(entry.options)
    await _setup(hass, entry)

    assert entry.minor_version == 3  # noqa: PLR2004
    assert entry.data == {**data_before, CONF_DEVICE_ID: relay_device_id}
    assert entry.options == options_before
    allowed = er.async_get(hass).async_get(
        er.async_get(hass).async_get_entity_id(
            "switch", DOMAIN, f"{entry.entry_id}_allowed"
        )
    )
    assert allowed.device_id == relay_device_id
    assert hass.states.get(allowed.entity_id).state == "off"
    assert _bestiarium_warnings(caplog) == []


@pytest.mark.parametrize("load_registries", [False])
async def test_migrated_entry_reconfigure_offers_the_device_entities(
    hass: HomeAssistant,
    hass_storage: dict,
    mode_entry: None,  # noqa: ARG001
) -> None:
    """Criterion 2: reconfigure lists the device's relays, which can be changed."""
    await async_load_split_device(
        hass, hass_storage, extra_switches=("switch.floor_hallway_2",)
    )
    entry = _owner_entry(COMPOSITE_ID)
    await _setup(hass, entry)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    result = await keep_device(hass, result)
    assert result["step_id"] == "select_entities"
    relay_selector = result["data_schema"].schema[CONF_SWITCH_ENTITY_ID]
    assert sorted(relay_selector.config["include_entities"]) == [
        RELAY,
        "switch.floor_hallway_2",
    ]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SWITCH_ENTITY_ID: "switch.floor_hallway_2"}
    )
    assert result["step_id"] == "configure_priorities"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 9000,
            CONF_OVERLOAD_FIRST_QUEUE: True,
            CONF_THERMOSTAT_DRIVEN: True,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert entry.data[CONF_SWITCH_ENTITY_ID] == "switch.floor_hallway_2"


@pytest.mark.parametrize("load_registries", [False])
async def test_migrated_entry_with_one_relay_goes_to_priorities(
    hass: HomeAssistant,
    hass_storage: dict,
    mode_entry: None,  # noqa: ARG001
) -> None:
    """Criterion 2: a single relay and sensor skip straight to priorities."""
    await async_load_split_device(hass, hass_storage)
    entry = _owner_entry(COMPOSITE_ID)
    await _setup(hass, entry)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    result = await keep_device(hass, result)

    assert result["step_id"] == "configure_priorities"


@pytest.mark.parametrize("load_registries", [False])
async def test_valid_device_id_is_left_alone(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """Criterion 3: an entry on a live device keeps its data."""
    relay_device_id = await async_load_split_device(hass, hass_storage)
    entry = _owner_entry(relay_device_id)
    data_before = dict(entry.data)
    await _setup(hass, entry)

    assert entry.minor_version == 3  # noqa: PLR2004
    assert entry.data == data_before


@pytest.mark.parametrize("load_registries", [False])
async def test_virtual_device_is_left_alone(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """A virtual device has no device_id to migrate."""
    await async_load_split_device(hass, hass_storage)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=1,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: "Garland",
            CONF_SWITCH_ENTITY_ID: RELAY,
            CONF_POWER_SENSOR_ENTITY_ID: POWER_SENSOR,
            CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 500},
        },
    )
    data_before = dict(entry.data)
    await _setup(hass, entry)

    assert entry.minor_version == 3  # noqa: PLR2004
    assert entry.data == data_before


async def test_entry_without_relay_is_left_alone(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Criterion 4: device and relay both gone ('Oven'): loads unchanged, quietly."""
    entry = _owner_entry(
        "725f71890000000000000000000000bb", **{CONF_SWITCH_ENTITY_ID: "switch.oven"}
    )
    data_before = dict(entry.data)
    await _setup(hass, entry)

    assert entry.minor_version == 3  # noqa: PLR2004
    assert entry.data == data_before
    assert _bestiarium_warnings(caplog) == []


@pytest.mark.parametrize("load_registries", [False])
async def test_relay_device_taken_by_another_entry(
    hass: HomeAssistant, hass_storage: dict, caplog: pytest.LogCaptureFixture
) -> None:
    """The relay's device already used by another entry: keep data, warn by name."""
    relay_device_id = await async_load_split_device(hass, hass_storage)
    other = _owner_entry(relay_device_id, **{CONF_DEVICE_NAME: "Other floor"})
    await _setup(hass, other)
    entry = _owner_entry(COMPOSITE_ID)
    data_before = dict(entry.data)
    caplog.clear()
    await _setup(hass, entry)

    assert entry.minor_version == 3  # noqa: PLR2004
    assert entry.data == data_before
    warnings = _bestiarium_warnings(caplog)
    assert len(warnings) == 1
    assert "Heating floor hallway" in warnings[0]


@pytest.mark.parametrize("load_registries", [False])
async def test_migration_does_not_add_devices(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """Migrating an entry creates no device."""
    await async_load_split_device(hass, hass_storage)
    devices_before = len(list(dr.async_get(hass).devices))

    await _setup(hass, _owner_entry(COMPOSITE_ID))

    assert len(list(dr.async_get(hass).devices)) == devices_before
