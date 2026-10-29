"""
Switch platform for bestiarium_climate: the virtual switch of a thermostat.

Its actions are sequences run by HA's script machinery. Heating or cooling is
the mode of the one thermostat that controls it. Its state is read from a
power sensor: on above the minimum power, off below it minus the hysteresis;
a change is shown only after it has lasted the delay. Without power data for
longer than the tolerance it is unavailable.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import CoreState, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.script import (
    SCRIPT_MODE_RESTART,
    Script,
    async_validate_actions_config,
)

from .const import (
    CONF_COOLING_ACTION,
    CONF_HEATING_ACTION,
    CONF_MIN_POWER,
    CONF_OFF_ACTION,
    CONF_POWER_DELAY,
    CONF_POWER_ENTITY,
    CONF_POWER_HYSTERESIS,
    CONF_POWER_TOLERANCE,
    CONF_TARGET_SWITCH,
    CONF_THERMOSTAT_MODE,
    DEFAULT_POWER_DELAY_SECONDS,
    DEFAULT_POWER_HYSTERESIS_PERCENT,
    DEFAULT_POWER_TOLERANCE_SECONDS,
    DOMAIN,
    ISSUE_INVALID_ACTION,
    ISSUE_SWITCH_NOW_HEATS,
    LOGGER,
    SUBENTRY_TYPE_SWITCH,
    SUBENTRY_TYPE_THERMOSTAT,
    ClimateMode,
)

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry, ConfigSubentry
    from homeassistant.core import (
        CALLBACK_TYPE,
        Event,
        EventStateChangedData,
        HomeAssistant,
        State,
    )
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .data import BestiariumClimateConfigEntry

ACTIONS = (CONF_HEATING_ACTION, CONF_COOLING_ACTION, CONF_OFF_ACTION)
ACTION_OF_MODE = {
    ClimateMode.HEATING: CONF_HEATING_ACTION,
    ClimateMode.COOLING: CONF_COOLING_ACTION,
}


async def async_validate_actions(hass: HomeAssistant, actions: Any) -> list[dict]:
    """Return the sequence validated as HA validates a script; raise if invalid."""
    return await async_validate_actions_config(hass, cv.SCRIPT_SCHEMA(actions))


def virtual_switch(
    hass: HomeAssistant, switch: str
) -> tuple[ConfigEntry, ConfigSubentry] | None:
    """Return the entry and subentry of a virtual switch of Climate, if it is one."""
    entity = er.async_get(hass).async_get(switch)
    if entity is None or entity.platform != DOMAIN:
        return None
    return next(
        (
            (entry, subentry)
            for entry in hass.config_entries.async_entries(DOMAIN)
            if (subentry := entry.subentries.get(entity.unique_id or ""))
            and subentry.subentry_type == SUBENTRY_TYPE_SWITCH
        ),
        None,
    )


def thermostats_of(
    hass: HomeAssistant, switch: str, *, besides: str | None = None
) -> list[ConfigSubentry]:
    """Return the thermostats (of any climate entry) that control the switch."""
    return [
        subentry
        for entry in hass.config_entries.async_entries(DOMAIN)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_THERMOSTAT
        and subentry.data.get(CONF_TARGET_SWITCH) == switch
        and subentry.subentry_id != besides
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """
    Set up switch entities from switch subentries.

    A switch whose saved sequence HA no longer accepts (an integration it
    used removed) is left out with a Repairs issue that opens its actions;
    the other switches are set up.
    """
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_SWITCH:
            continue
        issue_id = f"{ISSUE_INVALID_ACTION}_{subentry.subentry_id}"
        try:
            sequences = {
                key: await async_validate_actions(hass, subentry.data[key])
                for key in ACTIONS
                if subentry.data.get(key)
            }
        except (vol.Invalid, HomeAssistantError) as err:
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=True,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_INVALID_ACTION,
                translation_placeholders={"switch": subentry.title, "error": str(err)},
                data={
                    "entry_id": entry.entry_id,
                    "subentry_id": subentry.subentry_id,
                    "error": str(err),
                },
            )
            continue
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        scripts = {
            key: Script(
                hass,
                sequence,
                f"{subentry.title} {key}",
                DOMAIN,
                script_mode=SCRIPT_MODE_RESTART,
            )
            for key, sequence in sequences.items()
        }
        async_add_entities(
            [VirtualSwitch(subentry, scripts)],
            config_subentry_id=subentry.subentry_id,
        )


class VirtualSwitch(SwitchEntity):
    """Virtual switch that runs action sequences and reads its state from power."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, subentry: ConfigSubentry, scripts: dict[str, Script]) -> None:
        """Initialize the virtual switch."""
        self._subentry = subentry
        self._scripts = scripts
        self._power_entity: str = subentry.data[CONF_POWER_ENTITY]
        self._on_power = float(subentry.data[CONF_MIN_POWER])
        hysteresis = subentry.data.get(
            CONF_POWER_HYSTERESIS, DEFAULT_POWER_HYSTERESIS_PERCENT
        )
        self._off_power = self._on_power * (1 - hysteresis / 100)
        self._delay = subentry.data.get(CONF_POWER_DELAY, DEFAULT_POWER_DELAY_SECONDS)
        self._tolerance = subentry.data.get(
            CONF_POWER_TOLERANCE, DEFAULT_POWER_TOLERANCE_SECONDS
        )
        # The reading waiting to be shown, and its timer
        self._candidate: bool | None = None
        self._pending: CALLBACK_TYPE | None = None

        self._attr_unique_id = subentry.subentry_id
        self._attr_is_on = False
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Bestiarium",
            model="Virtual Switch",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _mode(self) -> ClimateMode:
        """
        Return heating or cooling: the mode of the thermostat it serves.

        Without a thermostat it heats if it has a heating action, else cools.
        One switch serves one thermostat; a shared one (configured before
        DEV-008 stage B) is a Repairs issue and does nothing.
        """
        thermostats = thermostats_of(self.hass, self.entity_id)
        if len(thermostats) > 1:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="switch_shared",
                translation_placeholders={"switch": self.entity_id},
            )
        if thermostats:
            return ClimateMode(
                thermostats[0].data.get(CONF_THERMOSTAT_MODE, ClimateMode.HEATING)
            )
        if CONF_HEATING_ACTION in self._scripts:
            return ClimateMode.HEATING
        return ClimateMode.COOLING

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Run the heating or the cooling sequence."""
        await self._async_run(ACTION_OF_MODE[self._mode()])

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Run the off sequence."""
        await self._async_run(CONF_OFF_ACTION)

    async def _async_run(self, key: str) -> None:
        """
        Run one sequence; the one still running for another command stops.

        Whatever fails in the sequence fails the command as a HomeAssistantError:
        the person sees it, the balancer counts an attempt.
        """
        script = self._scripts.get(key)
        if script is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="no_action",
                translation_placeholders={"switch": self.entity_id, "action": key},
            )
        for other in self._scripts.values():
            if other is not script:
                await other.async_stop()
        try:
            await script.async_run(context=self._context)
        except asyncio.CancelledError:
            # Stopped by another command (a step's call cancelled): a result
            # for the caller, not its cancellation; its own is passed on
            if (task := asyncio.current_task()) is not None and task.cancelling():
                raise
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_failed",
                translation_placeholders={"switch": self.entity_id, "error": str(err)},
            ) from err

    def _reading(self, state: State | None) -> bool | None:
        """Return on or off by the power, with the hysteresis; None without data."""
        if state is None:
            return None
        try:
            power = float(state.state)
        except ValueError:
            return None
        on = self._attr_available and self._attr_is_on
        return power >= (self._off_power if on else self._on_power)

    def _show(self, *, on: bool | None) -> None:
        """Show the reading; once HA runs, one info line when the data goes or back."""
        available = on is not None
        if available != self._attr_available and self.hass.state is CoreState.running:
            LOGGER.info(
                "%s: power data from %s %s",
                self.entity_id,
                self._power_entity,
                "is back" if available else "is gone",
            )
        self._attr_available = available
        self._attr_is_on = bool(on)

    @callback
    def _async_power_changed(self, event: Event[EventStateChangedData]) -> None:
        """
        Follow the power: data back at once, a change after the delay.

        No data is shown only after the tolerance; a shorter dropout is silent.
        """
        reading = self._reading(event.data["new_state"])
        shown = self._attr_is_on if self._attr_available else None
        if reading == shown:
            self._cancel_pending()
        elif shown is None:
            self._cancel_pending()
            self._show(on=reading)
            self.async_write_ha_state()
        elif self._pending is None or reading != self._candidate:
            self._cancel_pending()
            self._candidate = reading
            wait = self._tolerance if reading is None else self._delay
            self._pending = async_call_later(self.hass, wait, self._async_apply)

    @callback
    def _async_apply(self, _now: datetime) -> None:
        """Show the reading that has lasted its wait."""
        self._pending = None
        self._show(on=self._reading(self.hass.states.get(self._power_entity)))
        self.async_write_ha_state()

    def _cancel_pending(self) -> None:
        if self._pending is not None:
            self._pending()
            self._pending = None

    async def async_added_to_hass(self) -> None:
        """Show the power sensor's state and follow it."""
        self._show(on=self._reading(self.hass.states.get(self._power_entity)))
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._power_entity], self._async_power_changed
            )
        )
        self.async_on_remove(self._cancel_pending)
        if thermostats_of(self.hass, self.entity_id) or (
            CONF_HEATING_ACTION not in self._scripts
        ):
            ir.async_delete_issue(
                self.hass,
                DOMAIN,
                f"{ISSUE_SWITCH_NOW_HEATS}_{self._subentry.subentry_id}",
            )

    async def async_will_remove_from_hass(self) -> None:
        """Stop the sequences still running."""
        for script in self._scripts.values():
            await script.async_stop()
