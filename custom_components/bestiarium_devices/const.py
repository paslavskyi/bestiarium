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

# Cross-integration entity IDs
BESTIARIUM_MODE_SELECT_ENTITY_ID = "select.bestiarium_mode"

# Binary sensor entity IDs for power load zones (from bestiarium_mode)
BINARY_SENSOR_GREEN = "binary_sensor.power_load_green"
BINARY_SENSOR_YELLOW = "binary_sensor.power_load_yellow"
BINARY_SENSOR_RED = "binary_sensor.power_load_red"
BINARY_SENSOR_OVERLOAD = "binary_sensor.power_load_overload"

# Balancing strategy timing constants (in seconds)
GREEN_INTERVAL_SECONDS = 60
RED_INTERVAL_SECONDS = 5
OVERLOAD_WAIT_SECONDS = 5
