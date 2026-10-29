"""The zone's rules: who heats after a cycle, without HA."""

# ruff: noqa: S101

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.bestiarium_climate.const import Zone
from custom_components.bestiarium_climate.zone_rules import (
    Candidate,
    ZoneInput,
    arrange_zone,
)

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def _heater(  # noqa: PLR0913
    key: str,
    rank: float,
    *,
    running: bool = False,
    heating: bool | None = None,
    starting: bool = False,
    paused_until: datetime | None = None,
    diff: float = 1.0,
    power: float = 1000.0,
    measured: float = 1000.0,
) -> Candidate:
    return Candidate(
        key=key,
        name=key,
        rank=(rank, -diff),
        running=running,
        starting=starting,
        paused_until=paused_until,
        heating=running if heating is None else heating,
        temperature_diff=diff,
        power=power,
        power_source="thermostat",
        measured=measured,
    )


def _state(  # noqa: PLR0913
    zone: Zone,
    capacity: int = 1,
    *,
    load: float = 1000.0,
    threshold: float = 5000.0,
    started_at: datetime | None = None,
    red_off_at: datetime | None = None,
) -> ZoneInput:
    return ZoneInput(
        zone=zone,
        capacity=capacity,
        now=NOW,
        load_and_threshold=(load, threshold),
        started_at=started_at,
        red_off_at=red_off_at,
    )


def test_green_turns_the_worked_one_out_and_starts_the_other() -> None:
    """In a deficit the less worked takes the place; the rotation is told."""
    a = _heater("a", 2.0, running=True)
    b = _heater("b", 0.5)
    result = arrange_zone([a, b], _state(Zone.GREEN))
    assert result.heaters == {"b"}
    assert result.freed == {"a"}
    assert result.rotated
    assert result.started_at == NOW
    assert result.retry_in == timedelta(seconds=10)


def test_green_starts_one_at_a_time() -> None:
    """Two places free: one starts now, the next a step later."""
    a, b = _heater("a", 0.1), _heater("b", 0.2)
    result = arrange_zone([a, b], _state(Zone.GREEN, capacity=2))
    assert result.heaters == {"a"}
    assert result.retry_in == timedelta(seconds=10)


def test_green_waits_the_step_after_the_last_start() -> None:
    """A start 4 s ago: the next one waits the 6 s left of the step."""
    a = _heater("a", 0.1)
    result = arrange_zone(
        [a], _state(Zone.GREEN, started_at=NOW - timedelta(seconds=4))
    )
    assert result.heaters == frozenset()
    assert result.retry_in == timedelta(seconds=6)


def test_green_a_start_that_does_not_fit_takes_no_place() -> None:
    """Over the red threshold even with the running one freed: it keeps heating."""
    a = _heater("a", 2.0, running=True, measured=200.0)
    b = _heater("b", 0.5, power=2000.0)
    result = arrange_zone([a, b], _state(Zone.GREEN, load=3500.0, threshold=5000.0))
    assert result.heaters == {"a"}
    assert result.freed == frozenset()
    assert result.retry_in == timedelta(seconds=10)


def test_an_unknown_load_does_not_fit() -> None:
    """Without the load or the threshold no start fits."""
    state = ZoneInput(Zone.GREEN, 1, NOW, None, None, None)
    assert arrange_zone([_heater("a", 0.1)], state).heaters == frozenset()


def test_without_load_limits_all_fit() -> None:
    """No zone sensor: only the capacity counts, no step to wait."""
    a = _heater("a", 0.1, power=9000.0)
    state = ZoneInput(Zone.NONE, 1, NOW, None, None, None)
    result = arrange_zone([a], state)
    assert result.heaters == {"a"}
    assert result.retry_in is None


def test_yellow_only_turns_off() -> None:
    """Yellow turns the rotation's "off" and starts none."""
    a = _heater("a", 2.0, running=True)
    b = _heater("b", 0.5)
    result = arrange_zone([a, b], _state(Zone.YELLOW))
    assert result.heaters == frozenset()
    assert result.freed == {"a"}


@pytest.mark.parametrize("zone", [Zone.PAUSE, Zone.NO_DATA])
def test_the_pause_and_no_data_change_nothing(zone: Zone) -> None:
    """What heats goes on; what does not heat is not wanted on."""
    a = _heater("a", 2.0, running=True)
    b = _heater("b", 0.5)
    c = _heater("c", 1.0, running=True, heating=False)
    result = arrange_zone([a, b, c], _state(zone, capacity=1))
    assert result.heaters == {"a"}
    assert result.freed == frozenset()


def test_red_turns_off_the_room_that_needs_it_least() -> None:
    """One "off" a step: the smallest temperature difference first."""
    a = _heater("a", 0.1, running=True, diff=2.0)
    b = _heater("b", 0.2, running=True, diff=0.3)
    result = arrange_zone([a, b], _state(Zone.RED, capacity=2))
    assert result.heaters == {"a"}
    assert result.red_off_at == NOW
    assert result.retry_in == timedelta(seconds=5)
    assert not result.rotated


def test_red_waits_the_step_after_the_last_off() -> None:
    """An "off" 2 s ago: the next one in 3 s."""
    a = _heater("a", 0.1, running=True)
    result = arrange_zone([a], _state(Zone.RED, red_off_at=NOW - timedelta(seconds=2)))
    assert result.heaters == {"a"}
    assert result.retry_in == timedelta(seconds=3)


def test_overload_turns_all_off_at_once() -> None:
    """No rotation: nothing is told to it."""
    a = _heater("a", 0.1, running=True)
    b = _heater("b", 0.2, running=True)
    result = arrange_zone([a, b], _state(Zone.OVERLOAD, capacity=1))
    assert result.heaters == frozenset()
    assert not result.rotated
    assert result.red_off_at is None


def test_a_pause_decides_again_when_it_ends() -> None:
    """A waiting heater in its pause takes no place; the cycle comes at its end."""
    a = _heater("a", 0.1, paused_until=NOW + timedelta(seconds=40))
    result = arrange_zone([a], _state(Zone.GREEN))
    assert result.heaters == frozenset()
    assert result.retry_in == timedelta(seconds=40)
