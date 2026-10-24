"""
Custom integration to integrate Bestiarium Devices with Home Assistant.

For more details about this integration, please refer to
https://github.com/paslavskyi/bestiarium
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .balancer import BalancingCoordinator
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_MODE_OPTIONS_SNAPSHOT,
    CONF_SWITCH_ENTITY_ID,
    DATA_COORDINATOR,
    DEVICE_TYPE_EXTENDED,
    DOMAIN,
    LOGGER,
    SIGNAL_MODES_RENAMED,
)
from .control import async_get_response_memory
from .helpers import switch_taken
from .power_memory import (
    async_get_power_memory,
    async_import_history,
    async_track_runs,
)
from .repairs import async_watch_entities

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.typing import ConfigType

    from .data import BestiariumDevicesConfigEntry

PLATFORMS: list[Platform] = [
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Entities that became settings of the entry (1.3): estimated power, reset
ENTITIES_NOW_SETTINGS = (
    (Platform.NUMBER, "estimated_power"),
    (Platform.BUTTON, "reset_power_memory"),
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:  # noqa: ARG001
    """
    Start the one balancing coordinator, shared by all device entries.

    HA runs this once, before any entry is set up, so the entries cannot race
    to start one each; it lives as long as HA does.
    """
    coordinator = BalancingCoordinator(hass)
    hass.data[DOMAIN] = {DATA_COORDINATOR: coordinator}
    await coordinator.async_start()
    async_dispatcher_connect(hass, SIGNAL_MODES_RENAMED, _follow_renamed_modes(hass))

    async def stop(_event: Event) -> None:
        await coordinator.async_stop()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop)
    return True


RECORDER_DOMAIN = "recorder"


def _follow_renamed_modes(
    hass: HomeAssistant,
) -> Callable[[dict[str, str], list[str]], None]:
    """
    Return the handler that moves the priorities to renamed modes.

    The priorities are kept by mode name: a renamed mode would leave a stale
    key and the device would get its default priority in it.
    """

    @callback
    def follow(renamed: dict[str, str], removed: list[str]) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            priorities = {
                renamed.get(mode, mode): priority
                for mode, priority in entry.data.get(CONF_LOAD_PRIORITIES, {}).items()
                if mode not in removed
            }
            if priorities != entry.data.get(CONF_LOAD_PRIORITIES, {}):
                hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_LOAD_PRIORITIES: priorities},
                )

    return follow


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
) -> bool:
    """
    Migrate a device entry to version 1.4.

    1.2: an extended device whose device_id names no device any more (a
    pre-2026.8 composite id, or a deleted device) points to its relay's device.
    1.3: the estimated power and the reset of the power memory are in the
    options form; their entities go. The value already is in the options.
    1.4: mode_options_snapshot goes: it was written and never read.
    """
    if entry.minor_version < 2:  # noqa: PLR2004
        data = dict(entry.data)
        if relay_device_id := _relay_device_to_migrate_to(hass, entry):
            data[CONF_DEVICE_ID] = relay_device_id
        hass.config_entries.async_update_entry(entry, data=data, minor_version=2)
    if entry.minor_version < 3:  # noqa: PLR2004
        entities = er.async_get(hass)
        for platform, key in ENTITIES_NOW_SETTINGS:
            if entity_id := entities.async_get_entity_id(
                platform, DOMAIN, f"{entry.entry_id}_{key}"
            ):
                entities.async_remove(entity_id)
        hass.config_entries.async_update_entry(entry, minor_version=3)
    if entry.minor_version < 4:  # noqa: PLR2004
        data = {
            key: value
            for key, value in entry.data.items()
            if key != CONF_MODE_OPTIONS_SNAPSHOT
        }
        hass.config_entries.async_update_entry(entry, data=data, minor_version=4)
    return True


def _relay_device_to_migrate_to(
    hass: HomeAssistant, entry: BestiariumDevicesConfigEntry
) -> str | None:
    """Return the relay's device for a stale device_id, if it can take its place."""
    if entry.data.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_EXTENDED:
        return None
    device_id = entry.data.get(CONF_DEVICE_ID)
    if dr.async_get(hass).async_get(device_id, include_composite_devices=False):
        return None
    relay = er.async_get(hass).async_get(entry.data[CONF_SWITCH_ENTITY_ID])
    if relay is None or relay.device_id is None:
        return None
    if taken_by := switch_taken(
        hass, entry.data[CONF_SWITCH_ENTITY_ID], entry.entry_id
    ):
        LOGGER.warning(
            "Device %s keeps its stale device id: its relay is also controlled "
            "by %s; reconfigure one of them to pick another relay",
            entry.data.get(CONF_DEVICE_NAME, entry.title),
            taken_by,
        )
        return None
    return relay.device_id


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
) -> bool:
    """Set up this integration using UI."""
    LOGGER.debug("Setting up Bestiarium Devices for entry %s", entry.entry_id)
    coordinator: BalancingCoordinator = hass.data[DOMAIN][DATA_COORDINATOR]
    entry.async_on_unload(coordinator.async_add_device(entry))
    entry.async_on_unload(async_watch_entities(hass, entry))

    memory = await async_get_power_memory(hass)
    entry.async_on_unload(async_track_runs(hass, memory, entry))
    # Without the recorder the import waits for a start that has one
    if (
        not memory.filled_from_history(entry.entry_id)
        and RECORDER_DOMAIN in hass.config.components
    ):
        entry.async_create_background_task(
            hass,
            async_import_history(hass, memory, entry),
            f"{DOMAIN} power history import {entry.entry_id}",
        )

    # No update listener: options apply at once without a reload (the
    # settings form tells the expected power itself); reconfigure reloads.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    LOGGER.debug("Unloading Bestiarium Devices entry %s", entry.entry_id)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
) -> None:
    """Forget the power and response memory of a removed device."""
    (await async_get_power_memory(hass)).remove(entry.entry_id)
    (await async_get_response_memory(hass)).remove(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant,  # noqa: ARG001
    config_entry: BestiariumDevicesConfigEntry,  # noqa: ARG001
    device_entry: dr.DeviceEntry,  # noqa: ARG001
) -> bool:
    """
    Allow removal of config entry from device.

    Home Assistant automatically removes entities associated with a config entry
    when the entry is removed, so no explicit entity cleanup is needed here.
    """
    LOGGER.debug("Removing device entry from Bestiarium Devices")
    return True
