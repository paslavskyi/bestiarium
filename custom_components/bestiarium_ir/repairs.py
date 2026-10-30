"""
Repairs of Bestiarium IR: a blaster's TS1201 or an air conditioner's emitter gone.

A setting a person must fix is a Repairs issue (docs/concepts/problem-reporting.md):
checked once HA has started and on every reload, fixed in a form with the
subentry's field; the entry reloads with the fix, and the check deletes the
issue. The other subentries keep working meanwhile.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .config_flow import device_selector
from .const import (
    CONF_EMITTER,
    CONF_ZHA_DEVICE,
    DOMAIN,
    ISSUE_BLASTER_DEVICE_GONE,
    ISSUE_EMITTER_GONE,
    SUBENTRY_TYPE_BLASTER,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResult

    from .data import BestiariumIrConfigEntry


def _gone(hass: HomeAssistant, subentry: ConfigSubentry) -> str | None:
    """Return the issue of a subentry whose device or emitter is gone."""
    if subentry.subentry_type == SUBENTRY_TYPE_BLASTER:
        if dr.async_get(hass).async_get(subentry.data[CONF_ZHA_DEVICE]) is None:
            return ISSUE_BLASTER_DEVICE_GONE
        return None
    emitter = subentry.data[CONF_EMITTER]
    if er.async_get(hass).async_get(emitter) is None and not hass.states.get(emitter):
        return ISSUE_EMITTER_GONE
    return None


def async_check_issues(hass: HomeAssistant, entry: BestiariumIrConfigEntry) -> None:
    """Create the issues of the gone devices and emitters; delete the others."""
    registry = ir.async_get(hass)
    active: set[str] = set()
    for subentry in entry.subentries.values():
        if (key := _gone(hass, subentry)) is None:
            continue
        issue_id = f"{key}_{subentry.subentry_id}"
        active.add(issue_id)
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=key,
            translation_placeholders={"name": subentry.title}
            | (
                {"entity": subentry.data[CONF_EMITTER]}
                if key == ISSUE_EMITTER_GONE
                else {}
            ),
            data={"entry_id": entry.entry_id, "subentry_id": subentry.subentry_id},
        )
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN and issue_id not in active:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


async def async_create_fix_flow(
    hass: HomeAssistant,  # noqa: ARG001
    issue_id: str,  # noqa: ARG001
    data: dict[str, str] | None,
) -> RepairsFlow:
    """Return the form of the subentry's field."""
    return SubentryFieldRepairFlow(data or {})


class SubentryFieldRepairFlow(RepairsFlow):
    """Show what the subentry runs on (a TS1201, an emitter); save another."""

    def __init__(self, data: dict[str, str]) -> None:
        """Remember which subentry to fix."""
        self._entry_id = data["entry_id"]
        self._subentry_id = data["subentry_id"]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the field; save it."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or self._subentry_id not in entry.subentries:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            return self.async_abort(reason="subentry_removed")
        subentry = entry.subentries[self._subentry_id]
        key = (
            CONF_ZHA_DEVICE
            if subentry.subentry_type == SUBENTRY_TYPE_BLASTER
            else CONF_EMITTER
        )
        # The first step gets the issue_id from HA's repairs, not a field
        if user_input is not None and key in user_input:
            self.hass.config_entries.async_update_subentry(
                entry, subentry, data={**subentry.data, key: user_input[key]}
            )
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(key): device_selector(self.hass, subentry.subentry_type)}
            ),
            description_placeholders={"name": subentry.title},
        )
