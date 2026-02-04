"""Constants for bestiarium_mock."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "bestiarium_mock"

# Grid constants
GRID_MAX_POWER_W = 5000
GRID_RANDOM_MIN_W = 40
GRID_RANDOM_MAX_W = 100
GRID_ALARM_THRESHOLD_SECONDS = 10
GRID_UPDATE_INTERVAL_SECONDS = 5


@dataclass(frozen=True)
class MockDeviceSpec:
    """Specification for a mock device."""

    device_id: str
    name: str
    power_w: int
    power_min_w: int | None = None
    power_max_w: int | None = None

    @property
    def has_variable_power(self) -> bool:
        """Return True if device has variable power range."""
        return self.power_min_w is not None and self.power_max_w is not None


@dataclass(frozen=True)
class MockThermometerSpec:
    """Specification for a mock thermometer device."""

    device_id: str
    name: str
    default_temp_c: float = 20.0
    temp_min_c: float = -20.0
    temp_max_c: float = 50.0


# Device specifications (smart sockets with power monitoring)
MOCK_DEVICES: tuple[MockDeviceSpec, ...] = (
    MockDeviceSpec(device_id="boiler", name="Mock Boiler", power_w=1800),
    MockDeviceSpec(device_id="ac", name="Mock AC", power_w=1200),
    MockDeviceSpec(
        device_id="cooker",
        name="Mock Cooker",
        power_w=400,
        power_min_w=400,
        power_max_w=4200,
    ),
    MockDeviceSpec(device_id="router", name="Mock Router", power_w=20),
    MockDeviceSpec(device_id="kettle", name="Mock Kettle", power_w=1000),
    MockDeviceSpec(device_id="heating", name="Mock Heating", power_w=1000),
    MockDeviceSpec(
        device_id="washing_machine",
        name="Mock Washing Machine",
        power_w=60,
        power_min_w=60,
        power_max_w=1200,
    ),
    MockDeviceSpec(
        device_id="dishwasher",
        name="Mock Dishwasher",
        power_w=60,
        power_min_w=60,
        power_max_w=1800,
    ),
    MockDeviceSpec(device_id="fridge", name="Mock Fridge", power_w=80),
    # 4 additional smart sockets with fixed 600W power
    MockDeviceSpec(device_id="socket_1", name="Mock Socket 1", power_w=600),
    MockDeviceSpec(device_id="socket_2", name="Mock Socket 2", power_w=600),
    MockDeviceSpec(device_id="socket_3", name="Mock Socket 3", power_w=600),
    MockDeviceSpec(device_id="socket_4", name="Mock Socket 4", power_w=600),
)

# Thermometer specifications (passive temperature sensors)
MOCK_THERMOMETERS: tuple[MockThermometerSpec, ...] = (
    MockThermometerSpec(device_id="thermo_1", name="Mock Thermometer 1"),
    MockThermometerSpec(device_id="thermo_2", name="Mock Thermometer 2"),
    MockThermometerSpec(device_id="thermo_3", name="Mock Thermometer 3"),
    MockThermometerSpec(device_id="thermo_4", name="Mock Thermometer 4"),
)

# Grid device (special - not a smart socket)
MOCK_GRID = MockDeviceSpec(
    device_id="grid",
    name="Mock Grid",
    power_w=GRID_MAX_POWER_W,
)
