"""bestiarium_mode entities with the entity_ids of a production install."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.bestiarium_devices.const import DOMAIN_BESTIARIUM_MODE

BINARY_SENSOR_GREEN = "binary_sensor.power_load_green"
BINARY_SENSOR_YELLOW = "binary_sensor.power_load_yellow"
BINARY_SENSOR_RED = "binary_sensor.power_load_red"
BINARY_SENSOR_OVERLOAD = "binary_sensor.power_load_overload"
BESTIARIUM_MODE_SELECT_ENTITY_ID = "select.bestiarium_mode"

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
