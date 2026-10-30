"""Constants for bestiarium_mock."""

from __future__ import annotations

from dataclasses import Field, dataclass, field, fields
from enum import StrEnum
from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "bestiarium_mock"

# Grid constants
GRID_MAX_POWER_W = 5000
GRID_RANDOM_MIN_W = 40
GRID_RANDOM_MAX_W = 100
GRID_UPDATE_INTERVAL_SECONDS = 5


class FaultProfile(StrEnum):
    """How a mock socket misbehaves; each but CUSTOM sets all fault parameters."""

    RELIABLE = "reliable"
    SLOW_CLOUD = "slow_cloud"
    UNSTABLE = "unstable"
    STUCK_STATE = "stuck_state"
    UNRESPONSIVE = "unresponsive"
    CUSTOM = "custom"


def _param(default: float, low: float, high: float, unit: str) -> float:
    """Return a fault parameter with the range and unit of its number entity."""
    return field(default=default, metadata={"min": low, "max": high, "unit": unit})


@dataclass(frozen=True)
class FaultParams:
    """
    The fault model of a mock socket.

    A command is ignored with ignore_percent chance, else it takes effect after
    a delay from the range. With stuck_percent chance the socket switches but
    HA keeps the previous state. dropouts_per_hour times an hour on average the
    socket is unavailable for a duration from the range.
    """

    ignore_percent: float = _param(0, 0, 100, "%")
    delay_min_s: float = _param(0, 0, 120, "s")
    delay_max_s: float = _param(0, 0, 120, "s")
    stuck_percent: float = _param(0, 0, 100, "%")
    dropouts_per_hour: float = _param(0, 0, 60, "/h")
    dropout_min_s: float = _param(5, 1, 600, "s")
    dropout_max_s: float = _param(5, 1, 600, "s")


# One number entity per fault parameter
FAULT_PARAMS: tuple[Field, ...] = fields(FaultParams)


PROFILE_PARAMS: dict[FaultProfile, FaultParams] = {
    FaultProfile.RELIABLE: FaultParams(delay_max_s=1),
    FaultProfile.SLOW_CLOUD: FaultParams(delay_min_s=4, delay_max_s=15),
    FaultProfile.UNSTABLE: FaultParams(
        ignore_percent=10,
        delay_min_s=1,
        delay_max_s=5,
        dropouts_per_hour=6,
        dropout_min_s=5,
        dropout_max_s=40,
    ),
    FaultProfile.STUCK_STATE: FaultParams(
        delay_min_s=1, delay_max_s=3, stuck_percent=100
    ),
    FaultProfile.UNRESPONSIVE: FaultParams(ignore_percent=100),
}


@dataclass(frozen=True)
class MockDeviceSpec:
    """Specification for a mock device."""

    device_id: str
    power_w: int
    power_min_w: int | None = None
    power_max_w: int | None = None
    profile: FaultProfile = FaultProfile.RELIABLE
    # An air conditioner run by IR: a switch on its socket loses the IR,
    # independent of the socket's fault profile (the relay of its power)
    has_ir: bool = False

    @property
    def has_variable_power(self) -> bool:
        """Return True if device has variable power range."""
        return self.power_min_w is not None and self.power_max_w is not None


@dataclass(frozen=True)
class MockThermometerSpec:
    """Specification for a mock thermometer device."""

    device_id: str
    default_temp_c: float = 20.0
    temp_min_c: float = -20.0
    temp_max_c: float = 50.0


# Smart sockets with power monitoring, one per test scenario
MOCK_DEVICES: tuple[MockDeviceSpec, ...] = (
    MockDeviceSpec(device_id="night_light", power_w=15),
    MockDeviceSpec(device_id="boiler", power_w=2000),
    MockDeviceSpec(
        device_id="slow_cloud_socket",
        power_w=800,
        profile=FaultProfile.SLOW_CLOUD,
    ),
    MockDeviceSpec(
        device_id="tuya_socket",
        power_w=300,
        profile=FaultProfile.UNSTABLE,
    ),
    # The night of 2026-10-23: a SmartThings socket kept a stale 'off' state
    # while its neighbour of the same priority waited for its turn
    MockDeviceSpec(
        device_id="commode_socket",
        power_w=8,
        profile=FaultProfile.STUCK_STATE,
    ),
    MockDeviceSpec(device_id="bedside_socket", power_w=8),
    MockDeviceSpec(
        device_id="dead_socket",
        power_w=500,
        profile=FaultProfile.UNRESPONSIVE,
    ),
    MockDeviceSpec(
        device_id="cooker",
        power_w=400,
        power_min_w=400,
        # From the reset's ~0.5 kW the grid reaches red (4.5 kW) and the
        # overload (5 kW) with the cooker alone (DEV-010 review 06, R3)
        power_max_w=5500,
    ),
    MockDeviceSpec(device_id="fridge", power_w=100),
    MockDeviceSpec(device_id="floor_heating_1", power_w=1200),
    MockDeviceSpec(device_id="floor_heating_2", power_w=1200),
    MockDeviceSpec(device_id="floor_heating_3", power_w=1200),
    # Free on the stand: the boiler is re-pointed to it (SmartThings to Matter)
    MockDeviceSpec(device_id="matter_socket", power_w=2000),
    # A plain heater on its own socket: the universal thermostat's (DEV-011);
    # no Bestiarium device, one relay lives in one integration
    MockDeviceSpec(device_id="heater", power_w=1500),
    # The power of the stand's IR air conditioner (DEV-012): what it draws is
    # set by the unit (MockIrAc), not by a knob
    MockDeviceSpec(device_id="hisense_power", power_w=5),
    # The power of the second, under the air conditioner thermostat (DEV-013)
    MockDeviceSpec(device_id="hisense_2_power", power_w=5),
    # The power of four air conditioners: 5 W on standby; the stand's IR
    # scripts set 1200 W when one runs (review 18: as in the owner's home).
    # The fourth is the cloud AC of a universal thermostat (DEV-011)
    *(
        MockDeviceSpec(
            device_id=f"ac_{number}",
            power_w=5,
            power_min_w=5,
            power_max_w=1200,
            has_ir=True,
        )
        for number in range(1, 5)
    ),
)


@dataclass(frozen=True)
class MockIrAcSpec:
    """An IR air conditioner of the stand: its emitter, behind a mock socket."""

    device_id: str
    socket: str
    # The thermometer of its room: it warms or cools it (DEV-013)
    room: str | None = None


# The Hisense of the stand (DEV-012): it hears its emitter's Kelon168 frames.
# The second is the air conditioner thermostat's (DEV-013): its room follows it
MOCK_IR_ACS: tuple[MockIrAcSpec, ...] = (
    MockIrAcSpec("hisense", "hisense_power"),
    MockIrAcSpec("hisense_2", "hisense_2_power", room="thermo_6"),
)
# How fast an air conditioner moves its room's temperature
IR_AC_ROOM_STEP_C = 0.1
IR_AC_ROOM_INTERVAL_SECONDS = 10

# What the stand's air conditioner draws, by its state (design 6)
IR_AC_POWER_W: dict[str, float] = {
    "off": 5,
    "heat": 1200,
    "cool": 1200,
    "auto": 1200,
    "dry": 300,
    "fan_only": 40,
    # Heating or cooling with its own sensor at the setpoint
    "idle": 40,
}


class IrFrameOutcome(StrEnum):
    """What the stand's air conditioner did with the last frame it heard."""

    APPLIED = "applied"
    DISPLAY_LIT = "display_lit"  # the display was off: the frame only lit it
    OFF_IGNORED = "off_ignored"  # the knob "Ignores off"
    NOT_RECOGNISED = "not_recognised"


# Thermometer specifications (passive temperature sensors)
MOCK_THERMOMETERS: tuple[MockThermometerSpec, ...] = (
    MockThermometerSpec(device_id="thermo_1"),
    MockThermometerSpec(device_id="thermo_2"),
    MockThermometerSpec(device_id="thermo_3"),
    MockThermometerSpec(device_id="thermo_4"),
    MockThermometerSpec(device_id="thermo_5"),
    MockThermometerSpec(device_id="thermo_6"),
)

# Grid device (special - not a smart socket)
MOCK_GRID = MockDeviceSpec(
    device_id="grid",
    power_w=GRID_MAX_POWER_W,
)


class ReadingProfile(StrEnum):
    """How a sensor of the stand (thermometer, grid) misbehaves."""

    RELIABLE = "reliable"
    UNSTABLE = "unstable"  # short dropouts
    NO_DATA = "no_data"  # unavailable until the profile changes
    FROZEN = "frozen"  # the value no longer changes


# The dropouts of an unstable reading
READING_DROPOUTS_PER_HOUR = 6
READING_DROPOUT_MIN_S = 10
READING_DROPOUT_MAX_S = 40
