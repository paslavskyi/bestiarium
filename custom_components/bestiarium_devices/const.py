"""Constants for bestiarium_devices."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from logging import Logger, getLogger
from typing import TYPE_CHECKING, Any, Self, TypedDict

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

LOGGER: Logger = getLogger(__package__)

DOMAIN = "bestiarium_devices"
DOMAIN_BESTIARIUM_MODE = "bestiarium_mode"

# Config entry data keys
CONF_DEVICE_TYPE = "device_type"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_ID = "device_id"
CONF_SWITCH_ENTITY_ID = "switch_entity_id"
CONF_POWER_SENSOR_ENTITY_ID = "power_sensor_entity_id"
CONF_LOAD_PRIORITIES = "load_priorities"
CONF_MODE_OPTIONS_SNAPSHOT = "mode_options_snapshot"
CONF_OVERLOAD_FIRST_QUEUE = "overload_first_queue"
CONF_THERMOSTAT_DRIVEN = "thermostat_driven"

# Config entry options
CONF_ALLOWED = "allowed"
CONF_ESTIMATED_POWER = "estimated_power_w"  # 0 or missing = not set
# An action of the forms, not stored: forget the learned consumption
CONF_RESET_POWER_MEMORY = "reset_power_memory"

# Expected power of a device (learned from its runs)
MIN_CONSUMING_POWER_W = 10  # below this a switched-on device is idle (standby)
STABLE_POWER_SECONDS = 30  # a run's power is the highest level held this long
POWER_MEMORY_SIZE = 10  # runs remembered per device
REAL_RUNS_FOR_ESTIMATE = 3  # from this many real runs only they count
LARGE_DEVICE_POWER_W = 2000  # expected power when nothing is known

# Dispatcher signal: a device's expected power changed (format with entry_id)
SIGNAL_EXPECTED_POWER_CHANGED = f"{DOMAIN}_expected_power_changed_{{}}"
# Dispatcher signals of a device's control (format with entry_id): its control
# state or details changed; a control event happened (type, attributes)
SIGNAL_CONTROL_CHANGED = f"{DOMAIN}_control_changed_{{}}"
SIGNAL_CONTROL_EVENT = f"{DOMAIN}_control_event_{{}}"

# Response times remembered per device for the adaptive wait
RESPONSE_MEMORY_SIZE = 10
# Strategy timers fire within this of their step
STEP_JITTER_SECONDS = 1


def _setting(
    default: float, low: float, high: float, *, step: float = 1, advanced: bool = False
) -> Any:
    """Return a control setting with the bounds of its field in the options form."""
    return field(
        default=default,
        metadata={"min": low, "max": high, "step": step, "advanced": advanced},
    )


@dataclass(frozen=True)
class ControlSettings:
    """
    How the balancer controls a device: options of its entry, with defaults.

    See docs/concepts/stability.md: attempts before a device that does not
    respond is deferred, how long; tolerance and hysteresis of problems;
    dropouts an hour that make a connection unstable; the adaptive wait for
    a response and its hard limit in the red zone.
    """

    attempts: int = _setting(3, 1, 10)
    defer_minutes: int = _setting(15, 1, 240)
    tolerance_seconds: int = _setting(60, 0, 600)
    hysteresis_seconds: int = _setting(60, 0, 600)
    unstable_dropouts_per_hour: int = _setting(3, 1, 60)
    default_response_seconds: int = _setting(15, 1, 120, advanced=True)
    response_margin: float = _setting(1.5, 1, 5, step=0.1, advanced=True)
    red_max_wait_seconds: int = _setting(30, 5, 120, advanced=True)

    @classmethod
    def of(cls, entry: ConfigEntry) -> Self:
        """Return the settings of a device entry; a missing option is its default."""
        return cls(
            **{
                f.name: entry.options[f.name]
                for f in fields(cls)
                if f.name in entry.options
            }
        )


# Key for storing the balancing coordinator in hass.data[DOMAIN]
DATA_COORDINATOR = "balancing_coordinator"

# Device types
DEVICE_TYPE_VIRTUAL = "virtual"
DEVICE_TYPE_EXTENDED = "extended"

# Load priority constants
LOAD_PRIORITY_MIN = 0
LOAD_PRIORITY_MAX = 9999
LOAD_PRIORITY_DEFAULT = 5000
LOAD_PRIORITY_OFF = 0  # Device should be off in this mode
LOAD_PRIORITY_ALWAYS_ON = 9999  # Device should always be on in this mode

# Config entry data key for default priority
CONF_DEFAULT_PRIORITY = "default"

# Entity domain and device class constants
DOMAIN_SWITCH = "switch"
DOMAIN_SENSOR = "sensor"
DOMAIN_NUMBER = "number"
DEVICE_CLASS_POWER = "power"
UNIT_WATT = "W"

# bestiarium_mode entities are found by unique_id: their entity_ids depend on
# naming and can be renamed by the user.
MODE_SELECT_UNIQUE_ID = "select"
ZONE_SENSOR_UNIQUE_ID = "power_load_{zone}"

# bestiarium_mode entry data and zone sensor attributes the balancer reads
MODE_AC_LOAD_LIMITS = "ac_load_limits"
MODE_AC_POWER_SENSOR = "ac_power_sensor"
MODE_RED_THRESHOLD = "red_threshold"

# Balancing strategy timing constants (in seconds)
GREEN_INTERVAL_SECONDS = 60
RED_INTERVAL_SECONDS = 5
OVERLOAD_WAIT_SECONDS = 5

# Event types for automations
EVENT_DEVICE_TURNED_ON = "bestiarium_devices_device_turned_on"
EVENT_DEVICE_TURNED_OFF = "bestiarium_devices_device_turned_off"
EVENT_OVERLOAD_FIRST_QUEUE = "bestiarium_devices_overload_first_queue"
EVENT_OVERLOAD_SECOND_QUEUE = "bestiarium_devices_overload_second_queue"
EVENT_OVERLOAD_CLEARED = "bestiarium_devices_overload_cleared"
EVENT_MODE_APPLIED = "bestiarium_devices_mode_applied"

# Repairs issues (docs/concepts/problem-reporting.md)
ISSUE_NO_LOAD_ZONES = "no_load_zones"
ISSUE_MISSING_ENTITY = "missing_entity"


# Payloads of the events: a contract with the owner's automations (docs/events.md)
class DeviceSwitchedData(TypedDict):
    """Payload of EVENT_DEVICE_TURNED_ON and EVENT_DEVICE_TURNED_OFF."""

    strategy: str
    device_id: str
    device_name: str
    switch_entity_id: str
    priority: int


class OverloadFirstQueueData(TypedDict):
    """Payload of EVENT_OVERLOAD_FIRST_QUEUE."""

    devices_turned_off: int
    device_ids: list[str]
    device_names: list[str]
    overload_cleared: bool


class OverloadSecondQueueData(TypedDict):
    """Payload of EVENT_OVERLOAD_SECOND_QUEUE."""

    first_queue_count: int
    second_queue_count: int
    device_ids: list[str]
    device_names: list[str]


class OverloadClearedData(TypedDict):
    """Payload of EVENT_OVERLOAD_CLEARED."""

    cleared_after: str
    first_queue_count: int


class ModeAppliedData(TypedDict):
    """Payload of EVENT_MODE_APPLIED."""

    mode: str
    devices_turned_off: list[str]
    devices_turned_on: list[str]


EVENT_DATA: dict[str, type] = {
    EVENT_DEVICE_TURNED_ON: DeviceSwitchedData,
    EVENT_DEVICE_TURNED_OFF: DeviceSwitchedData,
    EVENT_OVERLOAD_FIRST_QUEUE: OverloadFirstQueueData,
    EVENT_OVERLOAD_SECOND_QUEUE: OverloadSecondQueueData,
    EVENT_OVERLOAD_CLEARED: OverloadClearedData,
    EVENT_MODE_APPLIED: ModeAppliedData,
}
