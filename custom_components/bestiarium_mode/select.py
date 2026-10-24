"""Select platform for bestiarium_mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity

from .const import SIGNAL_MODES_RENAMED

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BestiariumModeConfigEntry

CONF_OPTIONS = "options"
CONF_NAME = "name"
DEFAULT_ICON = "mdi:home"


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: BestiariumModeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the select platform."""
    async_add_entities([BestiariumModeSelect(entry)])


class BestiariumModeSelect(SelectEntity, RestoreEntity):
    """Bestiarium Mode select entity."""

    _attr_has_entity_name = True
    _attr_translation_key = "mode"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the select entity."""
        super().__init__()
        self._entry = entry
        self._attr_unique_id = "select"
        self._attr_name = "Bestiarium Mode"
        self._attr_icon = DEFAULT_ICON
        self._current_option: str | None = None
        self._options_data: list[dict[str, str]] = entry.data.get(CONF_OPTIONS, [])

    @property
    def options(self) -> list[str]:
        """Return the list of available options."""
        return [
            opt.get(CONF_NAME, "") for opt in self._options_data if opt.get(CONF_NAME)
        ]

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option in self.options:
            self._current_option = option
            self.async_write_ha_state()

    @callback
    def _async_modes_renamed(
        self, renamed: dict[str, str], _removed: list[str]
    ) -> None:
        """
        Follow the rename of the current mode before the entry reloads.

        The reload restores the state the select had: renamed here, the home
        keeps its mode instead of falling to the first one.
        """
        if self._current_option in renamed:
            self._options_data = [
                {**opt, CONF_NAME: renamed.get(opt[CONF_NAME], opt[CONF_NAME])}
                for opt in self._options_data
            ]
            self._current_option = renamed[self._current_option]
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_MODES_RENAMED, self._async_modes_renamed
            )
        )
        # Restore state if available
        if (last_state := await self.async_get_last_state()) and last_state.state:
            # Only restore if the option still exists
            if last_state.state in self.options:
                self._current_option = last_state.state
            elif self.options:
                # If restored option no longer exists, default to first option
                self._current_option = self.options[0]
        elif self.options:
            # If no previous state and we have options, default to first
            self._current_option = self.options[0]
