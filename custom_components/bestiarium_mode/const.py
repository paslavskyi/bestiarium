"""Constants for bestiarium_mode."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "bestiarium_mode"

CONF_AC_LOAD_LIMITS = "ac_load_limits"
CONF_AC_POWER_SENSOR = "ac_power_sensor"
CONF_AC_LOAD_LIMIT_W = "ac_load_limit_w"
CONF_AC_YELLOW_ZONE_PERCENT = "ac_yellow_zone_percent"
CONF_AC_RED_ZONE_PERCENT = "ac_red_zone_percent"
CONF_AC_OVERLOAD_RECOVERY_SECONDS = "ac_overload_recovery_seconds"

# Default values
DEFAULT_OVERLOAD_RECOVERY_SECONDS = 30

# AC load limit range (W), shared by the config flow and the limit entity
LOAD_LIMIT_MIN_W = 1
LOAD_LIMIT_MAX_W = 100000

EVENT_LOAD_LIMIT_CHANGED = f"{DOMAIN}_load_limit_changed"
