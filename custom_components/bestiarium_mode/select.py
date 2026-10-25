"""Select platform for bestiarium_mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoredExtraData, RestoreEntity

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
        # The new name of the current mode, renamed in the form, until the reload
        self._renamed_to: str | None = None
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
        Remember the new name of the current mode for the reload that follows.

        No state is written: the devices would see a change of mode and apply
        it. The reload restores the new name from the extra data; the select
        goes old → unavailable → new, which the devices take as no change.
        """
        if self._current_option in renamed:
            self._renamed_to = renamed[self._current_option]

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Keep the current mode, under its new name after a rename."""
        return RestoredExtraData(
            {"current_option": self._renamed_to or self._current_option}
        )

    async def async_added_to_hass(self) -> None:
        """Restore the current mode; the first one if it no longer exists."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_MODES_RENAMED, self._async_modes_renamed
            )
        )
        last: str | None = None
        if extra := await self.async_get_last_extra_data():
            last = extra.as_dict().get("current_option")
        elif last_state := await self.async_get_last_state():
            last = last_state.state
        if last in self.options:
            self._current_option = last
        elif self.options:
            self._current_option = self.options[0]
