"""
The balancer of Bestiarium Climate (DEV-008 stage B).

Three floor heaters share a capacity of 2. A switch that does not respond, or
a thermostat without temperature, frees its place for the others; one
switch never stops the others; nothing is repeated every minute.
"""

# ruff: noqa: S101

import asyncio
import contextlib
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryDisabler, ConfigSubentryData
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
    START_STEP_SECONDS,
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
RED_W = 4500
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

    async def setup(
        self,
        capacity: int = 2,
        *,
        zones: bool = False,
        settle: bool = True,
        heater_power: float | None = None,
        min_working_minutes: int = 10,
    ) -> None:
        """Set up the entry: three thermostats heating to 21 °C."""
        for floor in FLOORS:
            self.hass.states.async_set(f"switch.{floor}", STATE_OFF)
            self.hass.states.async_set(f"sensor.{floor}_temperature", "18")
        if zones:
            # Where bestiarium_mode reads the grid load; disabled, not set up, so
            # the zone sensors are the test's
            MockConfigEntry(
                domain="bestiarium_mode",
                data={"ac_load_limits": {"ac_power_sensor": "sensor.grid"}},
                disabled_by=ConfigEntryDisabler.USER,
            ).add_to_hass(self.hass)
            if self.hass.states.get("sensor.grid") is None:
                self.load(1000)
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
                CONF_MIN_WORKING_TIME: {
                    "hours": 0,
                    "minutes": min_working_minutes,
                    "seconds": 0,
                },
                CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
            },
            subentries_data=[
                ConfigSubentryData(
                    subentry_id=floor,
                    subentry_type=SUBENTRY_TYPE_THERMOSTAT,
                    title=floor.title(),
                    unique_id=None,
                    data={
                        "name": floor.title(),
                        "temperature_sensor": f"sensor.{floor}_temperature",
                        "target_switch": f"switch.{floor}",
                        "thermostat_mode": ClimateMode.HEATING,
                        **(
                            {"heater_power": heater_power}
                            if heater_power and floor == "floor_1"
                            else {}
                        ),
                    },
                )
                for floor in FLOORS
            ],
        )
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
        if zones and settle:
            # In green the heaters start one by one, a step apart
            await self.tick(capacity * START_STEP_SECONDS, step=START_STEP_SECONDS)

    def zone(self, zone: str) -> None:
        """Show a load zone on the four zone sensors ("pause": all off)."""
        for sensor in ZONES:
            state = STATE_ON if sensor == zone else STATE_OFF
            if zone == "no_data":
                state = STATE_UNAVAILABLE
            attributes = {"red_threshold": RED_W} if sensor == "red" else {}
            self.hass.states.async_set(
                f"binary_sensor.power_load_{sensor}", state, attributes
            )

    def load(self, watts: float) -> None:
        """Show the grid load."""
        self.hass.states.async_set("sensor.grid", str(watts))

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


async def test_attempts_are_shown_as_they_fail(floors: Floors) -> None:
    """failed_attempts counts the attempts whose window is over unanswered."""
    floors.stuck.add("floor_1")
    await floors.setup()
    sensor = floors.entity_id("sensor", "floor_1_control_state")
    assert floors.hass.states.get(sensor).attributes["failed_attempts"] == 0

    await floors.tick(MINUTE)
    assert floors.calls.count(("turn_on", "floor_1")) == 2
    assert floors.hass.states.get(sensor).attributes["failed_attempts"] == 1


async def test_memory_forgets_a_removed_thermostat(
    floors: Floors, hass_storage: dict
) -> None:
    """A thermostat removed from the configuration leaves nothing in the store."""
    await floors.setup()
    floors.hass.config_entries.async_remove_subentry(floors.entry, "floor_3")
    await floors.hass.async_block_till_done()
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

    assert len(floors.on()) < 2
    assert "floor_3" not in floors.on()
    assert ("turn_on", "floor_3") not in floors.calls


async def test_pause_and_no_data_start_nothing(floors: Floors) -> None:
    """The pause and no data: nothing starts, the heating goes on."""
    await floors.setup(zones=True)
    for zone in ("pause", "no_data"):
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
    """The first command after a start is no new decision: the kept series goes on."""
    memory = ThermostatMemory(hass)
    memory.update(
        "floor_1",
        expected_on=True,
        expected_changed_at=dt_util.utcnow().isoformat(),
        series={"on": 2},
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

    now = dt_util.utcnow()
    assert control.next_command(now) is True
    control.send(True, now)  # noqa: FBT003
    assert control.attributes["failed_attempts"] == 2


async def test_a_start_that_brings_yellow_is_not_cancelled(floors: Floors) -> None:
    """A start not answered yet when the load turns yellow goes on; no other starts."""
    floors.stuck.add("floor_1")
    await floors.setup(zones=True)
    assert floors.calls == [("turn_on", "floor_1")]

    floors.zone("yellow")
    await floors.hass.async_block_till_done()
    floors.stuck.clear()
    floors.hass.states.async_set("switch.floor_1", STATE_ON)
    await floors.tick(MINUTE)

    assert floors.on() == {"floor_1"}
    assert ("turn_off", "floor_1") not in floors.calls


async def test_green_starts_one_at_a_time(floors: Floors) -> None:
    """Green: the next heater starts a step later, when the load shows the first."""
    await floors.setup(zones=True, settle=False)
    assert floors.on() == {"floor_1"}

    await floors.tick(START_STEP_SECONDS, step=START_STEP_SECONDS)

    assert floors.on() == {"floor_1", "floor_2"}


async def test_only_a_heater_that_fits_starts(floors: Floors) -> None:
    """Load + heater power above the red threshold: it waits for a margin."""
    floors.load(3600)  # 3600 + 2000 (a large heater) > 4500: none fits
    await floors.setup(zones=True, settle=False)
    await floors.tick(MINUTE)
    assert floors.on() == set()

    floors.load(2400)  # one fits
    await floors.tick(MINUTE)
    floors.load(3600)  # the load with it: the second does not fit
    await floors.tick(MINUTE)
    assert len(floors.on()) == 1

    floors.load(2400)
    await floors.tick(MINUTE)
    assert len(floors.on()) == 2


async def test_heater_power_from_the_thermostat(floors: Floors) -> None:
    """The thermostat's own heater power counts instead of the large default."""
    floors.load(3800)
    await floors.setup(zones=True, settle=False, heater_power=600)

    assert floors.on() == {"floor_1"}


async def test_overload_turns_all_off_at_once(floors: Floors) -> None:
    """Review 18: not in the devices' emergency queue: all off at once."""
    await floors.setup(capacity=3, zones=True)
    assert floors.on() == set(FLOORS)

    floors.zone("overload")
    await floors.hass.async_block_till_done()

    assert floors.on() == set()


async def test_a_protection_off_is_a_new_start_that_must_fit(floors: Floors) -> None:
    """B1: started, then turned off by the protection: in green again only if it fits."""
    await floors.setup(capacity=1, zones=True)
    assert floors.on() == {"floor_1"}

    floors.zone("red")
    floors.hass.states.async_set("switch.floor_1", STATE_OFF)
    await floors.tick(5, step=5)
    floors.zone("pause")
    await floors.tick(30, step=10)
    floors.load(3600)  # 3600 + 2000 > 4500: it does not fit
    floors.zone("green")
    calls = len(floors.calls)
    await floors.tick(2 * MINUTE, step=10)

    assert ("turn_on", "floor_1") not in floors.calls[calls:]
    assert floors.state("floor_1") == "normal"

    floors.load(1000)
    await floors.tick(MINUTE, step=10)
    assert floors.on() == {"floor_1"}


async def test_a_stuck_switch_does_not_close_the_starts(floors: Floors) -> None:
    """B2: a switch that does not take its start holds the others one attempt only."""
    floors.stuck.add("floor_1")
    await floors.setup(zones=True, settle=False)

    await floors.tick(2 * MINUTE, step=10)

    assert "floor_2" in floors.on()


async def test_a_changed_wish_overtakes_a_running_command(floors: Floors) -> None:
    """B3: "off" while the "on" still runs is sent at once, not in a minute."""
    released, on_started = asyncio.Event(), asyncio.Event()

    async def slow_on() -> None:
        if ("turn_off", "floor_1") not in floors.calls:
            on_started.set()
            await released.wait()

    floors.before["floor_1"] = slow_on
    setup = floors.hass.async_create_task(floors.setup())
    await on_started.wait()

    await floors.hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": floors.climate("floor_1"), "hvac_mode": "off"},
        blocking=True,
    )

    assert ("turn_off", "floor_1") in floors.calls
    released.set()
    await setup


async def test_the_zone_by_the_devices_rule(floors: Floors) -> None:
    """B5: red on wins over a sensor that is unavailable, as in the devices."""
    await floors.setup(capacity=3, zones=True)
    floors.zone("red")
    floors.hass.states.async_set("binary_sensor.power_load_yellow", STATE_UNAVAILABLE)
    await floors.tick(5, step=5)

    assert len(floors.on()) < 3


async def test_zone_sensors_created_later_are_followed(floors: Floors) -> None:
    """B5: the load limits configured on a running system: the zones count at once."""
    await floors.setup(capacity=3)
    assert floors.on() == set(FLOORS)

    registry = er.async_get(floors.hass)
    for zone in ZONES:
        registry.async_get_or_create(
            "binary_sensor",
            "bestiarium_mode",
            f"power_load_{zone}",
            suggested_object_id=f"power_load_{zone}",
        )
    await floors.hass.async_block_till_done()
    floors.zone("red")
    await floors.hass.async_block_till_done()

    assert len(floors.on()) == 2


async def test_nothing_runs_after_the_unload(floors: Floors) -> None:
    """B8: the platforms go first, then the coordinator: no command after it."""
    await floors.setup()
    await floors.hass.config_entries.async_unload(floors.entry.entry_id)
    floors.calls.clear()

    await floors.tick(2 * MINUTE)

    assert floors.calls == []


async def test_a_deferred_place_goes_without_a_needless_rotation(
    floors: Floors,
) -> None:
    """
    The dead switch is deferred before the sharing: the heating one stays on.

    Its deferral comes in the cycle in which a rotation is due (as on the
    stand): counted in, it would push the heating floor out for one cycle.
    """
    floors.stuck.add("floor_1")
    await floors.setup(min_working_minutes=2)
    await _temperatures(floors, floor_1="17")
    floors.hass.config_entries.async_update_entry(floors.entry, options={"attempts": 3})

    await floors.tick(4 * MINUTE)

    assert floors.state("floor_1") == "unresponsive"
    assert floors.on() == {"floor_2", "floor_3"}
    assert ("turn_off", "floor_2") not in floors.calls


async def test_a_heater_without_power_holds_no_place(floors: Floors) -> None:
    """C1: capacity 1, the first AC's power is cut: the second starts."""
    cut = {"switch.floor_1"}
    with patch(
        "custom_components.bestiarium_climate.balancer.power_cut",
        lambda _hass, switch: switch in cut,
    ):
        await floors.setup(capacity=1)
        assert floors.on() == {"floor_2"}

        cut.clear()
        await floors.tick(MINUTE)
        assert len(floors.on()) == 1


async def test_a_long_command_spends_no_attempts(floors: Floors) -> None:
    """C2: an "on" still running after minutes is waited for, not sent again."""
    released, on_started = asyncio.Event(), asyncio.Event()

    async def slow_on() -> None:
        on_started.set()
        await released.wait()

    floors.before["floor_1"] = slow_on
    setup = floors.hass.async_create_task(floors.setup())
    await on_started.wait()
    sensor = floors.entity_id("sensor", "floor_1_control_state")
    for _ in range(4):
        floors.freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(floors.hass)
        await asyncio.sleep(0)

    assert floors.calls.count(("turn_on", "floor_1")) == 1
    assert floors.hass.states.get(sensor).attributes["failed_attempts"] == 0
    assert floors.state("floor_1") == "normal"
    released.set()
    await setup


async def test_an_overtaking_command_is_an_attempt(floors: Floors) -> None:
    """C5: "off" over a running "on" counts as an attempt; not taken, it stays."""
    released, on_started = asyncio.Event(), asyncio.Event()

    async def slow_on() -> None:
        # On already, the rest of its sequence still running
        floors.hass.states.async_set("switch.floor_1", STATE_ON)
        on_started.set()
        await released.wait()

    floors.before["floor_1"] = slow_on
    setup = floors.hass.async_create_task(floors.setup())
    await on_started.wait()
    floors.before.clear()
    floors.stuck.add("floor_1")  # the "off" is not taken
    await floors.hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": floors.climate("floor_1"), "hvac_mode": "off"},
        blocking=True,
    )

    sensor = floors.entity_id("sensor", "floor_1_control_state")
    assert ("turn_off", "floor_1") in floors.calls
    released.set()
    await setup
    await floors.tick(MINUTE)
    assert floors.hass.states.get(sensor).attributes["failed_attempts"] == 1


async def test_no_command_after_an_unload_during_a_slow_one(floors: Floors) -> None:
    """C3: unloaded while an "on" runs: it is cancelled, nothing is sent after."""
    released, on_started = asyncio.Event(), asyncio.Event()

    async def slow_on() -> None:
        on_started.set()
        await released.wait()

    floors.before["floor_1"] = slow_on
    floors.hass.async_create_task(floors.setup())
    await on_started.wait()
    calls = len(floors.calls)

    await floors.hass.config_entries.async_unload(floors.entry.entry_id)
    released.set()
    await floors.tick(2 * MINUTE)

    assert floors.calls[calls:] == []


def _minutes(floors: Floors, seconds: int) -> None:
    """Let time pass without waiting for the commands under way."""
    floors.freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(floors.hass)


async def test_an_unexpected_error_frees_the_switch(
    floors: Floors, caplog: pytest.LogCaptureFixture
) -> None:
    """D1: a command that raises (not a HomeAssistantError) is tried again."""
    broken = True

    async def breaks() -> None:
        if broken:
            msg = "the cloud client broke"
            raise RuntimeError(msg)

    floors.before["floor_1"] = breaks
    await floors.setup()
    assert "the cloud client broke" in caplog.text

    await floors.tick(MINUTE)

    sensor = floors.entity_id("sensor", "floor_1_control_state")
    assert floors.calls.count(("turn_on", "floor_1")) == 2
    assert floors.hass.states.get(sensor).attributes["failed_attempts"] == 1

    broken = False
    await floors.tick(MINUTE)
    assert "floor_1" in floors.on()


async def test_a_command_that_never_ends_is_a_problem(floors: Floors) -> None:
    """D3: an "on" that never ends: past the attempts' budget, deferred, then "off"."""
    released, on_started = asyncio.Event(), asyncio.Event()

    async def hangs() -> None:
        if ("turn_off", "floor_1") not in floors.calls:
            on_started.set()
            await released.wait()

    floors.before["floor_1"] = hangs
    setup = floors.hass.async_create_task(floors.setup())
    await on_started.wait()
    floors.hass.config_entries.async_update_entry(floors.entry, options={"attempts": 3})
    for _ in range(4):
        _minutes(floors, MINUTE)
        for _ in range(5):
            await asyncio.sleep(0)

    assert floors.state("floor_1") == "unresponsive"
    assert ("turn_off", "floor_1") in floors.calls
    released.set()
    await setup


async def test_a_hung_command_no_longer_wanted_is_still_a_problem(
    floors: Floors,
) -> None:
    """D3 on the stand: the rotation turns out a hung "on" past its budget: deferred."""
    released, on_started = asyncio.Event(), asyncio.Event()

    async def hangs() -> None:
        if ("turn_off", "floor_1") not in floors.calls:
            on_started.set()
            await released.wait()

    floors.before["floor_1"] = hangs
    setup = floors.hass.async_create_task(floors.setup())
    await on_started.wait()
    floors.hass.config_entries.async_update_entry(floors.entry, options={"attempts": 3})
    # Past the budget (3 x 55 s) with no cycle; then its thermostat goes off
    floors.freezer.tick(timedelta(minutes=3))
    await floors.hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": floors.climate("floor_1"), "hvac_mode": "off"},
    )
    for _ in range(5):
        await asyncio.sleep(0)

    assert floors.state("floor_1") == "unresponsive"
    assert ("turn_off", "floor_1") in floors.calls
    released.set()
    await setup


async def test_the_answer_is_awaited_from_the_end_of_the_command(
    floors: Floors,
) -> None:
    """D4: an "on" of 70 s, the switch on 10 s after it: one command, one attempt."""
    released, on_started = asyncio.Event(), asyncio.Event()

    async def seventy_seconds() -> None:
        on_started.set()
        await released.wait()

    floors.before["floor_1"] = seventy_seconds
    floors.stuck.add("floor_1")
    setup = floors.hass.async_create_task(floors.setup())
    await on_started.wait()
    _minutes(floors, 70)
    await asyncio.sleep(0)
    released.set()
    await setup
    await floors.tick(10, step=10)
    sensor = floors.entity_id("sensor", "floor_1_control_state")
    assert floors.calls.count(("turn_on", "floor_1")) == 1
    # Its window runs from its end: not failed yet
    assert floors.hass.states.get(sensor).attributes["failed_attempts"] == 0

    floors.hass.states.async_set("switch.floor_1", STATE_ON)
    await floors.tick(MINUTE)
    assert floors.calls.count(("turn_on", "floor_1")) == 1
    assert floors.hass.states.get(sensor).attributes["failed_attempts"] == 0


async def test_not_responding_ends_only_with_a_command_carried_out(
    floors: Floors,
) -> None:
    """D6: the deferral over, no heat needed: still "Not responding"; then a command."""
    floors.stuck.add("floor_1")
    await floors.setup()
    floors.hass.config_entries.async_update_entry(floors.entry, options={"attempts": 2})
    await floors.tick(4 * MINUTE)
    assert floors.state("floor_1") == "unresponsive"

    await _hvac(floors, "floor_1", "off")
    await floors.tick(70 * MINUTE)  # the deferral (1 hour) is over
    assert floors.state("floor_1") == "unresponsive"

    floors.stuck.clear()
    await _hvac(floors, "floor_1", "heat")
    await floors.tick(5 * MINUTE)
    assert "floor_1" in floors.on()
    assert floors.state("floor_1") == "normal"


async def _turns() -> None:
    """Let the tasks run without waiting for a command that hangs."""
    for _ in range(10):
        await asyncio.sleep(0)


async def test_an_overtaken_command_that_ends_late_changes_nothing(
    floors: Floors,
) -> None:
    """E4: an "on" ignores its cancel by "off"; wanted on again: no second "on"."""
    released, on_started = asyncio.Event(), asyncio.Event()

    async def ignores_its_cancel() -> None:
        if floors.calls[-1][0] != "turn_on":
            return
        on_started.set()
        while not released.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await released.wait()

    floors.before["floor_1"] = ignores_its_cancel
    setup = floors.hass.async_create_task(floors.setup())
    await on_started.wait()
    for hvac_mode in ("off", "heat"):
        await floors.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": floors.climate("floor_1"), "hvac_mode": hvac_mode},
        )
        await _turns()
    first = [call for call in floors.calls if call[1] == "floor_1"]
    released.set()  # the first "on" ends, the switch on
    assert first == [("turn_on", "floor_1"), ("turn_off", "floor_1")]

    await setup
    await floors.tick(MINUTE)
    assert floors.calls.count(("turn_on", "floor_1")) == 1
    assert "floor_1" in floors.on()
    assert floors.state("floor_1") == "normal"


async def test_a_hung_start_taken_off_by_yellow_goes_on_counting(
    floors: Floors,
) -> None:
    """E4: a hung start, taken off by yellow, is no new series in green: deferred."""
    released, on_started = asyncio.Event(), asyncio.Event()

    async def hangs() -> None:
        if floors.calls[-1][0] == "turn_on":
            on_started.set()
            await released.wait()

    floors.before["floor_1"] = hangs
    setup = floors.hass.async_create_task(
        floors.setup(capacity=1, zones=True, settle=False)
    )
    await on_started.wait()
    floors.hass.config_entries.async_update_entry(floors.entry, options={"attempts": 2})
    for attempt in (1, 2):
        assert floors.calls.count(("turn_on", "floor_1")) == attempt
        assert floors.calls[-1] == ("turn_on", "floor_1")
        _minutes(floors, MINUTE)  # past the start's window: not starting
        await _turns()
        floors.zone("yellow")
        await _turns()
        assert floors.calls[-1] == ("turn_off", "floor_1")
        floors.zone("green")
        await _turns()
        _minutes(floors, MINUTE)  # past the window of the start taken off
        await _turns()

    assert floors.calls.count(("turn_on", "floor_1")) == 2
    assert floors.state("floor_1") == "unresponsive"
    released.set()
    await setup


async def test_a_hung_command_frees_its_place_in_the_same_cycle(
    floors: Floors,
) -> None:
    """E1: A hangs past the budget, B works past its time, C waits: B stays, C starts."""
    released, on_started = asyncio.Event(), asyncio.Event()

    async def hangs() -> None:
        if floors.calls[-1][0] == "turn_on":
            on_started.set()
            await released.wait()

    floors.before["floor_1"] = hangs
    setup = floors.hass.async_create_task(floors.setup(min_working_minutes=1))
    await on_started.wait()
    await _turns()
    floors.hass.config_entries.async_update_entry(floors.entry, options={"attempts": 2})
    await floors.hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": floors.climate("floor_3"), "hvac_mode": "off"},
    )
    await _turns()
    assert floors.on() == {"floor_2"}

    # Past the budget (2 x 55 s) and B's working time, with no cycle between
    floors.freezer.tick(timedelta(seconds=115))
    await floors.hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": floors.climate("floor_3"), "hvac_mode": "heat"},
    )
    await _turns()

    assert floors.state("floor_1") == "unresponsive"
    assert ("turn_off", "floor_2") not in floors.calls
    assert floors.on() == {"floor_2", "floor_3"}
    released.set()
    await setup


async def test_a_command_whose_switch_answered_never_hangs(floors: Floors) -> None:
    """E2: an "on" whose sequence never ends, the switch on 10 s later: it stays."""
    released, on_started = asyncio.Event(), asyncio.Event()

    async def hangs() -> None:
        if floors.calls[-1][0] == "turn_on":
            on_started.set()
            await released.wait()

    floors.before["floor_1"] = hangs
    setup = floors.hass.async_create_task(floors.setup())
    await on_started.wait()
    floors.hass.config_entries.async_update_entry(floors.entry, options={"attempts": 3})
    _minutes(floors, 10)
    floors.hass.states.async_set("switch.floor_1", STATE_ON)
    for _ in range(5):
        _minutes(floors, MINUTE)
        await _turns()

    assert ("turn_off", "floor_1") not in floors.calls
    assert floors.state("floor_1") == "normal"
    released.set()
    await setup


async def test_not_responding_lasts_across_a_restart(
    floors: Floors, hass_storage: dict
) -> None:
    """E5: the deferral over, then a restart: "Not responding" until a command works."""
    hass_storage["bestiarium_climate.thermostats"] = {
        "version": 1,
        "minor_version": 2,
        "data": {
            "floor_1": {
                "expected_on": False,
                "expected_changed_at": dt_util.utcnow().isoformat(),
                "series": {},
                "unresponsive": True,
                "deferred_until": None,
            }
        },
    }
    floors.stuck.add("floor_1")
    await floors.setup()
    await floors.tick(MINUTE)
    assert floors.state("floor_1") == "unresponsive"

    floors.stuck.clear()
    await floors.tick(3 * MINUTE)
    assert "floor_1" in floors.on()
    assert floors.state("floor_1") == "normal"


async def test_the_old_memory_reads_as_series(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """The memory of 1.1 (one count of tries) reads as the series, 1.2."""
    hass_storage["bestiarium_climate.thermostats"] = {
        "version": 1,
        "data": {
            "floor_1": {
                "expected_on": True,
                "expected_changed_at": dt_util.utcnow().isoformat(),
                "tries": 2,
                "deferred_until": None,
            },
            "floor_2": {
                "expected_on": False,
                "expected_changed_at": dt_util.utcnow().isoformat(),
                "tries": 0,
                "deferred_until": (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
            },
        },
    }
    memory = ThermostatMemory(hass)
    await memory.async_load()

    assert memory.get("floor_1")["series"] == {"on": 2}
    assert memory.get("floor_1")["unresponsive"] is False
    assert "tries" not in memory.get("floor_1")
    assert memory.get("floor_2")["series"] == {"off": 0}
    assert memory.get("floor_2")["unresponsive"] is True
