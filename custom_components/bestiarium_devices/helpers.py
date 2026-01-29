"""Helper functions for bestiarium_devices."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers import selector

from .const import (
    BESTIARIUM_MODE_SELECT_ENTITY_ID,
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    DEVICE_CLASS_POWER,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN_NUMBER,
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
        key = build_priority_form_key(mode_name)
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


def build_priority_unique_id(device_id: str, mode_name: str | None = None) -> str:
    """Build unique ID for a priority entity."""
    suffix = "_default" if mode_name is None else f"_{mode_name}"
    return f"{device_id}_priority{suffix}"


def build_priority_entity_id(device_id: str, mode_name: str | None = None) -> str:
    """Build entity ID for a priority entity (includes domain prefix)."""
    return f"{DOMAIN_NUMBER}.{build_priority_unique_id(device_id, mode_name)}"


def build_priority_form_key(mode_name: str) -> str:
    """Build form field key for a mode priority in config flow."""
    return f"priority_{mode_name}"


def _parse_priority_from_state(hass: HomeAssistant, entity_id: str) -> int | None:
    """Parse priority value from entity state, returning None if invalid."""
    state = hass.states.get(entity_id)
    if not state or state.state in ("unknown", "unavailable"):
        return None
    try:
        return int(float(state.state))
    except (ValueError, TypeError):
        return None


def get_current_priority(
    hass: HomeAssistant,
    device_id: str,
) -> int:
    """Get the current effective priority for a device based on selected mode."""
    # Get current mode from bestiarium_mode select entity
    mode_state = hass.states.get(BESTIARIUM_MODE_SELECT_ENTITY_ID)
    if not mode_state or mode_state.state in ("unknown", "unavailable"):
        return LOAD_PRIORITY_DEFAULT

    current_mode = mode_state.state

    # Try mode-specific priority entity
    mode_priority = _parse_priority_from_state(
        hass, build_priority_entity_id(device_id, current_mode)
    )
    if mode_priority is not None:
        return mode_priority

    # Fall back to default priority entity
    default_priority = _parse_priority_from_state(
        hass, build_priority_entity_id(device_id, None)
    )
    if default_priority is not None:
        return default_priority

    return LOAD_PRIORITY_DEFAULT
