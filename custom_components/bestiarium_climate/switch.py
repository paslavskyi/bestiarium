"""
Switch platform for bestiarium_climate: the virtual switch of a thermostat.

Its actions are sequences run by HA's script machinery. Heating or cooling is
the mode of the one thermostat that controls it. Its state is read from a
power sensor: on above the minimum power, off below it minus the hysteresis;
a change is shown only after it has lasted the delay. Without power data for
longer than the tolerance it is unavailable (power_reading.py).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.script import (
    SCRIPT_MODE_RESTART,
    Script,
    async_validate_actions_config,
)

from .const import (
    CONF_COOLING_ACTION,
    CONF_HEATING_ACTION,
    CONF_OFF_ACTION,
    CONF_POWER_ENTITY,
    CONF_TARGET_SWITCH,
    CONF_THERMOSTAT_MODE,
    DOMAIN,
    ISSUE_INVALID_ACTION,
    ISSUE_SWITCH_NOW_HEATS,
    SUBENTRY_TYPE_SWITCH,
    SUBENTRY_TYPE_THERMOSTAT,
    ClimateMode,
)
from .power_reading import PowerReading, PowerThresholds

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, ConfigSubentry
    from homeassistant.core import (
        HomeAssistant,
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
            [VirtualSwitch(hass, subentry, scripts)],
            config_subentry_id=subentry.subentry_id,
        )


class VirtualSwitch(SwitchEntity):
    """Virtual switch that runs action sequences and reads its state from power."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self,
        hass: HomeAssistant,
        subentry: ConfigSubentry,
        scripts: dict[str, Script],
    ) -> None:
        """Initialize the virtual switch."""
        self._subentry = subentry
        self._scripts = scripts
        self._power = PowerReading(
            hass,
            subentry.title,
            subentry.data[CONF_POWER_ENTITY],
            PowerThresholds.of(subentry.data),
            self.async_write_ha_state,
        )

        self._attr_unique_id = subentry.subentry_id
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

    @property
    def available(self) -> bool:
        """Return False while the power sensor has had no data too long."""
        return self._power.available

    @property
    def is_on(self) -> bool:
        """Return whether the device draws power."""
        return self._power.is_on

    async def async_added_to_hass(self) -> None:
        """Show the power sensor's state and follow it."""
        self._power.name = self.entity_id
        self._power.start()
        self.async_on_remove(self._power.stop)
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
