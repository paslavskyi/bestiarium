"""Fixtures for bestiarium_ir tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from infrared_protocols.commands import Command
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.bestiarium_ir.const import (
    CONF_ZHA_DEVICE,
    DOMAIN,
    SUBENTRY_TYPE_BLASTER,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

IEEE = "00:11:22:33:44:55:66:77"


class RawCommand(Command):
    """A command of given timings."""

    def __init__(self, timings: list[int], repeat_count: int = 0) -> None:
        """Keep the timings."""
        super().__init__(modulation=38000, repeat_count=repeat_count)
        self.timings = timings

    @override
    def get_raw_timings(self) -> list[int]:
        return self.timings


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integrations of this repository."""


@pytest.fixture
def zha_device(hass: HomeAssistant) -> dr.DeviceEntry:
    """Return a TS1201 of ZHA."""
    zha = MockConfigEntry(domain="zha")
    zha.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=zha.entry_id,
        identifiers={("zha", IEEE)},
        manufacturer="_TZ3290_nba3knpsarkawgnt",
        model="TS1201",
        name="IR black",
    )


@pytest.fixture
def zha_calls(hass: HomeAssistant) -> list[ServiceCall]:
    """Return the calls of the ZHA cluster command service."""
    return async_mock_service(hass, "zha", "issue_zigbee_cluster_command")


def ir_entry(*subentries: ConfigSubentryData) -> MockConfigEntry:
    """Return a Bestiarium IR entry with the subentries."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        title="Bestiarium IR",
        data={},
        subentries_data=subentries,
    )


async def set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def subentry_entity(
    hass: HomeAssistant, entry: MockConfigEntry, domain: str, suffix: str = ""
) -> str:
    """Return the entity_id of the entry's only subentry's entity in the domain."""
    (subentry_id,) = entry.subentries
    entity_id = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, subentry_id + suffix
    )
    assert entity_id is not None
    return entity_id


@pytest.fixture
async def blaster(
    hass: HomeAssistant, zha_device: dr.DeviceEntry
) -> tuple[MockConfigEntry, str]:
    """Return the entry and the emitter of a blaster on the TS1201."""
    entry = ir_entry(
        ConfigSubentryData(
            data={CONF_ZHA_DEVICE: zha_device.id},
            subentry_type=SUBENTRY_TYPE_BLASTER,
            title="IR black",
            unique_id=zha_device.id,
        )
    )
    await set_up(hass, entry)
    return entry, subentry_entity(hass, entry, "infrared")
