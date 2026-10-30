"""
The air conditioner thermostat in auto (DEV-013).

Fake air conditioners: a climate state the thermostat commands, as an IR
one of Bestiarium IR shows it (assumed, changed within the call), and a
power sensor that draws by that state (1200 W running, 40 W idling, 5 W
off). The thermostat confirms by power; the climate is never the proof.
"""

# ruff: noqa: S101

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import ConfigEntryDisabler, ConfigSubentryData
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import Event, HomeAssistant, ServiceRegistry, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.bestiarium_climate.const import (
    CONF_CAPACITY,
    CONF_MIN_WORKING_TIME,
    CONF_RECOVERY_TIMEOUT,
    DOMAIN,
    SUBENTRY_TYPE_AC,
    SUBENTRY_TYPE_THERMOSTAT,
    ClimateMode,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

MINUTE = 60
ZONES = ("green", "yellow", "red", "overload")
RED_W = 4500
RUNNING_W = 1200
IDLE_W = 40
OFF_W = 5
AC_MODES = ["off", "heat", "cool", "dry", "fan_only", "auto"]
FAN_MODES = ["auto", "low", "medium", "high"]


class Units:
    """Air conditioners (and, if asked, an old thermostat's floor) and the clock."""

    def __init__(self, hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
        """Start with no entry."""
        self.hass = hass
        self.freezer = freezer
        self.entry: MockConfigEntry
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.events: list[str] = []
        self.payloads: dict[str, list[dict[str, Any]]] = {}
        # The units that idle at their setpoint, ignore "off", hear nothing
        self.idling: set[str] = set()
        self.stuck: set[str] = set()
        self.deaf: set[str] = set()
        # Fan modes a unit never takes
        self.refused_fans: dict[str, set[str]] = {}
        self.units: tuple[str, ...] = ()

    async def setup(  # noqa: PLR0913
        self,
        capacity: int = 2,
        *,
        units: tuple[str, ...] = ("ac_1",),
        floor: bool = False,
        zones: bool = False,
        socket: bool = False,
        margin: float = 1.0,
        hvac_modes: list[str] | None = None,
        options: dict[str, int] | None = None,
    ) -> None:
        """Set up the entry: the air conditioners in auto, heating to 21 °C."""
        self.units = units
        for unit in units:
            self._show(unit, "off", temperature=24, fan_mode="auto", modes=hvac_modes)
            self.hass.states.async_set(f"sensor.{unit}_power", str(OFF_W))
            self.hass.states.async_set(f"sensor.{unit}_temperature", "18")
            if socket:
                self.hass.states.async_set(f"switch.{unit}_socket", STATE_ON)
        if floor:
            self.hass.states.async_set("switch.floor_1", STATE_OFF)
            self.hass.states.async_set("sensor.floor_1_temperature", "18")
        if zones:
            MockConfigEntry(
                domain="bestiarium_mode",
                data={"ac_load_limits": {"ac_power_sensor": "sensor.grid"}},
                disabled_by=ConfigEntryDisabler.USER,
            ).add_to_hass(self.hass)
            self.hass.states.async_set("sensor.grid", "1000")
            registry = er.async_get(self.hass)
            for zone in ZONES:
                registry.async_get_or_create(
                    "binary_sensor",
                    "bestiarium_mode",
                    f"power_load_{zone}",
                    suggested_object_id=f"power_load_{zone}",
                )
            self.zone("green")
        subentries = [
            ConfigSubentryData(
                subentry_id=unit,
                subentry_type=SUBENTRY_TYPE_AC,
                title=unit.upper(),
                unique_id=None,
                data={
                    "name": unit.upper(),
                    "temperature_sensor": f"sensor.{unit}_temperature",
                    "air_conditioner": f"climate.{unit}",
                    "setpoint_margin": margin,
                    "cold_tolerance": 0.3,
                    "hot_tolerance": 0.3,
                    "power_entity": f"sensor.{unit}_power",
                    "min_power": 20.0,
                    "power_hysteresis_percent": 20,
                    "power_delay_seconds": 10,
                    "power_tolerance_seconds": 60,
                    **({"power_socket": f"switch.{unit}_socket"} if socket else {}),
                },
            )
            for unit in units
        ]
        if floor:
            subentries.append(
                ConfigSubentryData(
                    subentry_id="floor_1",
                    subentry_type=SUBENTRY_TYPE_THERMOSTAT,
                    title="Floor_1",
                    unique_id=None,
                    data={
                        "name": "Floor_1",
                        "temperature_sensor": "sensor.floor_1_temperature",
                        "target_switch": "switch.floor_1",
                        "thermostat_mode": ClimateMode.HEATING,
                    },
                )
            )
        self.entry = MockConfigEntry(
            domain=DOMAIN,
            minor_version=2,
            data={
                CONF_CAPACITY: capacity,
                CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 10, "seconds": 0},
                CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
            },
            options=options or {},
            subentries_data=subentries,
        )
        self.entry.add_to_hass(self.hass)

        @callback
        def record(event: Event) -> None:
            self.events.append(event.event_type)
            self.payloads.setdefault(event.event_type, []).append(dict(event.data))

        self.hass.bus.async_listen("*", record)
        assert await self.hass.config_entries.async_setup(self.entry.entry_id)
        await self.hass.async_block_till_done()
        for unit in units:
            await self.target(unit, 21)
            await self.hvac(unit, "auto")
        if floor:
            await self.target("floor_1", 21)
            await self.hvac("floor_1", "heat")
        await self.tick(15, step=5)

    # The fake air conditioners

    def _show(
        self,
        unit: str,
        state: str,
        *,
        temperature: float | None = None,
        fan_mode: str | None = None,
        modes: list[str] | None = None,
    ) -> None:
        """Show a unit's climate state, keeping what is not given."""
        old = self.hass.states.get(f"climate.{unit}")
        attributes = dict(old.attributes) if old else {}
        attributes.setdefault("hvac_modes", modes or AC_MODES)
        attributes.setdefault("fan_modes", FAN_MODES)
        attributes.setdefault("min_temp", 16)
        attributes.setdefault("max_temp", 30)
        attributes.setdefault("target_temp_step", 1)
        if temperature is not None:
            attributes["temperature"] = temperature
        if fan_mode is not None:
            attributes["fan_mode"] = fan_mode
        self.hass.states.async_set(f"climate.{unit}", state, attributes)

    def draw(self, unit: str) -> None:
        """Draw the power of the unit's state."""
        state = self.hass.states.get(f"climate.{unit}").state
        if state == "off":
            watts = OFF_W
        elif unit in self.idling:
            watts = IDLE_W
        else:
            watts = RUNNING_W
        self.hass.states.async_set(f"sensor.{unit}_power", str(watts))

    def carry_out(self, unit: str, service: str, data: dict[str, Any]) -> None:
        """Carry out a climate command, as the unit would."""
        self.calls.append(
            (service, unit, {k: v for k, v in data.items() if k != "entity_id"})
        )
        if unit in self.deaf:
            return
        state = self.hass.states.get(f"climate.{unit}")
        mode = data.get("hvac_mode", state.state)
        if mode == "off" and unit in self.stuck:
            return
        fan = data.get("fan_mode")
        if fan in self.refused_fans.get(unit, set()):
            fan = None
        self._show(unit, mode, temperature=data.get("temperature"), fan_mode=fan)
        self.draw(unit)

    def frames(self, unit: str) -> list[tuple[str, dict[str, Any]]]:
        """Return the commands a unit was sent."""
        return [(service, data) for service, u, data in self.calls if u == unit]

    async def change_past_the_thermostat(
        self, unit: str, state: str, **data: Any
    ) -> None:
        """Change a unit from a dashboard: its climate and its power, at once."""
        self._show(unit, state, **data)
        self.draw(unit)
        await self.hass.async_block_till_done()

    async def power(self, unit: str, watts: float) -> None:
        """Show a unit's power."""
        self.hass.states.async_set(f"sensor.{unit}_power", str(watts))
        await self.hass.async_block_till_done()

    # The thermostats

    def entity_id(self, domain: str, unique_id: str) -> str:
        """Return an entity_id of the integration."""
        entity_id = er.async_get(self.hass).async_get_entity_id(
            domain, DOMAIN, unique_id
        )
        assert entity_id is not None
        return entity_id

    def climate(self, unit: str) -> str:
        """Return a unit's thermostat."""
        return self.entity_id("climate", unit)

    def auto_mode(self, unit: str) -> str:
        """Return a unit's select "Auto mode"."""
        return self.entity_id("select", f"{unit}_auto_mode")

    def state(self, unit: str) -> str:
        """Return a unit's control state."""
        return self.hass.states.get(
            self.entity_id("sensor", f"{unit}_control_state")
        ).state

    def attributes(self, unit: str) -> dict[str, Any]:
        """Return the attributes of a unit's control state."""
        return dict(
            self.hass.states.get(
                self.entity_id("sensor", f"{unit}_control_state")
            ).attributes
        )

    def running(self) -> set[str]:
        """Return the units that draw power, and the floor if on."""
        running = {
            u
            for u in self.units
            if float(self.hass.states.get(f"sensor.{u}_power").state) >= IDLE_W
        }
        floor = self.hass.states.get("switch.floor_1")
        if floor is not None and floor.state == STATE_ON:
            running.add("floor_1")
        return running

    async def target(self, unit: str, temperature: float) -> None:
        """Set a thermostat's target."""
        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": self.climate(unit), "temperature": temperature},
            blocking=True,
        )
        await self.hass.async_block_till_done()

    async def hvac(self, unit: str, mode: str) -> None:
        """Set a thermostat's mode."""
        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": self.climate(unit), "hvac_mode": mode},
            blocking=True,
        )
        await self.hass.async_block_till_done()

    async def fan(self, unit: str, fan_mode: str) -> None:
        """Set a thermostat's fan speed."""
        await self.hass.services.async_call(
            "climate",
            "set_fan_mode",
            {"entity_id": self.climate(unit), "fan_mode": fan_mode},
            blocking=True,
        )
        await self.hass.async_block_till_done()

    async def select_auto_mode(self, unit: str, option: str) -> None:
        """Choose what auto does."""
        await self.hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": self.auto_mode(unit), "option": option},
            blocking=True,
        )
        await self.hass.async_block_till_done()

    async def temperatures(self, **rooms: str) -> None:
        """Show room temperatures."""
        for unit, value in rooms.items():
            self.hass.states.async_set(f"sensor.{unit}_temperature", value)
        await self.hass.async_block_till_done()

    def zone(self, zone: str) -> None:
        """Show a load zone."""
        for sensor in ZONES:
            attributes = {"red_threshold": RED_W} if sensor == "red" else {}
            self.hass.states.async_set(
                f"binary_sensor.power_load_{sensor}",
                STATE_ON if sensor == zone else STATE_OFF,
                attributes,
            )

    async def tick(self, seconds: float, step: float = MINUTE) -> None:
        """Let time pass."""
        while seconds > 0:
            move = min(step, seconds)
            self.freezer.tick(timedelta(seconds=move))
            async_fire_time_changed(self.hass)
            await self.hass.async_block_till_done()
            seconds -= move

    async def reload(self) -> None:
        """Reload the entry, as a restart of HA."""
        await self.hass.config_entries.async_reload(self.entry.entry_id)
        await self.hass.async_block_till_done()
        await self.tick(15, step=5)


@pytest.fixture
def units(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> Any:
    """Return the units; climate commands to them are carried out by the fakes."""
    home = Units(hass, freezer)
    original = ServiceRegistry.async_call

    async def async_call(
        registry: ServiceRegistry,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        data = service_data or {}
        entity_id = data.get("entity_id", "")
        if isinstance(entity_id, str):
            unit = entity_id.removeprefix("climate.")
            if domain == "climate" and unit in home.units:
                home.carry_out(unit, service, data)
                return None
            if domain == "switch" and entity_id in (
                "switch.floor_1",
                *(f"switch.{u}_socket" for u in home.units),
            ):
                home.calls.append((service, entity_id, {}))
                hass.states.async_set(
                    entity_id, STATE_ON if service == "turn_on" else STATE_OFF
                )
                return None
        return await original(registry, domain, service, service_data, *args, **kwargs)

    ServiceRegistry.async_call = async_call
    yield home
    ServiceRegistry.async_call = original


HEAT_22 = ("set_temperature", {"hvac_mode": "heat", "temperature": 22})
OFF = ("set_hvac_mode", {"hvac_mode": "off"})


async def test_auto_heats_with_the_margin_and_stops(units: Units) -> None:
    """Room 18, target 21, margin 1: heat to 22, confirmed by power; warm: off."""
    await units.setup()
    assert units.frames("ac_1") == [HEAT_22]
    await units.tick(MINUTE)
    assert units.state("ac_1") == "normal"
    assert units.frames("ac_1") == [HEAT_22]
    await units.temperatures(ac_1="21")
    await units.tick(MINUTE)
    assert units.frames("ac_1")[-1] == OFF
    assert units.running() == set()


async def test_auto_mode_cooling_cools_with_the_margin(units: Units) -> None:
    """Auto mode "cooling", room 26, target 23: cool to 22."""
    await units.setup()
    await units.temperatures(ac_1="21")
    await units.tick(MINUTE)
    await units.select_auto_mode("ac_1", "cooling")
    await units.target("ac_1", 23)
    await units.temperatures(ac_1="26")
    # The pause after its "off" first
    await units.tick(6 * MINUTE)
    assert units.frames("ac_1")[-1] == (
        "set_temperature",
        {"hvac_mode": "cool", "temperature": 22},
    )


async def test_auto_mode_is_kept_across_a_restart(
    hass: HomeAssistant, units: Units
) -> None:
    """The auto mode lives in the thermostats' memory; heating by default."""
    await units.setup()
    assert hass.states.get(units.auto_mode("ac_1")).state == "heating"
    await units.select_auto_mode("ac_1", "cooling")
    await units.reload()
    assert hass.states.get(units.auto_mode("ac_1")).state == "cooling"


async def test_modes_and_fan_modes_are_the_acs(
    hass: HomeAssistant, units: Units
) -> None:
    """Auto and off, the modes the AC has, its fan speeds; no dry if it has none."""
    await units.setup(hvac_modes=["off", "heat", "cool", "fan_only"])
    attributes = hass.states.get(units.climate("ac_1")).attributes
    assert attributes["hvac_modes"] == ["auto", "off", "heat", "cool", "fan_only"]
    assert attributes["fan_modes"] == FAN_MODES


async def test_fan_mode_goes_with_on(units: Units) -> None:
    """A speed chosen on the thermostat follows the setpoint."""
    await units.setup()
    await units.fan("ac_1", "high")
    await units.tick(MINUTE)
    assert units.frames("ac_1")[-1] == ("set_fan_mode", {"fan_mode": "high"})


async def test_start_not_confirmed_by_power_is_an_attempt(units: Units) -> None:
    """An AC that hears nothing: after the attempts, "Not responding"."""
    units.deaf.add("ac_1")
    await units.setup()
    await units.tick(10 * MINUTE)
    assert units.state("ac_1") == "unresponsive"


async def test_setpoint_clamped_and_rounded_to_the_ac(units: Units) -> None:
    """Target 29.5 + 1 is the AC's max 30; 21.4 + 1 is 22 at its step of 1."""
    await units.setup()
    await units.target("ac_1", 29.5)
    await units.tick(MINUTE)
    assert units.frames("ac_1")[-1][1]["temperature"] == 30
    await units.target("ac_1", 21.4)
    await units.tick(MINUTE)
    assert units.frames("ac_1")[-1][1]["temperature"] == 22


async def test_a_setting_the_ac_does_not_take_goes_once(units: Units) -> None:
    """A speed the AC never shows: one repair, no more; still "normal"."""
    units.refused_fans["ac_1"] = {"high"}
    await units.setup()
    await units.fan("ac_1", "high")
    await units.tick(10 * MINUTE)
    assert units.frames("ac_1").count(("set_fan_mode", {"fan_mode": "high"})) == 1
    assert units.state("ac_1") == "normal"
    assert units.attributes("ac_1")["failed_attempts"] == 0


async def test_target_change_while_running_goes_at_once(units: Units) -> None:
    """A new target while it heats: the new setpoint in this cycle."""
    await units.setup()
    await units.target("ac_1", 23)
    assert units.frames("ac_1")[-1] == (
        "set_temperature",
        {"hvac_mode": "heat", "temperature": 24},
    )


async def test_a_change_past_the_thermostat_is_restored_in_auto(
    units: Units,
) -> None:
    """Set to cool 20 from a dashboard while auto heats: heat 22 again."""
    await units.setup()
    await units.change_past_the_thermostat("ac_1", "cool", temperature=20)
    await units.tick(MINUTE)
    assert units.frames("ac_1")[-1] == HEAT_22
    assert units.state("ac_1") == "normal"


async def test_restart_with_the_ac_running_sends_nothing(units: Units) -> None:
    """After a restart the AC shows heat 22 and draws: not one frame."""
    await units.setup()
    sent = len(units.calls)
    await units.reload()
    await units.tick(5 * MINUTE)
    assert len(units.calls) == sent


async def test_ac_unavailable_is_switch_unavailable(
    hass: HomeAssistant, units: Units
) -> None:
    """The emitter gone: after the tolerance "Switch unavailable", no command."""
    await units.setup()
    sent = len(units.calls)
    hass.states.async_set("climate.ac_1", STATE_UNAVAILABLE)
    await units.tick(2 * MINUTE)
    assert units.state("ac_1") == "switch_unavailable"
    assert len(units.calls) == sent


async def test_ac_and_old_thermostats_share_the_capacity(units: Units) -> None:
    """Capacity 1: the AC in auto and the old floor heater; one runs."""
    await units.setup(capacity=1, floor=True)
    assert len(units.running()) == 1


async def test_auto_mode_change_while_running_turns_the_ac_off(units: Units) -> None:
    """Heating at room 18; auto mode "cooling": no demand, off; no out of control."""
    await units.setup()
    await units.select_auto_mode("ac_1", "cooling")
    await units.tick(MINUTE)
    assert units.frames("ac_1")[-1] == OFF
    assert "bestiarium_climate_switch_out_of_control" not in units.events


async def test_auto_without_the_acs_mode_is_idle(
    hass: HomeAssistant, units: Units
) -> None:
    """An AC without heat: auto heating has nothing to do."""
    await units.setup(hvac_modes=["off", "cool"])
    assert hass.states.get(units.climate("ac_1")).attributes["hvac_action"] == "idle"
    assert units.frames("ac_1") == []


async def test_air_conditioner_rename_is_followed(
    hass: HomeAssistant, units: Units
) -> None:
    """A renamed air conditioner is written into its thermostat."""
    registry = er.async_get(hass)
    registry.async_get_or_create("climate", "test", "ac_1", suggested_object_id="ac_1")
    await units.setup()
    registry.async_update_entity("climate.ac_1", new_entity_id="climate.ac_1_new")
    await hass.async_block_till_done()
    assert units.entry.subentries["ac_1"].data["air_conditioner"] == (
        "climate.ac_1_new"
    )


# Idling at the setpoint and the stepwise "off" (design 10)

STEP = 5 * MINUTE


async def test_idle_at_40_w_holds_the_place(units: Units) -> None:
    """Idling at 40 W for half an hour: still running, normal, no frame."""
    await units.setup(capacity=1)
    units.idling.add("ac_1")
    units.draw("ac_1")
    await units.tick(30 * MINUTE)
    assert units.frames("ac_1") == [HEAT_22]
    assert units.state("ac_1") == "normal"
    assert units.attributes("ac_1")["failed_attempts"] == 0


async def test_idle_dip_shorter_than_a_step_holds_the_place(units: Units) -> None:
    """Below the threshold for 4 minutes of a 5-minute step: no frame, no pause."""
    await units.setup()
    await units.power("ac_1", 10)
    await units.tick(4 * MINUTE)
    assert units.frames("ac_1") == [HEAT_22]
    assert units.attributes("ac_1")["pause_until"] is None
    await units.power("ac_1", RUNNING_W)
    await units.tick(STEP)
    assert units.frames("ac_1") == [HEAT_22]
    assert units.attributes("ac_1")["pause_until"] is None


async def test_no_power_for_a_whole_step_is_off_by_another(units: Units) -> None:
    """
    5 W for a whole step: off by another, no failed attempt; then a new start.

    Its pause runs from the "off" it saw, so the step has already served it.
    """
    await units.setup()
    await units.power("ac_1", OFF_W)
    await units.tick(STEP - MINUTE)
    assert units.frames("ac_1") == [HEAT_22]
    await units.tick(2 * MINUTE)
    assert units.frames("ac_1") == [HEAT_22, HEAT_22]
    assert units.attributes("ac_1")["failed_attempts"] == 0


async def test_idle_does_not_end_the_stepwise_off(units: Units) -> None:
    """Ignoring "off" and idling at 40 W: three "off"s a step apart, then the event."""
    await units.setup()
    units.stuck.add("ac_1")
    units.idling.add("ac_1")
    units.draw("ac_1")
    await units.temperatures(ac_1="22")
    await units.tick(3 * STEP + MINUTE)
    assert units.frames("ac_1").count(OFF) == 3
    assert units.events.count("bestiarium_climate_switch_out_of_control") == 1
    assert units.state("ac_1") == "not_turning_off"


async def test_stepwise_off_cuts_the_socket(units: Units) -> None:
    """With a socket: after the event, the socket is cut and the event tells it."""
    await units.setup(socket=True)
    units.stuck.add("ac_1")
    await units.temperatures(ac_1="22")
    await units.tick(4 * STEP + MINUTE)
    assert ("turn_off", "switch.ac_1_socket", {}) in units.calls
    assert units.payloads["bestiarium_climate_power_cut"] == [
        {
            "thermostat": units.climate("ac_1"),
            "switch_entity_id": "climate.ac_1",
            "socket_entity_id": "switch.ac_1_socket",
        }
    ]


async def test_off_thermostat_turns_a_running_ac_off_stepwise(units: Units) -> None:
    """Off, the AC turned on from a dashboard: it works against: "off" at once."""
    await units.setup()
    await units.hvac("ac_1", "off")
    await units.tick(STEP)
    await units.change_past_the_thermostat("ac_1", "heat", temperature=25)
    await units.tick(15, step=5)
    assert units.frames("ac_1")[-1] == OFF
    assert units.running() == set()


async def test_a_gone_socket_is_a_repairs_issue_of_the_ac_thermostat(
    hass: HomeAssistant, units: Units
) -> None:
    """The socket gone from HA: the issue opens the AC thermostat's socket."""
    await units.setup(socket=True)
    hass.states.async_remove("switch.ac_1_socket")
    await units.tick(MINUTE)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "socket_gone_ac_1")
    assert issue is not None
    assert issue.data == {"entry_id": units.entry.entry_id, "subentry_id": "ac_1"}


async def test_an_idle_dip_after_a_restart_waits_a_step(units: Units) -> None:
    """Restarted running, then below the threshold for 4 minutes: no frame."""
    await units.setup()
    await units.reload()
    sent = len(units.calls)
    await units.power("ac_1", 10)
    await units.tick(4 * MINUTE)
    assert len(units.calls) == sent
    assert units.attributes("ac_1")["failed_attempts"] == 0


async def test_the_same_override_twice_is_repaired_twice(units: Units) -> None:
    """Cool 20 from a dashboard, repaired; the same again later: repaired again."""
    await units.setup()
    for _ in range(2):
        await units.change_past_the_thermostat("ac_1", "cool", temperature=20)
        await units.tick(MINUTE)
        assert units.frames("ac_1")[-1] == HEAT_22
    assert units.frames("ac_1").count(HEAT_22) == 3
