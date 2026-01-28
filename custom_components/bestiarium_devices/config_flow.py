"""Config flow for Bestiarium Devices."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_MODE_OPTIONS_SNAPSHOT,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_CLASS_POWER,
    DEVICE_TYPE_EXTENDED,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    DOMAIN_BESTIARIUM_MODE,
    DOMAIN_SENSOR,
    DOMAIN_SWITCH,
    LOAD_PRIORITY_DEFAULT,
)
from .helpers import (
    build_priority_number_selector,
    extract_priorities_from_user_input,
    validate_power_sensor,
)


def get_bestiarium_modes(hass: HomeAssistant) -> list[dict[str, str]]:
    """Get the list of Bestiarium Mode options."""
    entries = hass.config_entries.async_entries(DOMAIN_BESTIARIUM_MODE)
    if not entries:
        return []
    return entries[0].data.get("options", [])


def get_mode_names(hass: HomeAssistant) -> list[str]:
    """Get just the mode names."""
    return [opt["name"] for opt in get_bestiarium_modes(hass) if opt.get("name")]


def find_power_sensors_for_device(
    hass: HomeAssistant,
    device_id: str,
) -> list[str]:
    """Find power sensors for a device."""
    entity_registry = er.async_get(hass)
    power_sensors: list[str] = []

    for entity in entity_registry.entities.values():
        if entity.domain == DOMAIN_SENSOR and entity.device_id == device_id:
            error = validate_power_sensor(hass, entity.entity_id)
            if error is None:
                power_sensors.append(entity.entity_id)

    return power_sensors


def find_devices_with_switch(hass: HomeAssistant) -> list[str]:
    """Find device IDs that have switch entities."""
    entity_registry = er.async_get(hass)
    devices_with_switch: set[str] = set()

    for entity in entity_registry.entities.values():
        if entity.domain == DOMAIN_SWITCH and entity.device_id:
            devices_with_switch.add(entity.device_id)

    # Convert to list of device IDs
    return list(devices_with_switch)


def is_virtual_device(hass: HomeAssistant, device_id: str) -> bool:
    """Check if device is a virtual device created by this integration."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    if not device:
        return False

    # Check if device has our domain identifier
    return any(identifier[0] == DOMAIN for identifier in device.identifiers)


def find_config_entry_for_device(
    hass: HomeAssistant,
    device_id: str,
) -> config_entries.ConfigEntry | None:
    """Find config entry for a virtual device."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    if not device:
        return None

    # Find config entry with matching identifier
    for identifier in device.identifiers:
        if identifier[0] == DOMAIN:
            entry_id = identifier[1]
            return hass.config_entries.async_get_entry(entry_id)
    return None


class BestiariumDevicesFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Bestiarium Devices."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        super().__init__()
        self._device_type: str | None = None
        self._device_id: str | None = None
        self._device_name: str | None = None
        self._switch_entity_id: str | None = None
        self._power_sensor_entity_id: str | None = None
        self._power_sensors: list[str] = []
        self._load_priorities: dict[str, int] = {}

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        # Check if Bestiarium Mode is configured
        if not get_bestiarium_modes(self.hass):
            return self.async_abort(reason="no_bestiarium_mode")

        if user_input is not None:
            next_step = user_input.get("next_step_id")
            if next_step == "create_virtual":
                return await self.async_step_create_virtual()
            if next_step == "extend_device":
                return await self.async_step_extend_device()

        menu_options = {
            "create_virtual": "Create a new virtual device",
            "extend_device": "Extend an existing device",
        }

        return self.async_show_menu(
            step_id="user",
            menu_options=menu_options,
        )

    async def async_step_create_virtual(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle creating a virtual device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_name = user_input.get(CONF_DEVICE_NAME, "").strip()
            switch_entity_id = user_input.get(CONF_SWITCH_ENTITY_ID)
            power_sensor_entity_id = user_input.get(CONF_POWER_SENSOR_ENTITY_ID)

            if not device_name:
                errors[CONF_DEVICE_NAME] = "name_required"

            if not switch_entity_id:
                errors[CONF_SWITCH_ENTITY_ID] = "required"

            if not power_sensor_entity_id:
                errors[CONF_POWER_SENSOR_ENTITY_ID] = "required"

            # Validate power sensor
            if power_sensor_entity_id and not errors:
                error = validate_power_sensor(self.hass, power_sensor_entity_id)
                if error:
                    errors[CONF_POWER_SENSOR_ENTITY_ID] = error

            if not errors:
                self._device_type = DEVICE_TYPE_VIRTUAL
                self._device_name = device_name
                self._switch_entity_id = switch_entity_id
                self._power_sensor_entity_id = power_sensor_entity_id
                return await self.async_step_configure_priorities()

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEVICE_NAME,
                    default=self._device_name or "",
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
                ),
                vol.Required(
                    CONF_SWITCH_ENTITY_ID,
                    default=self._switch_entity_id,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=DOMAIN_SWITCH),
                ),
                vol.Required(
                    CONF_POWER_SENSOR_ENTITY_ID,
                    default=self._power_sensor_entity_id,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=DOMAIN_SENSOR,
                        device_class=DEVICE_CLASS_POWER,
                    ),
                ),
            },
        )

        return self.async_show_form(
            step_id="create_virtual",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_extend_device(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle extending an existing device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_id = user_input.get(CONF_DEVICE_ID)

            if not device_id:
                errors[CONF_DEVICE_ID] = "required"
            else:
                # Check if this is a virtual device created by us
                if is_virtual_device(self.hass, device_id):
                    entry = find_config_entry_for_device(self.hass, device_id)
                    if entry:
                        # Redirect to reconfigure flow for this entry
                        return self.async_abort(
                            reason="already_configured",
                        )

                # Find power sensors for this device
                power_sensors = find_power_sensors_for_device(self.hass, device_id)

                if not power_sensors:
                    errors[CONF_DEVICE_ID] = "no_power_sensor"
                elif len(power_sensors) == 1:
                    # Single power sensor, use it directly
                    self._device_type = DEVICE_TYPE_EXTENDED
                    self._device_id = device_id
                    self._power_sensor_entity_id = power_sensors[0]
                    return await self.async_step_configure_priorities()
                else:
                    # Multiple power sensors, need to select
                    self._device_type = DEVICE_TYPE_EXTENDED
                    self._device_id = device_id
                    self._power_sensors = power_sensors
                    return await self.async_step_select_power_sensor()

        # Check if there are any devices with switches
        devices_with_switch = find_devices_with_switch(self.hass)
        if not devices_with_switch:
            return self.async_abort(reason="no_devices_with_switch")

        data_schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID): selector.DeviceSelector(
                    selector.DeviceSelectorConfig(),
                ),
            },
        )

        return self.async_show_form(
            step_id="extend_device",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_select_power_sensor(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle selecting power sensor when device has multiple."""
        errors: dict[str, str] = {}

        if user_input is not None:
            power_sensor_entity_id = user_input.get(CONF_POWER_SENSOR_ENTITY_ID)

            if not power_sensor_entity_id:
                errors[CONF_POWER_SENSOR_ENTITY_ID] = "required"
            elif power_sensor_entity_id not in self._power_sensors:
                errors[CONF_POWER_SENSOR_ENTITY_ID] = "invalid_power_sensor"
            else:
                self._power_sensor_entity_id = power_sensor_entity_id
                return await self.async_step_configure_priorities()

        data_schema = vol.Schema(
            {
                vol.Required(CONF_POWER_SENSOR_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=DOMAIN_SENSOR,
                        device_class=DEVICE_CLASS_POWER,
                    ),
                ),
            },
        )

        return self.async_show_form(
            step_id="select_power_sensor",
            data_schema=data_schema,
            errors=errors,
        )

    def _build_priority_schema(
        self,
        mode_names: list[str],
        existing_priorities: dict[str, int] | None = None,
    ) -> vol.Schema:
        """Build schema for load priorities."""
        existing = existing_priorities or {}
        schema_dict: dict[str, Any] = {}

        # Default priority
        default_value = existing.get(CONF_DEFAULT_PRIORITY, LOAD_PRIORITY_DEFAULT)
        schema_dict[vol.Required(CONF_DEFAULT_PRIORITY, default=default_value)] = (
            build_priority_number_selector()
        )

        # Per-mode priorities
        for mode_name in mode_names:
            key = f"priority_{mode_name}"
            default_value = existing.get(mode_name, LOAD_PRIORITY_DEFAULT)
            schema_dict[vol.Required(key, default=default_value)] = (
                build_priority_number_selector()
            )

        return vol.Schema(schema_dict)

    async def async_step_configure_priorities(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle configuring load priorities."""
        mode_names = get_mode_names(self.hass)
        if not mode_names:
            return self.async_abort(reason="no_bestiarium_mode")

        if user_input is not None:
            # Extract priorities
            self._load_priorities = extract_priorities_from_user_input(
                user_input,
                mode_names,
            )

            # Create or update config entry
            if self.source == SOURCE_RECONFIGURE:
                entry = self._get_reconfigure_entry()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=self._build_config_entry_data(),
                )

            # Create new entry
            return self.async_create_entry(
                title=self._device_name or f"Device {self._device_id}",
                data=self._build_config_entry_data(),
            )

        # Build schema with existing values if reconfigure
        existing_priorities: dict[str, int] | None = None
        if self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            existing_priorities = entry.data.get(CONF_LOAD_PRIORITIES, {})

        data_schema = self._build_priority_schema(mode_names, existing_priorities)

        return self.async_show_form(
            step_id="configure_priorities",
            data_schema=data_schema,
        )

    def _build_config_entry_data(self) -> dict[str, Any]:
        """Build config entry data dictionary."""
        data: dict[str, Any] = {
            CONF_DEVICE_TYPE: self._device_type,
            CONF_POWER_SENSOR_ENTITY_ID: self._power_sensor_entity_id,
            CONF_LOAD_PRIORITIES: self._load_priorities,
            CONF_MODE_OPTIONS_SNAPSHOT: list(self._load_priorities.keys()),
        }

        if self._device_type == DEVICE_TYPE_VIRTUAL:
            data[CONF_DEVICE_NAME] = self._device_name
            data[CONF_SWITCH_ENTITY_ID] = self._switch_entity_id
        elif self._device_type == DEVICE_TYPE_EXTENDED:
            data[CONF_DEVICE_ID] = self._device_id

        return data

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration of an existing config entry."""
        entry = self._get_reconfigure_entry()

        # Load existing data
        self._device_type = entry.data.get(CONF_DEVICE_TYPE)
        self._device_id = entry.data.get(CONF_DEVICE_ID)
        self._device_name = entry.data.get(CONF_DEVICE_NAME)
        self._switch_entity_id = entry.data.get(CONF_SWITCH_ENTITY_ID)
        self._power_sensor_entity_id = entry.data.get(CONF_POWER_SENSOR_ENTITY_ID)
        self._load_priorities = entry.data.get(CONF_LOAD_PRIORITIES, {}).copy()

        if self._device_type == DEVICE_TYPE_VIRTUAL:
            # Use same form as create
            return await self.async_step_create_virtual()
        # Extended device - go to priorities
        return await self.async_step_configure_priorities()
