"""The stand script sets up the test stand through the HA APIs, repeatably."""

# ruff: noqa: S101

import shutil
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import dump
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.bestiarium_devices.const import DOMAIN as DEVICES
from custom_components.bestiarium_mock.const import DOMAIN as MOCK
from scripts.stand.ha import Registry
from scripts.stand.layout import (
    CLIMATE,
    DASHBOARD,
    SCRIPT_PREFIX,
    STAND_DEVICES,
    THERMOSTATS,
)
from scripts.stand.scenarios import scenario_scripts
from scripts.stand_setup import setup_stand

pytestmark = pytest.mark.usefixtures(
    "recorder_mock", "enable_custom_integrations", "socket_enabled"
)

DEV_CONFIG = Path(__file__).parents[2] / "config" / "configuration.yaml"
# An automation of an old test device: it no longer validates
BROKEN_AUTOMATION = {
    "id": "old_mock_click",
    "alias": "Turn off mock devices on click",
    "triggers": [
        {
            "trigger": "device",
            "device_id": "gone",
            "domain": "switch",
            "entity_id": "switch.mock_socket_3_switch",
            "type": "turned_on",
        }
    ],
    "actions": [],
}
# A broken automation of something else: the stand script leaves it alone
FOREIGN_AUTOMATION = {
    **BROKEN_AUTOMATION,
    "id": "kitchen_click",
    "alias": "Kitchen light on click",
    "triggers": [
        {**BROKEN_AUTOMATION["triggers"][0], "entity_id": "switch.kitchen_light"}
    ],
}
MODE_ON_OLD_GRID_ID = {
    "options": [{"name": "Grid on"}],
    "ac_load_limits": {
        "ac_power_sensor": "sensor.mock_grid_grid_power",
        "ac_load_limit_w": 5000,
        "ac_yellow_zone_percent": 75,
        "ac_red_zone_percent": 90,
        "ac_overload_recovery_seconds": 30,
    },
}


async def _dev_ha(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, config_dir: Path
) -> str:
    """Run HA on the development configuration; return its URL."""
    hass.config.config_dir = str(config_dir)
    shutil.copy(DEV_CONFIG, config_dir / "configuration.yaml")
    automations = [BROKEN_AUTOMATION, FOREIGN_AUTOMATION]
    (config_dir / "automations.yaml").write_text(dump(automations))
    (config_dir / "scripts.yaml").write_text("")
    for component in ("api", "websocket_api", "config", "lovelace", "script"):
        assert await async_setup_component(hass, component, {})
    assert await async_setup_component(hass, "automation", {"automation": automations})
    client = await hass_client()
    return str(client.make_url("")).rstrip("/")


async def _rename_a_test_device(hass: HomeAssistant, url: str, token: str) -> None:
    """Rename the boiler and move it to an area, as on the owner's stand."""
    await setup_stand(url, token)
    devices = dr.async_get(hass)
    boiler = devices.async_get_device_by_identifier(
        (MOCK, "mock_boiler"), hass.config_entries.async_entries(MOCK)[0].entry_id
    )
    kitchen = ar.async_get(hass).async_create("Kitchen")
    devices.async_update_device(
        boiler.id, name_by_user="Mock Boiler 1", area_id=kitchen.id
    )
    entities = er.async_get(hass)
    power = entities.async_get_entity_id("sensor", MOCK, "mock_boiler_power")
    entities.async_update_entity(power, new_entity_id="sensor.none_power")
    await hass.async_block_till_done()


def _device_name(hass: HomeAssistant, mock_id: str) -> str:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (MOCK, f"mock_{mock_id}"), hass.config_entries.async_entries(MOCK)[0].entry_id
    )
    return device.name


def _zone_states(hass: HomeAssistant) -> set[str]:
    entities = er.async_get(hass)
    return {
        hass.states.get(
            entities.async_get_entity_id(
                "binary_sensor", "bestiarium_mode", f"power_load_{zone}"
            )
        ).state
        for zone in ("green", "yellow", "red", "overload")
    }


async def test_stand_setup_is_repeatable(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    hass_access_token: str,
    tmp_path: Path,
) -> None:
    """Twice in a row: the same entries, scripts, entity_ids and dashboard."""
    url = await _dev_ha(hass, hass_client, tmp_path)
    await _rename_a_test_device(hass, url, hass_access_token)

    await setup_stand(url, hass_access_token)
    await hass.async_block_till_done()

    assert [entry.title for entry in hass.config_entries.async_entries(DEVICES)] == [
        _device_name(hass, device.mock_id) for device in STAND_DEVICES
    ]
    climate = hass.config_entries.async_entries("bestiarium_climate")
    assert len(climate) == 1
    # Each thermostat runs an air conditioner through its own virtual switch,
    # whose power socket is the "Allowed" of the AC socket's device (DEV-010)
    assert len(climate[0].subentries) == 2 * len(THERMOSTATS)
    sockets = [
        subentry.data["power_socket"]
        for subentry in climate[0].subentries.values()
        if subentry.subentry_type == "switch"
    ]
    entities = er.async_get(hass)
    assert all(
        entities.async_get(socket).platform == DEVICES
        and entities.async_get(socket).unique_id.endswith("_allowed")
        for socket in sockets
    )
    assert climate[0].options["off_interval_seconds"] == 60  # noqa: PLR2004
    assert len(hass.config_entries.async_entries("bestiarium_mode")) == 1
    assert len(hass.config_entries.async_entries("bestiarium_mock")) == 1
    assert DASHBOARD in hass.data["lovelace"].dashboards
    assert _zone_states(hass) <= {"on", "off"}


async def test_existing_mode_follows_the_renamed_grid(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    hass_access_token: str,
    tmp_path: Path,
) -> None:
    """A Mode on the old grid entity_id watches the renamed one; zones work."""
    url = await _dev_ha(hass, hass_client, tmp_path)
    mode = MockConfigEntry(
        domain="bestiarium_mode", unique_id="bestiarium_mode", data=MODE_ON_OLD_GRID_ID
    )
    mode.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mode.entry_id)

    await setup_stand(url, hass_access_token)
    await hass.async_block_till_done()

    limits = mode.data["ac_load_limits"]
    assert limits["ac_power_sensor"] == "sensor.mock_grid_power"
    assert (
        limits["ac_load_limit_w"]
        == MODE_ON_OLD_GRID_ID["ac_load_limits"]["ac_load_limit_w"]
    )
    assert _zone_states(hass) <= {"on", "off"}
    # Its modes stay; the Night the scenarios select is added
    assert [o["name"] for o in mode.data["options"]] == ["Grid on", "Night"]


async def test_only_stand_things_are_removed(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    hass_access_token: str,
    tmp_path: Path,
) -> None:
    """Entries and automations that are not the stand's stay."""
    url = await _dev_ha(hass, hass_client, tmp_path)
    standalone = MockConfigEntry(domain=DEVICES, title="Standalone floor", data={})
    retired = MockConfigEntry(domain=DEVICES, title="Mock Kettle", data={})
    own_climate = MockConfigEntry(
        domain="bestiarium_climate", title="Bestiarium Climate", data={}
    )
    for entry in (standalone, retired, own_climate):
        entry.add_to_hass(hass)

    await setup_stand(url, hass_access_token)
    await hass.async_block_till_done()

    assert hass.config_entries.async_get_entry(standalone.entry_id)
    assert hass.config_entries.async_get_entry(retired.entry_id) is None
    assert hass.config_entries.async_get_entry(own_climate.entry_id)
    assert hass.states.get("automation.kitchen_light_on_click")
    assert "kitchen_click" in (tmp_path / "automations.yaml").read_text()


async def test_scenario_scripts_are_loaded(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    hass_access_token: str,
    tmp_path: Path,
) -> None:
    """With the development configuration, the scenario buttons have scripts."""
    url = await _dev_ha(hass, hass_client, tmp_path)

    await setup_stand(url, hass_access_token)

    for name in ("reset", "night_case", "red_zone_dead_socket", "slow_cloud"):
        assert hass.states.get(f"script.{SCRIPT_PREFIX}{name}")


async def test_test_entities_are_named_from_their_unique_id(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    hass_access_token: str,
    tmp_path: Path,
) -> None:
    """A renamed test device gets back its name and `<domain>.<unique_id>` ids."""
    url = await _dev_ha(hass, hass_client, tmp_path)
    await _rename_a_test_device(hass, url, hass_access_token)

    await setup_stand(url, hass_access_token)
    await hass.async_block_till_done()

    for entity in er.async_get(hass).entities.values():
        if entity.platform == MOCK:
            assert entity.entity_id == f"{entity.domain}.{entity.unique_id}"
    boiler = dr.async_get(hass).async_get_device_by_identifier(
        (MOCK, "mock_boiler"), hass.config_entries.async_entries(MOCK)[0].entry_id
    )
    assert boiler.name_by_user is None
    assert boiler.area_id is None


async def test_broken_automation_is_removed(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    hass_access_token: str,
    tmp_path: Path,
) -> None:
    """An automation of old test devices that no longer loads is removed."""
    url = await _dev_ha(hass, hass_client, tmp_path)
    assert hass.states.get("automation.turn_off_mock_devices_on_click").state == (
        "unavailable"
    )

    await setup_stand(url, hass_access_token)
    await hass.async_block_till_done()

    assert hass.states.get("automation.turn_off_mock_devices_on_click") is None
    assert "old_mock_click" not in (tmp_path / "automations.yaml").read_text()


class _Registry:
    """The stand's registry with every mock entity named by its unique_id."""

    entities: list = []  # noqa: RUF012

    def mock(self, domain: str, unique_id: str) -> str:
        return f"{domain}.{unique_id}"

    def entity_id(self, domain: str, _platform: str, unique_id: str) -> str:
        return f"{domain}.{unique_id}"

    def stand_climate(self) -> list:
        return [
            *({"entity_id": f"climate.stand_ac_{n}"} for n in (1, 2, 3)),
            {"entity_id": "number.stand_capacity"},
        ]

    def mock_device(self, mock_id: str) -> dict:
        return {"id": mock_id}

    def control(self, device_id: str, key: str) -> str:
        return f"switch.{device_id}_{key}"

    def control_state(self, _thermostat: str) -> str:
        return "sensor.stand_ac_1_control_state"


def test_reset_leaves_always_on_devices_on() -> None:
    """The reset turns the cooker and fridge on, so the queue starts at once."""
    reset = scenario_scripts(_Registry())["reset"]["sequence"]
    switched: dict[str, set[str]] = {}
    for step in reset:
        if step.get("action", "").startswith("switch."):
            switched.setdefault(step["action"], set()).update(
                step["target"]["entity_id"]
            )
    sockets = {e for e in switched["switch.turn_on"] if e.startswith("switch.mock_")}
    assert sockets == {
        "switch.mock_cooker",
        "switch.mock_fridge",
        *(f"switch.mock_{ac}" for _, _, ac in THERMOSTATS),
    }
    assert "switch.mock_fridge" not in switched["switch.turn_off"]


def test_stand_climate_is_only_the_stand_entry() -> None:
    """The reset and the dashboard touch the stand's climate entry, no other."""
    reg = Registry(
        devices=[
            {"name": THERMOSTATS[0][0], "config_entries": ["stand"]},
            {"name": "Living room", "config_entries": ["home"]},
        ],
        entities=[
            {"platform": CLIMATE, "config_entry_id": "stand", "entity_id": "climate.a"},
            {"platform": CLIMATE, "config_entry_id": "home", "entity_id": "climate.b"},
        ],
    )

    assert [e["entity_id"] for e in reg.stand_climate()] == ["climate.a"]


def test_a_dead_ir_leaves_the_power_working() -> None:
    """Review 20, C7: the dead IR is its own knob, not the AC socket's profile."""
    scripts = scenario_scripts(_Registry())
    dead = scripts["thermostats_dead_switch"]["sequence"]
    assert {
        "action": "switch.turn_on",
        "target": {"entity_id": "switch.mock_ac_1_ir_lost"},
    } in dead
    assert all(
        step.get("target", {}).get("entity_id") != "select.mock_ac_1_fault_profile"
        for step in dead
    )
    assert scripts["ir_ac_1_on"]["sequence"][0] == {
        "condition": "state",
        "entity_id": "switch.mock_ac_1_ir_lost",
        "state": "off",
    }


def test_a_hung_ir_waits_for_its_knob() -> None:
    """Review 22, D3: the "on" of a hung IR does not end while its knob is on."""
    scripts = scenario_scripts(_Registry())
    assert {
        "action": "switch.turn_on",
        "target": {"entity_id": "switch.mock_ac_3_ir_hangs"},
    } in scripts["thermostats_hung_command"]["sequence"]
    assert scripts["ir_ac_1_on"]["sequence"][1] == {
        "wait_template": "{{ is_state('switch.mock_ac_1_ir_hangs', 'off') }}"
    }
    assert all(
        "wait_template" not in step for step in scripts["ir_ac_1_off"]["sequence"]
    )


def test_a_fault_scenario_shows_its_fault_first() -> None:
    """Review 24, E7: the faulty AC's room the coldest, the stand's loads waiting."""
    scripts = scenario_scripts(_Registry())
    loads = [
        "switch.commode_socket_allowed",
        "switch.bedside_socket_allowed",
        "switch.boiler_allowed",
        "switch.slow_cloud_socket_allowed",
        "switch.tuya_socket_allowed",
        "switch.night_light_allowed",
        "switch.dead_socket_allowed",
    ]
    for name, ac in (
        ("thermostats_dead_switch", 1),
        ("thermostats_hung_command", 3),
        ("thermostats_no_temperature", 2),
    ):
        sequence = scripts[name]["sequence"]
        assert {"action": "switch.turn_off", "target": {"entity_id": loads}} in sequence
        rooms = {
            s["target"]["entity_id"]: s["data"]["value"]
            for s in sequence
            if s.get("action") == "number.set_value"
            and "thermo" in s["target"]["entity_id"]
        }
        assert rooms == {
            f"number.mock_thermo_{n}_mock_temperature": 15 if n == ac else 21
            for n in (1, 2, 3)
        }
    # Review 30, F1: the reset allows them again, a restart of HA in between too
    reset = scripts["reset"]["sequence"]
    assert {"action": "switch.turn_on", "target": {"entity_id": loads}} in reset
    assert not [s for s in reset if s.get("action", "").startswith("scene.")]


def test_the_stepwise_off_has_its_scenarios() -> None:
    """DEV-010: the reset gives the ACs their power back; the stuck AC loses its IR."""
    scripts = scenario_scripts(_Registry())
    sockets = [f"switch.{ac}_allowed" for _, _, ac in THERMOSTATS]
    assert {
        "action": "switch.turn_on",
        "target": {"entity_id": sockets},
    } in scripts["reset"]["sequence"]
    lost = {
        "action": "switch.turn_on",
        "target": {"entity_id": "switch.mock_ac_1_ir_lost"},
    }
    assert lost in scripts["climate_stuck_on"]["sequence"]
    assert lost in scripts["climate_bypass_on_pause"]["sequence"]
    # Review 06, R3: only AC 1 heats, capacity 1; the cooker holds red
    bypass = scripts["climate_bypass_on_pause"]["sequence"]
    assert {
        "action": "number.set_value",
        "target": {"entity_id": ["number.stand_capacity"]},
        "data": {"value": 1},
    } in bypass
    assert {
        "wait_template": (
            "{{ is_state('sensor.stand_ac_1_control_state', 'unresponsive') }}"
        ),
        "timeout": {"minutes": 5},
        "continue_on_timeout": False,
    } in bypass
    red = scripts["climate_red_entry"]["sequence"]
    assert red[1]["target"]["entity_id"] == "number.mock_cooker_mock_power"
    assert {"delay": {"seconds": 60}} in red
