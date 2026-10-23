"""
The Repairs issue of a power sensor the zones cannot find.

Checked only after HA has fully started, deleted once the sensor is back or
another one is picked (docs/concepts/problem-reporting.md). A sensor that is
only unavailable gives the zone "no data", not an issue.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.start import async_at_started

from .const import CONF_AC_LOAD_LIMITS, CONF_AC_POWER_SENSOR, DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import (
        CALLBACK_TYPE,
        Event,
        EventStateChangedData,
        HomeAssistant,
    )
    from homeassistant.data_entry_flow import FlowResult

    from .data import BestiariumModeConfigEntry

ISSUE_MISSING_POWER_SENSOR = "missing_power_sensor"


@callback
def async_watch_power_sensor(
    hass: HomeAssistant, entry: BestiariumModeConfigEntry
) -> CALLBACK_TYPE:
    """Report the zones' power sensor while it is gone."""
    sensor = entry.data[CONF_AC_LOAD_LIMITS][CONF_AC_POWER_SENSOR]
    started = False

    @callback
    def check(*_args: object) -> None:
        nonlocal started
        started = True
        if hass.states.get(sensor) is not None:
            ir.async_delete_issue(hass, DOMAIN, ISSUE_MISSING_POWER_SENSOR)
            return
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_MISSING_POWER_SENSOR,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_MISSING_POWER_SENSOR,
            translation_placeholders={"sensor": sensor},
            data={"entry_id": entry.entry_id},
        )

    @callback
    def on_state(event: Event[EventStateChangedData]) -> None:
        if started and None in (event.data["old_state"], event.data["new_state"]):
            check()

    unsubscribe = [
        async_track_state_change_event(hass, [sensor], on_state),
        async_at_started(hass, check),
    ]

    @callback
    def stop() -> None:
        for unsub in unsubscribe:
            unsub()
        ir.async_delete_issue(hass, DOMAIN, ISSUE_MISSING_POWER_SENSOR)

    return stop


class PowerSensorFixFlow(RepairsFlow):
    """Pick the power sensor of the zones again."""

    def __init__(self, entry: BestiariumModeConfigEntry) -> None:
        """Fix the given mode entry."""
        self._entry = entry

    async def async_step_init(
        self,
        _user_input: dict[str, Any] | None = None,  # the issue's data
    ) -> FlowResult:
        """Start with the sensor."""
        return await self.async_step_power_sensor()

    async def async_step_power_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Store the sensor and reload the entry."""
        limits = self._entry.data[CONF_AC_LOAD_LIMITS]
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={
                    **self._entry.data,
                    CONF_AC_LOAD_LIMITS: {**limits, **user_input},
                },
            )
            self.hass.config_entries.async_schedule_reload(self._entry.entry_id)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="power_sensor",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_AC_POWER_SENSOR): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor", device_class="power"
                        )
                    )
                }
            ),
            description_placeholders={"sensor": limits[CONF_AC_POWER_SENSOR]},
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,  # noqa: ARG001
    data: dict[str, str] | None,
) -> RepairsFlow:
    """Return the fix flow of the zones' power sensor."""
    return PowerSensorFixFlow(hass.config_entries.async_get_entry(data["entry_id"]))
