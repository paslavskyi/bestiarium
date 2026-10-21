"""Helper functions for bestiarium_devices."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_ALLOWED,
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_THERMOSTAT_DRIVEN,
    DEVICE_CLASS_POWER,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN_BESTIARIUM_MODE,
    LOAD_PRIORITY_DEFAULT,
    LOAD_PRIORITY_MAX,
    LOAD_PRIORITY_MIN,
    MODE_SELECT_UNIQUE_ID,
    UNIT_WATT,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, State


def mode_entity_id(hass: HomeAssistant, domain: str, unique_id: str) -> str | None:
    """Return the current entity_id of a bestiarium_mode entity."""
    return er.async_get(hass).async_get_entity_id(
        domain, DOMAIN_BESTIARIUM_MODE, unique_id
    )


def is_allowed(entry: ConfigEntry) -> bool:
    """Return the state of the device's 'Allowed' switch."""
    return entry.options.get(CONF_ALLOWED, True)


def balancer_may_turn_on(entry: ConfigEntry) -> bool:
    """
    Return whether the balancer may turn the device on.

    Not when the family disallowed it, nor when its own thermostat turns it on.
    """
    return is_allowed(entry) and not entry.data.get(CONF_THERMOSTAT_DRIVEN, False)


def get_mode_state(hass: HomeAssistant) -> State | None:
    """Return the state of the bestiarium_mode select entity."""
    entity_id = mode_entity_id(hass, "select", MODE_SELECT_UNIQUE_ID)
    return hass.states.get(entity_id) if entity_id else None


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
    """
    Extract load priorities from user input.

    Default priority is always stored. Per-mode priorities are only stored when
    the user provided a value; omitted mode fields are left out of the config.
    """
    priorities: dict[str, int] = {}
    priorities[CONF_DEFAULT_PRIORITY] = int(
        user_input.get(CONF_DEFAULT_PRIORITY, LOAD_PRIORITY_DEFAULT),
    )

    for mode_name in mode_names:
        key = build_priority_form_key(mode_name)
        value = user_input.get(key)
        if value is not None and value != "":
            priorities[mode_name] = int(value)

    return priorities


def get_device_id_from_entry(entry: ConfigEntry) -> str:
    """Get device ID from config entry (for unique ID generation)."""
    device_type = entry.data.get(CONF_DEVICE_TYPE)
    if device_type == DEVICE_TYPE_VIRTUAL:
        return entry.entry_id
    return entry.data.get(CONF_DEVICE_ID, entry.entry_id)


def get_device_name_from_entry(entry: ConfigEntry) -> str:
    """Get display name for a device from config entry (fallback to device_id)."""
    name = entry.data.get(CONF_DEVICE_NAME)
    if name:
        return name
    return get_device_id_from_entry(entry)


def build_priority_form_key(mode_name: str) -> str:
    """Build form field key for a mode priority in config flow."""
    return f"priority_{mode_name}"


def get_current_priority(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """Get the device's effective priority for the selected mode."""
    mode_state = get_mode_state(hass)
    if not mode_state or mode_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return LOAD_PRIORITY_DEFAULT

    priorities = entry.data.get(CONF_LOAD_PRIORITIES, {})
    if mode_state.state in priorities:
        return priorities[mode_state.state]
    return priorities.get(CONF_DEFAULT_PRIORITY, LOAD_PRIORITY_DEFAULT)
