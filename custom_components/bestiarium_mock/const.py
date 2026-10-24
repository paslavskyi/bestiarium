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
GRID_ALARM_THRESHOLD_SECONDS = 10
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
        power_max_w=4200,
    ),
    MockDeviceSpec(device_id="fridge", power_w=100),
    MockDeviceSpec(device_id="floor_heating_1", power_w=1200),
    MockDeviceSpec(device_id="floor_heating_2", power_w=1200),
    MockDeviceSpec(device_id="floor_heating_3", power_w=1200),
    # Free on the stand: the boiler is re-pointed to it (SmartThings to Matter)
    MockDeviceSpec(device_id="matter_socket", power_w=2000),
)

# Thermometer specifications (passive temperature sensors)
MOCK_THERMOMETERS: tuple[MockThermometerSpec, ...] = (
    MockThermometerSpec(device_id="thermo_1"),
    MockThermometerSpec(device_id="thermo_2"),
    MockThermometerSpec(device_id="thermo_3"),
    MockThermometerSpec(device_id="thermo_4"),
)

# Grid device (special - not a smart socket)
MOCK_GRID = MockDeviceSpec(
    device_id="grid",
    power_w=GRID_MAX_POWER_W,
)
