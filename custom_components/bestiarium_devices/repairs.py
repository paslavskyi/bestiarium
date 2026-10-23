"""
Repairs of a device entry whose relay or power sensor is gone.

A setting a person must fix is a Repairs issue (docs/concepts/problem-reporting.md):
checked only after HA has fully started, deleted once fixed. An entity that
is only unavailable is a control state, not a setting. A renamed entity is
followed, not reported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector
from homeassistant.helpers.event import (
    async_track_entity_registry_updated_event,
    async_track_state_change_event,
)
from homeassistant.helpers.start import async_at_started

from .config_flow import RelaySteps, virtual_relay_schema
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_RESET_POWER_MEMORY,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    ISSUE_MISSING_ENTITY,
    LOGGER,
)
from .helpers import get_device_name_from_entry, validate_power_sensor

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import (
        CALLBACK_TYPE,
        Event,
        EventStateChangedData,
        HomeAssistant,
    )
    from homeassistant.data_entry_flow import FlowResult
    from homeassistant.helpers.entity_registry import EventEntityRegistryUpdatedData

ENTITY_KEYS = (CONF_SWITCH_ENTITY_ID, CONF_POWER_SENSOR_ENTITY_ID)


def _issue_id(entry: ConfigEntry) -> str:
    return f"{ISSUE_MISSING_ENTITY}_{entry.entry_id}"


@callback
def async_watch_entities(hass: HomeAssistant, entry: ConfigEntry) -> CALLBACK_TYPE:
    """Report a gone relay or power sensor; follow a renamed one."""
    entity_ids = [entry.data[key] for key in ENTITY_KEYS]
    started = False

    @callback
    def check(*_args: object) -> None:
        nonlocal started
        started = True
        missing = [e for e in entity_ids if hass.states.get(e) is None]
        if not missing:
            ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))
            return
        ir.async_create_issue(
            hass,
            DOMAIN,
            _issue_id(entry),
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_MISSING_ENTITY,
            translation_placeholders={
                "device": get_device_name_from_entry(entry),
                "entities": ", ".join(missing),
            },
            data={"entry_id": entry.entry_id},
        )

    @callback
    def on_state(event: Event[EventStateChangedData]) -> None:
        # Only an entity that goes or comes back changes the issue
        if started and None in (event.data["old_state"], event.data["new_state"]):
            check()

    @callback
    def on_renamed(event: Event[EventEntityRegistryUpdatedData]) -> None:
        if event.data["action"] != "update" or "old_entity_id" not in event.data:
            return
        old, new = event.data["old_entity_id"], event.data["entity_id"]
        LOGGER.info("%s: follows %s renamed to %s", entry.title, old, new)
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                **{key: new for key in ENTITY_KEYS if entry.data[key] == old},
            },
        )
        hass.config_entries.async_schedule_reload(entry.entry_id)

    unsubscribe = [
        async_track_state_change_event(hass, entity_ids, on_state),
        async_track_entity_registry_updated_event(hass, entity_ids, on_renamed),
        async_at_started(hass, check),
    ]

    @callback
    def stop() -> None:
        for unsub in unsubscribe:
            unsub()
        ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))

    return stop


class MissingEntityFixFlow(RelaySteps, RepairsFlow):
    """
    Pick the relay and the power sensor again.

    The fields of the reconfigure form: an extended device picks its device
    (it may be another one), a virtual device its switch and sensor.
    """

    def __init__(self, entry: ConfigEntry) -> None:
        """Fix the given device entry."""
        self._entry = entry
        self._entry_id = entry.entry_id
        self._device_id = entry.data.get(CONF_DEVICE_ID)
        self._switch_entity_id = entry.data[CONF_SWITCH_ENTITY_ID]
        self._power_sensor_entity_id = entry.data[CONF_POWER_SENSOR_ENTITY_ID]
        self._switches, self._power_sensors = [], []
        self._reset_power_memory = False

    async def async_step_init(
        self,
        _user_input: dict[str, Any] | None = None,  # the issue's data
    ) -> FlowResult:
        """Start with the device, or the relay of a virtual device."""
        if self._entry.data[CONF_DEVICE_TYPE] == DEVICE_TYPE_VIRTUAL:
            return await self.async_step_virtual_relay()
        return await self.async_step_pick_device()

    async def async_step_virtual_relay(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick the switch and the power sensor of a virtual device."""
        errors: dict[str, str] = {}
        if user_input is not None:
            power = user_input[CONF_POWER_SENSOR_ENTITY_ID]
            if error := validate_power_sensor(self.hass, power):
                errors[CONF_POWER_SENSOR_ENTITY_ID] = error
            else:
                self._switch_entity_id = user_input[CONF_SWITCH_ENTITY_ID]
                self._power_sensor_entity_id = power
                self._reset_power_memory = user_input[CONF_RESET_POWER_MEMORY]
                return await self._async_relay_chosen()
        schema = virtual_relay_schema(
            self.hass, self._switch_entity_id, self._power_sensor_entity_id
        ).extend(
            {
                vol.Required(
                    CONF_RESET_POWER_MEMORY, default=False
                ): selector.BooleanSelector()
            }
        )
        return self.async_show_form(
            step_id="virtual_relay", data_schema=schema, errors=errors
        )

    async def _async_relay_chosen(self) -> FlowResult:
        """Store the relay, reload the entry; the issue goes with the check."""
        entry = self._entry
        data = {
            **entry.data,
            CONF_SWITCH_ENTITY_ID: self._switch_entity_id,
            CONF_POWER_SENSOR_ENTITY_ID: self._power_sensor_entity_id,
        }
        if entry.data[CONF_DEVICE_TYPE] == DEVICE_TYPE_VIRTUAL:
            changed = self._switch_entity_id != entry.data[CONF_SWITCH_ENTITY_ID]
        else:
            data[CONF_DEVICE_ID] = self._device_id
            changed = self._device_id != entry.data.get(CONF_DEVICE_ID)
        await self._async_forget(entry, device_changed=changed)
        self.hass.config_entries.async_update_entry(entry, data=data)
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,  # noqa: ARG001
    data: dict[str, str] | None,
) -> RepairsFlow:
    """Return the fix flow of a device entry's issue."""
    entry = hass.config_entries.async_get_entry(data["entry_id"])
    return MissingEntityFixFlow(entry)
