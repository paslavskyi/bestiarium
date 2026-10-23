"""The fault model of a mock socket: ignored, delayed, stuck commands, dropouts."""

# ruff: noqa: S101

from collections.abc import Callable

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from custom_components.bestiarium_mock.const import FaultProfile
from custom_components.bestiarium_mock.coordinator import MockDevicesCoordinator
from custom_components.bestiarium_mock.faults import MockSocket

from .conftest import SEED

EntityId = Callable[[str, str], str]


def _custom(stand: MockDevicesCoordinator, device: str, **params: float) -> MockSocket:
    socket = stand.sockets[device]
    socket.set_profile(FaultProfile.CUSTOM)
    for key, value in params.items():
        socket.set_param(key, value)
    return socket


def _state(hass: HomeAssistant, entity_id: EntityId, device: str) -> str:
    return hass.states.get(entity_id("switch", f"mock_{device}")).state


def _power(hass: HomeAssistant, entity_id: EntityId, device: str) -> str:
    return hass.states.get(entity_id("sensor", f"mock_{device}_power")).state


async def test_ignored_command_changes_nothing(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,
    entity_id: EntityId,
    advance: Callable,
) -> None:
    """At 100 % the socket ignores every command."""
    _custom(stand, "boiler", ignore_percent=100)

    for _ in range(5):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id("switch", "mock_boiler")}
        )
        await advance(2)

    assert _state(hass, entity_id, "boiler") == STATE_OFF
    assert _power(hass, entity_id, "boiler") == "0"


async def test_ignoring_is_reproducible(
    stand: MockDevicesCoordinator, advance: Callable
) -> None:
    """At 50 % the same seed ignores the same commands."""
    socket = _custom(stand, "boiler", ignore_percent=50)

    async def executed() -> list[bool]:
        socket.rng.seed(SEED)
        results = []
        for number in range(20):
            on = number % 2 == 0
            socket.restore(on=not on)
            socket.command(on=on)
            await advance(1)
            results.append(socket.physical_on == on)
        return results

    first = await executed()
    assert first == await executed()
    assert True in first
    assert False in first


async def test_delay_from_the_range(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,
    entity_id: EntityId,
    advance: Callable,
) -> None:
    """The state and the power change only after the delay."""
    _custom(stand, "boiler", delay_min_s=5, delay_max_s=5)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id("switch", "mock_boiler")}
    )

    await advance(4)
    assert _state(hass, entity_id, "boiler") == STATE_OFF
    assert _power(hass, entity_id, "boiler") == "0"

    await advance(1)
    assert _state(hass, entity_id, "boiler") == STATE_ON
    assert _power(hass, entity_id, "boiler") == "2000"


async def test_repeated_command_keeps_its_delay(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,
    entity_id: EntityId,
    advance: Callable,
) -> None:
    """A repeat of the command on its way does not start the delay again."""
    _custom(stand, "boiler", delay_min_s=5, delay_max_s=5)
    switch = entity_id("switch", "mock_boiler")

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch})
    await advance(3)
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch})
    await advance(2)

    assert _state(hass, entity_id, "boiler") == STATE_ON


async def test_stuck_state_until_a_clean_command(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,
    entity_id: EntityId,
    advance: Callable,
) -> None:
    """The load switches but HA keeps the old state, as on 2026-10-23."""
    socket = _custom(stand, "commode_socket", stuck_percent=100)
    switch = entity_id("switch", "mock_commode_socket")

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch})
    await advance(3)
    assert _state(hass, entity_id, "commode_socket") == STATE_OFF
    assert _power(hass, entity_id, "commode_socket") == "8"

    socket.set_param("stuck_percent", 0)
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch})
    await advance(3)
    assert _state(hass, entity_id, "commode_socket") == STATE_ON


async def test_dropout_makes_switch_and_power_unavailable(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,
    entity_id: EntityId,
    advance: Callable,
) -> None:
    """A dropout hides switch and power for its duration; the load keeps going."""
    socket = stand.sockets["boiler"]
    socket.restore(on=True)
    _custom(stand, "boiler", dropouts_per_hour=60, dropout_min_s=10, dropout_max_s=10)

    for _ in range(720):
        await advance(5)
        if _state(hass, entity_id, "boiler") == STATE_UNAVAILABLE:
            break
    assert _power(hass, entity_id, "boiler") == STATE_UNAVAILABLE
    # The tester still sees that the load works
    state = hass.states.get(entity_id("binary_sensor", "mock_boiler_state"))
    assert state.state == STATE_ON
    assert socket.physical_on

    await advance(10)
    assert _state(hass, entity_id, "boiler") == STATE_ON
    assert _power(hass, entity_id, "boiler") == "2000"


async def test_no_dropouts_without_a_rate(
    hass: HomeAssistant,
    stand: MockDevicesCoordinator,  # noqa: ARG001
    entity_id: EntityId,
    advance: Callable,
) -> None:
    """A reliable socket never drops out."""
    for _ in range(72):
        await advance(50)
        assert _state(hass, entity_id, "boiler") == STATE_OFF
