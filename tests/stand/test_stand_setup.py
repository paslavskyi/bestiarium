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
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.bestiarium_devices.const import DOMAIN as DEVICES
from custom_components.bestiarium_mock.const import DOMAIN as MOCK
from custom_components.bestiarium_mock.const import MOCK_DEVICES
from scripts.stand_setup import (
    DASHBOARD,
    SCRIPT_PREFIX,
    STAND_DEVICES,
    THERMOSTATS,
    _scripts,
    setup_stand,
)

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


async def _dev_ha(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, config_dir: Path
) -> str:
    """Run HA on the development configuration; return its URL."""
    hass.config.config_dir = str(config_dir)
    shutil.copy(DEV_CONFIG, config_dir / "configuration.yaml")
    (config_dir / "automations.yaml").write_text(dump([BROKEN_AUTOMATION]))
    (config_dir / "scripts.yaml").write_text("")
    for component in ("api", "config", "lovelace", "script", "logbook"):
        assert await async_setup_component(hass, component, {})
    assert await async_setup_component(
        hass, "automation", {"automation": [BROKEN_AUTOMATION]}
    )
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

    names = {spec.device_id: spec.name for spec in MOCK_DEVICES}
    assert [entry.title for entry in hass.config_entries.async_entries(DEVICES)] == [
        names[device.mock_id] for device in STAND_DEVICES
    ]
    climate = hass.config_entries.async_entries("bestiarium_climate")
    assert len(climate) == 1
    assert len(climate[0].subentries) == len(THERMOSTATS)
    assert len(hass.config_entries.async_entries("bestiarium_mode")) == 1
    assert len(hass.config_entries.async_entries("bestiarium_mock")) == 1
    assert DASHBOARD in hass.data["lovelace"].dashboards


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


def test_reset_leaves_always_on_devices_on() -> None:
    """The reset turns the cooker and fridge on, so the queue starts at once."""

    class _Registry:
        def mock(self, domain: str, unique_id: str) -> str:
            return f"{domain}.{unique_id}"

    reset = _scripts(_Registry())["reset"]["sequence"]
    switched = {
        step["action"]: step["target"]["entity_id"]
        for step in reset
        if step.get("action", "").startswith("switch.")
    }
    assert set(switched["switch.turn_on"]) == {
        "switch.mock_cooker",
        "switch.mock_fridge",
    }
    assert "switch.mock_fridge" not in switched["switch.turn_off"]
