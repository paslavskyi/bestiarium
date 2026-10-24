"""
Switch platform for bestiarium_climate: the virtual switch of a thermostat.

Its actions are sequences run by HA's script machinery. Heating or cooling is
the mode of the thermostats that control it. Its state is read from a power
sensor: on above the minimum power, off below it minus the hysteresis; a
change is shown only after it has lasted the delay. Without power data it is
unavailable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
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
    CONF_TARGET_SWITCH,
    CONF_THERMOSTAT_MODE,
    DEFAULT_POWER_DELAY_SECONDS,
    DEFAULT_POWER_HYSTERESIS_PERCENT,
    DOMAIN,
    LOGGER,
    SUBENTRY_TYPE_SWITCH,
    SUBENTRY_TYPE_THERMOSTAT,
    ClimateMode,
)

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigSubentry
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


def thermostat_modes(
    hass: HomeAssistant, switch: str, *, besides: str | None = None
) -> set[ClimateMode]:
    """Return the modes of the thermostats that control the switch."""
    return {
        ClimateMode(subentry.data.get(CONF_THERMOSTAT_MODE, ClimateMode.HEATING))
        for entry in hass.config_entries.async_entries(DOMAIN)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_THERMOSTAT
        and subentry.data.get(CONF_TARGET_SWITCH) == switch
        and subentry.subentry_id != besides
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switch entities from switch subentries."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_SWITCH:
            continue
        scripts = {
            key: Script(
                hass,
                await async_validate_actions(hass, subentry.data[key]),
                f"{subentry.title} {key}",
                DOMAIN,
                script_mode=SCRIPT_MODE_RESTART,
            )
            for key in ACTIONS
            if subentry.data.get(key)
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
        self._scripts = scripts
        self._power_entity: str = subentry.data[CONF_POWER_ENTITY]
        self._on_power = float(subentry.data[CONF_MIN_POWER])
        hysteresis = subentry.data.get(
            CONF_POWER_HYSTERESIS, DEFAULT_POWER_HYSTERESIS_PERCENT
        )
        self._off_power = self._on_power * (1 - hysteresis / 100)
        self._delay = subentry.data.get(CONF_POWER_DELAY, DEFAULT_POWER_DELAY_SECONDS)
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
        Return heating or cooling: the mode of the thermostats it serves.

        Without a thermostat it heats if it has a heating action, else cools.
        """
        modes = thermostat_modes(self.hass, self.entity_id)
        if len(modes) > 1:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="thermostat_modes_differ",
                translation_placeholders={"switch": self.entity_id},
            )
        if modes:
            return modes.pop()
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
        """Run one sequence; the one still running for another command stops."""
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
        await script.async_run(context=self._context)

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
        """Show the reading; one info line when the power data goes or is back."""
        available = on is not None
        if available != self._attr_available:
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
        """Follow the power: data back at once, any other change after the delay."""
        reading = self._reading(event.data["new_state"])
        shown = self._attr_is_on if self._attr_available else None
        if reading == shown:
            self._cancel_pending()
        elif shown is None:
            self._show(on=reading)
            self.async_write_ha_state()
        elif self._pending is None:
            self._pending = async_call_later(self.hass, self._delay, self._async_apply)

    @callback
    def _async_apply(self, _now: datetime) -> None:
        """Show the reading that has lasted the delay."""
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

    async def async_will_remove_from_hass(self) -> None:
        """Stop the sequences still running."""
        for script in self._scripts.values():
            await script.async_stop()
