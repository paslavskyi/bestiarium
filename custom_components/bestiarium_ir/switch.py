"""The display switch of a Hisense air conditioner; its state is assumed."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, override

from homeassistant.components.infrared import InfraredEmitterConsumerEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .climate import hisense_device
from .const import CONF_EMITTER, SUBENTRY_TYPE_HISENSE

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .data import BestiariumIrConfigEntry
    from .hisense import HisenseLink


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: BestiariumIrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the display switch of each air conditioner."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_HISENSE:
            async_add_entities(
                [
                    HisenseDisplaySwitch(
                        subentry, entry.runtime_data[subentry.subentry_id]
                    )
                ],
                config_subentry_id=subentry.subentry_id,
            )


@dataclass
class _DisplayStoredData(ExtraStoredData):
    """The assumed display, apart from the visible state (see the climate's)."""

    display_on: bool

    @override
    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class HisenseDisplaySwitch(InfraredEmitterConsumerEntity, SwitchEntity, RestoreEntity):
    """
    The display of the unit, as HA assumes it.

    A change with the remote is not seen; the next frame of a running state
    makes up for it (design 10).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "display"
    _attr_assumed_state = True

    def __init__(self, subentry: ConfigSubentry, link: HisenseLink) -> None:
        """Place the switch on the air conditioner's device."""
        self._link = link
        self._infrared_emitter_entity_id = subentry.data[CONF_EMITTER]
        self._attr_unique_id = f"{subentry.subentry_id}_display"
        self._attr_device_info = hisense_device(subentry)

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the display; follow it."""
        await super().async_added_to_hass()
        if (extra := await self.async_get_last_extra_data()) is not None and (
            isinstance(display_on := extra.as_dict().get("display_on"), bool)
        ):
            self._link.display_on = display_on
        self.async_on_remove(self._link.add_listener(self.async_write_ha_state))

    @property
    @override
    def extra_restore_state_data(self) -> ExtraStoredData:
        return _DisplayStoredData(self._link.display_on)

    @property
    @override
    def is_on(self) -> bool:
        return self._link.display_on

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._link.set_display(on=True, context=self._context)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._link.set_display(on=False, context=self._context)
