"""Helper functions for bestiarium_devices."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers import selector

from .const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    DEVICE_CLASS_POWER,
    DEVICE_TYPE_VIRTUAL,
    LOAD_PRIORITY_DEFAULT,
    LOAD_PRIORITY_MAX,
    LOAD_PRIORITY_MIN,
    UNIT_WATT,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def validate_power_sensor(hass: HomeAssistant, entity_id: str) -> str | None:
    """Validate that an entity is a valid power sensor."""
    state = hass.states.get(entity_id)
    if not state:
        return "invalid_power_sensor"

    device_class = state.attributes.get("device_class")
    unit = state.attributes.get("unit_of_measurement")
    if device_class is None or device_class != DEVICE_CLASS_POWER or unit != UNIT_WATT:
        return "invalid_power_sensor"

    return None


def build_priority_number_selector() -> selector.NumberSelector:
    """Build a number selector for load priority."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=LOAD_PRIORITY_MIN,
            max=LOAD_PRIORITY_MAX,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
        ),
    )


def extract_priorities_from_user_input(
    user_input: dict[str, Any],
    mode_names: list[str],
) -> dict[str, int]:
    """Extract load priorities from user input."""
    priorities: dict[str, int] = {}
    priorities[CONF_DEFAULT_PRIORITY] = int(
        user_input.get(CONF_DEFAULT_PRIORITY, LOAD_PRIORITY_DEFAULT),
    )

    for mode_name in mode_names:
        key = f"priority_{mode_name}"
        priorities[mode_name] = int(
            user_input.get(key, LOAD_PRIORITY_DEFAULT),
        )

    return priorities


def get_device_id_from_entry(entry: ConfigEntry) -> str:
    """Get device ID from config entry (for unique ID generation)."""
    device_type = entry.data.get(CONF_DEVICE_TYPE)
    if device_type == DEVICE_TYPE_VIRTUAL:
        return entry.entry_id
    return entry.data.get(CONF_DEVICE_ID, entry.entry_id)
