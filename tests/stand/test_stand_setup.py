"""The stand script sets up the test stand through the HA APIs, repeatably."""

# ruff: noqa: S101

from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.bestiarium_devices.const import DOMAIN as DEVICES
from custom_components.bestiarium_mock.const import MOCK_DEVICES
from scripts.stand_setup import DASHBOARD, STAND_DEVICES, THERMOSTATS, setup_stand

pytestmark = pytest.mark.usefixtures(
    "recorder_mock", "enable_custom_integrations", "socket_enabled"
)


async def _stand_url(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, config_dir: Path
) -> str:
    # The script editor writes scripts.yaml and reloads it from configuration.yaml
    hass.config.config_dir = str(config_dir)
    (config_dir / "configuration.yaml").write_text("script: !include scripts.yaml\n")
    (config_dir / "scripts.yaml").write_text("")
    for component in ("api", "config", "lovelace", "script", "logbook"):
        assert await async_setup_component(hass, component, {})
    client = await hass_client()
    return str(client.make_url("")).rstrip("/")


async def test_stand_setup_is_repeatable(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    hass_access_token: str,
    tmp_path: Path,
) -> None:
    """Twice in a row: the same entries, scripts and dashboard."""
    url = await _stand_url(hass, hass_client, tmp_path)

    for _ in range(2):
        await setup_stand(url, hass_access_token)
        await hass.async_block_till_done()

        names = {spec.device_id: spec.name for spec in MOCK_DEVICES}
        assert [
            entry.title for entry in hass.config_entries.async_entries(DEVICES)
        ] == [names[device.mock_id] for device in STAND_DEVICES]
        climate = hass.config_entries.async_entries("bestiarium_climate")
        assert len(climate) == 1
        assert len(climate[0].subentries) == len(THERMOSTATS)
        assert len(hass.config_entries.async_entries("bestiarium_mode")) == 1
        assert len(hass.config_entries.async_entries("bestiarium_mock")) == 1

    scripts = [
        entity.entity_id
        for entity in er.async_get(hass).entities.values()
        if entity.domain == "script"
    ]
    assert "script.bestiarium_stand_night_case" in scripts
    assert DASHBOARD in hass.data["lovelace"].dashboards
