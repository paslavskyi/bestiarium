"""
Repairs of Bestiarium Climate: settings of a switch a person must fix.

A setting a person must fix is a Repairs issue (docs/concepts/problem-reporting.md),
fixed in its own form: the actions of a virtual switch (an invalid saved
action, a cooling switch that now heats), or other switches for the extra
thermostats of a shared switch. The entry reloads with the fix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector

from .config_flow import actions_schema, async_action_errors, switch_error
from .const import (
    CONF_TARGET_SWITCH,
    CONF_THERMOSTAT_MODE,
    DOMAIN,
    DOMAIN_SWITCH,
    ISSUE_SWITCH_IS_DEVICE,
    ISSUE_SWITCH_SHARED,
    ClimateMode,
)
from .devices import device_of_relay
from .switch import ACTIONS, thermostats_of

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResult


async def async_create_fix_flow(
    hass: HomeAssistant,  # noqa: ARG001
    issue_id: str,
    data: dict[str, str] | None,
) -> RepairsFlow:
    """Return the fix of the issue: the shared switch's, or a switch's actions."""
    flow: _GoneRepairFlow
    if issue_id.startswith(ISSUE_SWITCH_SHARED):
        flow = SharedSwitchRepairFlow(data or {})
    elif issue_id.startswith(ISSUE_SWITCH_IS_DEVICE):
        flow = ThermostatSwitchRepairFlow(data or {})
    else:
        flow = ActionsRepairFlow(data or {})
    flow.issue_id = issue_id
    return flow


class _GoneRepairFlow(RepairsFlow):
    """A fix whose switch or thermostat may be gone: then its issue goes too."""

    issue_id = ""

    def _gone(self, reason: str) -> FlowResult:
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        return self.async_abort(reason=reason)


class ActionsRepairFlow(_GoneRepairFlow):
    """Show the actions of a virtual switch and save the corrected ones."""

    def __init__(self, data: dict[str, str]) -> None:
        """Remember which switch to fix."""
        self._entry_id = data["entry_id"]
        self._subentry_id = data["subentry_id"]
        self._error = data.get("error", "")

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the actions; save them once valid."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or self._subentry_id not in entry.subentries:
            return self._gone("switch_removed")
        subentry = entry.subentries[self._subentry_id]
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await async_action_errors(self.hass, user_input)
            if not errors:
                data = {k: v for k, v in subentry.data.items() if k not in ACTIONS}
                data.update({k: user_input[k] for k in ACTIONS if user_input.get(k)})
                self.hass.config_entries.async_update_subentry(
                    entry, subentry, data=data
                )
                return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(actions_schema(subentry.data)),
            errors=errors,
            description_placeholders={"switch": subentry.title, "error": self._error},
        )


class SharedSwitchRepairFlow(_GoneRepairFlow):
    """
    Give each thermostat of a shared switch but the first a switch of its own.

    Each new switch is checked as in the thermostat's form.
    """

    def __init__(self, data: dict[str, str]) -> None:
        """Remember the shared switch."""
        self._switch = data["switch"]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask a switch for each extra thermostat; save them once valid."""
        _keep, *others = thermostats_of(self.hass, self._switch)
        if not others:
            return self._gone("switch_not_shared")
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = [user_input[thermostat.title] for thermostat in others]
            for thermostat, switch in zip(others, chosen, strict=True):
                mode = ClimateMode(
                    thermostat.data.get(CONF_THERMOSTAT_MODE, ClimateMode.HEATING)
                )
                if switch == self._switch or chosen.count(switch) > 1:
                    errors[thermostat.title] = "switch_taken"
                elif error := switch_error(
                    self.hass, switch, mode, besides=thermostat.subentry_id
                ):
                    errors[thermostat.title] = error
            if not errors:
                for thermostat, switch in zip(others, chosen, strict=True):
                    entry = next(
                        e
                        for e in self.hass.config_entries.async_entries(DOMAIN)
                        if thermostat.subentry_id in e.subentries
                    )
                    self.hass.config_entries.async_update_subentry(
                        entry,
                        thermostat,
                        data={**thermostat.data, CONF_TARGET_SWITCH: switch},
                    )
                return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(thermostat.title): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=DOMAIN_SWITCH)
                    )
                    for thermostat in others
                }
            ),
            errors=errors,
            description_placeholders={"switch": self._switch},
        )


class ThermostatSwitchRepairFlow(_GoneRepairFlow):
    """Give a thermostat on a Bestiarium Devices relay a switch of its own."""

    def __init__(self, data: dict[str, str]) -> None:
        """Remember the thermostat."""
        self._entry_id = data["entry_id"]
        self._subentry_id = data["subentry_id"]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask another switch; save it once valid."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or self._subentry_id not in entry.subentries:
            return self._gone("thermostat_removed")
        thermostat = entry.subentries[self._subentry_id]
        device = device_of_relay(self.hass, thermostat.data[CONF_TARGET_SWITCH])
        errors: dict[str, str] = {}
        if user_input is not None:
            switch = user_input[CONF_TARGET_SWITCH]
            mode = ClimateMode(
                thermostat.data.get(CONF_THERMOSTAT_MODE, ClimateMode.HEATING)
            )
            if error := switch_error(
                self.hass, switch, mode, besides=thermostat.subentry_id
            ):
                errors[CONF_TARGET_SWITCH] = error
            else:
                self.hass.config_entries.async_update_subentry(
                    entry,
                    thermostat,
                    data={**thermostat.data, CONF_TARGET_SWITCH: switch},
                )
                return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TARGET_SWITCH): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=DOMAIN_SWITCH)
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "thermostat": thermostat.title,
                "device": device.title if device else "",
            },
        )
