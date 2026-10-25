"""
Custom integration to integrate Bestiarium Climate with Home Assistant.

For more details about this integration, please refer to
https://github.com/paslavskyi/bestiarium
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .balancer import BalancingCoordinator
from .const import (
    CONF_HEATING_ACTION,
    DOMAIN,
    ISSUE_SWITCH_NOW_HEATS,
    LOGGER,
    SUBENTRY_TYPE_SWITCH,
    ClimateMode,
)
from .switch import thermostats_of

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

    from .data import BestiariumClimateConfigEntry

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
) -> bool:
    """Set up Bestiarium Climate from a config entry."""
    LOGGER.debug("Setting up Bestiarium Climate for entry %s", entry.entry_id)

    coordinator = BalancingCoordinator(hass, entry)
    await coordinator.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start()

    configuration = _configuration(entry)

    async def _async_reload_on_change(
        hass: HomeAssistant, entry: BestiariumClimateConfigEntry
    ) -> None:
        """
        Reload when the configuration changes, not the options.

        The control settings (options) are read on every use; a reload would
        stop the switches' sequences and the tolerance timers for nothing.
        """
        if _configuration(entry) != configuration:
            await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_reload_on_change))
    return True


def _configuration(entry: BestiariumClimateConfigEntry) -> tuple[Any, ...]:
    """Return the entry's data and subentries, compared for a reload."""
    return (
        dict(entry.data),
        {
            subentry_id: (subentry.title, dict(subentry.data))
            for subentry_id, subentry in entry.subentries.items()
        },
    )


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
) -> bool:
    """
    Migrate the entry to version 1.2.

    1.2: a virtual switch takes heating or cooling from its thermostats; its
    own mode field goes (DEV-008 stage B).
    """
    if entry.minor_version < 2:  # noqa: PLR2004
        for subentry in entry.subentries.values():
            if (
                subentry.subentry_type == SUBENTRY_TYPE_SWITCH
                and "mode" in subentry.data
            ):
                _report_cooling_switch_now_heats(hass, entry, subentry)
                data = {k: v for k, v in subentry.data.items() if k != "mode"}
                hass.config_entries.async_update_subentry(entry, subentry, data=data)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


def _report_cooling_switch_now_heats(
    hass: HomeAssistant, entry: BestiariumClimateConfigEntry, subentry: ConfigSubentry
) -> None:
    """
    Tell that a cooling switch with a heating action, without thermostat, heats.

    No silent change (the owner's decision): a Repairs issue until the switch
    gets a thermostat or loses its heating action.
    """
    switch = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, subentry.subentry_id
    )
    if (
        subentry.data["mode"] == ClimateMode.COOLING
        and subentry.data.get(CONF_HEATING_ACTION)
        and not (switch and thermostats_of(hass, switch))
    ):
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{ISSUE_SWITCH_NOW_HEATS}_{subentry.subentry_id}",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_SWITCH_NOW_HEATS,
            translation_placeholders={"switch": subentry.title},
            data={"entry_id": entry.entry_id, "subentry_id": subentry.subentry_id},
        )


async def async_unload_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
) -> bool:
    """Unload a config entry."""
    LOGGER.debug("Unloading Bestiarium Climate entry %s", entry.entry_id)

    # The platforms first: no timer is set on a stopped coordinator, and no
    # two coordinators control the same switches across a reload
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id).stop()
    return unload_ok
