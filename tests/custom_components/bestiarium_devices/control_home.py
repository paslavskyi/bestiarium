"""A home for the control tests: real Bestiarium Mode, devices set up, a clock."""

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    SIGNAL_CONTROL_EVENT,
)
from custom_components.bestiarium_devices.consumption import SOURCE_RUN, PowerRecord
from custom_components.bestiarium_devices.power_memory import async_get_power_memory

from .conftest import FakeRelays
from .test_headroom import GRID, _setup_mode


class Home:
    """The devices of a test, the grid load, the clock and the control events."""

    def __init__(
        self, hass: HomeAssistant, relays: FakeRelays, freezer: FrozenDateTimeFactory
    ) -> None:
        """Start with no devices."""
        self.hass = hass
        self.relays = relays
        self._freezer = freezer
        self.events: dict[str, list[str]] = {}
        self.payloads: dict[str, list[dict[str, Any]]] = {}
        self.entries: list[MockConfigEntry] = []

    async def mode(self, load: str) -> None:
        """Set up Bestiarium Mode (red zone from 4250 W) with the grid load."""
        await _setup_mode(self.hass, load)

    def load(self, watts: str) -> None:
        """Change the grid load."""
        self.hass.states.async_set(GRID, watts)

    async def device(  # noqa: PLR0913
        self,
        switch: str,
        priority: int,
        *,
        on: bool = False,
        power: str = "0",
        priorities: dict[str, int] | None = None,
        options: dict[str, Any] | None = None,
    ) -> MockConfigEntry:
        """Set up a 100 W device whose power sensor reads `power`."""
        data = {
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: switch.split(".")[1].title(),
            CONF_SWITCH_ENTITY_ID: switch,
            CONF_POWER_SENSOR_ENTITY_ID: f"{switch}_power",
            CONF_LOAD_PRIORITIES: priorities or {CONF_DEFAULT_PRIORITY: priority},
        }
        self.hass.states.async_set(f"{switch}_power", power)
        entry = MockConfigEntry(domain=DOMAIN, data=data, options=options or {})
        entry.add_to_hass(self.hass)
        self.relays.add(switch, on=on)
        memory = await async_get_power_memory(self.hass)
        for minute in range(3):
            memory.add_run(
                entry.entry_id,
                PowerRecord(
                    start=dt_util.utcnow() - timedelta(minutes=minute),
                    duration_s=600,
                    power_w=100,
                    source=SOURCE_RUN,
                ),
            )
        assert await self.hass.config_entries.async_setup(entry.entry_id)  # noqa: S101
        await self.hass.async_block_till_done()
        events = self.events.setdefault(entry.entry_id, [])
        payloads = self.payloads.setdefault(entry.entry_id, [])

        @callback
        def record(event: str, attributes: dict[str, Any]) -> None:
            events.append(event)
            payloads.append(attributes)

        async_dispatcher_connect(
            self.hass, SIGNAL_CONTROL_EVENT.format(entry.entry_id), record
        )
        return entry

    async def tick(self, seconds: float, step: float = 1) -> None:
        """Let time pass, running what becomes due every step."""
        while seconds > 0:
            move = min(step, seconds)
            self._freezer.tick(timedelta(seconds=move))
            async_fire_time_changed(self.hass)
            await self.hass.async_block_till_done()
            seconds -= move

    def set(self, entity_id: str, state: str) -> None:
        """Set a relay or power sensor state, as the device would report it."""
        self.hass.states.async_set(entity_id, state)

    def entity_id(self, entry: MockConfigEntry, domain: str, key: str) -> str:
        """Return the entity_id of an entity of a device entry."""
        entity_id = er.async_get(self.hass).async_get_entity_id(
            domain, DOMAIN, f"{entry.entry_id}_{key}"
        )
        assert entity_id is not None  # noqa: S101
        return entity_id

    def state(self, entry: MockConfigEntry) -> str:
        """Return the control state of a device, as its sensor shows it."""
        return self.hass.states.get(
            self.entity_id(entry, "sensor", "control_state")
        ).state

    def commands(self, switch: str) -> list[str]:
        """Return the commands the relay received."""
        return [
            service for service, entity_id in self.relays.calls if entity_id == switch
        ]
