"""Event platform for bestiarium_devices: what happened to a device's control."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.event import EventEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL_CONTROL_EVENT
from .control import ControlEvent
from .entity import BestiariumDeviceEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BestiariumDevicesConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the control events of a device entry."""
    async_add_entities([ControlEventEntity(hass, entry)])


class ControlEventEntity(BestiariumDeviceEntity, EventEntity):
    """
    What happened to a device that does not respond: deferred, retried, recovered.

    The control state stays "not responding" meanwhile; these events tell what
    happens, once each, for the home's automations to react to.
    """

    _key = "control_events"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def event_types(self) -> list[str]:
        """Return the control events."""
        return [event.value for event in ControlEvent]

    async def async_added_to_hass(self) -> None:
        """Fire each control event of the device."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CONTROL_EVENT.format(self._entry.entry_id),
                self._on_event,
            )
        )

    @callback
    def _on_event(self, event: ControlEvent, attributes: dict[str, Any]) -> None:
        self._trigger_event(event, attributes)
        self.async_write_ha_state()
