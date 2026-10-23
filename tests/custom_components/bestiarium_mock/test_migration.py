"""Migration of the Bestiarium Mock entry to the DEV-007 device set."""

# ruff: noqa: S101

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_mock.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_version_1_drops_the_old_device_set(hass: HomeAssistant) -> None:
    """Old sockets and 'Mock Enabled' switches go; kept sockets keep their ids."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={}, version=1)
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    for device_id in ("ac", "boiler"):
        device = devices.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"mock_{device_id}")}
        )
        for unique_id in (f"mock_{device_id}", f"mock_{device_id}_mock_enabled"):
            entities.async_get_or_create(
                "switch",
                DOMAIN,
                unique_id,
                config_entry=entry,
                device_id=device.id,
            )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2  # noqa: PLR2004
    assert not devices.async_get_device_by_identifier(
        (DOMAIN, "mock_ac"), entry.entry_id
    )
    assert devices.async_get_device_by_identifier(
        (DOMAIN, "mock_boiler"), entry.entry_id
    )
    for unique_id in ("mock_ac", "mock_ac_mock_enabled", "mock_boiler_mock_enabled"):
        assert entities.async_get_entity_id("switch", DOMAIN, unique_id) is None
    assert entities.async_get_entity_id("switch", DOMAIN, "mock_boiler")
