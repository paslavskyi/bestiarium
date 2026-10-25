"""Constants for bestiarium_climate."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import timedelta
from enum import StrEnum
from logging import Logger, getLogger
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

LOGGER: Logger = getLogger(__package__)

DOMAIN = "bestiarium_climate"
DOMAIN_BESTIARIUM_MODE = "bestiarium_mode"
DOMAIN_BESTIARIUM_DEVICES = "bestiarium_devices"

# Subentry types
SUBENTRY_TYPE_SWITCH = "switch"
SUBENTRY_TYPE_THERMOSTAT = "thermostat"

# Config keys — entry-level
CONF_CAPACITY = "capacity"
CONF_MIN_WORKING_TIME = "min_working_time"
CONF_RECOVERY_TIMEOUT = "recovery_timeout"

# Config keys — switch subentry
CONF_SWITCH_NAME = "name"
CONF_HEATING_ACTION = "heating_action"
CONF_COOLING_ACTION = "cooling_action"
CONF_OFF_ACTION = "off_action"
CONF_POWER_ENTITY = "power_entity"
CONF_MIN_POWER = "min_power"
CONF_POWER_HYSTERESIS = "power_hysteresis_percent"
CONF_POWER_DELAY = "power_delay_seconds"
CONF_POWER_TOLERANCE = "power_tolerance_seconds"
DEFAULT_POWER_HYSTERESIS_PERCENT = 20
DEFAULT_POWER_DELAY_SECONDS = 10
DEFAULT_POWER_TOLERANCE_SECONDS = 60

# Config keys — thermostat subentry
CONF_THERMOSTAT_NAME = "name"
CONF_TEMPERATURE_SENSOR = "temperature_sensor"
CONF_TARGET_SWITCH = "target_switch"
CONF_THERMOSTAT_MODE = "thermostat_mode"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_COLD_TOLERANCE = "cold_tolerance"
CONF_HOT_TOLERANCE = "hot_tolerance"
CONF_MIN_CYCLE_DURATION = "min_cycle_duration"
CONF_HEATER_POWER = "heater_power"

# Defaults
DEFAULT_CAPACITY = 1
DEFAULT_MIN_WORKING_TIME = timedelta(minutes=20)
DEFAULT_RECOVERY_TIMEOUT = timedelta(hours=1)
DEFAULT_TOLERANCE = 0.3
CAPACITY_MIN = 0
CAPACITY_MAX = 100
CAPACITY_STEP = 1

# Balancing
BALANCING_INTERVAL_SECONDS = 60
EVENT_DEVICE_ROTATED_IN = "bestiarium_climate_device_rotated_in"
EVENT_DEVICE_ROTATED_OUT = "bestiarium_climate_device_rotated_out"
SIGNAL_CONTROL_CHANGED = f"{DOMAIN}_control_changed_{{}}"

# The thermostats' state the balancer keeps across restarts (Store)
STORAGE_KEY = f"{DOMAIN}.thermostats"
STORAGE_VERSION = 1
SAVE_DELAY_SECONDS = 10


def _setting(default: int, low: int, high: int) -> Any:
    """Return a control setting with the bounds of its field in the options form."""
    return field(default=default, metadata={"min": low, "max": high})


@dataclass(frozen=True)
class ControlSettings:
    """
    How the balancer controls the thermostats' switches: options of the entry.

    See docs/concepts/stability.md. A switch that does not take the expected
    state for `attempts` balancing cycles is deferred for the recovery
    timeout of the entry.
    """

    attempts: int = _setting(7, 1, 20)
    tolerance_seconds: int = _setting(60, 0, 600)
    hysteresis_seconds: int = _setting(60, 0, 600)

    @classmethod
    def of(cls, entry: ConfigEntry) -> Self:
        """Return the settings of the entry; a missing option is its default."""
        return cls(
            **{
                f.name: entry.options[f.name]
                for f in fields(cls)
                if f.name in entry.options
            }
        )


# The load zones are read from the four zone sensors of Bestiarium Mode, found
# by unique_id: the whole contract between the two integrations
ZONE_SENSOR_UNIQUE_ID = "power_load_{zone}"
# In the red zone one heater goes off every 5 s, as the red strategy of devices
RED_STEP_SECONDS = 5
# In green one heater starts at a time; the next once the load has shown it
START_STEP_SECONDS = 10
# A start fits when load + heater power <= the red threshold (devices, DEV-006).
# The load is the power sensor of bestiarium_mode, the threshold its red zone
# sensor's attribute, the power the expected power of the bestiarium_devices
# device whose relay is the switch, else the thermostat's own, else large.
MODE_AC_LOAD_LIMITS = "ac_load_limits"
MODE_AC_POWER_SENSOR = "ac_power_sensor"
MODE_RED_THRESHOLD = "red_threshold"
DEVICES_SWITCH = "switch_entity_id"
DEVICES_EXPECTED_POWER_UNIQUE_ID = "{entry_id}_expected_power"
LARGE_HEATER_POWER_W = 2000
ISSUE_SWITCH_SHARED = "switch_shared"
ISSUE_INVALID_ACTION = "invalid_action"
ISSUE_SWITCH_NOW_HEATS = "switch_now_heats"


class Zone(StrEnum):
    """The load zone the climate follows; NONE: no load limits configured."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    OVERLOAD = "overload"
    PAUSE = "pause"
    NO_DATA = "no_data"
    NONE = "none"


SENSOR_ZONES = (Zone.OVERLOAD, Zone.RED, Zone.YELLOW, Zone.GREEN)


class ControlState(StrEnum):
    """The control state of a thermostat, the most important problem first."""

    SWITCH_UNAVAILABLE = "switch_unavailable"
    UNRESPONSIVE = "unresponsive"
    NO_TEMPERATURE = "no_temperature"
    NORMAL = "normal"


# Presets (same keys as generic_thermostat)
PRESET_KEYS = ("away", "comfort", "eco", "home", "sleep", "activity")
CONF_PRESETS = {p: f"{p}_temp" for p in PRESET_KEYS}

# Entity domains and device classes
DOMAIN_SWITCH = "switch"
DOMAIN_SENSOR = "sensor"
DOMAIN_CLIMATE = "climate"
DEVICE_CLASS_POWER = "power"
DEVICE_CLASS_TEMPERATURE = "temperature"


class ClimateMode(StrEnum):
    """Heating or cooling mode of a thermostat."""

    HEATING = "heating"
    COOLING = "cooling"
