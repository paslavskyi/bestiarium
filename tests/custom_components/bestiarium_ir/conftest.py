"""Fixtures for bestiarium_ir tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

import pytest
from homeassistant.components.infrared import InfraredEmitterEntity
from homeassistant.components.infrared.const import DATA_COMPONENT as INFRARED_COMPONENT
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from infrared_protocols.commands import Command
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.bestiarium_ir import hisense
from custom_components.bestiarium_ir.const import (
    CONF_EMITTER,
    CONF_ZHA_DEVICE,
    DOMAIN,
    SUBENTRY_TYPE_BLASTER,
    SUBENTRY_TYPE_HISENSE,
)
from custom_components.bestiarium_ir.kelon168 import Kelon168Command

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


class Clock:
    """The monotonic clock of the air conditioners' frames; sleeping moves it."""

    def __init__(self) -> None:
        """Start at an arbitrary moment."""
        self.now = 1000.0
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        """Move the clock instead of waiting."""
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Run the pauses between frames without waiting."""
    fake = Clock()
    monkeypatch.setattr(hisense, "monotonic", lambda: fake.now)
    monkeypatch.setattr(hisense, "sleep", fake.sleep)
    return fake


class RecordingEmitter(InfraredEmitterEntity):
    """An emitter of another integration: records the Kelon168 frames sent."""

    _attr_name = "Test emitter"
    _attr_unique_id = "test_emitter"

    def __init__(self, clock: Clock) -> None:
        """Record on the clock."""
        self.entity_id = "infrared.test_emitter"
        self._clock = clock
        self.frames: list[Kelon168Command] = []
        self.times: list[float] = []
        self.fail = False

    async def async_send_command(self, command: Command) -> None:
        """Record the frame, or fail as a broken emitter."""
        if self.fail:
            msg = "the emitter is broken"
            raise HomeAssistantError(msg)
        frame = Kelon168Command.from_raw_timings(command.get_raw_timings())
        assert frame is not None
        self.frames.append(frame)
        self.times.append(self._clock.now)


@pytest.fixture
async def emitter(hass: HomeAssistant, clock: Clock) -> RecordingEmitter:
    """Return a test emitter, registered as an infrared entity."""
    assert await async_setup_component(hass, "infrared", {})
    entity = RecordingEmitter(clock)
    await hass.data[INFRARED_COMPONENT].async_add_entities([entity])
    return entity


def hisense_subentry(emitter_id: str = "infrared.test_emitter") -> ConfigSubentryData:
    """Return the subentry of a Hisense on the emitter."""
    return ConfigSubentryData(
        data={CONF_EMITTER: emitter_id},
        subentry_type=SUBENTRY_TYPE_HISENSE,
        title="Stasi AC",
        unique_id=None,
    )


@pytest.fixture
async def ac(
    hass: HomeAssistant,
    emitter: RecordingEmitter,  # noqa: ARG001
) -> tuple[MockConfigEntry, str]:
    """Return the entry and the climate entity of a Hisense on the test emitter."""
    entry = ir_entry(hisense_subentry())
    await set_up(hass, entry)
    return entry, subentry_entity(hass, entry, "climate")


async def call(
    hass: HomeAssistant, domain: str, service: str, entity_id: str, **data: object
) -> None:
    """Call a service of an entity and wait for it."""
    await hass.services.async_call(
        domain, service, {"entity_id": entity_id, **data}, blocking=True
    )
