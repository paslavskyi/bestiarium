"""bestiarium_mode entities with the entity_ids of a production install."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.bestiarium_devices.const import DOMAIN_BESTIARIUM_MODE

BINARY_SENSOR_GREEN = "binary_sensor.power_load_green"
BINARY_SENSOR_YELLOW = "binary_sensor.power_load_yellow"
BINARY_SENSOR_RED = "binary_sensor.power_load_red"
BINARY_SENSOR_OVERLOAD = "binary_sensor.power_load_overload"
BESTIARIUM_MODE_SELECT_ENTITY_ID = "select.bestiarium_mode"
GRID_POWER_SENSOR = "sensor.grid_power"

# A bestiarium_mode entry watching GRID_POWER_SENSOR (data["ac_load_limits"])
AC_LOAD_LIMITS = {
    "ac_power_sensor": GRID_POWER_SENSOR,
    "ac_load_limit_w": 5000,
    "ac_yellow_zone_percent": 60,
    "ac_red_zone_percent": 85,
    "ac_overload_recovery_seconds": 30,
}
# The attributes the red zone sensor of that entry publishes
RED_SENSOR_ATTRIBUTES = {"red_threshold": 4250}

_UNIQUE_IDS = {
    BINARY_SENSOR_GREEN: "power_load_green",
    BINARY_SENSOR_YELLOW: "power_load_yellow",
    BINARY_SENSOR_RED: "power_load_red",
    BINARY_SENSOR_OVERLOAD: "power_load_overload",
    BESTIARIUM_MODE_SELECT_ENTITY_ID: "select",
}


def register_mode_entities(hass: HomeAssistant) -> None:
    """Register the bestiarium_mode entities so tests can set their states."""
    registry = er.async_get(hass)
    for entity_id, unique_id in _UNIQUE_IDS.items():
        domain, object_id = entity_id.split(".")
        registry.async_get_or_create(
            domain, DOMAIN_BESTIARIUM_MODE, unique_id, suggested_object_id=object_id
        )
