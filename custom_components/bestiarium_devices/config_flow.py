"""Config flow for Bestiarium Devices."""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.helpers.dispatcher import async_dispatcher_send

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_ESTIMATED_POWER,
    CONF_LOAD_PRIORITIES,
    CONF_MODE_OPTIONS_SNAPSHOT,
    CONF_OVERLOAD_FIRST_QUEUE,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_RESET_POWER_MEMORY,
    CONF_SWITCH_ENTITY_ID,
    CONF_THERMOSTAT_DRIVEN,
    DEVICE_CLASS_POWER,
    DEVICE_TYPE_EXTENDED,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    DOMAIN_BESTIARIUM_MODE,
    DOMAIN_SENSOR,
    DOMAIN_SWITCH,
    LOAD_PRIORITY_DEFAULT,
    SIGNAL_EXPECTED_POWER_CHANGED,
    ControlSettings,
)
from .control import async_get_response_memory
from .helpers import (
    build_priority_form_key,
    build_priority_number_selector,
    extract_priorities_from_user_input,
    validate_power_sensor,
)
from .power_memory import async_get_power_memory


def get_bestiarium_modes(hass: HomeAssistant) -> list[dict[str, str]]:
    """Get the list of Bestiarium Mode options."""
    entries = hass.config_entries.async_entries(DOMAIN_BESTIARIUM_MODE)
    if not entries:
        return []
    return entries[0].data.get("options", [])


def get_mode_names(hass: HomeAssistant) -> list[str]:
    """Get just the mode names."""
    return [opt["name"] for opt in get_bestiarium_modes(hass) if opt.get("name")]


def _may_be_relay_or_sensor(entity: er.RegistryEntry) -> bool:
    """
    Return whether an entity may be a device's relay or power sensor.

    Not this integration's own entities, nor config or diagnostic ones: those
    are settings of a device (such as the knobs of a test socket), not its relay.
    """
    return entity.platform != DOMAIN and entity.entity_category is None


def non_candidate_entity_ids(hass: HomeAssistant, domain: str) -> list[str]:
    """Return the entities of `domain` that may not be a relay or power sensor."""
    return [
        entity.entity_id
        for entity in er.async_get(hass).entities.values()
        if entity.domain == domain and not _may_be_relay_or_sensor(entity)
    ]


def _candidate_entities(hass: HomeAssistant, domain: str) -> Iterator[er.RegistryEntry]:
    """Yield registry entries of `domain` that may be a device's relay or sensor."""
    return (
        entity
        for entity in er.async_get(hass).entities.values()
        if entity.domain == domain and _may_be_relay_or_sensor(entity)
    )


def find_power_sensors_for_device(hass: HomeAssistant, device_id: str) -> list[str]:
    """Find valid power sensors of a device."""
    return [
        entity.entity_id
        for entity in _candidate_entities(hass, DOMAIN_SENSOR)
        if entity.device_id == device_id
        and validate_power_sensor(hass, entity.entity_id) is None
    ]


def find_devices_with_switch(hass: HomeAssistant) -> list[str]:
    """Find device IDs that have switch entities."""
    return list(
        {
            entity.device_id
            for entity in _candidate_entities(hass, DOMAIN_SWITCH)
            if entity.device_id
        }
    )


def find_switches_for_device(hass: HomeAssistant, device_id: str) -> list[str]:
    """Find enabled switch entities of a device."""
    return [
        entity.entity_id
        for entity in _candidate_entities(hass, DOMAIN_SWITCH)
        if entity.device_id == device_id and entity.disabled_by is None
    ]


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


def virtual_relay_schema(
    hass: HomeAssistant, switch: str | None, power_sensor: str | None
) -> vol.Schema:
    """Return the switch and power sensor fields of a virtual device."""
    return vol.Schema(
        {
            vol.Required(
                CONF_SWITCH_ENTITY_ID, default=switch
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=DOMAIN_SWITCH,
                    exclude_entities=non_candidate_entity_ids(hass, DOMAIN_SWITCH),
                ),
            ),
            vol.Required(
                CONF_POWER_SENSOR_ENTITY_ID, default=power_sensor
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=DOMAIN_SENSOR,
                    device_class=DEVICE_CLASS_POWER,
                    exclude_entities=non_candidate_entity_ids(hass, DOMAIN_SENSOR),
                ),
            ),
        }
    )


class RelaySteps:
    """
    The steps that pick the device, relay and power sensor of a device entry.

    Shared by the reconfigure flow and the Repairs fix flow; each says in
    _async_relay_chosen what follows.
    """

    hass: HomeAssistant
    _entry_id: str | None  # the entry being changed
    _device_id: str | None
    _switch_entity_id: str | None
    _power_sensor_entity_id: str | None
    _switches: list[str]
    _power_sensors: list[str]
    _reset_power_memory: bool

    async def _async_relay_chosen(self) -> FlowResult:
        raise NotImplementedError

    def _use_device(self, device_id: str) -> str | None:
        """Take the device's switches and power sensors; return an error key."""
        switches = find_switches_for_device(self.hass, device_id)
        power_sensors = find_power_sensors_for_device(self.hass, device_id)
        if not switches:
            return "no_switch"
        if not power_sensors:
            return "no_power_sensor"
        self._device_id = device_id
        self._switches, self._power_sensors = switches, power_sensors
        self._switch_entity_id = _keep_or_only(self._switch_entity_id, switches)
        self._power_sensor_entity_id = _keep_or_only(
            self._power_sensor_entity_id, power_sensors
        )
        return None

    async def _async_entities_of_device(self) -> FlowResult:
        """Ask for the switch and the sensor only if the device has several."""
        if len(self._switches) > 1 or len(self._power_sensors) > 1:
            return await self.async_step_select_entities()
        return await self._async_relay_chosen()

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick the device the entry is on; it may be another one (re-pointing)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._reset_power_memory = user_input[CONF_RESET_POWER_MEMORY]
            if not (error := self._use_device(user_input[CONF_DEVICE_ID])):
                return await self._async_entities_of_device()
            errors[CONF_DEVICE_ID] = error
        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEVICE_ID, default=self._device_id
                    ): selector.DeviceSelector(selector.DeviceSelectorConfig()),
                    vol.Required(
                        CONF_RESET_POWER_MEMORY, default=False
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_select_entities(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle selecting switch and/or sensor when device has multiple."""
        errors: dict[str, str] = {}

        # Determine what needs selection
        needs_switch_selection = len(self._switches) > 1
        needs_sensor_selection = len(self._power_sensors) > 1

        if user_input is not None:
            # Validate and store switch selection if needed
            if needs_switch_selection:
                switch_entity_id = user_input.get(CONF_SWITCH_ENTITY_ID)
                if not switch_entity_id:
                    errors[CONF_SWITCH_ENTITY_ID] = "required"
                elif switch_entity_id not in self._switches:
                    errors[CONF_SWITCH_ENTITY_ID] = "invalid_switch"
                else:
                    self._switch_entity_id = switch_entity_id

            # Validate and store sensor selection if needed
            if needs_sensor_selection:
                power_sensor_entity_id = user_input.get(CONF_POWER_SENSOR_ENTITY_ID)
                if not power_sensor_entity_id:
                    errors[CONF_POWER_SENSOR_ENTITY_ID] = "required"
                elif power_sensor_entity_id not in self._power_sensors:
                    errors[CONF_POWER_SENSOR_ENTITY_ID] = "invalid_power_sensor"
                else:
                    self._power_sensor_entity_id = power_sensor_entity_id

            if not errors:
                return await self._async_relay_chosen()

        # Build dynamic schema based on what needs selection
        schema_dict: dict[Any, Any] = {}

        if needs_switch_selection:
            schema_dict[
                vol.Required(
                    CONF_SWITCH_ENTITY_ID,
                    default=self._switch_entity_id,
                )
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(include_entities=self._switches),
            )

        if needs_sensor_selection:
            schema_dict[
                vol.Required(
                    CONF_POWER_SENSOR_ENTITY_ID,
                    default=self._power_sensor_entity_id,
                )
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(include_entities=self._power_sensors),
            )

        data_schema = vol.Schema(schema_dict)

        return self.async_show_form(
            step_id="select_entities",
            data_schema=data_schema,
            errors=errors,
        )

    async def _async_forget(
        self, entry: config_entries.ConfigEntry, *, device_changed: bool
    ) -> None:
        """
        Forget what no longer holds for the device.

        The response time belongs to the connection, so another device starts
        anew; the consumption only if the owner asked.
        """
        if self._reset_power_memory:
            (await async_get_power_memory(self.hass)).reset(entry.entry_id)
        if device_changed:
            (await async_get_response_memory(self.hass)).remove(entry.entry_id)


def _keep_or_only(current: str | None, candidates: list[str]) -> str | None:
    """Keep the chosen entity if the device has it, else take the only one."""
    if current in candidates:
        return current
    return candidates[0] if len(candidates) == 1 else None


class BestiariumDevicesFlowHandler(
    RelaySteps, config_entries.ConfigFlow, domain=DOMAIN
):
    """Config flow for Bestiarium Devices."""

    VERSION = 1
    MINOR_VERSION = 3

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,  # noqa: ARG004
    ) -> ControlOptionsFlow:
        """Return the form of how the balancer controls the device."""
        return ControlOptionsFlow()

    def __init__(self) -> None:
        """Initialize the config flow."""
        super().__init__()
        self._entry_id: str | None = None
        self._reset_power_memory = False
        self._device_type: str | None = None
        self._device_id: str | None = None
        self._device_name: str | None = None
        self._switch_entity_id: str | None = None
        self._power_sensor_entity_id: str | None = None
        self._switches: list[str] = []
        self._power_sensors: list[str] = []
        self._load_priorities: dict[str, int] = {}
        self._overload_first_queue: bool = False
        self._thermostat_driven: bool = False

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
            },
        ).extend(
            virtual_relay_schema(
                self.hass, self._switch_entity_id, self._power_sensor_entity_id
            ).schema
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
            elif is_virtual_device(
                self.hass, device_id
            ) and find_config_entry_for_device(self.hass, device_id):
                return self.async_abort(reason="already_configured")
            elif error := self._use_device(device_id):
                errors[CONF_DEVICE_ID] = error
            else:
                self._device_type = DEVICE_TYPE_EXTENDED
                device = dr.async_get(self.hass).async_get(device_id)
                self._device_name = (
                    (device.name_by_user or device.name) if device else None
                ) or f"Device {device_id}"
                return await self._async_entities_of_device()

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

    def _build_priority_schema(
        self,
        mode_names: list[str],
        existing_priorities: dict[str, int] | None = None,
    ) -> vol.Schema:
        """Build schema for load priorities."""
        existing = existing_priorities or {}
        schema_dict: dict[str, Any] = {}

        # Default priority (only numeric field with a default value in the UI)
        default_value = existing.get(CONF_DEFAULT_PRIORITY, LOAD_PRIORITY_DEFAULT)
        schema_dict[vol.Required(CONF_DEFAULT_PRIORITY, default=default_value)] = (
            build_priority_number_selector()
        )

        # Per-mode priorities (optional, no default - user leaves empty to omit)
        for mode_name in mode_names:
            key = build_priority_form_key(mode_name)
            existing_val = existing.get(mode_name)
            if existing_val is not None:
                schema_dict[vol.Optional(key, default=existing_val)] = (
                    build_priority_number_selector()
                )
            else:
                schema_dict[vol.Optional(key)] = build_priority_number_selector()

        # Overload first-queue flag (boolean, required with default)
        schema_dict[
            vol.Required(
                CONF_OVERLOAD_FIRST_QUEUE,
                default=self._overload_first_queue,
            )
        ] = selector.BooleanSelector(selector.BooleanSelectorConfig())

        schema_dict[
            vol.Required(CONF_THERMOSTAT_DRIVEN, default=self._thermostat_driven)
        ] = selector.BooleanSelector(selector.BooleanSelectorConfig())

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

            # Extract overload first-queue flag
            self._overload_first_queue = bool(
                user_input.get(CONF_OVERLOAD_FIRST_QUEUE, False),
            )
            self._thermostat_driven = bool(
                user_input.get(CONF_THERMOSTAT_DRIVEN, False),
            )

            # Create or update config entry
            if self.source == SOURCE_RECONFIGURE:
                entry = self._get_reconfigure_entry()
                await self._async_forget(
                    entry,
                    device_changed=self._device_id != entry.data.get(CONF_DEVICE_ID),
                )
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=self._build_config_entry_data(),
                    reason="reconfigure_successful",
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
            CONF_SWITCH_ENTITY_ID: self._switch_entity_id,
            CONF_POWER_SENSOR_ENTITY_ID: self._power_sensor_entity_id,
            CONF_LOAD_PRIORITIES: self._load_priorities,
            CONF_MODE_OPTIONS_SNAPSHOT: list(self._load_priorities.keys()),
            CONF_OVERLOAD_FIRST_QUEUE: self._overload_first_queue,
            CONF_THERMOSTAT_DRIVEN: self._thermostat_driven,
        }

        if self._device_type == DEVICE_TYPE_VIRTUAL:
            data[CONF_DEVICE_NAME] = self._device_name
        elif self._device_type == DEVICE_TYPE_EXTENDED:
            data[CONF_DEVICE_ID] = self._device_id
            data[CONF_DEVICE_NAME] = self._device_name

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
        self._overload_first_queue = bool(
            entry.data.get(CONF_OVERLOAD_FIRST_QUEUE, False),
        )
        self._thermostat_driven = bool(
            entry.data.get(CONF_THERMOSTAT_DRIVEN, False),
        )

        if self._device_type == DEVICE_TYPE_VIRTUAL:
            # Use same form as create
            return await self.async_step_create_virtual()

        # Extended device: its device first, it may be another one
        self._entry_id = entry.entry_id
        return await self.async_step_pick_device()

    async def _async_relay_chosen(self) -> config_entries.ConfigFlowResult:
        return await self.async_step_configure_priorities()


ADVANCED = "advanced"


def _control_schema(settings: ControlSettings, estimated_power: int) -> vol.Schema:
    """
    Return the form of the device's settings; the rarely changed ones folded.

    The estimated power and the reset of the power memory come first.
    """
    main: dict[vol.Marker, Any] = {
        vol.Required(
            CONF_ESTIMATED_POWER, default=estimated_power
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=100_000,
                step=10,
                unit_of_measurement="W",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Required(
            CONF_RESET_POWER_MEMORY, default=False
        ): selector.BooleanSelector(),
    }
    advanced: dict[vol.Marker, Any] = {}
    for setting in fields(ControlSettings):
        bounds = setting.metadata
        form = advanced if bounds["advanced"] else main
        form[vol.Required(setting.name, default=getattr(settings, setting.name))] = (
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=bounds["min"],
                    max=bounds["max"],
                    step=bounds["step"],
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
        )
    main[vol.Required(ADVANCED)] = section(vol.Schema(advanced), {"collapsed": True})
    return vol.Schema(main)


class ControlOptionsFlow(config_entries.OptionsFlow):
    """
    The device's settings: its estimated power and how the balancer controls it.

    Algorithm parameters live in the entry's options, not in entities
    (docs/concepts/settings-vs-entities.md). They apply at once: the balancer
    reads them on every step.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show and store the settings; keep the other options."""
        options = self.config_entry.options
        if user_input is not None:
            if user_input.pop(CONF_RESET_POWER_MEMORY):
                memory = await async_get_power_memory(self.hass)
                memory.reset(self.config_entry.entry_id)
            values = {**user_input.pop(ADVANCED), **user_input}
            settings = {
                setting.name: type(setting.default)(values[setting.name])
                for setting in fields(ControlSettings)
            }
            options = {
                **options,
                **settings,
                CONF_ESTIMATED_POWER: int(values[CONF_ESTIMATED_POWER]),
            }
            # Store before telling the expected power, which reads the options
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=options
            )
            async_dispatcher_send(
                self.hass,
                SIGNAL_EXPECTED_POWER_CHANGED.format(self.config_entry.entry_id),
            )
            return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="init",
            data_schema=_control_schema(
                ControlSettings.of(self.config_entry),
                options.get(CONF_ESTIMATED_POWER, 0),
            ),
        )
