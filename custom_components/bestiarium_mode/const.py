"""Constants for bestiarium_mode."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "bestiarium_mode"

CONF_AC_LOAD_LIMITS = "ac_load_limits"
CONF_AC_POWER_SENSOR = "ac_power_sensor"
CONF_AC_LOAD_LIMIT_W = "ac_load_limit_w"
CONF_AC_YELLOW_ZONE_PERCENT = "ac_yellow_zone_percent"
CONF_AC_RED_ZONE_PERCENT = "ac_red_zone_percent"
