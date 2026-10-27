"""
The modes seen by the devices (DEV-008 stage B).

A renamed mode keeps the devices' priorities; after an HA restart the mode
is applied again and tells what it turned off; without the mode select the
devices keep their own default priority and Repairs says so.
"""

# ruff: noqa: S101

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import CoreState, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    DOMAIN_BESTIARIUM_MODE,
    EVENT_MODE_APPLIED,
    ISSUE_NO_MODE_SELECT,
    LOAD_PRIORITY_ALWAYS_ON,
    LOAD_PRIORITY_OFF,
    MODE_SELECT_UNIQUE_ID,
)

from .conftest import FakeRelays

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

PRIORITIES = {CONF_DEFAULT_PRIORITY: 5000, "Eco": LOAD_PRIORITY_OFF, "Boost": 9000}


async def _modes(hass: HomeAssistant, *names: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN_BESTIARIUM_MODE,
        unique_id="bestiarium_mode",
        data={
            "options": [{"name": name} for name in names],
            "ac_load_limits": {
                "ac_power_sensor": "sensor.grid",
                "ac_load_limit_w": 5000,
                "ac_yellow_zone_percent": 75,
                "ac_red_zone_percent": 90,
            },
        },
    )
    entry.add_to_hass(hass)
    return entry


def _device(
    hass: HomeAssistant, switch: str, priorities: dict[str, int] = PRIORITIES
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=switch,
        minor_version=4,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: switch,
            CONF_SWITCH_ENTITY_ID: switch,
            CONF_POWER_SENSOR_ENTITY_ID: f"sensor.{switch.split('.')[1]}_power",
            CONF_LOAD_PRIORITIES: priorities,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_renamed_mode_keeps_the_priorities(
    hass: HomeAssistant, relays: FakeRelays
) -> None:
    """Eco renamed Economy, Boost removed: the keys follow in every device."""
    modes = await _modes(hass, "Eco", "Boost")
    relays.add("switch.boiler", on=False)
    boiler = _device(hass, "switch.boiler")
    assert await hass.config_entries.async_setup(modes.entry_id)
    assert await hass.config_entries.async_setup(boiler.entry_id)
    await hass.async_block_till_done()

    flow = hass.config_entries.flow
    result = await flow.async_init(
        DOMAIN_BESTIARIUM_MODE,
        context={"source": SOURCE_RECONFIGURE, "entry_id": modes.entry_id},
    )
    result = await flow.async_configure(result["flow_id"], {"next_step_id": "edit_0"})
    result = await flow.async_configure(result["flow_id"], {"name": "Economy"})
    result = await flow.async_configure(result["flow_id"], {"next_step_id": "edit_1"})
    result = await flow.async_configure(
        result["flow_id"], {"name": "Boost", "delete": True}
    )
    result = await flow.async_configure(result["flow_id"], {"next_step_id": "complete"})
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert boiler.data[CONF_LOAD_PRIORITIES] == {
        CONF_DEFAULT_PRIORITY: 5000,
        "Economy": LOAD_PRIORITY_OFF,
    }


async def _start_with_mode(
    hass: HomeAssistant, relays: FakeRelays, *, on: bool
) -> list:
    """HA starts with Eco selected; the boiler, off in Eco, is on or off."""
    hass.set_state(CoreState.not_running)
    modes = await _modes(hass, "Eco", "Boost")
    relays.add("switch.boiler", on=on)
    boiler = _device(hass, "switch.boiler")
    relays.add("switch.fridge", on=True)
    _device(hass, "switch.fridge", {CONF_DEFAULT_PRIORITY: LOAD_PRIORITY_ALWAYS_ON})
    applied: list[dict] = []

    @callback
    def record(event: Event) -> None:
        applied.append(dict(event.data))

    hass.bus.async_listen(EVENT_MODE_APPLIED, record)
    assert await hass.config_entries.async_setup(modes.entry_id)
    await hass.async_block_till_done()
    select = er.async_get(hass).async_get_entity_id(
        "select", DOMAIN_BESTIARIUM_MODE, MODE_SELECT_UNIQUE_ID
    )
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": "Eco"}, blocking=True
    )
    assert await hass.config_entries.async_setup(boiler.entry_id)
    await hass.async_block_till_done()
    await hass.async_start()
    await hass.async_block_till_done()
    return applied


async def test_mode_applied_again_after_a_restart(
    hass: HomeAssistant, relays: FakeRelays
) -> None:
    """The owner's choice (b): a restart turns off what the mode says off, and tells."""
    applied = await _start_with_mode(hass, relays, on=True)

    assert relays.is_on("switch.boiler") is False
    restart = [event for event in applied if event["reason"] == "restart"]
    assert len(restart) == 1
    assert restart[0]["mode"] == "Eco"
    assert len(restart[0]["devices_turned_off"]) == 1
    # The fridge, always on, was on: it is not told as turned on
    assert restart[0]["devices_turned_on"] == []


async def test_restart_tells_nothing_when_nothing_was_on(
    hass: HomeAssistant, relays: FakeRelays
) -> None:
    """No device was on: no restart event, no command."""
    applied = await _start_with_mode(hass, relays, on=False)

    assert [event for event in applied if event["reason"] == "restart"] == []
    assert ("turn_off", "switch.boiler") not in relays.calls
    assert hass.states.get("switch.boiler").state == STATE_OFF


async def test_no_mode_select_is_a_repair(
    hass: HomeAssistant, relays: FakeRelays
) -> None:
    """Without the select: own default priority, and Repairs until it is back."""
    relays.add("switch.boiler", on=True)
    boiler = _device(hass, "switch.boiler")
    assert await hass.config_entries.async_setup(boiler.entry_id)
    await hass.async_block_till_done()
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, ISSUE_NO_MODE_SELECT) is not None

    er.async_get(hass).async_get_or_create(
        "select", DOMAIN_BESTIARIUM_MODE, MODE_SELECT_UNIQUE_ID
    )
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, ISSUE_NO_MODE_SELECT) is None
    assert hass.states.get("switch.boiler").state == STATE_ON


async def test_the_current_mode_renamed_stays_current(
    hass: HomeAssistant, relays: FakeRelays
) -> None:
    """Review 13, 6: Boost (not the first) renamed Turbo: the home stays in it."""
    modes = await _modes(hass, "Eco", "Boost")
    relays.add("switch.boiler", on=False)
    _device(hass, "switch.boiler")
    assert await hass.config_entries.async_setup(modes.entry_id)
    await hass.async_block_till_done()
    select = er.async_get(hass).async_get_entity_id(
        "select", DOMAIN_BESTIARIUM_MODE, MODE_SELECT_UNIQUE_ID
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select, "option": "Boost"},
        blocking=True,
    )

    flow = hass.config_entries.flow
    result = await flow.async_init(
        DOMAIN_BESTIARIUM_MODE,
        context={"source": SOURCE_RECONFIGURE, "entry_id": modes.entry_id},
    )
    result = await flow.async_configure(result["flow_id"], {"next_step_id": "edit_1"})
    result = await flow.async_configure(result["flow_id"], {"name": "Turbo"})
    applied: list[dict] = []
    hass.bus.async_listen(
        EVENT_MODE_APPLIED, callback(lambda event: applied.append(event.data))
    )
    relays.calls.clear()
    result = await flow.async_configure(result["flow_id"], {"next_step_id": "complete"})
    await hass.async_block_till_done()

    assert hass.states.get(select).state == "Turbo"
    # Review 17, B4: a rename is no change of mode: nothing is applied
    assert applied == []
    assert relays.calls == []


async def test_a_mode_name_is_unique(hass: HomeAssistant) -> None:
    """Two modes of one name would merge the devices' priorities: refused."""
    modes = await _modes(hass, "Eco", "Boost")
    assert await hass.config_entries.async_setup(modes.entry_id)
    flow = hass.config_entries.flow
    result = await flow.async_init(
        DOMAIN_BESTIARIUM_MODE,
        context={"source": SOURCE_RECONFIGURE, "entry_id": modes.entry_id},
    )
    edit = await flow.async_configure(result["flow_id"], {"next_step_id": "edit_1"})
    edit = await flow.async_configure(edit["flow_id"], {"name": "Eco"})
    assert edit["errors"] == {"name": "name_exists"}


async def test_an_unknown_relay_gets_the_mode_after_a_restart(
    hass: HomeAssistant, relays: FakeRelays
) -> None:
    """Review 13, 7: a cloud relay still unknown at the start gets the turn-off."""
    hass.set_state(CoreState.not_running)
    modes = await _modes(hass, "Eco", "Boost")
    relays.add("switch.boiler", on=True)
    hass.states.async_set("switch.boiler", "unknown")
    boiler = _device(hass, "switch.boiler")
    assert await hass.config_entries.async_setup(modes.entry_id)
    await hass.async_block_till_done()
    select = er.async_get(hass).async_get_entity_id(
        "select", DOMAIN_BESTIARIUM_MODE, MODE_SELECT_UNIQUE_ID
    )
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": "Eco"}, blocking=True
    )
    assert await hass.config_entries.async_setup(boiler.entry_id)
    await hass.async_start()
    await hass.async_block_till_done()

    assert ("turn_off", "switch.boiler") in relays.calls
