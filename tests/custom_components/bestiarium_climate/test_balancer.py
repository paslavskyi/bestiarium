"""
The balancer of Bestiarium Climate (DEV-008 stage B).

Three floor heaters share a capacity of 2. A switch that does not respond, or
a thermostat without temperature, frees its place for the others; one
switch never stops the others; nothing is repeated every minute.
"""

# ruff: noqa: S101

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import Event, HomeAssistant, ServiceRegistry, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.bestiarium_climate.const import (
    CONF_CAPACITY,
    CONF_MIN_WORKING_TIME,
    CONF_RECOVERY_TIMEOUT,
    DOMAIN,
    EVENT_DEVICE_ROTATED_IN,
    SUBENTRY_TYPE_THERMOSTAT,
    ClimateMode,
)

from custom_components.bestiarium_climate.control import (
    ThermostatControl,
    ThermostatMemory,
)

from .conftest import _make_subentry

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

MINUTE = 60
ZONES = ("green", "yellow", "red", "overload")
FLOORS = ("floor_1", "floor_2", "floor_3")


class Floors:
    """Three floor heaters: switches, thermometers and the clock."""

    def __init__(self, hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
        """Start with no entry."""
        self.hass = hass
        self.freezer = freezer
        self.entry: MockConfigEntry
        self.calls: list[tuple[str, str]] = []
        self.stuck: set[str] = set()
        self.refusing: set[str] = set()
        self.events: list[str] = []
        # What a switch does before it takes a command (a slow one, a sequence)
        self.before: dict[str, Callable[[], Awaitable[None]]] = {}

    async def setup(self, capacity: int = 2, *, zones: bool = False) -> None:
        """Set up the entry: three thermostats heating to 21 °C."""
        for floor in FLOORS:
            self.hass.states.async_set(f"switch.{floor}", STATE_OFF)
            self.hass.states.async_set(f"sensor.{floor}_temperature", "18")
        if zones:
            registry = er.async_get(self.hass)
            for zone in ZONES:
                registry.async_get_or_create(
                    "binary_sensor",
                    "bestiarium_mode",
                    f"power_load_{zone}",
                    suggested_object_id=f"power_load_{zone}",
                )
            self.zone("green")
        self.entry = MockConfigEntry(
            domain=DOMAIN,
            minor_version=2,
            data={
                CONF_CAPACITY: capacity,
                CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 10, "seconds": 0},
                CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
            },
        )
        self.entry.subentries = {
            floor: _make_subentry(
                floor,
                SUBENTRY_TYPE_THERMOSTAT,
                floor.title(),
                {
                    "name": floor.title(),
                    "temperature_sensor": f"sensor.{floor}_temperature",
                    "target_switch": f"switch.{floor}",
                    "thermostat_mode": ClimateMode.HEATING,
                },
            )
            for floor in FLOORS
        }
        self.entry.add_to_hass(self.hass)

        @callback
        def record(event: Event) -> None:
            self.events.append(event.event_type)

        self.hass.bus.async_listen("*", record)
        assert await self.hass.config_entries.async_setup(self.entry.entry_id)
        await self.hass.async_block_till_done()
        for floor in FLOORS:
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": self.climate(floor), "temperature": 21},
                blocking=True,
            )
            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": self.climate(floor), "hvac_mode": "heat"},
                blocking=True,
            )
        await self.hass.async_block_till_done()

    def zone(self, zone: str) -> None:
        """Show a load zone on the four zone sensors ("pause": all off)."""
        for sensor in ZONES:
            state = STATE_ON if sensor == zone else STATE_OFF
            if zone == "no_data":
                state = STATE_UNAVAILABLE
            self.hass.states.async_set(f"binary_sensor.power_load_{sensor}", state)

    def entity_id(self, domain: str, unique_id: str) -> str:
        """Return an entity_id of the integration."""
        entity_id = er.async_get(self.hass).async_get_entity_id(
            domain, DOMAIN, unique_id
        )
        assert entity_id is not None
        return entity_id

    def climate(self, floor: str) -> str:
        """Return a floor's thermostat."""
        return self.entity_id("climate", floor)

    def state(self, floor: str) -> str:
        """Return a floor's control state."""
        return self.hass.states.get(
            self.entity_id("sensor", f"{floor}_control_state")
        ).state

    def on(self) -> set[str]:
        """Return the floors whose switch is on."""
        return {
            f for f in FLOORS if self.hass.states.get(f"switch.{f}").state == STATE_ON
        }

    async def tick(self, seconds: float, step: float = MINUTE) -> None:
        """Let time pass."""
        while seconds > 0:
            move = min(step, seconds)
            self.freezer.tick(timedelta(seconds=move))
            async_fire_time_changed(self.hass)
            await self.hass.async_block_till_done()
            seconds -= move


@pytest.fixture
def floors(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> Any:
    """Return the floors; their switches take commands unless stuck or refusing."""
    home = Floors(hass, freezer)
    original = ServiceRegistry.async_call

    async def async_call(
        registry: ServiceRegistry,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        entity_id = (service_data or {}).get("entity_id", "")
        floor = entity_id.removeprefix("switch.")
        if domain == "switch" and floor in FLOORS:
            home.calls.append((service, floor))
            if before := home.before.get(floor):
                await before()
            if floor in home.refusing:
                msg = "refused"
                raise HomeAssistantError(msg)
            if floor not in home.stuck:
                hass.states.async_set(
                    entity_id, STATE_ON if service == "turn_on" else STATE_OFF
                )
            return None
        return await original(registry, domain, service, service_data, *args, **kwargs)

    with patch.object(ServiceRegistry, "async_call", async_call):
        yield home


async def test_capacity_shared(floors: Floors) -> None:
    """Three want heat, capacity 2: two switches on."""
    await floors.setup()
    await floors.tick(MINUTE)
    assert len(floors.on()) == 2


async def test_dead_switch_frees_its_place(
    floors: Floors, caplog: pytest.LogCaptureFixture
) -> None:
    """A switch that does not respond is deferred; the third heater takes its place."""
    await floors.setup()
    await floors.tick(MINUTE)
    first, second = sorted(floors.on())
    floors.hass.states.async_set(f"switch.{first}", STATE_OFF)
    floors.stuck.add(first)

    await floors.tick(10 * MINUTE)

    assert floors.state(first) == "unresponsive"
    assert floors.on() == {second, *(set(FLOORS) - {first, second})}
    assert caplog.text.count("control state unresponsive") == 1


async def test_refused_command_does_not_stop_the_others(floors: Floors) -> None:
    """HA refuses the command of one switch: the others still get theirs."""
    floors.refusing.add("floor_1")
    await floors.setup(capacity=3)
    await floors.tick(MINUTE)
    assert floors.on() == {"floor_2", "floor_3"}


async def test_no_temperature_is_out_of_rotation(floors: Floors) -> None:
    """No reading for more than a minute: off, unavailable, its place to another."""
    await floors.setup()
    await floors.tick(MINUTE)
    first = sorted(floors.on())[0]
    floors.hass.states.async_set(f"sensor.{first}_temperature", STATE_UNAVAILABLE)

    await floors.tick(30, step=30)
    assert first in floors.on()

    await floors.tick(2 * MINUTE)
    assert floors.state(first) == "no_temperature"
    assert floors.hass.states.get(floors.climate(first)).state == STATE_UNAVAILABLE
    assert first not in floors.on()
    assert len(floors.on()) == 2

    floors.hass.states.async_set(f"sensor.{first}_temperature", "18")
    await floors.tick(2 * MINUTE)
    assert floors.state(first) == "normal"
    assert floors.hass.states.get(floors.climate(first)).state == "heat"


async def test_capacity_entity_applies_at_once(floors: Floors) -> None:
    """A power automation lowers the capacity: a switch goes off without waiting."""
    await floors.setup()
    await floors.tick(MINUTE)
    await floors.hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": floors.entity_id(
                "number", f"{floors.entry.entry_id}_capacity"
            ),
            "value": 1,
        },
        blocking=True,
    )
    await floors.hass.async_block_till_done()
    assert len(floors.on()) == 1


async def test_nothing_repeats_every_minute(floors: Floors) -> None:
    """An hour of deficit and a dead switch: no capacity or switch events."""
    floors.stuck.add("floor_1")
    await floors.setup()
    await floors.tick(60 * MINUTE)
    for removed in ("capacity_exhausted", "switch_failed", "switch_recovered"):
        assert f"bestiarium_climate_{removed}" not in floors.events
    assert floors.events.count(EVENT_DEVICE_ROTATED_IN) < 10


async def test_internals_are_not_attributes(floors: Floors) -> None:
    """The balancer's internals live in a Store, not in the climate's state."""
    await floors.setup()
    await floors.tick(MINUTE)
    attributes = floors.hass.states.get(floors.climate("floor_1")).attributes
    for internal in (
        "expected_switch_state",
        "expected_switch_state_changed_at",
        "switch_state_change_tries",
        "switch_not_responding",
        "recovery_time",
    ):
        assert internal not in attributes


async def test_deferral_survives_a_restart(floors: Floors) -> None:
    """A deferred switch stays deferred after a reload of the entry."""
    floors.stuck.update(FLOORS)
    await floors.setup()
    await floors.tick(10 * MINUTE)
    assert floors.state("floor_1") == "unresponsive"

    await floors.hass.config_entries.async_reload(floors.entry.entry_id)
    await floors.hass.async_block_till_done()
    calls = floors.calls.count(("turn_on", "floor_1"))
    await floors.tick(5 * MINUTE)

    assert floors.state("floor_1") == "unresponsive"
    # Its place went to the third floor; floor 1 waits for its recovery
    assert floors.calls.count(("turn_on", "floor_1")) == calls


async def test_virtual_switch_without_power_data_is_unavailable(
    hass: HomeAssistant, climate_entry_with_switch: MockConfigEntry
) -> None:
    """B3: no power reading — the virtual switch is unavailable, not off."""
    await hass.config_entries.async_setup(climate_entry_with_switch.entry_id)
    await hass.async_block_till_done()
    switch = hass.states.async_all("switch")[0].entity_id
    assert hass.states.get(switch).state == STATE_UNAVAILABLE

    hass.states.async_set("sensor.power", "1500")
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == STATE_ON


async def test_attempts_are_one_a_cycle(floors: Floors) -> None:
    """A thermostat's changes run cycles in between: they count no attempt."""
    floors.stuck.add("floor_1")
    await floors.setup()
    for _ in range(5):
        await floors.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": floors.climate("floor_1"), "hvac_mode": "heat"},
            blocking=True,
        )
    await floors.tick(2 * MINUTE)
    assert floors.state("floor_1") == "normal"
    assert floors.calls.count(("turn_on", "floor_1")) <= 3


async def test_attempts_are_shown_as_they_are_made(floors: Floors) -> None:
    """The control state's failed_attempts follows each attempt."""
    floors.stuck.add("floor_1")
    await floors.setup()
    await floors.tick(MINUTE)

    sensor = floors.entity_id("sensor", "floor_1_control_state")
    assert floors.hass.states.get(sensor).attributes["failed_attempts"] == 2


async def test_memory_forgets_a_removed_thermostat(
    floors: Floors, hass_storage: dict
) -> None:
    """A thermostat removed from the configuration leaves nothing in the store."""
    await floors.setup()
    floors.entry.subentries.pop("floor_3")

    await floors.hass.config_entries.async_reload(floors.entry.entry_id)
    await floors.tick(MINUTE)

    assert set(hass_storage["bestiarium_climate.thermostats"]["data"]) == {
        "floor_1",
        "floor_2",
    }


async def test_a_new_decision_is_sent_at_once(floors: Floors) -> None:
    """Off and back to heat within a minute: the switch is turned on at once."""
    await floors.setup(capacity=3)
    assert floors.on() == set(FLOORS)

    for hvac_mode in ("off", "heat"):
        await floors.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": floors.climate("floor_1"), "hvac_mode": hvac_mode},
            blocking=True,
        )
        await floors.hass.async_block_till_done()

    assert "floor_1" in floors.on()


async def test_unavailable_as_soon_as_confirmed(floors: Floors) -> None:
    """The thermostat is unavailable when no temperature is confirmed, not a cycle later."""
    await floors.setup()
    await floors.tick(MINUTE + 20)
    floors.hass.states.async_set("sensor.floor_1_temperature", STATE_UNAVAILABLE)

    await floors.tick(65, step=5)

    assert floors.state("floor_1") == "no_temperature"
    assert floors.hass.states.get(floors.climate("floor_1")).state == STATE_UNAVAILABLE


async def test_a_confirmed_problem_frees_the_place_at_once(floors: Floors) -> None:
    """No temperature confirmed between two cycles: the third floor starts at once."""
    await floors.setup()
    await floors.tick(MINUTE + 20)
    first = sorted(floors.on())[0]
    floors.hass.states.async_set(f"sensor.{first}_temperature", STATE_UNAVAILABLE)

    await floors.tick(65, step=5)

    assert first not in floors.on()
    assert len(floors.on()) == 2


async def _temperatures(floors: Floors, **temperatures: str) -> None:
    for floor, value in temperatures.items():
        floors.hass.states.async_set(f"sensor.{floor}_temperature", value)
    await floors.hass.async_block_till_done()


async def _hvac(floors: Floors, floor: str, hvac_mode: str) -> None:
    await floors.hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": floors.climate(floor), "hvac_mode": hvac_mode},
        blocking=True,
    )
    await floors.hass.async_block_till_done()


async def test_yellow_keeps_the_heating_and_starts_none(floors: Floors) -> None:
    """Yellow: the two heating go on heating; a freed place is not given."""
    await floors.setup(zones=True)
    assert floors.on() == {"floor_1", "floor_2"}

    floors.zone("yellow")
    await floors.tick(MINUTE)
    assert floors.on() == {"floor_1", "floor_2"}

    await _temperatures(floors, floor_1="22")
    await floors.tick(MINUTE)
    assert floors.on() == {"floor_2"}

    floors.zone("green")
    await floors.tick(MINUTE)
    assert floors.on() == {"floor_2", "floor_3"}


async def test_yellow_rotates_out_without_starting(floors: Floors) -> None:
    """Yellow: after the minimum working time one goes out; the next waits."""
    await floors.setup(zones=True)
    floors.zone("yellow")

    await floors.tick(11 * MINUTE)

    assert len(floors.on()) == 1
    assert "floor_3" not in floors.on()


async def test_pause_no_data_and_overload_start_nothing(floors: Floors) -> None:
    """The pause, no data and the overload: nothing starts, the heating goes on."""
    await floors.setup(zones=True)
    for zone in ("pause", "no_data", "overload"):
        floors.zone(zone)
        await _hvac(floors, "floor_1", "off")
        await floors.tick(MINUTE)
        assert floors.on() == {"floor_2"}, zone
        await _hvac(floors, "floor_1", "heat")
        await floors.tick(MINUTE)
        assert floors.on() == {"floor_2"}, zone

    floors.zone("green")
    await floors.tick(MINUTE)
    assert len(floors.on()) == 2


async def test_red_turns_off_one_by_one(floors: Floors) -> None:
    """Red: every 5 s one goes off, the room that needs it least first."""
    await floors.setup(capacity=3, zones=True)
    await _temperatures(floors, floor_1="20", floor_2="19", floor_3="18")
    assert floors.on() == set(FLOORS)

    floors.zone("red")
    await floors.hass.async_block_till_done()
    assert floors.on() == {"floor_2", "floor_3"}
    await floors.tick(5, step=5)
    assert floors.on() == {"floor_3"}
    await floors.tick(5, step=5)
    assert floors.on() == set()


async def test_load_protection_off_is_no_failed_attempt(floors: Floors) -> None:
    """A switch the devices' protection turned off is not turned on in red."""
    await floors.setup(zones=True)
    floors.zone("red")
    floors.hass.states.async_set("switch.floor_2", STATE_OFF)
    calls = len(floors.calls)

    await floors.tick(10 * MINUTE)

    assert ("turn_on", "floor_2") not in floors.calls[calls:]
    assert floors.state("floor_2") == "normal"


async def test_a_switch_sequence_may_call_the_thermostat(floors: Floors) -> None:
    """A switch whose command calls a climate service does not lock the balancer."""

    async def set_temperature() -> None:
        await floors.hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": floors.climate("floor_1"), "temperature": 22},
            blocking=True,
        )

    floors.before["floor_1"] = set_temperature
    await floors.setup()
    await floors.tick(MINUTE)

    assert "floor_1" in floors.on()


async def test_a_slow_switch_does_not_hold_the_others(floors: Floors) -> None:
    """The switches get their commands at once, not one after another."""
    released, commanded = asyncio.Event(), asyncio.Event()

    async def slow() -> None:
        await released.wait()

    async def quick() -> None:
        commanded.set()

    floors.before.update(floor_1=slow, floor_2=quick)
    setup = floors.hass.async_create_task(floors.setup())
    await commanded.wait()

    assert ("turn_on", "floor_1") in floors.calls
    released.set()
    await setup
    assert floors.on() == {"floor_1", "floor_2"}


async def test_a_restored_deferral_warns_once(
    floors: Floors, hass_storage: dict, caplog: pytest.LogCaptureFixture
) -> None:
    """A deferral kept across a restart is no new problem: no second warning."""
    hass_storage["bestiarium_climate.thermostats"] = {
        "version": 1,
        "data": {
            "floor_1": {
                "expected_on": False,
                "expected_changed_at": dt_util.utcnow().isoformat(),
                "tries": 0,
                "deferred_until": (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
            }
        },
    }
    await floors.setup()

    assert floors.state("floor_1") == "unresponsive"
    assert "control state" not in caplog.text


async def test_kept_attempts_count_on_after_a_restart(hass: HomeAssistant) -> None:
    """The first command after a start is no new decision: the kept tries go on."""
    memory = ThermostatMemory(hass)
    memory.update(
        "floor_1",
        expected_on=True,
        expected_changed_at=dt_util.utcnow().isoformat(),
        tries=2,
        deferred_until=None,
    )
    entry = MockConfigEntry(domain=DOMAIN, minor_version=2, data={})
    subentry = _make_subentry(
        "floor_1",
        SUBENTRY_TYPE_THERMOSTAT,
        "Floor_1",
        {"temperature_sensor": "sensor.t", "target_switch": "switch.floor_1"},
    )
    hass.states.async_set("switch.floor_1", STATE_OFF)
    control = ThermostatControl(hass, entry, subentry, memory)

    assert control.to_command(dt_util.utcnow()) is True
    assert control.attributes["failed_attempts"] == 3
