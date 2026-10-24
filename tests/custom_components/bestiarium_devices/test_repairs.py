"""
Repairs of a device whose relay or power sensor is gone (DEV-008 A3).

Checked only after HA has fully started; fixed with the fields of the
reconfigure form; a renamed entity is followed, not reported.
"""

# ruff: noqa: S101

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_RESET_POWER_MEMORY,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_EXTENDED,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

POWER = {"device_class": "power", "unit_of_measurement": "W"}


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"missing_entity_{entry.entry_id}"
    )


def _virtual(hass: HomeAssistant, name: str, switch: str) -> MockConfigEntry:
    power = f"sensor.{switch.split('.')[1]}_power"
    hass.states.async_set(power, "0", POWER)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=name,
        minor_version=3,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: name,
            CONF_SWITCH_ENTITY_ID: switch,
            CONF_POWER_SENSOR_ENTITY_ID: power,
            CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the integration: every device entry added."""
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_gone_switch_is_reported_after_start(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Criterion 5: no issue while HA starts; one after; the rest works."""
    hass.set_state(CoreState.starting)
    boiler = _virtual(hass, "Boiler", "switch.boiler")
    lamp = _virtual(hass, "Lamp", "switch.lamp")
    hass.states.async_set("switch.lamp", "off")
    await _setup(hass, boiler)
    assert _issue(hass, boiler) is None

    await hass.async_start()
    await hass.async_block_till_done()

    issue = _issue(hass, boiler)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders == {
        "device": "Boiler",
        "entities": "switch.boiler",
    }
    assert _issue(hass, lamp) is None
    assert lamp.state is ConfigEntryState.LOADED


async def test_issue_goes_when_the_entity_is_back(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """The switch appears again: the issue is gone."""
    boiler = _virtual(hass, "Boiler", "switch.boiler")
    await _setup(hass, boiler)
    assert _issue(hass, boiler) is not None

    hass.states.async_set("switch.boiler", "off")
    await hass.async_block_till_done()
    assert _issue(hass, boiler) is None

    hass.states.async_remove("switch.boiler")
    await hass.async_block_till_done()
    assert _issue(hass, boiler) is not None


async def test_unavailable_switch_is_no_repair(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """Unavailable is the control state's business, not a setting to fix."""
    hass.states.async_set("switch.boiler", "unavailable")
    boiler = _virtual(hass, "Boiler", "switch.boiler")
    await _setup(hass, boiler)
    assert _issue(hass, boiler) is None


async def test_disabled_entry_is_not_checked(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """A disabled device entry gives no issue."""
    boiler = _virtual(hass, "Boiler", "switch.boiler")
    await hass.config_entries.async_set_disabled_by(
        boiler.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert _issue(hass, boiler) is None


async def test_renamed_switch_is_followed(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """The owner renames the relay: the entry follows it, no issue."""
    registry = er.async_get(hass)
    relay = registry.async_get_or_create("switch", "test", "boiler_relay")
    hass.states.async_set(relay.entity_id, "off")
    boiler = _virtual(hass, "Boiler", relay.entity_id)
    await _setup(hass, boiler)

    registry.async_update_entity(relay.entity_id, new_entity_id="switch.boiler_new")
    hass.states.async_set("switch.boiler_new", "off")
    await hass.async_block_till_done()

    assert boiler.data[CONF_SWITCH_ENTITY_ID] == "switch.boiler_new"
    assert boiler.state is ConfigEntryState.LOADED
    assert _issue(hass, boiler) is None


async def _fix(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    assert await async_setup_component(hass, "repairs", {})
    return await repairs_flow_manager(hass).async_init(
        DOMAIN, data={"issue_id": f"missing_entity_{entry.entry_id}"}
    )


async def test_fix_a_virtual_device(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """'Fix' picks another switch; the entry reloads; the issue is gone."""
    boiler = _virtual(hass, "Boiler", "switch.boiler")
    await _setup(hass, boiler)
    hass.states.async_set("switch.boiler_2", "off")

    result = await _fix(hass, boiler)
    assert result["step_id"] == "virtual_relay"
    result = await repairs_flow_manager(hass).async_configure(
        result["flow_id"],
        {
            CONF_SWITCH_ENTITY_ID: "switch.boiler_2",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.boiler_power",
            CONF_RESET_POWER_MEMORY: False,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert boiler.data[CONF_SWITCH_ENTITY_ID] == "switch.boiler_2"
    assert boiler.state is ConfigEntryState.LOADED
    assert _issue(hass, boiler) is None


async def test_fix_an_extended_device_on_another_device(
    hass: HomeAssistant,
    bestiarium_mode_entry: MockConfigEntry,  # noqa: ARG001
) -> None:
    """The SmartThings device is gone: 'Fix' re-points to the Matter one."""
    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    devices, registry = dr.async_get(hass), er.async_get(hass)
    matter = devices.async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("test", "matter")}
    )
    relay = registry.async_get_or_create(
        "switch", "test", "m_relay", device_id=matter.id
    )
    power = registry.async_get_or_create(
        "sensor",
        "test",
        "m_power",
        device_id=matter.id,
        original_device_class="power",
        unit_of_measurement="W",
    )
    hass.states.async_set(relay.entity_id, "off")
    hass.states.async_set(power.entity_id, "0", POWER)
    boiler = MockConfigEntry(
        domain=DOMAIN,
        title="Boiler",
        minor_version=3,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
            CONF_DEVICE_ID: "gone_smartthings_device",
            CONF_DEVICE_NAME: "Boiler",
            CONF_SWITCH_ENTITY_ID: "switch.st_boiler",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.st_boiler_power",
            CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
        },
    )
    boiler.add_to_hass(hass)
    await _setup(hass, boiler)
    assert _issue(hass, boiler) is not None

    result = await _fix(hass, boiler)
    assert result["step_id"] == "pick_device"
    result = await repairs_flow_manager(hass).async_configure(
        result["flow_id"], {CONF_DEVICE_ID: matter.id, CONF_RESET_POWER_MEMORY: False}
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert boiler.data[CONF_DEVICE_ID] == matter.id
    assert boiler.data[CONF_SWITCH_ENTITY_ID] == relay.entity_id
    assert boiler.data[CONF_POWER_SENSOR_ENTITY_ID] == power.entity_id
    assert boiler.data[CONF_DEVICE_NAME] == "Boiler"
    assert _issue(hass, boiler) is None
