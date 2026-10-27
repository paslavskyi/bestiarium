"""The arrangement of the capacity, without the zone (the owner's decision 32)."""

# ruff: noqa: S101

import pytest

from custom_components.bestiarium_climate.rotation import Heater, arrange

# Ranks: the less worked first (working time / minimum), then the colder room
FRESH = (0.2, -3.0)  # started a moment ago
PAST = (1.5, -3.0)  # past its minimum working time
LONG = (3.0, -3.0)  # long past it
WAITING = (1.0, -4.0)  # waiting: coefficient 1


@pytest.mark.parametrize(
    ("heaters", "capacity", "expected"),
    [
        # Nobody waits: the running ones stay
        (
            [Heater("a", PAST, running=True), Heater("b", LONG, running=True)],
            2,
            {"a", "b"},
        ),
        # A waiting one takes the place of the one longest past its time
        (
            [
                Heater("a", PAST, running=True),
                Heater("b", LONG, running=True),
                Heater("c", WAITING, running=False),
            ],
            2,
            {"a", "c"},
        ),
        # Within its minimum working time a running one keeps its place
        (
            [
                Heater("a", FRESH, running=True),
                Heater("b", FRESH, running=True),
                Heater("c", WAITING, running=False),
            ],
            2,
            {"a", "b"},
        ),
        # A waiting one in its pause takes no place and turns out nobody
        (
            [
                Heater("a", LONG, running=True),
                Heater("b", LONG, running=True),
                Heater("c", WAITING, running=False, paused=True),
            ],
            2,
            {"a", "b"},
        ),
        # ... and a free place goes to the next one that is not in its pause
        (
            [
                Heater("a", WAITING, running=False, paused=True),
                Heater("b", (1.0, -2.0), running=False),
            ],
            1,
            {"b"},
        ),
        # A start under way keeps its place even past the working time
        (
            [
                Heater("a", LONG, running=True, starting=True),
                Heater("b", WAITING, running=False),
            ],
            1,
            {"a"},
        ),
        # The capacity lowered: the worst-ranked go
        (
            [
                Heater("a", FRESH, running=True),
                Heater("b", PAST, running=True),
                Heater("c", LONG, running=True),
            ],
            1,
            {"a"},
        ),
        # Of two waiting, the colder room first
        (
            [
                Heater("a", (1.0, -2.0), running=False),
                Heater("b", (1.0, -6.0), running=False),
            ],
            1,
            {"b"},
        ),
        # No capacity: nobody
        ([Heater("a", FRESH, running=True)], 0, set()),
    ],
)
def test_arrange(heaters: list[Heater], capacity: int, expected: set[str]) -> None:
    """Who should heat, by capacity, rank, pause and the starts under way."""
    assert arrange(heaters, capacity) == expected
