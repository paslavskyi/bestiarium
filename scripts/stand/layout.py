"""What the test stand is made of: devices, thermostats, modes, names."""

# A command-line script: it prints its progress. The stand dashboard and
# scripts are for the owner, in Ukrainian.

from __future__ import annotations

import re
from dataclasses import dataclass

from custom_components.bestiarium_devices.const import LOAD_PRIORITY_ALWAYS_ON

TOKEN_ENV = "HA_TEST_ENV_TOKEN"  # noqa: S105 - the name of the variable

MOCK = "bestiarium_mock"
MODE = "bestiarium_mode"
DEVICES = "bestiarium_devices"
CLIMATE = "bestiarium_climate"

DASHBOARD = "bestiarium-stand"
SCRIPT_PREFIX = "bestiarium_stand_"
ZONES = ("green", "yellow", "red", "overload")
ZONE_CHECK_SECONDS = 15
# Bestiarium Devices entries of the test sockets removed in DEV-007: without
# their devices they have no entities left to recognise them by
RETIRED_TEST_DEVICES = frozenset(
    {
        "Mock AC",
        "Mock Kettle",
        "Mock Router",
        "Mock Heating",
        "Mock Washing Machine",
        "Mock Dishwasher",
        *(f"Mock Socket {number}" for number in range(1, 5)),
    }
)
# A test entity_id in an automation, such as switch.mock_socket_3_switch
TEST_ENTITY_ID = re.compile(r"\b[a-z_]+\.mock_\w+")


@dataclass(frozen=True)
class StandDevice:
    """How a test socket is added to Bestiarium Devices."""

    mock_id: str
    priority: int
    first_queue: bool = False
    thermostat_driven: bool = False


# Added in this order: of two devices with the same priority the balancer
# picks the one added first, so the commode socket goes before the bedside one
STAND_DEVICES = (
    StandDevice("commode_socket", 5200),
    StandDevice("bedside_socket", 5200),
    StandDevice("boiler", 5000, first_queue=True),
    StandDevice("slow_cloud_socket", 4000),
    StandDevice("tuya_socket", 3500),
    StandDevice("night_light", 3000),
    StandDevice("dead_socket", 1000),
    StandDevice("cooker", LOAD_PRIORITY_ALWAYS_ON),
    StandDevice("fridge", LOAD_PRIORITY_ALWAYS_ON),
    StandDevice("floor_heating_1", 2000, first_queue=True, thermostat_driven=True),
    StandDevice("floor_heating_2", 2000, first_queue=True, thermostat_driven=True),
    StandDevice("floor_heating_3", 2000, first_queue=True, thermostat_driven=True),
)
# Thermostat name, thermometer, floor heating socket
THERMOSTATS = (
    ("Stand floor 1", "thermo_1", "floor_heating_1"),
    ("Stand floor 2", "thermo_2", "floor_heating_2"),
    ("Stand floor 3", "thermo_3", "floor_heating_3"),
)
MODES = ("Grid on", "Night")
LOAD_LIMIT_W = 5000
YELLOW_PERCENT = 75
RED_PERCENT = 90
