"""Adds config flow for Bestiarium Mode."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.helpers import selector

from .const import (
    CONF_AC_LOAD_LIMIT_W,
    CONF_AC_LOAD_LIMITS,
    CONF_AC_OVERLOAD_RECOVERY_SECONDS,
    CONF_AC_POWER_SENSOR,
    CONF_AC_POWER_TOLERANCE_SECONDS,
    CONF_AC_RED_ZONE_PERCENT,
    CONF_AC_YELLOW_ZONE_PERCENT,
    CONF_AC_ZONE_HYSTERESIS_PERCENT,
    DEFAULT_OVERLOAD_RECOVERY_SECONDS,
    DEFAULT_POWER_TOLERANCE_SECONDS,
    DEFAULT_ZONE_HYSTERESIS_PERCENT,
    DOMAIN,
    LOAD_LIMIT_MAX_W,
    LOAD_LIMIT_MIN_W,
)

CONF_OPTIONS = "options"
CONF_NAME = "name"
CONF_DESCRIPTION = "description"
CONF_ICON = "icon"
DEFAULT_ICON = "mdi:home"
MIN_YELLOW_ZONE_PERCENT = 50


class BestiariumModeFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Bestiarium Mode."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        super().__init__()
        self._options: list[dict[str, str]] = []
        self._ac_load_limits: dict[str, Any] | None = None

    def _build_menu_options(self) -> dict[str, str]:
        """Build menu options for the options list step."""
        menu_options: dict[str, str] = {}
        for i, option in enumerate(self._options):
            option_name = option.get(CONF_NAME, f"Option {i + 1}")
            menu_options[f"edit_{i}"] = f"🏷️ {option_name}"

        menu_options["configure_ac_load_limits"] = "⚡ Configure AC load limits"
        menu_options["add"] = "➕ Add option"  # noqa: RUF001
        menu_options["complete"] = "💾 Save"

        return menu_options

    def _get_list_step_id(self) -> str:
        """Get the step ID for the options list (user or reconfigure)."""
        return "reconfigure" if self.source == SOURCE_RECONFIGURE else "user"

    async def _async_step_options_list(
        self,
        user_input: dict | None = None,  # noqa: ARG002
    ) -> config_entries.ConfigFlowResult:
        """Handle the options list step (shared by user and reconfigure flows)."""
        return self.async_show_menu(
            step_id=self._get_list_step_id(),
            menu_options=self._build_menu_options(),
        )

    async def async_step_user(
        self,
        user_input: dict | None = None,  # noqa: ARG002
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        await self.async_set_unique_id("bestiarium_mode")
        self._abort_if_unique_id_configured()

        return await self._async_step_options_list()

    async def async_step_reconfigure(
        self,
        user_input: dict | None = None,  # noqa: ARG002
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration of an existing config entry."""
        entry = self._get_reconfigure_entry()
        await self.async_set_unique_id("bestiarium_mode")
        self._abort_if_unique_id_mismatch()

        # Load existing options from the config entry
        if not self._options:
            self._options = entry.data.get(CONF_OPTIONS, []).copy()
        if self._ac_load_limits is None:
            self._ac_load_limits = entry.data.get(CONF_AC_LOAD_LIMITS)

        return await self._async_step_options_list()

    async def async_step_add(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle adding a new option."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input.get(CONF_NAME, "").strip()
            if not name:
                errors[CONF_NAME] = "name_required"
            else:
                option = {
                    CONF_NAME: name,
                    CONF_DESCRIPTION: user_input.get(CONF_DESCRIPTION, "").strip(),
                    CONF_ICON: user_input.get(CONF_ICON, DEFAULT_ICON),
                }
                self._options.append(option)
                return await self._async_step_options_list()

        data_schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
                ),
                vol.Optional(CONF_DESCRIPTION, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
                ),
                vol.Optional(CONF_ICON, default=DEFAULT_ICON): selector.IconSelector(
                    selector.IconSelectorConfig(),
                ),
            },
        )

        return self.async_show_form(
            step_id="add",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_complete(
        self,
        user_input: dict | None = None,  # noqa: ARG002
    ) -> config_entries.ConfigFlowResult:
        """Handle completing the configuration."""
        if not self._options:
            return await self._async_step_options_list()

        if self._ac_load_limits is None:
            return await self.async_step_configure_ac_load_limits()

        # If this is a reconfigure flow, update the existing entry
        if self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            return self.async_update_reload_and_abort(
                entry,
                data_updates={
                    CONF_OPTIONS: self._options,
                    CONF_AC_LOAD_LIMITS: self._ac_load_limits,
                },
                reason="reconfigure_successful",
            )

        # Otherwise, create a new entry
        return self.async_create_entry(
            title="Bestiarium Mode",
            data={
                CONF_OPTIONS: self._options,
                CONF_AC_LOAD_LIMITS: self._ac_load_limits,
            },
        )

    async def async_step_configure_ac_load_limits(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle configuring AC load limits."""
        errors: dict[str, str] = {}

        existing_limits = self._ac_load_limits or {}
        default_sensor = existing_limits.get(CONF_AC_POWER_SENSOR)
        default_limit = existing_limits.get(CONF_AC_LOAD_LIMIT_W)
        default_yellow = existing_limits.get(CONF_AC_YELLOW_ZONE_PERCENT, 75)
        default_red = existing_limits.get(CONF_AC_RED_ZONE_PERCENT, 90)
        default_recovery = existing_limits.get(
            CONF_AC_OVERLOAD_RECOVERY_SECONDS, DEFAULT_OVERLOAD_RECOVERY_SECONDS
        )
        default_hysteresis = existing_limits.get(
            CONF_AC_ZONE_HYSTERESIS_PERCENT, DEFAULT_ZONE_HYSTERESIS_PERCENT
        )
        default_tolerance = existing_limits.get(
            CONF_AC_POWER_TOLERANCE_SECONDS, DEFAULT_POWER_TOLERANCE_SECONDS
        )

        if user_input is not None:
            yellow = int(user_input[CONF_AC_YELLOW_ZONE_PERCENT])
            red = int(user_input[CONF_AC_RED_ZONE_PERCENT])

            if yellow < MIN_YELLOW_ZONE_PERCENT:
                errors[CONF_AC_YELLOW_ZONE_PERCENT] = "yellow_zone_min"
            if yellow >= red:
                errors[CONF_AC_RED_ZONE_PERCENT] = "red_zone_must_exceed_yellow"

            if not errors:
                self._ac_load_limits = {
                    CONF_AC_POWER_SENSOR: user_input[CONF_AC_POWER_SENSOR],
                    CONF_AC_LOAD_LIMIT_W: int(user_input[CONF_AC_LOAD_LIMIT_W]),
                    CONF_AC_YELLOW_ZONE_PERCENT: yellow,
                    CONF_AC_RED_ZONE_PERCENT: red,
                    CONF_AC_OVERLOAD_RECOVERY_SECONDS: int(
                        user_input[CONF_AC_OVERLOAD_RECOVERY_SECONDS]
                    ),
                    CONF_AC_ZONE_HYSTERESIS_PERCENT: float(
                        user_input[CONF_AC_ZONE_HYSTERESIS_PERCENT]
                    ),
                    CONF_AC_POWER_TOLERANCE_SECONDS: int(
                        user_input[CONF_AC_POWER_TOLERANCE_SECONDS]
                    ),
                }
                return await self._async_step_options_list()

        sensor_key = (
            vol.Required(CONF_AC_POWER_SENSOR, default=default_sensor)
            if default_sensor
            else vol.Required(CONF_AC_POWER_SENSOR)
        )
        limit_key = (
            vol.Required(CONF_AC_LOAD_LIMIT_W, default=default_limit)
            if default_limit is not None
            else vol.Required(CONF_AC_LOAD_LIMIT_W)
        )

        data_schema = vol.Schema(
            {
                sensor_key: selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="sensor",
                        device_class="power",
                    ),
                ),
                limit_key: selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=LOAD_LIMIT_MIN_W,
                        max=LOAD_LIMIT_MAX_W,
                        step=1,
                        unit_of_measurement="W",
                        mode=selector.NumberSelectorMode.BOX,
                    ),
                ),
                vol.Required(
                    CONF_AC_YELLOW_ZONE_PERCENT, default=default_yellow
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=100,
                        step=1,
                        unit_of_measurement="%",
                        mode=selector.NumberSelectorMode.BOX,
                    ),
                ),
                vol.Required(
                    CONF_AC_RED_ZONE_PERCENT, default=default_red
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=100,
                        step=1,
                        unit_of_measurement="%",
                        mode=selector.NumberSelectorMode.BOX,
                    ),
                ),
                vol.Required(
                    CONF_AC_OVERLOAD_RECOVERY_SECONDS, default=default_recovery
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=300,
                        step=1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    ),
                ),
                vol.Required(
                    CONF_AC_ZONE_HYSTERESIS_PERCENT, default=default_hysteresis
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=20,
                        step=0.5,
                        unit_of_measurement="%",
                        mode=selector.NumberSelectorMode.BOX,
                    ),
                ),
                vol.Required(
                    CONF_AC_POWER_TOLERANCE_SECONDS, default=default_tolerance
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=600,
                        step=1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    ),
                ),
            },
        )

        return self.async_show_form(
            step_id="configure_ac_load_limits",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_edit_0(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle editing option 0."""
        return await self._async_step_edit(0, user_input)

    async def async_step_edit_1(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle editing option 1."""
        return await self._async_step_edit(1, user_input)

    async def async_step_edit_2(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle editing option 2."""
        return await self._async_step_edit(2, user_input)

    async def async_step_edit_3(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle editing option 3."""
        return await self._async_step_edit(3, user_input)

    async def async_step_edit_4(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle editing option 4."""
        return await self._async_step_edit(4, user_input)

    async def async_step_edit_5(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle editing option 5."""
        return await self._async_step_edit(5, user_input)

    async def async_step_edit_6(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle editing option 6."""
        return await self._async_step_edit(6, user_input)

    async def async_step_edit_7(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle editing option 7."""
        return await self._async_step_edit(7, user_input)

    async def async_step_edit_8(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle editing option 8."""
        return await self._async_step_edit(8, user_input)

    async def async_step_edit_9(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle editing option 9."""
        return await self._async_step_edit(9, user_input)

    async def _async_step_edit(
        self,
        edit_index: int,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle editing an existing option."""
        if edit_index < 0 or edit_index >= len(self._options):
            return await self._async_step_options_list()

        option = self._options[edit_index]
        errors: dict[str, str] = {}

        if user_input is not None:
            # Check if user wants to delete
            if user_input.get("delete"):
                self._options.pop(edit_index)
                return await self._async_step_options_list()

            name = user_input.get(CONF_NAME, "").strip()
            if not name:
                errors[CONF_NAME] = "name_required"
            else:
                self._options[edit_index] = {
                    CONF_NAME: name,
                    CONF_DESCRIPTION: user_input.get(CONF_DESCRIPTION, "").strip(),
                    CONF_ICON: user_input.get(CONF_ICON, DEFAULT_ICON),
                }
                return await self._async_step_options_list()

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_NAME, default=option.get(CONF_NAME, "")
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
                ),
                vol.Optional(
                    CONF_DESCRIPTION,
                    default=option.get(CONF_DESCRIPTION, ""),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
                ),
                vol.Optional(
                    CONF_ICON, default=option.get(CONF_ICON, DEFAULT_ICON)
                ): selector.IconSelector(
                    selector.IconSelectorConfig(),
                ),
                vol.Optional("delete", default=False): selector.BooleanSelector(
                    selector.BooleanSelectorConfig(),
                ),
            },
        )

        return self.async_show_form(
            step_id=f"edit_{edit_index}",
            data_schema=data_schema,
            errors=errors,
        )
