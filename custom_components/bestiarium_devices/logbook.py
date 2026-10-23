"""
Describe the balancer's bus events in the HA logbook.

The events and their fields are unchanged; this only makes them readable in
the logbook. HA has no translations for logbook messages, so they are English.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import callback

from .const import (
    DOMAIN,
    EVENT_DEVICE_TURNED_OFF,
    EVENT_DEVICE_TURNED_ON,
    EVENT_MODE_APPLIED,
    EVENT_OVERLOAD_CLEARED,
    EVENT_OVERLOAD_FIRST_QUEUE,
    EVENT_OVERLOAD_SECOND_QUEUE,
    EVENT_ZONE_CHANGED,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.components.logbook import LazyEventPartialState
    from homeassistant.core import HomeAssistant

BALANCER = "Bestiarium balancer"


def _zone_changed(data: dict[str, Any]) -> dict[str, Any]:
    return {
        LOGBOOK_ENTRY_NAME: BALANCER,
        LOGBOOK_ENTRY_MESSAGE: (
            f"zone changed from {data['previous_zone']} to {data['zone']}"
        ),
    }


def _device_switched(action: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def describe(data: dict[str, Any]) -> dict[str, Any]:
        return {
            LOGBOOK_ENTRY_NAME: data["device_name"],
            LOGBOOK_ENTRY_MESSAGE: (
                f"{action} by the balancer ({data['strategy']} strategy, "
                f"priority {data['priority']})"
            ),
            LOGBOOK_ENTRY_ENTITY_ID: data["switch_entity_id"],
        }

    return describe


def _overload_first_queue(data: dict[str, Any]) -> dict[str, Any]:
    names = ", ".join(data["device_names"])
    return {
        LOGBOOK_ENTRY_NAME: BALANCER,
        LOGBOOK_ENTRY_MESSAGE: (
            f"overload: first queue turned off {data['devices_turned_off']} "
            f"devices ({names})"
        ),
    }


def _overload_second_queue(data: dict[str, Any]) -> dict[str, Any]:
    names = ", ".join(data["device_names"])
    return {
        LOGBOOK_ENTRY_NAME: BALANCER,
        LOGBOOK_ENTRY_MESSAGE: (
            f"overload: second queue turned off {data['second_queue_count']} "
            f"devices ({names})"
        ),
    }


def _overload_cleared(data: dict[str, Any]) -> dict[str, Any]:
    after = data["cleared_after"].replace("_", " ")
    return {
        LOGBOOK_ENTRY_NAME: BALANCER,
        LOGBOOK_ENTRY_MESSAGE: f"overload cleared after the {after}",
    }


def _mode_applied(data: dict[str, Any]) -> dict[str, Any]:
    return {
        LOGBOOK_ENTRY_NAME: BALANCER,
        LOGBOOK_ENTRY_MESSAGE: (
            f"mode {data['mode']} applied: turned off "
            f"{len(data['devices_turned_off'])}, turned on "
            f"{len(data['devices_turned_on'])} devices"
        ),
    }


DESCRIBERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    EVENT_ZONE_CHANGED: _zone_changed,
    EVENT_DEVICE_TURNED_ON: _device_switched("turned on"),
    EVENT_DEVICE_TURNED_OFF: _device_switched("turned off"),
    EVENT_OVERLOAD_FIRST_QUEUE: _overload_first_queue,
    EVENT_OVERLOAD_SECOND_QUEUE: _overload_second_queue,
    EVENT_OVERLOAD_CLEARED: _overload_cleared,
    EVENT_MODE_APPLIED: _mode_applied,
}


@callback
def async_describe_events(
    hass: HomeAssistant,  # noqa: ARG001
    async_describe_event: Callable[
        [str, str, Callable[[LazyEventPartialState], dict[str, Any]]], None
    ],
) -> None:
    """Describe the balancer's events."""
    for event_type, describe in DESCRIBERS.items():
        async_describe_event(
            DOMAIN,
            event_type,
            lambda event, describe=describe: describe(event.data),
        )
