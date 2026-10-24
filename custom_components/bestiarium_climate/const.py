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

# Subentry types
SUBENTRY_TYPE_SWITCH = "switch"
SUBENTRY_TYPE_THERMOSTAT = "thermostat"

# Config keys — entry-level
CONF_CAPACITY = "capacity"
CONF_MIN_WORKING_TIME = "min_working_time"
CONF_RECOVERY_TIMEOUT = "recovery_timeout"

# Config keys — switch subentry
CONF_SWITCH_NAME = "name"
CONF_SWITCH_MODE = "mode"
CONF_HEATING_ACTION = "heating_action"
CONF_COOLING_ACTION = "cooling_action"
CONF_OFF_ACTION = "off_action"
CONF_POWER_ENTITY = "power_entity"
CONF_MIN_POWER = "min_power"

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
    """Heating or cooling mode for a switch or thermostat."""

    HEATING = "heating"
    COOLING = "cooling"
