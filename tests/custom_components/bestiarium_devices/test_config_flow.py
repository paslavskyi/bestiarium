"""Tests for the Bestiarium Devices config flow."""

# ruff: noqa: S101

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
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
    CONF_OVERLOAD_FIRST_QUEUE,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_EXTENDED,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    DOMAIN_BESTIARIUM_MODE,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
async def bestiarium_mode_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create a Bestiarium Mode config entry for testing."""
    entry = MockConfigEntry(
        domain=DOMAIN_BESTIARIUM_MODE,
        unique_id="bestiarium_mode",
        data={
            "options": [
                {"name": "Eco", "description": "Energy saving", "icon": "mdi:leaf"},
                {"name": "Boost", "description": "High priority", "icon": "mdi:flash"},
            ],
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_config_flow_aborts_without_bestiarium_mode(
    hass: HomeAssistant,
) -> None:
    """Test config flow aborts when Bestiarium Mode is not configured."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_bestiarium_mode"


async def test_configure_priorities_form_includes_overload_first_queue(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test that configure_priorities step includes overload_first_queue and stores it."""
    hass.states.async_set("switch.test", "off")
    hass.states.async_set(
        "sensor.test",
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "create_virtual"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_NAME: "Overload Test",
            CONF_SWITCH_ENTITY_ID: "switch.test",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.test",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_priorities"
    # Submit with overload_first_queue True and verify it is stored
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 5000,
            CONF_OVERLOAD_FIRST_QUEUE: True,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data[CONF_OVERLOAD_FIRST_QUEUE] is True


async def test_config_flow_create_virtual_device(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test creating a virtual device via the flow."""
    # Create test entities
    hass.states.async_set("switch.test_switch", "off")
    hass.states.async_set(
        "sensor.test_power",
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "create_virtual"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "create_virtual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_NAME: "Test Virtual Device",
            CONF_SWITCH_ENTITY_ID: "switch.test_switch",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.test_power",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_priorities"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 5000,
            "priority_Eco": 1000,
            "priority_Boost": 9999,
            CONF_OVERLOAD_FIRST_QUEUE: True,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data[CONF_DEVICE_TYPE] == DEVICE_TYPE_VIRTUAL
    assert entries[0].data[CONF_DEVICE_NAME] == "Test Virtual Device"
    assert entries[0].data[CONF_SWITCH_ENTITY_ID] == "switch.test_switch"
    assert entries[0].data[CONF_POWER_SENSOR_ENTITY_ID] == "sensor.test_power"
    assert entries[0].data[CONF_LOAD_PRIORITIES] == {
        CONF_DEFAULT_PRIORITY: 5000,
        "Eco": 1000,
        "Boost": 9999,
    }
    assert set(entries[0].data[CONF_MODE_OPTIONS_SNAPSHOT]) == {
        CONF_DEFAULT_PRIORITY,
        "Eco",
        "Boost",
    }
    assert entries[0].data[CONF_OVERLOAD_FIRST_QUEUE] is True


async def test_config_flow_create_device_with_only_default_priority(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test creating a device with only default priority (no per-mode values)."""
    # Create test entities
    hass.states.async_set("switch.test_switch", "off")
    hass.states.async_set(
        "sensor.test_power",
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "create_virtual"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_NAME: "Test Device Optional",
            CONF_SWITCH_ENTITY_ID: "switch.test_switch",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.test_power",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_priorities"

    # Only provide default priority, skip per-mode priorities
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 4000,
            # Eco and Boost are optional and not provided
            CONF_OVERLOAD_FIRST_QUEUE: False,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    # Default priority should be what user specified
    assert entries[0].data[CONF_LOAD_PRIORITIES][CONF_DEFAULT_PRIORITY] == 4000  # noqa: PLR2004
    # Per-mode priorities omitted by user are left out of config
    assert "Eco" not in entries[0].data[CONF_LOAD_PRIORITIES]
    assert "Boost" not in entries[0].data[CONF_LOAD_PRIORITIES]
    assert entries[0].data[CONF_OVERLOAD_FIRST_QUEUE] is False


async def test_config_flow_create_device_with_partial_priorities(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test creating a device with only some per-mode priorities specified."""
    # Create test entities
    hass.states.async_set("switch.test_switch2", "off")
    hass.states.async_set(
        "sensor.test_power2",
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "create_virtual"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_NAME: "Test Device Partial",
            CONF_SWITCH_ENTITY_ID: "switch.test_switch2",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.test_power2",
        },
    )

    # Provide default and only Eco priority, skip Boost
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 3000,
            "priority_Eco": 1500,
            # Boost is optional and not provided
            CONF_OVERLOAD_FIRST_QUEUE: True,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data[CONF_LOAD_PRIORITIES][CONF_DEFAULT_PRIORITY] == 3000  # noqa: PLR2004
    assert entries[0].data[CONF_LOAD_PRIORITIES]["Eco"] == 1500  # noqa: PLR2004
    # Boost omitted by user is left out of config
    assert "Boost" not in entries[0].data[CONF_LOAD_PRIORITIES]


async def test_config_flow_create_virtual_device_validation(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test validation errors in virtual device creation."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "create_virtual"},
    )

    # Test missing device name
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_NAME: "",
            CONF_SWITCH_ENTITY_ID: "switch.test",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.test",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_DEVICE_NAME] == "name_required"

    # Test invalid power sensor
    hass.states.async_set("sensor.invalid", "100")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_NAME: "Test Device",
            CONF_SWITCH_ENTITY_ID: "switch.test",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.invalid",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_POWER_SENSOR_ENTITY_ID] == "invalid_power_sensor"


async def test_config_flow_extend_device(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test extending an existing device."""
    # Create a mock config entry for the device
    test_entry = MockConfigEntry(domain="test", entry_id="test_entry")
    test_entry.add_to_hass(hass)

    # Create a device with switch and power sensor
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device = device_registry.async_get_or_create(
        config_entry_id=test_entry.entry_id,
        identifiers={("test", "device1")},
        name="Test Device",
    )

    switch_entity = entity_registry.async_get_or_create(
        "switch",
        "test",
        "switch1",
        device_id=device.id,
    )
    hass.states.async_set(switch_entity.entity_id, "off")

    power_entity = entity_registry.async_get_or_create(
        "sensor",
        "test",
        "power1",
        device_id=device.id,
    )
    hass.states.async_set(
        power_entity.entity_id,
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "extend_device"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "extend_device"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE_ID: device.id},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_priorities"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 5000,
            "priority_Eco": 2000,
            "priority_Boost": 8000,
            CONF_OVERLOAD_FIRST_QUEUE: False,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data[CONF_DEVICE_TYPE] == DEVICE_TYPE_EXTENDED
    assert entries[0].data[CONF_DEVICE_ID] == device.id
    assert entries[0].data[CONF_POWER_SENSOR_ENTITY_ID] == power_entity.entity_id


async def test_config_flow_extend_device_no_power_sensor(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test extending device without power sensor shows error."""
    # Create a mock config entry for the device
    test_entry = MockConfigEntry(domain="test", entry_id="test_entry2")
    test_entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device = device_registry.async_get_or_create(
        config_entry_id=test_entry.entry_id,
        identifiers={("test", "device2")},
        name="Test Device No Power",
    )

    switch_entity = entity_registry.async_get_or_create(
        "switch",
        "test",
        "switch2",
        device_id=device.id,
    )
    hass.states.async_set(switch_entity.entity_id, "off")

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "extend_device"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE_ID: device.id},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_DEVICE_ID] == "no_power_sensor"


async def test_config_flow_extend_virtual_device_redirects(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test extending a virtual device redirects to reconfigure."""
    # Create a virtual device entry first
    virtual_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: "Virtual Device",
            CONF_SWITCH_ENTITY_ID: "switch.virtual",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.virtual",
            CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            CONF_MODE_OPTIONS_SNAPSHOT: [CONF_DEFAULT_PRIORITY],
        },
    )
    virtual_entry.add_to_hass(hass)

    # Set up the entry to create device registry entry
    assert await hass.config_entries.async_setup(virtual_entry.entry_id)
    await hass.async_block_till_done()

    # Get device registry entry that was created
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(
        identifiers={(DOMAIN, virtual_entry.entry_id)},
    )
    assert device is not None

    # Register switch entity for the virtual device so it shows up in device selector
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "switch",
        "test",
        "virtual_switch",
        device_id=device.id,
    )
    hass.states.async_set("switch.virtual", "off")

    # Start new flow to extend device
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    assert result["type"] == FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "extend_device"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "extend_device"

    # Configure with virtual device - should abort
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE_ID: device.id},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_config_flow_reconfigure_virtual_device(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test reconfiguring a virtual device."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: "Original Name",
            CONF_SWITCH_ENTITY_ID: "switch.original",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.original",
            CONF_LOAD_PRIORITIES: {
                CONF_DEFAULT_PRIORITY: 5000,
                "Eco": 1000,
            },
            CONF_MODE_OPTIONS_SNAPSHOT: [CONF_DEFAULT_PRIORITY, "Eco"],
        },
    )
    entry.add_to_hass(hass)

    hass.states.async_set("switch.new_switch", "off")
    hass.states.async_set(
        "sensor.new_power",
        "200",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "create_virtual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEVICE_NAME: "Updated Name",
            CONF_SWITCH_ENTITY_ID: "switch.new_switch",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.new_power",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_priorities"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 6000,
            "priority_Eco": 2000,
            "priority_Boost": 9000,
            CONF_OVERLOAD_FIRST_QUEUE: False,
        },
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry is not None
    assert updated_entry.data[CONF_DEVICE_NAME] == "Updated Name"
    assert updated_entry.data[CONF_SWITCH_ENTITY_ID] == "switch.new_switch"
    assert updated_entry.data[CONF_LOAD_PRIORITIES][CONF_DEFAULT_PRIORITY] == 6000  # noqa: PLR2004
    assert updated_entry.data[CONF_OVERLOAD_FIRST_QUEUE] is False


async def test_config_flow_reconfigure_extended_device(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test reconfiguring an extended device."""
    # Create a mock config entry for the device
    test_entry = MockConfigEntry(domain="test", entry_id="test_entry3")
    test_entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=test_entry.entry_id,
        identifiers={("test", "device3")},
        name="Extended Device",
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
            CONF_DEVICE_ID: device.id,
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.extended",
            CONF_LOAD_PRIORITIES: {
                CONF_DEFAULT_PRIORITY: 5000,
                "Eco": 1000,
            },
            CONF_MODE_OPTIONS_SNAPSHOT: [CONF_DEFAULT_PRIORITY, "Eco"],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_priorities"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 7000,
            "priority_Eco": 3000,
            "priority_Boost": 9500,
            CONF_OVERLOAD_FIRST_QUEUE: False,
        },
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry is not None
    assert updated_entry.data[CONF_LOAD_PRIORITIES][CONF_DEFAULT_PRIORITY] == 7000  # noqa: PLR2004
    assert updated_entry.data[CONF_LOAD_PRIORITIES]["Boost"] == 9500  # noqa: PLR2004
    assert updated_entry.data[CONF_OVERLOAD_FIRST_QUEUE] is False


async def test_reconfigure_preserves_overload_first_queue(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test that reconfiguring an entry preserves or updates overload_first_queue."""
    test_entry = MockConfigEntry(domain="test", entry_id="test_entry_overload")
    test_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=test_entry.entry_id,
        identifiers={("test", "device_overload")},
        name="Overload Reconfig Device",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
            CONF_DEVICE_ID: device.id,
            CONF_SWITCH_ENTITY_ID: "switch.overload",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.overload",
            CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000, "Eco": 1000},
            CONF_MODE_OPTIONS_SNAPSHOT: [CONF_DEFAULT_PRIORITY, "Eco"],
            CONF_OVERLOAD_FIRST_QUEUE: True,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_priorities"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 5500,
            "priority_Eco": 1500,
            "priority_Boost": 8500,
            CONF_OVERLOAD_FIRST_QUEUE: True,
        },
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry is not None
    assert updated_entry.data[CONF_OVERLOAD_FIRST_QUEUE] is True


async def test_config_flow_extend_device_no_switch(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test extending device with only disabled switch shows error."""
    # Create a mock config entry for the device
    test_entry = MockConfigEntry(domain="test", entry_id="test_entry_no_switch")
    test_entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device = device_registry.async_get_or_create(
        config_entry_id=test_entry.entry_id,
        identifiers={("test", "device_no_switch")},
        name="Test Device No Switch",
    )

    # Add a disabled switch (so device shows up in selector but has no usable switch)
    switch_entity = entity_registry.async_get_or_create(
        "switch",
        "test",
        "switch_disabled",
        device_id=device.id,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    hass.states.async_set(switch_entity.entity_id, "off")

    # Add power sensor
    power_entity = entity_registry.async_get_or_create(
        "sensor",
        "test",
        "power_no_switch",
        device_id=device.id,
    )
    hass.states.async_set(
        power_entity.entity_id,
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "extend_device"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE_ID: device.id},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_DEVICE_ID] == "no_switch"


async def test_config_flow_extend_device_multiple_switches(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test extending device with multiple switches shows combined selection step."""
    test_entry = MockConfigEntry(domain="test", entry_id="test_entry_multi_switch")
    test_entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device = device_registry.async_get_or_create(
        config_entry_id=test_entry.entry_id,
        identifiers={("test", "device_multi_switch")},
        name="Test Device Multi Switch",
    )

    # Add multiple switches
    switch1 = entity_registry.async_get_or_create(
        "switch",
        "test",
        "switch_a",
        device_id=device.id,
    )
    switch2 = entity_registry.async_get_or_create(
        "switch",
        "test",
        "switch_b",
        device_id=device.id,
    )
    hass.states.async_set(switch1.entity_id, "off")
    hass.states.async_set(switch2.entity_id, "off")

    # Add single power sensor
    power_entity = entity_registry.async_get_or_create(
        "sensor",
        "test",
        "power_multi_switch",
        device_id=device.id,
    )
    hass.states.async_set(
        power_entity.entity_id,
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "extend_device"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE_ID: device.id},
    )
    # Should show combined selection step
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_entities"

    # Select the switch (sensor is pre-selected since only one)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SWITCH_ENTITY_ID: switch1.entity_id},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_priorities"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 5000,
            "priority_Eco": 2000,
            "priority_Boost": 8000,
            CONF_OVERLOAD_FIRST_QUEUE: True,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data[CONF_SWITCH_ENTITY_ID] == switch1.entity_id
    assert entries[0].data[CONF_POWER_SENSOR_ENTITY_ID] == power_entity.entity_id


async def test_config_flow_extend_device_multiple_sensors(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test extending device with multiple sensors shows combined selection step."""
    test_entry = MockConfigEntry(domain="test", entry_id="test_entry_multi_sensor")
    test_entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device = device_registry.async_get_or_create(
        config_entry_id=test_entry.entry_id,
        identifiers={("test", "device_multi_sensor")},
        name="Test Device Multi Sensor",
    )

    # Add single switch
    switch_entity = entity_registry.async_get_or_create(
        "switch",
        "test",
        "switch_multi_sensor",
        device_id=device.id,
    )
    hass.states.async_set(switch_entity.entity_id, "off")

    # Add multiple power sensors
    power1 = entity_registry.async_get_or_create(
        "sensor",
        "test",
        "power_a",
        device_id=device.id,
    )
    power2 = entity_registry.async_get_or_create(
        "sensor",
        "test",
        "power_b",
        device_id=device.id,
    )
    hass.states.async_set(
        power1.entity_id,
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )
    hass.states.async_set(
        power2.entity_id,
        "200",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "extend_device"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE_ID: device.id},
    )
    # Should show combined selection step
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_entities"

    # Select the sensor (switch is pre-selected since only one)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_POWER_SENSOR_ENTITY_ID: power2.entity_id},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_priorities"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 5000,
            "priority_Eco": 2000,
            "priority_Boost": 8000,
            CONF_OVERLOAD_FIRST_QUEUE: False,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data[CONF_SWITCH_ENTITY_ID] == switch_entity.entity_id
    assert entries[0].data[CONF_POWER_SENSOR_ENTITY_ID] == power2.entity_id


async def test_config_flow_extend_device_multiple_both(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test extending device with multiple switches AND sensors shows combined step."""
    test_entry = MockConfigEntry(domain="test", entry_id="test_entry_multi_both")
    test_entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device = device_registry.async_get_or_create(
        config_entry_id=test_entry.entry_id,
        identifiers={("test", "device_multi_both")},
        name="Test Device Multi Both",
    )

    # Add multiple switches
    switch1 = entity_registry.async_get_or_create(
        "switch",
        "test",
        "switch_both_a",
        device_id=device.id,
    )
    switch2 = entity_registry.async_get_or_create(
        "switch",
        "test",
        "switch_both_b",
        device_id=device.id,
    )
    hass.states.async_set(switch1.entity_id, "off")
    hass.states.async_set(switch2.entity_id, "off")

    # Add multiple power sensors
    power1 = entity_registry.async_get_or_create(
        "sensor",
        "test",
        "power_both_a",
        device_id=device.id,
    )
    power2 = entity_registry.async_get_or_create(
        "sensor",
        "test",
        "power_both_b",
        device_id=device.id,
    )
    hass.states.async_set(
        power1.entity_id,
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )
    hass.states.async_set(
        power2.entity_id,
        "200",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "extend_device"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_DEVICE_ID: device.id},
    )
    # Should show combined selection step
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_entities"

    # Select both switch and sensor
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SWITCH_ENTITY_ID: switch2.entity_id,
            CONF_POWER_SENSOR_ENTITY_ID: power1.entity_id,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_priorities"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 5000,
            "priority_Eco": 2000,
            "priority_Boost": 8000,
            CONF_OVERLOAD_FIRST_QUEUE: False,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data[CONF_SWITCH_ENTITY_ID] == switch2.entity_id
    assert entries[0].data[CONF_POWER_SENSOR_ENTITY_ID] == power1.entity_id


async def test_config_flow_reconfigure_extended_device_with_multiple_entities(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Test reconfiguring an extended device with multiple switches/sensors."""
    test_entry = MockConfigEntry(domain="test", entry_id="test_entry_reconfig_multi")
    test_entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device = device_registry.async_get_or_create(
        config_entry_id=test_entry.entry_id,
        identifiers={("test", "device_reconfig_multi")},
        name="Extended Device Multi",
    )

    # Add multiple switches
    switch1 = entity_registry.async_get_or_create(
        "switch",
        "test",
        "switch_reconfig_a",
        device_id=device.id,
    )
    switch2 = entity_registry.async_get_or_create(
        "switch",
        "test",
        "switch_reconfig_b",
        device_id=device.id,
    )
    hass.states.async_set(switch1.entity_id, "off")
    hass.states.async_set(switch2.entity_id, "off")

    # Add multiple power sensors
    power1 = entity_registry.async_get_or_create(
        "sensor",
        "test",
        "power_reconfig_a",
        device_id=device.id,
    )
    power2 = entity_registry.async_get_or_create(
        "sensor",
        "test",
        "power_reconfig_b",
        device_id=device.id,
    )
    hass.states.async_set(
        power1.entity_id,
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )
    hass.states.async_set(
        power2.entity_id,
        "200",
        {"device_class": "power", "unit_of_measurement": "W"},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
            CONF_DEVICE_ID: device.id,
            CONF_SWITCH_ENTITY_ID: switch1.entity_id,
            CONF_POWER_SENSOR_ENTITY_ID: power1.entity_id,
            CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000, "Eco": 1000},
            CONF_MODE_OPTIONS_SNAPSHOT: [CONF_DEFAULT_PRIORITY, "Eco"],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    # Should show select_entities step since device has multiple switches/sensors
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_entities"

    # Change to different switch and sensor
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SWITCH_ENTITY_ID: switch2.entity_id,
            CONF_POWER_SENSOR_ENTITY_ID: power2.entity_id,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "configure_priorities"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFAULT_PRIORITY: 6000,
            "priority_Eco": 3000,
            "priority_Boost": 9000,
            CONF_OVERLOAD_FIRST_QUEUE: True,
        },
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry is not None
    assert updated_entry.data[CONF_SWITCH_ENTITY_ID] == switch2.entity_id
    assert updated_entry.data[CONF_POWER_SENSOR_ENTITY_ID] == power2.entity_id
    assert updated_entry.data[CONF_LOAD_PRIORITIES][CONF_DEFAULT_PRIORITY] == 6000  # noqa: PLR2004
    assert updated_entry.data[CONF_OVERLOAD_FIRST_QUEUE] is True
