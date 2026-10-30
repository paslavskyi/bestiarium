"""What the test stand is made of: devices, thermostats, modes, names."""

# A command-line script: it prints its progress. The stand dashboard and
# scripts are for the owner, in Ukrainian.

from __future__ import annotations

import re
from dataclasses import dataclass

from custom_components.bestiarium_devices.const import (
    LOAD_PRIORITY_ALWAYS_ON,
    LOAD_PRIORITY_OFF,
)

TOKEN_ENV = "HA_TEST_ENV_TOKEN"  # noqa: S105 - the name of the variable

MOCK = "bestiarium_mock"
MODE = "bestiarium_mode"
DEVICES = "bestiarium_devices"
CLIMATE = "bestiarium_climate"
IR = "bestiarium_ir"
TEMPLATE = "template"

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
    night: int | None = None  # its priority in the Night mode, if not the default


# Added in this order: of two devices with the same priority the balancer
# picks the one added first, so the commode socket goes before the bedside one
STAND_DEVICES = (
    StandDevice("commode_socket", 5200),
    StandDevice("bedside_socket", 5200),
    StandDevice("boiler", 5000, first_queue=True),
    StandDevice("slow_cloud_socket", 4000),
    StandDevice("tuya_socket", 3500, night=LOAD_PRIORITY_OFF),
    StandDevice("night_light", 3000),
    StandDevice("dead_socket", 1000),
    StandDevice("cooker", LOAD_PRIORITY_ALWAYS_ON),
    StandDevice("fridge", LOAD_PRIORITY_ALWAYS_ON),
    StandDevice("floor_heating_1", 2000, first_queue=True, thermostat_driven=True),
    StandDevice("floor_heating_2", 2000, first_queue=True, thermostat_driven=True),
    StandDevice("floor_heating_3", 2000, first_queue=True, thermostat_driven=True),
    # The power of the air conditioners: always on, in the first queue; the
    # fourth is the cloud AC of a universal thermostat (DEV-011)
    *(
        StandDevice(f"ac_{number}", LOAD_PRIORITY_ALWAYS_ON, first_queue=True)
        for number in range(1, 5)
    ),
    # The power of the stand's Hisense (DEV-012), as of the other ACs
    StandDevice("hisense_power", LOAD_PRIORITY_ALWAYS_ON, first_queue=True),
    # The power of the second Hisense, the air conditioner thermostat's (DEV-013)
    StandDevice("hisense_2_power", LOAD_PRIORITY_ALWAYS_ON, first_queue=True),
)
# One relay lives in one integration (review 18): the floors are only
# devices; the climate thermostats run air conditioners through virtual
# switches whose power sensor is the AC's socket.
# Thermostat name, thermometer, the AC socket
THERMOSTATS = (
    ("Stand AC 1", "thermo_1", "ac_1"),
    ("Stand AC 2", "thermo_2", "ac_2"),
    ("Stand AC 3", "thermo_3", "ac_3"),
)
# The universal thermostats (DEV-011): name, thermometer, the socket whose
# power they read. The heater's switch is its socket's; the cloud AC's a
# template switch over its IR scripts, as the home's Tuya scenes
CLOUD_AC = "ac_4"
CLOUD_AC_SWITCH = "Stand cloud AC switch"
UNIVERSAL_THERMOSTATS = (
    ("Stand heater", "thermo_4", "heater"),
    ("Stand cloud AC", "thermo_5", CLOUD_AC),
)
HEATER_W = 1500
# The Hisense air conditioners of Bestiarium IR (DEV-012): name, mock id; each
# on its mock emitter. The first is driven by hand (scenarios 5, 6); the
# second by the air conditioner thermostat (DEV-013), its room following it
STAND_HISENSE = (("Stand Hisense", "hisense"), ("Stand Hisense 2", "hisense_2"))
# The air conditioner thermostat (DEV-013): name, thermometer, its air
# conditioner (a Hisense above), the socket whose power it reads
AC_THERMOSTAT = (
    "Stand AC thermostat",
    "thermo_6",
    "Stand Hisense 2",
    "hisense_2_power",
)
# The thermostats of the stand before review 18, on the floors
RETIRED_THERMOSTATS = frozenset({"Stand floor 1", "Stand floor 2", "Stand floor 3"})
AC_RUNNING_W = 1200
AC_STANDBY_W = 5
MODES = ("Grid on", "Night")
YELLOW_W = 3750  # the yellow threshold: 75 % of 5000 W
RED_W = 4500  # the red threshold: 90 % of 5000 W
LOAD_LIMIT_W = 5000
YELLOW_PERCENT = 75
RED_PERCENT = 90
