"""
The stepwise "off" of a virtual switch (DEV-010, FR-027).

An air conditioner that runs while its thermostat wants it off: "off" three
times 5 minutes apart, the event "out of control", 5 minutes more, its socket
cut and the event "power cut". The thermostat never turns the socket on.
"""

# ruff: noqa: S101

from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntry, ConfigSubentryData
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import Event, HomeAssistant, ServiceRegistry, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
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
    EVENT_POWER_CUT,
    EVENT_SWITCH_OUT_OF_CONTROL,
    SUBENTRY_TYPE_SWITCH,
    SUBENTRY_TYPE_THERMOSTAT,
    ClimateMode,
    PowerCutData,
    SwitchOutOfControlData,
)
from custom_components.bestiarium_climate.repairs import SocketRepairFlow

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SWITCH = "switch.ac"
POWER = "sensor.ac_power"
SOCKET = "switch.ac_socket"
CLIMATE = "climate.heater"
RUNNING = "1000"
MINUTE = 60


class Home:
    """An air conditioner on a virtual switch, its socket, the thermostat, the clock."""

    def __init__(self, hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
        """Start with nothing set up."""
        self.hass = hass
        self.freezer = freezer
        self.entry: ConfigEntry
        # The commands to the socket, and the events of the stepwise "off"
        self.socket_calls: list[str] = []
        self.events: list[Event] = []
        # The IR reaches the air conditioner: its "off" turns it off
        self.obeys_off = False
        self.offs = async_mock_service(hass, "test", "off", self._off)
        self.heats = async_mock_service(hass, "test", "heat")

    @callback
    def _off(self, _call: Any) -> None:
        if self.obeys_off:
            self.hass.states.async_set(POWER, "0")

    async def setup(
        self, *, socket: str | None = SOCKET, power: str = RUNNING, **options: int
    ) -> None:
        """Set up the entry: the AC runs, its thermostat is off."""
        self.hass.states.async_set(POWER, power)
        self.hass.states.async_set("sensor.temp", "25")
        if socket:
            self.hass.states.async_set(socket, STATE_ON)
        self.entry = MockConfigEntry(
            domain=DOMAIN,
            minor_version=2,
            data={
                CONF_CAPACITY: 1,
                CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 20, "seconds": 0},
                CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
            },
            options=options,
            subentries_data=[
                ConfigSubentryData(
                    subentry_id="ac",
                    subentry_type=SUBENTRY_TYPE_SWITCH,
                    title="AC",
                    unique_id=None,
                    data={
                        "name": "AC",
                        "power_entity": POWER,
                        "min_power": 50.0,
                        "power_hysteresis_percent": 20,
                        "power_delay_seconds": 10,
                        "power_tolerance_seconds": 60,
                        "heating_action": [{"action": "test.heat"}],
                        "off_action": [{"action": "test.off"}],
                        **({"power_socket": socket} if socket else {}),
                    },
                ),
                ConfigSubentryData(
                    subentry_id="Heater",
                    subentry_type=SUBENTRY_TYPE_THERMOSTAT,
                    title="Heater",
                    unique_id=None,
                    data={
                        "name": "Heater",
                        "temperature_sensor": "sensor.temp",
                        "target_switch": SWITCH,
                        "thermostat_mode": ClimateMode.HEATING,
                        "cold_tolerance": 0.3,
                        "hot_tolerance": 0.3,
                    },
                ),
            ],
        )
        self.entry.add_to_hass(self.hass)
        for event_type in (EVENT_SWITCH_OUT_OF_CONTROL, EVENT_POWER_CUT):
            self.hass.bus.async_listen(event_type, self.events.append)
        assert await self.hass.config_entries.async_setup(self.entry.entry_id)
        await self.hass.async_block_till_done()

    def of(self, event_type: str) -> list[dict[str, Any]]:
        """Return the data of the events of a type."""
        return [dict(e.data) for e in self.events if e.event_type == event_type]

    @property
    def control_state(self) -> str:
        """Return the thermostat's control state."""
        entity_id = er.async_get(self.hass).async_get_entity_id(
            "sensor", DOMAIN, "Heater_control_state"
        )
        return self.hass.states.get(entity_id).state

    @property
    def control(self) -> Any:
        """Return the thermostat's control in the balancer."""
        return self.hass.data[DOMAIN][self.entry.entry_id].controls["Heater"]

    async def heat(self) -> None:
        """Make the thermostat want heat: the room is cold."""
        self.hass.states.async_set("sensor.temp", "15")
        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": CLIMATE, "hvac_mode": "heat"},
            blocking=True,
        )
        await self.hass.async_block_till_done()

    async def tick(self, seconds: float, step: float = 10) -> None:
        """Let time pass."""
        while seconds > 0:
            move = min(step, seconds)
            self.freezer.tick(timedelta(seconds=move))
            async_fire_time_changed(self.hass)
            await self.hass.async_block_till_done()
            seconds -= move

    async def first_off(self) -> None:
        """Let time pass until the first "off": the first balancing cycle."""
        while not self.offs:
            await self.tick(10)


@pytest.fixture
async def home(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> AsyncGenerator[Home]:
    """Return the home; its socket takes commands; unload the entry at the end."""
    home = Home(hass, freezer)
    original = ServiceRegistry.async_call

    async def async_call(
        registry: ServiceRegistry,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if domain == "switch" and (service_data or {}).get("entity_id") == SOCKET:
            home.socket_calls.append(service)
            hass.states.async_set(
                SOCKET, STATE_ON if service == "turn_on" else STATE_OFF
            )
            if service == "turn_off":
                hass.states.async_set(POWER, "0")
            return None
        return await original(registry, domain, service, service_data, *args, **kwargs)

    with patch.object(ServiceRegistry, "async_call", async_call):
        yield home
        if hass.config_entries.async_get_entry(home.entry.entry_id):
            await hass.config_entries.async_unload(home.entry.entry_id)


async def test_off_three_times_then_the_event_then_the_socket(home: Home) -> None:
    """Criteria 1, 2: "off" at 0, 5, 10 min; the event at 15; the socket at 20."""
    await home.setup()
    await home.first_off()

    await home.tick(4 * MINUTE + 50)
    assert len(home.offs) == 1
    await home.tick(10)
    assert len(home.offs) == 2
    await home.tick(5 * MINUTE)
    assert len(home.offs) == 3
    await home.tick(4 * MINUTE + 50)
    assert home.of(EVENT_SWITCH_OUT_OF_CONTROL) == []

    await home.tick(10)
    assert len(home.offs) == 3
    assert home.of(EVENT_SWITCH_OUT_OF_CONTROL) == [
        SwitchOutOfControlData(
            thermostat=CLIMATE, switch_entity_id=SWITCH, power=1000.0, attempts=3
        )
    ]
    assert home.socket_calls == []

    await home.tick(5 * MINUTE)
    assert home.socket_calls == ["turn_off"]
    assert home.of(EVENT_POWER_CUT) == [
        PowerCutData(
            thermostat=CLIMATE, switch_entity_id=SWITCH, socket_entity_id=SOCKET
        )
    ]
    assert len(home.offs) == 3


async def test_without_a_socket_only_the_event(home: Home) -> None:
    """Criterion 2: no socket: the event, then nothing more."""
    await home.setup(socket=None)
    await home.tick(40 * MINUTE, step=MINUTE)

    assert len(home.offs) == 3
    assert len(home.of(EVENT_SWITCH_OUT_OF_CONTROL)) == 1
    assert home.of(EVENT_POWER_CUT) == []
    assert home.control_state == "not_turning_off"


async def test_not_responding_never_comes_of_an_off(home: Home) -> None:
    """Criterion 6: "off"s not taken give "Does not turn off", never a deferral."""
    await home.setup(socket=None)
    await home.tick(30, step=10)
    assert home.control_state == "normal"  # the tolerance first
    await home.tick(40 * MINUTE, step=MINUTE)

    assert home.control_state == "not_turning_off"
    assert home.control.deferred_until is None


async def test_an_off_taken_at_once_shows_nothing(
    home: Home, caplog: pytest.LogCaptureFixture
) -> None:
    """Criterion 6: an ordinary "off", taken in seconds, is no problem."""
    home.obeys_off = True
    await home.setup()
    await home.tick(3 * MINUTE)

    assert len(home.offs) == 1
    assert home.control_state == "normal"
    assert "control state not_turning_off" not in caplog.text


async def test_it_ends_when_the_device_stops(home: Home) -> None:
    """
    The AC stops for a whole step: the end; on again: from the first "off".

    No step goes while it is off.
    """
    await home.setup()
    await home.first_off()
    await home.tick(5 * MINUTE)
    assert len(home.offs) == 2

    home.hass.states.async_set(POWER, "0")
    await home.tick(8 * MINUTE)
    assert len(home.offs) == 2
    assert home.control_state == "normal"

    home.hass.states.async_set(POWER, RUNNING)  # a person with the remote
    await home.tick(30)
    assert len(home.offs) == 3
    await home.tick(14 * MINUTE)
    assert len(home.offs) == 5
    assert home.of(EVENT_SWITCH_OUT_OF_CONTROL) == []


async def test_it_ends_when_the_thermostat_wants_it_on(home: Home) -> None:
    """The thermostat wants heat again: no more "off"s."""
    await home.setup()
    await home.tick(MINUTE)
    assert len(home.offs) == 1

    await home.heat()
    await home.tick(20 * MINUTE, step=MINUTE)

    assert len(home.offs) == 1
    assert home.control_state == "normal"


async def test_power_back_after_the_cut_is_a_new_chain(home: Home) -> None:
    """
    Criterion 5 (review 03): the socket on again by a person, the AC runs.

    A new stepwise "off" from its first "off", cut again; the thermostat
    never turns the socket on.
    """
    await home.setup()
    await home.first_off()
    await home.tick(21 * MINUTE)
    assert home.socket_calls == ["turn_off"]

    home.hass.states.async_set(SOCKET, STATE_ON)
    home.hass.states.async_set(POWER, RUNNING)
    await home.tick(MINUTE)
    assert len(home.offs) == 4
    await home.tick(21 * MINUTE)

    assert len(home.offs) == 6
    assert home.socket_calls == ["turn_off", "turn_off"]
    assert len(home.of(EVENT_POWER_CUT)) == 2


async def test_a_cut_socket_is_no_power(home: Home) -> None:
    """Owner's answer 2: the socket off: "No power", no attempt to turn on."""
    await home.setup()
    await home.tick(21 * MINUTE)
    await home.heat()
    await home.tick(10 * MINUTE, step=MINUTE)

    assert home.heats == []
    assert home.control_state == "no_power"
    assert home.socket_calls == ["turn_off"]


async def test_turned_on_around_the_pause(home: Home) -> None:
    """
    Criterion 3: "Not responding", then a person turns the AC on.

    It is turned off stepwise; the "off"s neither count nor lengthen the pause.
    """
    await home.setup(power="0", attempts=2)
    await home.heat()  # the AC does not start: its "on" is lost
    await home.tick(3 * MINUTE)
    assert home.control_state == "unresponsive"
    deferred_until = home.control.deferred_until
    offs = len(home.offs)

    home.hass.states.async_set(POWER, RUNNING)
    await home.tick(MINUTE)
    assert len(home.offs) == offs + 1
    await home.tick(10 * MINUTE)

    assert len(home.offs) == offs + 3
    assert home.control_state == "not_turning_off"
    assert home.control.deferred_until == deferred_until

    home.hass.states.async_set(POWER, "0")
    await home.tick(8 * MINUTE)
    assert home.control_state == "unresponsive"


async def test_a_restart_starts_from_the_first_off(home: Home) -> None:
    """After a reload in the middle, the first "off" again; no event early."""
    await home.setup()
    await home.first_off()
    await home.tick(5 * MINUTE)
    assert len(home.offs) == 2

    await home.hass.config_entries.async_reload(home.entry.entry_id)
    await home.hass.async_block_till_done()
    await home.tick(MINUTE)
    assert len(home.offs) == 3
    await home.tick(13 * MINUTE)

    assert len(home.offs) == 5
    assert home.of(EVENT_SWITCH_OUT_OF_CONTROL) == []


async def test_a_kept_off_series_is_not_read(
    home: Home, hass_storage: dict[str, Any]
) -> None:
    """The memory of before DEV-010 (5 "off"s failed) defers nothing."""
    hass_storage["bestiarium_climate.thermostats"] = {
        "version": 1,
        "minor_version": 2,
        "key": "bestiarium_climate.thermostats",
        "data": {"Heater": {"series": {"off": 5}, "expected_on": False}},
    }
    await home.setup()
    await home.tick(2 * MINUTE)

    assert len(home.offs) == 1
    assert home.control.deferred_until is None


async def test_no_steps_while_the_switch_is_unavailable(home: Home) -> None:
    """No power data: no step; back and still on: the step that is due goes."""
    await home.setup()
    await home.tick(MINUTE)
    home.hass.states.async_set(POWER, STATE_UNAVAILABLE)
    await home.tick(10 * MINUTE)
    assert len(home.offs) == 1
    assert home.control_state == "switch_unavailable"

    home.hass.states.async_set(POWER, RUNNING)
    await home.tick(30)
    assert len(home.offs) == 2


async def test_the_off_action_runs_on_a_switch_off(
    hass: HomeAssistant, home: Home
) -> None:
    """FR-029.3: HA runs the "off" of a virtual switch that is off already."""
    await home.setup(power="0")
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": SWITCH}, blocking=True
    )
    assert len(home.offs) == 1


async def test_a_socket_gone_is_a_repair(hass: HomeAssistant, home: Home) -> None:
    """A socket gone from HA: a Repairs issue; its fix saves another socket."""
    await home.setup(socket=None)
    entry = home.entry
    hass.config_entries.async_update_subentry(
        entry,
        entry.subentries["ac"],
        data={**entry.subentries["ac"].data, "power_socket": "switch.gone"},
    )
    await hass.async_block_till_done()
    await home.tick(MINUTE)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "socket_gone_ac")
    assert issue is not None
    assert issue.is_fixable

    flow = SocketRepairFlow(issue.data)
    flow.hass = hass
    flow.issue_id = "socket_gone_ac"
    await flow.async_step_init({"power_socket": SOCKET})
    await hass.async_block_till_done()

    assert entry.subentries["ac"].data["power_socket"] == SOCKET
    assert ir.async_get(hass).async_get_issue(DOMAIN, "socket_gone_ac") is None


async def test_an_idle_dip_does_not_start_it_again(home: Home) -> None:
    """
    Review 06, R1: an AC at its setpoint draws 30 W for 90 s, then runs on.

    The attempts go on counting; the event comes on time; the state holds.
    """
    await home.setup()
    await home.first_off()
    await home.tick(2 * MINUTE)
    assert home.control_state == "not_turning_off"

    home.hass.states.async_set(POWER, "30")
    await home.tick(90)
    assert home.control_state == "not_turning_off"
    home.hass.states.async_set(POWER, RUNNING)
    await home.tick(2 * MINUTE + 40)
    assert len(home.offs) == 2
    await home.tick(10 * MINUTE)

    assert len(home.offs) == 3
    assert len(home.of(EVENT_SWITCH_OUT_OF_CONTROL)) == 1
