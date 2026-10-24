"""Config flow for Bestiarium Climate."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CAPACITY_MAX,
    CAPACITY_MIN,
    CAPACITY_STEP,
    CONF_CAPACITY,
    CONF_COLD_TOLERANCE,
    CONF_COOLING_ACTION,
    CONF_HEATING_ACTION,
    CONF_HOT_TOLERANCE,
    CONF_MAX_TEMP,
    CONF_MIN_CYCLE_DURATION,
    CONF_MIN_POWER,
    CONF_MIN_TEMP,
    CONF_MIN_WORKING_TIME,
    CONF_OFF_ACTION,
    CONF_POWER_DELAY,
    CONF_POWER_ENTITY,
    CONF_POWER_HYSTERESIS,
    CONF_PRESETS,
    CONF_RECOVERY_TIMEOUT,
    CONF_SWITCH_NAME,
    CONF_TARGET_SWITCH,
    CONF_TEMPERATURE_SENSOR,
    CONF_THERMOSTAT_MODE,
    CONF_THERMOSTAT_NAME,
    DEFAULT_CAPACITY,
    DEFAULT_POWER_DELAY_SECONDS,
    DEFAULT_POWER_HYSTERESIS_PERCENT,
    DEFAULT_TOLERANCE,
    DEVICE_CLASS_POWER,
    DEVICE_CLASS_TEMPERATURE,
    DOMAIN,
    DOMAIN_SENSOR,
    DOMAIN_SWITCH,
    PRESET_KEYS,
    SUBENTRY_TYPE_SWITCH,
    SUBENTRY_TYPE_THERMOSTAT,
    ClimateMode,
    ControlSettings,
)
from .switch import (
    ACTION_OF_MODE,
    ACTIONS,
    async_validate_actions,
    thermostat_modes,
)


def _timedelta_to_dict(td: dict[str, int] | None) -> dict[str, int]:
    """Convert stored duration dict to standard form."""
    if not td:
        return {"hours": 0, "minutes": 20, "seconds": 0}
    return {
        "hours": td.get("hours", 0),
        "minutes": td.get("minutes", 0),
        "seconds": td.get("seconds", 0),
    }


def _dict_to_timedelta(data: dict[str, int]) -> dict[str, int]:
    """Ensure duration dict has hours, minutes, seconds."""
    return {
        "hours": data.get("hours", 0),
        "minutes": data.get("minutes", 0),
        "seconds": data.get("seconds", 0),
    }


class BestiariumClimateFlowHandler(ConfigFlow, domain=DOMAIN):
    """Main config flow for Bestiarium Climate."""

    VERSION = 1
    MINOR_VERSION = 2

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            capacity = user_input.get(CONF_CAPACITY, DEFAULT_CAPACITY)
            if not isinstance(capacity, (int, float)):
                try:
                    capacity = int(capacity)
                except (TypeError, ValueError):
                    errors[CONF_CAPACITY] = "invalid_capacity"
            elif not (CAPACITY_MIN <= capacity <= CAPACITY_MAX):
                errors[CONF_CAPACITY] = "invalid_capacity"

            if not errors:
                min_working = user_input.get(CONF_MIN_WORKING_TIME)
                recovery = user_input.get(CONF_RECOVERY_TIMEOUT)
                data: dict[str, Any] = {
                    CONF_CAPACITY: int(capacity),
                    CONF_MIN_WORKING_TIME: _dict_to_timedelta(min_working or {}),
                    CONF_RECOVERY_TIMEOUT: _dict_to_timedelta(recovery or {}),
                }
                return self.async_create_entry(title="Bestiarium Climate", data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CAPACITY,
                        default=DEFAULT_CAPACITY,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=CAPACITY_MIN,
                            max=CAPACITY_MAX,
                            step=CAPACITY_STEP,
                            mode=selector.NumberSelectorMode.BOX,
                        ),
                    ),
                    vol.Required(
                        CONF_MIN_WORKING_TIME,
                        default=self._get_default_min_working_time(),
                    ): selector.DurationSelector(),
                    vol.Required(
                        CONF_RECOVERY_TIMEOUT,
                        default=self._get_default_recovery_timeout(),
                    ): selector.DurationSelector(),
                },
            ),
            errors=errors,
        )

    def _get_default_min_working_time(self) -> dict[str, int]:
        """Get default min working time for form."""
        if self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            if entry and entry.data.get(CONF_MIN_WORKING_TIME):
                return _timedelta_to_dict(entry.data[CONF_MIN_WORKING_TIME])
        return {"hours": 0, "minutes": 20, "seconds": 0}

    def _get_default_recovery_timeout(self) -> dict[str, int]:
        """Get default recovery timeout for form."""
        if self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            if entry and entry.data.get(CONF_RECOVERY_TIMEOUT):
                return _timedelta_to_dict(entry.data[CONF_RECOVERY_TIMEOUT])
        return {"hours": 1, "minutes": 0, "seconds": 0}

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """
        Change the entry's times; the capacity is its entity's.

        The capacity in the form is only the initial value: a power
        automation changes the entity, so the form would not be the source.
        """
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            return self.async_update_and_abort(
                entry,
                data_updates={
                    CONF_MIN_WORKING_TIME: _dict_to_timedelta(
                        user_input.get(CONF_MIN_WORKING_TIME) or {}
                    ),
                    CONF_RECOVERY_TIMEOUT: _dict_to_timedelta(
                        user_input.get(CONF_RECOVERY_TIMEOUT) or {}
                    ),
                },
                reason="reconfigure_successful",
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MIN_WORKING_TIME,
                        default=_timedelta_to_dict(
                            entry.data.get(CONF_MIN_WORKING_TIME)
                        ),
                    ): selector.DurationSelector(),
                    vol.Required(
                        CONF_RECOVERY_TIMEOUT,
                        default=_timedelta_to_dict(
                            entry.data.get(CONF_RECOVERY_TIMEOUT)
                        ),
                    ): selector.DurationSelector(),
                },
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,  # noqa: ARG004
    ) -> ControlOptionsFlow:
        """Return the form of how the balancer controls the switches."""
        return ControlOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls,
        config_entry: ConfigEntry,  # noqa: ARG003
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentry types supported by this integration."""
        return {
            SUBENTRY_TYPE_SWITCH: SwitchSubentryFlowHandler,
            SUBENTRY_TYPE_THERMOSTAT: ThermostatSubentryFlowHandler,
        }


class SwitchSubentryFlowHandler(ConfigSubentryFlow):
    """Subentry flow for virtual switch."""

    async def async_step_user(  # noqa: PLR0912
        self,
        user_input: dict[str, Any] | None = None,
    ) -> SubentryFlowResult:
        """Handle adding or editing a switch subentry."""
        errors: dict[str, str] = {}
        subentry = self._get_reconfigure_subentry() if self.source != "user" else None

        if user_input is not None:
            name = (user_input.get(CONF_SWITCH_NAME) or "").strip()
            if not name:
                errors[CONF_SWITCH_NAME] = "name_required"

            if not (
                user_input.get(CONF_HEATING_ACTION)
                or user_input.get(CONF_COOLING_ACTION)
            ):
                errors["base"] = "heating_or_cooling_required"
            if subentry:
                errors.update(self._actions_needed(subentry, user_input))
            for key in ACTIONS:
                if user_input.get(key):
                    try:
                        await async_validate_actions(self.hass, user_input[key])
                    except (vol.Invalid, HomeAssistantError):
                        errors[key] = "invalid_action"

            if not user_input.get(CONF_OFF_ACTION):
                errors[CONF_OFF_ACTION] = "required"

            if not user_input.get(CONF_POWER_ENTITY):
                errors[CONF_POWER_ENTITY] = "required"

            min_power = user_input.get(CONF_MIN_POWER)
            if min_power is None:
                errors[CONF_MIN_POWER] = "required"
            elif not isinstance(min_power, (int, float)):
                try:
                    float(min_power)
                except (TypeError, ValueError):
                    errors[CONF_MIN_POWER] = "invalid_min_power"

            if not errors:
                data: dict[str, Any] = {
                    CONF_SWITCH_NAME: name,
                    CONF_POWER_ENTITY: user_input[CONF_POWER_ENTITY],
                    CONF_MIN_POWER: float(user_input[CONF_MIN_POWER]),
                    CONF_POWER_HYSTERESIS: int(user_input[CONF_POWER_HYSTERESIS]),
                    CONF_POWER_DELAY: int(user_input[CONF_POWER_DELAY]),
                }
                for key in ACTIONS:
                    if user_input.get(key):
                        data[key] = user_input[key]

                if subentry is None:
                    return self.async_create_entry(title=name, data=data)
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    data=data,
                    title=name,
                    reason="reconfigure_successful",
                )

        saved = subentry.data if subentry else {}
        # Suggested, not default: an action cleared in the form goes
        actions = {
            vol.Optional(
                key, description={"suggested_value": saved.get(key)}
            ): selector.ActionSelector()
            for key in (CONF_HEATING_ACTION, CONF_COOLING_ACTION)
        }
        schema_dict: dict[vol.Marker, Any] = {
            vol.Required(
                CONF_SWITCH_NAME,
                default=subentry.title if subentry else "",
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
            ),
            vol.Required(
                CONF_POWER_ENTITY,
                default=saved.get(CONF_POWER_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=DOMAIN_SENSOR,
                    device_class=DEVICE_CLASS_POWER,
                ),
            ),
            vol.Required(
                CONF_MIN_POWER,
                default=saved.get(CONF_MIN_POWER, 0.0),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    step=0.1,
                    mode=selector.NumberSelectorMode.BOX,
                ),
            ),
            vol.Required(
                CONF_POWER_HYSTERESIS,
                default=saved.get(
                    CONF_POWER_HYSTERESIS, DEFAULT_POWER_HYSTERESIS_PERCENT
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=90, step=1, mode=selector.NumberSelectorMode.BOX
                ),
            ),
            vol.Required(
                CONF_POWER_DELAY,
                default=saved.get(CONF_POWER_DELAY, DEFAULT_POWER_DELAY_SECONDS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=600, step=1, mode=selector.NumberSelectorMode.BOX
                ),
            ),
            **actions,
            vol.Required(CONF_OFF_ACTION, default=saved.get(CONF_OFF_ACTION)): (
                selector.ActionSelector()
            ),
        }

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    def _actions_needed(
        self, subentry: ConfigSubentry, user_input: dict[str, Any]
    ) -> dict[str, str]:
        """Return an error for an action a thermostat of this switch needs."""
        switch = er.async_get(self.hass).async_get_entity_id(
            DOMAIN_SWITCH, DOMAIN, subentry.subentry_id
        )
        if switch is None:
            return {}
        return {
            ACTION_OF_MODE[mode]: "needed_by_thermostat"
            for mode in thermostat_modes(self.hass, switch)
            if not user_input.get(ACTION_OF_MODE[mode])
        }

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> SubentryFlowResult:
        """Handle reconfiguring an existing switch subentry."""
        return await self.async_step_user(user_input)


class ThermostatSubentryFlowHandler(ConfigSubentryFlow):
    """Subentry flow for virtual thermostat (two-step)."""

    def __init__(self) -> None:
        """Initialize the flow."""
        super().__init__()
        self._options: dict[str, Any] = {}

    @property
    def _is_new(self) -> bool:
        """True if creating a new subentry."""
        return self.source == "user"

    def _switch_errors(self, user_input: dict[str, Any]) -> dict[str, str]:
        """
        Return an error if the switch cannot serve this thermostat's mode.

        A switch serves one mode; a virtual switch needs the mode's action.
        """
        switch = user_input[CONF_TARGET_SWITCH]
        mode = ClimateMode(user_input.get(CONF_THERMOSTAT_MODE, ClimateMode.HEATING))
        besides = None if self._is_new else self._get_reconfigure_subentry().subentry_id
        if thermostat_modes(self.hass, switch, besides=besides) - {mode}:
            return {CONF_THERMOSTAT_MODE: "switch_serves_other_mode"}
        entity = er.async_get(self.hass).async_get(switch)
        if entity is None or entity.platform != DOMAIN:
            return {}
        subentry = self._get_entry().subentries.get(entity.unique_id)
        if subentry and not subentry.data.get(ACTION_OF_MODE[mode]):
            return {CONF_TARGET_SWITCH: "switch_has_no_action"}
        return {}

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> SubentryFlowResult:
        """Entry point for creating a new thermostat subentry."""
        self._options = {}
        return await self.async_step_basic(user_input)

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> SubentryFlowResult:
        """Entry point for reconfiguring an existing thermostat subentry."""
        subentry = self._get_reconfigure_subentry()
        self._options = dict(subentry.data)
        return await self.async_step_basic(user_input)

    async def async_step_basic(  # noqa: PLR0912
        self,
        user_input: dict[str, Any] | None = None,
    ) -> SubentryFlowResult:
        """Step 1: basic thermostat settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = (user_input.get(CONF_THERMOSTAT_NAME) or "").strip()
            if not name:
                errors[CONF_THERMOSTAT_NAME] = "name_required"
            if not user_input.get(CONF_TEMPERATURE_SENSOR):
                errors[CONF_TEMPERATURE_SENSOR] = "required"
            if not user_input.get(CONF_TARGET_SWITCH):
                errors[CONF_TARGET_SWITCH] = "required"
            else:
                errors.update(self._switch_errors(user_input))

            if not errors:
                self._options.update(user_input)
                return await self.async_step_presets()

        subentry = self._get_reconfigure_subentry() if self.source != "user" else None
        name_default = self._options.get(CONF_THERMOSTAT_NAME) or (
            subentry.title if subentry else ""
        )
        temp_sensor_default = self._options.get(CONF_TEMPERATURE_SENSOR) or (
            subentry.data.get(CONF_TEMPERATURE_SENSOR) if subentry else None
        )
        target_switch_default = self._options.get(CONF_TARGET_SWITCH) or (
            subentry.data.get(CONF_TARGET_SWITCH) if subentry else None
        )
        thermo_mode_default = self._options.get(CONF_THERMOSTAT_MODE) or (
            subentry.data.get(CONF_THERMOSTAT_MODE, ClimateMode.HEATING)
            if subentry
            else ClimateMode.HEATING
        )
        cold_tol_default = self._options.get(CONF_COLD_TOLERANCE)
        if cold_tol_default is None and subentry:
            cold_tol_default = subentry.data.get(CONF_COLD_TOLERANCE, DEFAULT_TOLERANCE)
        if cold_tol_default is None:
            cold_tol_default = DEFAULT_TOLERANCE
        hot_tol_default = self._options.get(CONF_HOT_TOLERANCE)
        if hot_tol_default is None and subentry:
            hot_tol_default = subentry.data.get(CONF_HOT_TOLERANCE, DEFAULT_TOLERANCE)
        if hot_tol_default is None:
            hot_tol_default = DEFAULT_TOLERANCE
        min_cycle_default = self._options.get(CONF_MIN_CYCLE_DURATION) or (
            subentry.data.get(CONF_MIN_CYCLE_DURATION) if subentry else None
        )
        min_temp_default = self._options.get(CONF_MIN_TEMP) or (
            subentry.data.get(CONF_MIN_TEMP) if subentry else None
        )
        max_temp_default = self._options.get(CONF_MAX_TEMP) or (
            subentry.data.get(CONF_MAX_TEMP) if subentry else None
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_THERMOSTAT_NAME,
                    default=name_default,
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
                ),
                vol.Required(
                    CONF_TEMPERATURE_SENSOR,
                    default=temp_sensor_default,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=DOMAIN_SENSOR,
                        device_class=DEVICE_CLASS_TEMPERATURE,
                    ),
                ),
                vol.Required(
                    CONF_TARGET_SWITCH,
                    default=target_switch_default,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=DOMAIN_SWITCH),
                ),
                vol.Required(
                    CONF_THERMOSTAT_MODE,
                    default=thermo_mode_default,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=ClimateMode.HEATING, label="Heating"
                            ),
                            selector.SelectOptionDict(
                                value=ClimateMode.COOLING, label="Cooling"
                            ),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    ),
                ),
                vol.Required(
                    CONF_COLD_TOLERANCE,
                    default=cold_tol_default,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=5,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                    ),
                ),
                vol.Required(
                    CONF_HOT_TOLERANCE,
                    default=hot_tol_default,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=5,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                    ),
                ),
            },
        )

        temp_sel = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=-50,
                max=50,
                step=0.5,
                mode=selector.NumberSelectorMode.BOX,
            ),
        )
        # vol.Optional(default=None) makes voluptuous pass None to the selector,
        # which fails validation. Only set default when we have a real value.
        opt_fields: dict[vol.Marker, Any] = {}
        if min_cycle_default is not None:
            opt_fields[
                vol.Optional(CONF_MIN_CYCLE_DURATION, default=min_cycle_default)
            ] = selector.DurationSelector()
        else:
            opt_fields[vol.Optional(CONF_MIN_CYCLE_DURATION)] = (
                selector.DurationSelector()
            )
        if min_temp_default is not None:
            opt_fields[vol.Optional(CONF_MIN_TEMP, default=min_temp_default)] = temp_sel
        else:
            opt_fields[vol.Optional(CONF_MIN_TEMP)] = temp_sel
        if max_temp_default is not None:
            opt_fields[vol.Optional(CONF_MAX_TEMP, default=max_temp_default)] = temp_sel
        else:
            opt_fields[vol.Optional(CONF_MAX_TEMP)] = temp_sel

        schema = schema.extend(opt_fields)

        return self.async_show_form(
            step_id="basic",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_presets(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> SubentryFlowResult:
        """Step 2: preset temperatures."""
        if user_input is not None:
            self._options.update(user_input)
            preset_data = {
                k: v
                for k, v in user_input.items()
                if k in CONF_PRESETS.values() and v is not None
            }
            self._options.update(preset_data)

            if self._is_new:
                return self.async_create_entry(
                    title=self._options.get(CONF_THERMOSTAT_NAME, "Thermostat"),
                    data=self._options,
                )

            subentry = self._get_reconfigure_subentry()
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                data=self._options,
                title=self._options.get(CONF_THERMOSTAT_NAME, subentry.title),
                reason="reconfigure_successful",
            )

        preset_sel = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=-50,
                max=50,
                step=0.5,
                mode=selector.NumberSelectorMode.BOX,
            ),
        )
        schema_dict: dict[vol.Marker, Any] = {}
        for key in PRESET_KEYS:
            preset_key = f"{key}_temp"
            saved = self._options.get(preset_key)
            if saved is not None:
                schema_dict[vol.Optional(preset_key, default=saved)] = preset_sel
            else:
                schema_dict[vol.Optional(preset_key)] = preset_sel

        return self.async_show_form(
            step_id="presets",
            data_schema=vol.Schema(schema_dict),
            errors={},
        )


class ControlOptionsFlow(OptionsFlow):
    """
    How the balancer controls the thermostats' switches.

    Algorithm parameters live in the entry's options, not in entities
    (docs/concepts/settings-vs-entities.md); one set for all thermostats of
    the entry.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the control settings."""
        if user_input is not None:
            return self.async_create_entry(
                data={key: int(value) for key, value in user_input.items()}
            )
        settings = ControlSettings.of(self.config_entry)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        setting.name, default=getattr(settings, setting.name)
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=setting.metadata["min"],
                            max=setting.metadata["max"],
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    )
                    for setting in fields(ControlSettings)
                }
            ),
        )
