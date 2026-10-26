"""The virtual switch: script sequences, the thermostats' mode, power with tolerance."""

# ruff: noqa: S101

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryDisabler,
    ConfigSubentryData,
)
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, ServiceCall, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.bestiarium_climate.const import (
    CONF_CAPACITY,
    CONF_MIN_WORKING_TIME,
    CONF_RECOVERY_TIMEOUT,
    DOMAIN,
    SUBENTRY_TYPE_SWITCH,
    SUBENTRY_TYPE_THERMOSTAT,
    ClimateMode,
)
from custom_components.bestiarium_climate.control import async_get_thermostat_memory
from custom_components.bestiarium_climate.devices import (
    expected_power,
    power_cut,
    power_device,
)
from custom_components.bestiarium_climate.repairs import (
    ActionsRepairFlow,
    SharedSwitchRepairFlow,
    ThermostatSwitchRepairFlow,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SWITCH = "switch.floor"
POWER = "sensor.floor_power"
POWER_B = "sensor.floor_b_power"
SWITCH_B = "switch.floor_b"
RELAY = "switch.ac_relay"
DELAY = 10
TOLERANCE = 60

Setup = Callable[..., Awaitable[ConfigEntry]]


def _switch(**data: Any) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_id="switch_01",
        subentry_type=SUBENTRY_TYPE_SWITCH,
        title="Floor",
        unique_id=None,
        data={
            "name": "Floor",
            "power_entity": POWER,
            "min_power": 50.0,
            "power_hysteresis_percent": 20,
            "power_delay_seconds": DELAY,
            "power_tolerance_seconds": TOLERANCE,
            "heating_action": [{"action": "test.valve"}, {"action": "test.heat"}],
            "cooling_action": [{"action": "test.cool"}],
            "off_action": [{"action": "test.off"}],
            **data,
        },
    )


def _thermostat(name: str, mode: ClimateMode) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_id=name,
        subentry_type=SUBENTRY_TYPE_THERMOSTAT,
        title=name,
        unique_id=None,
        data={
            "name": name,
            "temperature_sensor": "sensor.temp",
            "target_switch": SWITCH,
            "thermostat_mode": mode,
            "cold_tolerance": 0.3,
            "hot_tolerance": 0.3,
        },
    )


@pytest.fixture
async def setup(hass: HomeAssistant) -> AsyncGenerator[Setup]:
    """Set up an entry with the given subentries; unload it at the end."""
    entries: list[MockConfigEntry] = []

    async def _setup(
        *subentries: ConfigSubentryData, power: str = "0", minor_version: int = 2
    ) -> ConfigEntry:
        hass.states.async_set(POWER, power)
        entry = MockConfigEntry(
            domain=DOMAIN,
            minor_version=minor_version,
            data={
                CONF_CAPACITY: 2,
                CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 20, "seconds": 0},
                CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
            },
            subentries_data=list(subentries),
        )
        entry.add_to_hass(hass)
        entries.append(entry)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    yield _setup
    for entry in entries:
        if hass.config_entries.async_get_entry(entry.entry_id):
            await hass.config_entries.async_unload(entry.entry_id)


@pytest.fixture
def services(hass: HomeAssistant) -> dict[str, list[ServiceCall]]:
    """Record the calls of the switch's test actions."""
    return {
        name: async_mock_service(hass, "test", name)
        for name in ("valve", "heat", "cool", "off")
    }


async def _turn(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(
        "switch", service, {"entity_id": SWITCH}, blocking=True
    )


def _state(hass: HomeAssistant) -> str:
    return hass.states.get(SWITCH).state


def _later(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))


async def test_on_runs_the_whole_sequence(
    hass: HomeAssistant, setup: Setup, services: dict[str, list[ServiceCall]]
) -> None:
    """Every action of the heating sequence runs, not only the first."""
    await setup(_switch())

    await _turn(hass, "turn_on")

    assert len(services["valve"]) == 1
    assert len(services["heat"]) == 1
    assert services["cool"] == []


async def test_mode_comes_from_the_thermostat(
    hass: HomeAssistant, setup: Setup, services: dict[str, list[ServiceCall]]
) -> None:
    """A cooling thermostat makes "on" run the cooling sequence."""
    await setup(_switch(), _thermostat("Cooler", ClimateMode.COOLING))

    await _turn(hass, "turn_on")

    assert len(services["cool"]) == 1
    assert services["heat"] == []


async def test_without_a_thermostat_its_only_action(
    hass: HomeAssistant, setup: Setup, services: dict[str, list[ServiceCall]]
) -> None:
    """No thermostat: heating if it has a heating action, else cooling."""
    await setup(_switch(heating_action=None))

    await _turn(hass, "turn_on")

    assert len(services["cool"]) == 1


async def test_thermostats_of_both_modes_refuse(
    hass: HomeAssistant, setup: Setup, services: dict[str, list[ServiceCall]]
) -> None:
    """Thermostats with different modes on one switch: "on" fails, nothing runs."""
    await setup(
        _switch(),
        _thermostat("Heater", ClimateMode.HEATING),
        _thermostat("Cooler", ClimateMode.COOLING),
    )

    with pytest.raises(HomeAssistantError):
        await _turn(hass, "turn_on")

    assert services["heat"] == services["cool"] == []


async def test_off_stops_a_running_sequence(
    hass: HomeAssistant, setup: Setup, services: dict[str, list[ServiceCall]]
) -> None:
    """A heating sequence still in its delay stops when the switch is turned off."""
    await setup(
        _switch(
            heating_action=[
                {"action": "test.started"},
                {"delay": {"seconds": 60}},
                {"action": "test.heat"},
            ]
        )
    )
    started = asyncio.Event()
    hass.services.async_register("test", "started", lambda _call: started.set())
    turning_on = hass.async_create_task(_turn(hass, "turn_on"))
    await started.wait()

    await _turn(hass, "turn_off")
    await turning_on

    assert len(services["off"]) == 1
    assert services["heat"] == []


async def test_hysteresis_and_delay(hass: HomeAssistant, setup: Setup) -> None:
    """On from 50 W; off only below 40 W (20 %) and after the delay."""
    await setup(_switch(), power="100")
    assert _state(hass) == STATE_ON

    hass.states.async_set(POWER, "45")
    _later(hass, DELAY + 1)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_ON

    hass.states.async_set(POWER, "30")
    await hass.async_block_till_done()
    assert _state(hass) == STATE_ON
    _later(hass, DELAY + 1)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_OFF

    hass.states.async_set(POWER, "45")
    _later(hass, 2 * DELAY + 2)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_OFF


async def test_a_short_glitch_is_not_shown(hass: HomeAssistant, setup: Setup) -> None:
    """A drop shorter than the delay leaves the switch on."""
    await setup(_switch(), power="100")

    hass.states.async_set(POWER, "0")
    _later(hass, DELAY / 2)
    await hass.async_block_till_done()
    hass.states.async_set(POWER, "100")
    _later(hass, 2 * DELAY)
    await hass.async_block_till_done()

    assert _state(hass) == STATE_ON


async def test_no_power_data_after_the_tolerance(
    hass: HomeAssistant, setup: Setup
) -> None:
    """Power data gone: unavailable after the tolerance (60 s), not the delay; back at once."""
    await setup(_switch(), power="100")

    hass.states.async_set(POWER, STATE_UNAVAILABLE)
    _later(hass, DELAY + 1)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_ON
    _later(hass, TOLERANCE + 1)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_UNAVAILABLE

    hass.states.async_set(POWER, "100")
    await hass.async_block_till_done()
    assert _state(hass) == STATE_ON


async def test_migration_removes_the_switch_mode(setup: Setup) -> None:
    """1.2: the switch's own mode field goes; the rest stays."""
    entry = await setup(_switch(mode=ClimateMode.COOLING), minor_version=1)

    subentry = entry.subentries["switch_01"]
    assert entry.minor_version == 2
    assert "mode" not in subentry.data
    assert subentry.data["min_power"] == 50.0


async def test_a_failing_sequence_fails_the_command(
    hass: HomeAssistant, setup: Setup
) -> None:
    """Whatever breaks in the sequence comes out as a HomeAssistantError."""

    def boom(_call: ServiceCall) -> None:
        msg = "broken"
        raise ValueError(msg)

    hass.services.async_register("test", "boom", boom)
    await setup(_switch(heating_action=[{"action": "test.boom"}]))

    with pytest.raises(HomeAssistantError) as err:
        await _turn(hass, "turn_on")

    assert err.value.translation_key == "action_failed"


async def test_an_invalid_saved_action_is_a_repair(
    hass: HomeAssistant, setup: Setup
) -> None:
    """The bad switch is left out with a fixable issue; its fix saves the actions."""
    entry = await setup(_switch(heating_action=[{"nonsense": 1}]))

    assert hass.states.get(SWITCH) is None
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "invalid_action_switch_01")
    assert issue is not None
    assert issue.is_fixable

    flow = ActionsRepairFlow(issue.data)
    flow.hass = hass
    result = await flow.async_step_init(
        {
            "heating_action": [{"action": "test.heat"}],
            "off_action": [{"action": "test.off"}],
        }
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert entry.subentries["switch_01"].data["heating_action"] == [
        {"action": "test.heat"}
    ]
    assert hass.states.get(SWITCH) is not None
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, "invalid_action_switch_01") is None
    )


async def test_a_shared_switch_is_a_repair(
    hass: HomeAssistant, setup: Setup, services: dict[str, list[ServiceCall]]
) -> None:
    """Two thermostats on one switch (an old setting): Repairs, the switch idle."""
    await setup(
        _switch(),
        _thermostat("Heater", ClimateMode.HEATING),
        _thermostat("Second", ClimateMode.HEATING),
    )

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"switch_shared_{SWITCH}")
    with pytest.raises(HomeAssistantError):
        await _turn(hass, "turn_on")
    assert services["heat"] == []


async def test_a_cooling_switch_that_now_heats_is_a_repair(
    hass: HomeAssistant, setup: Setup
) -> None:
    """1.2: cooling, a heating action, no thermostat: no silent change."""
    await setup(_switch(mode=ClimateMode.COOLING), minor_version=1)

    assert ir.async_get(hass).async_get_issue(DOMAIN, "switch_now_heats_switch_01")


async def test_the_options_do_not_reload_the_entry(
    hass: HomeAssistant, setup: Setup
) -> None:
    """The control settings are read on use: changing them reloads nothing."""
    entry = await setup(_switch())
    coordinator = hass.data[DOMAIN][entry.entry_id]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"attempts": 3, "tolerance_seconds": 30, "hysteresis_seconds": 30},
    )
    await hass.async_block_till_done()

    assert hass.data[DOMAIN][entry.entry_id] is coordinator


async def test_a_shared_switch_is_fixed_in_its_form(
    hass: HomeAssistant, setup: Setup, services: dict[str, list[ServiceCall]]
) -> None:
    """B7: the fix gives the second thermostat its own switch; nothing ran meanwhile."""
    hass.states.async_set("switch.other", "off")
    entry = await setup(
        _switch(),
        _thermostat("Heater", ClimateMode.HEATING),
        _thermostat("Second", ClimateMode.HEATING),
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"switch_shared_{SWITCH}")
    assert issue.is_fixable
    assert services["heat"] == services["off"] == []

    flow = SharedSwitchRepairFlow(issue.data)
    flow.hass = hass
    refused = await flow.async_step_init({"Second": SWITCH})
    assert refused["errors"] == {"Second": "switch_taken"}
    result = await flow.async_step_init({"Second": "switch.other"})
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert entry.subentries["Second"].data["target_switch"] == "switch.other"
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"switch_shared_{SWITCH}") is None


async def test_a_cooling_switch_that_now_heats_is_fixed_in_its_form(
    hass: HomeAssistant, setup: Setup
) -> None:
    """B7: the fix removes the heating action; the switch cools; the issue goes."""
    entry = await setup(_switch(mode=ClimateMode.COOLING), minor_version=1)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "switch_now_heats_switch_01")
    assert issue.is_fixable

    flow = ActionsRepairFlow(issue.data)
    flow.hass = hass
    await flow.async_step_init(
        {
            "cooling_action": [{"action": "test.cool"}],
            "off_action": [{"action": "test.off"}],
        }
    )
    await hass.async_block_till_done()

    assert "heating_action" not in entry.subentries["switch_01"].data
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, "switch_now_heats_switch_01") is None
    )


async def _relay_powers_the_switch(hass: HomeAssistant, *, on: bool) -> None:
    """Add the Bestiarium device whose power sensor the virtual switch reads."""
    hass.states.async_set(RELAY, STATE_ON if on else STATE_OFF)
    MockConfigEntry(
        domain="bestiarium_devices",
        title="AC power",
        data={"switch_entity_id": RELAY, "power_sensor_entity_id": POWER},
        disabled_by=ConfigEntryDisabler.USER,
    ).add_to_hass(hass)


def _control_state(hass: HomeAssistant) -> State:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "Heater_control_state"
    )
    return hass.states.get(entity_id)


async def _heat(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.temp", "15")
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.heater", "hvac_mode": "heat"},
        blocking=True,
    )
    await hass.async_block_till_done()


async def test_no_start_while_the_power_is_cut(
    hass: HomeAssistant, setup: Setup, services: dict[str, list[ServiceCall]]
) -> None:
    """Decision 28: the device powering the AC is off: "No power", no command; back."""
    await _relay_powers_the_switch(hass, on=False)
    await setup(_switch(), _thermostat("Heater", ClimateMode.HEATING))
    await _heat(hass)
    _later(hass, 3 * 60)
    await hass.async_block_till_done()

    assert services["heat"] == []
    state = _control_state(hass)
    assert state.state == "no_power"
    assert state.attributes["failed_attempts"] == 0

    hass.states.async_set(RELAY, STATE_ON)
    _later(hass, 4 * 60)
    await hass.async_block_till_done()
    assert len(services["heat"]) == 1
    assert _control_state(hass).state == "normal"


async def test_a_power_cut_is_no_failed_attempt(
    hass: HomeAssistant, setup: Setup, services: dict[str, list[ServiceCall]]
) -> None:
    """Review 18: the AC heats, its power is cut (0 W): no attempt, no "unresponsive"."""
    await _relay_powers_the_switch(hass, on=True)
    await setup(_switch(), _thermostat("Heater", ClimateMode.HEATING))
    await _heat(hass)
    assert len(services["heat"]) == 1
    hass.states.async_set(POWER, "1000")  # the AC runs
    _later(hass, DELAY + 1)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_ON

    hass.states.async_set(RELAY, STATE_OFF)
    hass.states.async_set(POWER, "0")
    for minute in range(1, 11):
        _later(hass, minute * 60)
        await hass.async_block_till_done()

    state = _control_state(hass)
    assert state.state == "no_power"
    assert state.attributes["failed_attempts"] == 0
    assert len(services["heat"]) == 1


async def test_a_device_relay_is_no_thermostat_switch(
    hass: HomeAssistant, setup: Setup
) -> None:
    """Review 18: one relay lives in one integration: the form refuses it."""
    hass.states.async_set(RELAY, STATE_OFF)
    MockConfigEntry(
        domain="bestiarium_devices",
        title="Floor 1",
        data={"switch_entity_id": RELAY},
        disabled_by=ConfigEntryDisabler.USER,
    ).add_to_hass(hass)
    entry = await setup()
    flow = hass.config_entries.subentries
    result = await flow.async_init(
        (entry.entry_id, SUBENTRY_TYPE_THERMOSTAT), context={"source": "user"}
    )
    result = await flow.async_configure(
        result["flow_id"],
        {
            "name": "Room",
            "temperature_sensor": "sensor.temp",
            "target_switch": RELAY,
            "thermostat_mode": ClimateMode.HEATING,
            "cold_tolerance": 0.3,
            "hot_tolerance": 0.3,
        },
    )

    assert result["errors"] == {"target_switch": "switch_is_device"}
    assert result["description_placeholders"] == {"device": "Floor 1"}


async def test_an_old_thermostat_on_a_device_relay_is_a_repair(
    hass: HomeAssistant, setup: Setup
) -> None:
    """Review 18: an existing one: a fixable issue, left alone, fixed in its form."""
    hass.states.async_set(RELAY, STATE_OFF)
    hass.states.async_set("switch.own", STATE_OFF)
    MockConfigEntry(
        domain="bestiarium_devices",
        title="Floor 1",
        data={"switch_entity_id": RELAY},
        disabled_by=ConfigEntryDisabler.USER,
    ).add_to_hass(hass)
    thermostat = _thermostat("Heater", ClimateMode.HEATING)
    thermostat["data"] = {**thermostat["data"], "target_switch": RELAY}
    entry = await setup(thermostat)
    await _heat(hass)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, "switch_is_device_Heater")
    assert issue is not None
    assert issue.is_fixable
    flow = ThermostatSwitchRepairFlow(issue.data)
    flow.hass = hass
    result = await flow.async_step_init({"target_switch": "switch.own"})
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert entry.subentries["Heater"].data["target_switch"] == "switch.own"
    assert ir.async_get(hass).async_get_issue(DOMAIN, "switch_is_device_Heater") is None


def _device(
    hass: HomeAssistant, title: str, *, power: str = POWER, relay: str = RELAY
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain="bestiarium_devices",
        title=title,
        data={"switch_entity_id": relay, "power_sensor_entity_id": power},
        disabled_by=ConfigEntryDisabler.USER,
    )
    entry.add_to_hass(hass)
    return entry


async def test_the_power_comes_back_and_it_starts_at_once(
    hass: HomeAssistant, setup: Setup, services: dict[str, list[ServiceCall]]
) -> None:
    """C1: the relay of its power back on: a cycle at once, not in a minute."""
    await _relay_powers_the_switch(hass, on=False)
    await setup(_switch(), _thermostat("Heater", ClimateMode.HEATING))
    await _heat(hass)
    assert services["heat"] == []

    hass.states.async_set(RELAY, STATE_ON)
    await hass.async_block_till_done()

    assert len(services["heat"]) == 1


async def test_two_devices_on_one_power_sensor_are_no_power(
    hass: HomeAssistant, setup: Setup
) -> None:
    """C4: the power device only if it is the one device with that sensor."""
    await setup(_switch())
    one = _device(hass, "AC power")
    assert power_device(hass, SWITCH) is one

    _device(hass, "Another", relay="switch.other")
    assert power_device(hass, SWITCH) is None
    assert not power_cut(hass, SWITCH)


async def test_the_large_default_of_devices_is_no_power(
    hass: HomeAssistant, setup: Setup
) -> None:
    """C8: the devices' 2000 W default is no knowledge: the thermostat's counts."""
    await setup(_switch())
    device = _device(hass, "AC power")
    er.async_get(hass).async_get_or_create(
        "sensor",
        "bestiarium_devices",
        f"{device.entry_id}_expected_power",
        suggested_object_id="ac_power_expected_power",
    )
    hass.states.async_set(
        "sensor.ac_power_expected_power",
        "2000",
        {"estimate_source": "large_device_default"},
    )
    assert expected_power(hass, device) is None

    hass.states.async_set(
        "sensor.ac_power_expected_power", "1150", {"estimate_source": "memory"}
    )
    assert expected_power(hass, device) == 1150.0


async def test_the_repairs_of_a_removed_thermostat_go(
    hass: HomeAssistant, setup: Setup
) -> None:
    """C6: the thermostat on a device relay removed: its issue goes with it."""
    hass.states.async_set(RELAY, STATE_OFF)
    _device(hass, "Floor 1", power="sensor.floor_1_power")
    thermostat = _thermostat("Heater", ClimateMode.HEATING)
    thermostat["data"] = {**thermostat["data"], "target_switch": RELAY}
    entry = await setup(thermostat)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "switch_is_device_Heater")

    hass.config_entries.async_remove_subentry(entry, "Heater")
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, "switch_is_device_Heater") is None


async def test_the_repairs_of_a_removed_entry_go(
    hass: HomeAssistant, setup: Setup
) -> None:
    """C6: the climate entry removed: its issues go."""
    entry = await setup(_switch(heating_action=[{"nonsense": 1}]))
    assert ir.async_get(hass).async_get_issue(DOMAIN, "invalid_action_switch_01")

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, "invalid_action_switch_01") is None
    )


def _second_switch(**data: Any) -> ConfigSubentryData:
    return ConfigSubentryData(
        subentry_id="switch_02",
        subentry_type=SUBENTRY_TYPE_SWITCH,
        title="Floor B",
        unique_id=None,
        data={**_switch()["data"], "name": "Floor B", "power_entity": POWER_B, **data},
    )


def _second_thermostat() -> ConfigSubentryData:
    thermostat = _thermostat("Second", ClimateMode.HEATING)
    return ConfigSubentryData(
        **{**thermostat, "data": {**thermostat["data"], "target_switch": SWITCH_B}}
    )


async def test_a_reload_during_a_long_sequence_turns_nothing_off(
    hass: HomeAssistant, setup: Setup, services: dict[str, list[ServiceCall]]
) -> None:
    """D2: A starts a long "on", B heats; the entry reloads: B gets no "off"."""
    started = asyncio.Event()
    hass.services.async_register("test", "started", lambda _call: started.set())
    hass.states.async_set(POWER_B, "1000")
    hass.states.async_set("sensor.temp", "15")
    entry = await setup(
        _switch(
            heating_action=[
                {"action": "test.started"},
                {"delay": {"seconds": 60}},
                {"action": "test.heat"},
            ]
        ),
        _second_switch(),
        _thermostat("Heater", ClimateMode.HEATING),
        _second_thermostat(),
    )
    for name in ("climate.heater", "climate.second"):
        await hass.services.async_call(
            "climate", "set_hvac_mode", {"entity_id": name, "hvac_mode": "heat"}
        )
    await started.wait()
    # B was on before its thermostat heated: that "off" came at the start
    offs = list(services["off"])
    assert hass.states.get(SWITCH_B).state == STATE_ON
    memory = await async_get_thermostat_memory(hass)
    assert memory.get("Second")["expected_on"] is True

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert services["off"] == offs
    assert memory.get("Second")["expected_on"] is True


async def test_a_short_glitch_of_the_power_relay_commands_nothing(
    hass: HomeAssistant, setup: Setup, services: dict[str, list[ServiceCall]]
) -> None:
    """D5: the cloud relay of a running AC "off" for a moment: no command."""
    await _relay_powers_the_switch(hass, on=True)
    await setup(_switch(), _thermostat("Heater", ClimateMode.HEATING))
    await _heat(hass)
    hass.states.async_set(POWER, "1000")
    _later(hass, DELAY + 1)
    await hass.async_block_till_done()
    assert _state(hass) == STATE_ON

    hass.states.async_set(RELAY, STATE_OFF)
    await hass.async_block_till_done()
    hass.states.async_set(RELAY, STATE_ON)
    await hass.async_block_till_done()

    assert services["off"] == []
    assert len(services["heat"]) == 1


async def test_a_fix_of_a_gone_switch_removes_its_issue(
    hass: HomeAssistant, setup: Setup
) -> None:
    """D7: the fix finds its switch removed: it aborts and deletes its issue."""
    entry = await setup(_switch())
    ir.async_create_issue(
        hass,
        DOMAIN,
        "invalid_action_gone",
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="invalid_action",
        data={"entry_id": entry.entry_id, "subentry_id": "gone"},
    )
    assert await async_setup_component(hass, "repairs", {})
    flows = hass.data["repairs"]["flow_manager"]

    result = await flows.async_init(DOMAIN, context={"issue_id": "invalid_action_gone"})

    assert result["type"] == "abort"
    assert ir.async_get(hass).async_get_issue(DOMAIN, "invalid_action_gone") is None


async def test_a_start_cut_by_the_power_is_no_attempt(
    hass: HomeAssistant,
    setup: Setup,
    services: dict[str, list[ServiceCall]],
    freezer: FrozenDateTimeFactory,
) -> None:
    """E3: the power cut during a start: no "on" again, no failed attempt."""
    await _relay_powers_the_switch(hass, on=True)
    await setup(_switch(), _thermostat("Heater", ClimateMode.HEATING))
    await _heat(hass)
    assert len(services["heat"]) == 1
    freezer.tick(timedelta(seconds=5))
    hass.states.async_set(RELAY, STATE_OFF)  # the AC never started
    for _ in range(5):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    state = _control_state(hass)
    assert len(services["heat"]) == 1
    assert state.state == "no_power"
    assert state.attributes["failed_attempts"] == 0


async def test_a_heater_kept_on_without_power_after_a_restart(
    hass: HomeAssistant,
    setup: Setup,
    services: dict[str, list[ServiceCall]],
    hass_storage: dict,
) -> None:
    """E3: wanted on in the memory, its switch off, its power cut: no "on"."""
    hass_storage["bestiarium_climate.thermostats"] = {
        "version": 1,
        "minor_version": 2,
        "data": {
            "Heater": {
                "expected_on": True,
                "expected_changed_at": dt_util.utcnow().isoformat(),
                "series": {"on": 1},
                "unresponsive": False,
                "deferred_until": None,
            }
        },
    }
    await _relay_powers_the_switch(hass, on=False)
    await setup(_switch(), _thermostat("Heater", ClimateMode.HEATING))
    await _heat(hass)
    for minute in range(1, 6):
        _later(hass, minute * 60)
        await hass.async_block_till_done()

    state = _control_state(hass)
    assert services["heat"] == []
    assert state.state == "no_power"
    assert state.attributes["failed_attempts"] == 0


async def test_the_power_back_waits_for_the_pause_after_the_off(
    hass: HomeAssistant,
    setup: Setup,
    services: dict[str, list[ServiceCall]],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Decision 29: the AC's power cut, back a minute later: it starts after the pause."""

    async def seconds(count: int) -> None:
        for _ in range(count // 10):
            freezer.tick(timedelta(seconds=10))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()

    await _relay_powers_the_switch(hass, on=True)
    await setup(_switch(), _thermostat("Heater", ClimateMode.HEATING))
    await _heat(hass)
    hass.states.async_set(POWER, "1000")  # the AC runs
    await seconds(60)
    assert _state(hass) == STATE_ON

    hass.states.async_set(RELAY, STATE_OFF)
    hass.states.async_set(POWER, "0")
    off_at = None
    while off_at is None:  # its switch off by its 0 W
        await seconds(10)
        if _state(hass) == STATE_OFF:
            off_at = dt_util.utcnow()
    await seconds(60)
    hass.states.async_set(RELAY, STATE_ON)
    starts = len(services["heat"])
    while dt_util.utcnow() < off_at + timedelta(minutes=5) - timedelta(seconds=10):
        await seconds(10)
        assert len(services["heat"]) == starts  # in its pause
    await seconds(80)  # the pause over, the next cycle
    assert len(services["heat"]) == starts + 1


async def test_no_power_is_no_fault(
    hass: HomeAssistant,
    setup: Setup,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Decision 28: "No power" after the tolerance, below no temperature, no warning."""

    async def seconds(count: int) -> None:
        for _ in range(count // 10):
            freezer.tick(timedelta(seconds=10))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()

    await _relay_powers_the_switch(hass, on=False)
    await setup(_switch(), _thermostat("Heater", ClimateMode.HEATING))
    await _heat(hass)
    await seconds(40)
    assert _control_state(hass).state == "normal"  # within the tolerance
    await seconds(40)
    assert _control_state(hass).state == "no_power"
    assert not [
        r
        for r in caplog.records
        if r.levelname == "WARNING" and "bestiarium_climate" in r.name
    ]

    hass.states.async_set("sensor.temp", STATE_UNAVAILABLE)
    await seconds(80)
    assert _control_state(hass).state == "no_temperature"


async def test_not_responding_goes_before_no_power(
    hass: HomeAssistant, setup: Setup, hass_storage: dict
) -> None:
    """Decision 28: a heater "Not responding" whose power is cut shows the problem."""
    hass_storage["bestiarium_climate.thermostats"] = {
        "version": 1,
        "minor_version": 2,
        "data": {
            "Heater": {
                "expected_on": False,
                "expected_changed_at": dt_util.utcnow().isoformat(),
                "series": {},
                "unresponsive": True,
                "deferred_until": None,
            }
        },
    }
    await _relay_powers_the_switch(hass, on=False)
    await setup(_switch(), _thermostat("Heater", ClimateMode.HEATING))
    await _heat(hass)
    for minute in range(1, 4):
        _later(hass, minute * 60)
        await hass.async_block_till_done()

    assert _control_state(hass).state == "unresponsive"


async def test_no_power_while_the_thermostat_is_off_is_normal(
    hass: HomeAssistant, setup: Setup, freezer: FrozenDateTimeFactory
) -> None:
    """Decision 32: the thermostat off, its power off: "Normal"; heating: "No power"."""

    async def seconds(count: int) -> None:
        for _ in range(count // 10):
            freezer.tick(timedelta(seconds=10))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()

    await _relay_powers_the_switch(hass, on=False)
    hass.states.async_set("sensor.temp", "15")
    await setup(_switch(), _thermostat("Heater", ClimateMode.HEATING))
    await seconds(120)
    assert _control_state(hass).state == "normal"

    await _heat(hass)
    await seconds(80)
    assert _control_state(hass).state == "no_power"
