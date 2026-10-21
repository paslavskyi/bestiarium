"""Constants for bestiarium_devices."""

from logging import Logger, getLogger

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

# Expected power of a device (learned from its runs)
MIN_CONSUMING_POWER_W = 10  # below this a switched-on device is idle (standby)
STABLE_POWER_SECONDS = 30  # a run's power is the highest level held this long
POWER_MEMORY_SIZE = 10  # runs remembered per device
REAL_RUNS_FOR_ESTIMATE = 3  # from this many real runs only they count
LARGE_DEVICE_POWER_W = 2000  # expected power when nothing is known

# Dispatcher signal: a device's expected power changed (format with entry_id)
SIGNAL_EXPECTED_POWER_CHANGED = f"{DOMAIN}_expected_power_changed_{{}}"

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
EVENT_ZONE_CHANGED = "bestiarium_devices_zone_changed"
EVENT_DEVICE_TURNED_ON = "bestiarium_devices_device_turned_on"
EVENT_DEVICE_TURNED_OFF = "bestiarium_devices_device_turned_off"
EVENT_OVERLOAD_FIRST_QUEUE = "bestiarium_devices_overload_first_queue"
EVENT_OVERLOAD_SECOND_QUEUE = "bestiarium_devices_overload_second_queue"
EVENT_OVERLOAD_CLEARED = "bestiarium_devices_overload_cleared"
EVENT_MODE_APPLIED = "bestiarium_devices_mode_applied"
