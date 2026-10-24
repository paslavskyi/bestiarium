"""The balancer's bus events are readable in the HA logbook."""

# ruff: noqa: S101

from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import HomeAssistant

from custom_components.bestiarium_devices.const import (
    DOMAIN,
    EVENT_DATA,
    EVENT_DEVICE_TURNED_OFF,
    EVENT_DEVICE_TURNED_ON,
    EVENT_MODE_APPLIED,
    EVENT_OVERLOAD_CLEARED,
    EVENT_OVERLOAD_FIRST_QUEUE,
    EVENT_OVERLOAD_SECOND_QUEUE,
    DeviceSwitchedData,
    ModeAppliedData,
    OverloadClearedData,
    OverloadFirstQueueData,
    OverloadSecondQueueData,
)
from custom_components.bestiarium_devices.logbook import (
    DESCRIBERS,
    async_describe_events,
)

Describe = Callable[[str, Any], dict[str, Any]]

TURNED = DeviceSwitchedData(
    strategy="green",
    device_id="dev1",
    device_name="Mock Boiler",
    switch_entity_id="switch.mock_boiler",
    priority=5200,
)


class _Event:
    def __init__(self, data: Any) -> None:
        self.data = data


@pytest.fixture
def describe(hass: HomeAssistant) -> Describe:
    """Return the logbook description of an event with the given payload."""
    describers: dict[str, Callable] = {}

    def register(domain: str, event_type: str, describer: Callable) -> None:
        assert domain == DOMAIN
        describers[event_type] = describer

    async_describe_events(hass, register)
    return lambda event_type, data: describers[event_type](_Event(data))


def test_every_event_of_the_contract_is_described() -> None:
    """Each event with a payload contract has a logbook description."""
    assert set(DESCRIBERS) == set(EVENT_DATA)


@pytest.mark.parametrize(
    ("event_type", "data", "message"),
    [
        (
            EVENT_DEVICE_TURNED_ON,
            TURNED,
            "turned on by the balancer (green strategy, priority 5200)",
        ),
        (
            EVENT_DEVICE_TURNED_OFF,
            DeviceSwitchedData(**{**TURNED, "strategy": "red"}),
            "turned off by the balancer (red strategy, priority 5200)",
        ),
        (
            EVENT_OVERLOAD_FIRST_QUEUE,
            OverloadFirstQueueData(
                devices_turned_off=2,
                device_ids=["a", "b"],
                device_names=["Mock Boiler", "Mock Cooker"],
                overload_cleared=False,
            ),
            "overload: first queue turned off 2 devices (Mock Boiler, Mock Cooker)",
        ),
        (
            EVENT_OVERLOAD_SECOND_QUEUE,
            OverloadSecondQueueData(
                first_queue_count=2,
                second_queue_count=1,
                device_ids=["c"],
                device_names=["Mock Fridge"],
            ),
            "overload: second queue turned off 1 devices (Mock Fridge)",
        ),
        (
            EVENT_OVERLOAD_CLEARED,
            OverloadClearedData(cleared_after="wait_period", first_queue_count=2),
            "overload cleared after the wait period",
        ),
        (
            EVENT_MODE_APPLIED,
            ModeAppliedData(
                mode="Night", devices_turned_off=["a"], devices_turned_on=[]
            ),
            "mode Night applied: turned off 1, turned on 0 devices",
        ),
    ],
)
def test_every_balancer_event_is_described(
    describe: Describe, event_type: str, data: Any, message: str
) -> None:
    """Each event of the bus contract gets a readable logbook line."""
    assert describe(event_type, data)[LOGBOOK_ENTRY_MESSAGE] == message


def test_device_events_point_at_the_relay(describe: Describe) -> None:
    """A turned on/off line is named after the device and linked to its relay."""
    entry = describe(EVENT_DEVICE_TURNED_ON, TURNED)

    assert entry[LOGBOOK_ENTRY_NAME] == "Mock Boiler"
    assert entry[LOGBOOK_ENTRY_ENTITY_ID] == "switch.mock_boiler"
