"""The load zones of bestiarium_mode, as the devices' balancer reads them."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, callback
from homeassistant.helpers.event import (
    async_track_entity_registry_updated_event,
    async_track_state_change_event,
)

from .const import LOGGER, ZONE_SENSOR_UNIQUE_ID
from .helpers import mode_entity_id

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant, State
    from homeassistant.helpers.entity_registry import EventEntityRegistryUpdatedData


class ActiveZone(StrEnum):
    """
    Active power load zone for balancing.

    Pause (green on pause) and no data run no strategy.
    """

    NO_DATA = "no_data"
    PAUSE = "pause"
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    OVERLOAD = "overload"


ZONES = (ActiveZone.GREEN, ActiveZone.YELLOW, ActiveZone.RED, ActiveZone.OVERLOAD)


class ZoneWatcher:
    """The four zone sensors: their entity_ids, the zone they show, a change."""

    def __init__(self, hass: HomeAssistant, on_changed: Callable[[], None]) -> None:
        """Call on_changed when a zone sensor changes its state; call follow."""
        self._hass = hass
        self._on_changed = on_changed
        self._subscriptions: list[Callable[[], None]] = []

    def entity_ids(self) -> list[str]:
        """Return the current entity_ids of the zone sensors there are."""
        return [e for zone in ZONES if (e := self._entity_id(zone))]

    def active_zone(self) -> ActiveZone:
        """
        Return the zone whose sensor is on.

        None on: the pause before green while the sensors are available, no
        data when they are not.
        """
        for zone in reversed(ZONES):
            state = self._state(zone)
            if state is not None and state.state == STATE_ON:
                return zone
        green = self._state(ActiveZone.GREEN)
        if green is not None and green.state == STATE_OFF:
            return ActiveZone.PAUSE
        return ActiveZone.NO_DATA

    def overload_cleared(self) -> bool:
        """
        Return True only if the overload sensor reports OFF.

        Fail-safe for the emergency shutdown: a missing, unknown or
        unavailable sensor means the overload may still be going on.
        """
        state = self._state(ActiveZone.OVERLOAD)
        return state is not None and state.state == STATE_OFF

    def follow(self) -> None:
        """
        Listen to the zone sensors by their current entity_ids.

        The listeners are made again whenever one of them is renamed.
        """
        entity_ids = self.entity_ids()
        self._subscriptions = [
            async_track_state_change_event(self._hass, entity_ids, self._on_state),
            async_track_entity_registry_updated_event(
                self._hass, entity_ids, self._on_renamed
            ),
        ]

    def stop(self) -> None:
        """Stop listening to the zone sensors."""
        for unsubscribe in self._subscriptions:
            unsubscribe()
        self._subscriptions.clear()

    def _entity_id(self, zone: ActiveZone) -> str | None:
        return mode_entity_id(
            self._hass, "binary_sensor", ZONE_SENSOR_UNIQUE_ID.format(zone=zone)
        )

    def _state(self, zone: ActiveZone) -> State | None:
        entity_id = self._entity_id(zone)
        return self._hass.states.get(entity_id) if entity_id else None

    @callback
    def _on_state(self, event: Event) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        LOGGER.debug(
            "Zone sensor changed: %s from %s to %s",
            event.data.get("entity_id"),
            old.state if old else "None",
            new.state if new else "None",
        )
        self._on_changed()

    @callback
    def _on_renamed(self, event: Event[EventEntityRegistryUpdatedData]) -> None:
        if "old_entity_id" not in event.data:
            return
        LOGGER.debug(
            "bestiarium_mode entity renamed from %s to %s",
            event.data["old_entity_id"],
            event.data["entity_id"],
        )
        self.stop()
        self.follow()
