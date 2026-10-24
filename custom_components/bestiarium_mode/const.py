"""Constants for bestiarium_mode."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "bestiarium_mode"

CONF_AC_LOAD_LIMITS = "ac_load_limits"
CONF_AC_POWER_SENSOR = "ac_power_sensor"
CONF_AC_LOAD_LIMIT_W = "ac_load_limit_w"
CONF_AC_YELLOW_ZONE_PERCENT = "ac_yellow_zone_percent"
CONF_AC_RED_ZONE_PERCENT = "ac_red_zone_percent"
CONF_AC_OVERLOAD_RECOVERY_SECONDS = "ac_overload_recovery_seconds"  # green's pause
CONF_AC_ZONE_HYSTERESIS_PERCENT = "ac_zone_hysteresis_percent"
CONF_AC_POWER_TOLERANCE_SECONDS = "ac_power_tolerance_seconds"

# Default values
DEFAULT_YELLOW_ZONE_PERCENT = 75
DEFAULT_RED_ZONE_PERCENT = 90
DEFAULT_OVERLOAD_RECOVERY_SECONDS = 30
DEFAULT_ZONE_HYSTERESIS_PERCENT = 3
DEFAULT_POWER_TOLERANCE_SECONDS = 60

# AC load limit range (W), shared by the config flow and the limit entity
LOAD_LIMIT_MIN_W = 1
LOAD_LIMIT_MAX_W = 100000

EVENT_LOAD_LIMIT_CHANGED = f"{DOMAIN}_load_limit_changed"
SIGNAL_ZONE_CHANGED = f"{DOMAIN}_zone_changed_{{}}"
# Sent by the reconfigure form: ({old name: new name}, [removed names])
SIGNAL_MODES_RENAMED = f"{DOMAIN}_options_renamed"
