"""
Who should heat: the arrangement of the capacity (the owner's decision 32).

A pure function, without the zone: the balancer then applies the zone's rules
to its changes (green turns off and starts one at a time, yellow only turns
off; the pause and no data change nothing).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True)
class Heater:
    """A thermostat that wants to heat, as the arrangement sees it."""

    key: str
    # Lower first: the less worked (working time / minimum), then the colder room
    rank: tuple[float, float]
    # The balancer has it on
    running: bool
    # Its start is under way: it keeps its place, its answer decides first
    starting: bool = False
    # In its pause after an "off": it takes no place
    paused: bool = False


def arrange(heaters: Iterable[Heater], capacity: int) -> set[str]:
    """
    Return who should heat.

    The starts under way keep their places; the places left go by rank to
    the others. A waiting heater in its pause takes no place and turns out
    no running one. Heaters without power, deferred or without temperature
    are no heaters here.
    """
    candidates = sorted(
        (h for h in heaters if h.running or not h.paused), key=lambda h: h.rank
    )
    starting = [h for h in candidates if h.starting][:capacity]
    others = [h for h in candidates if not h.starting]
    return {h.key for h in [*starting, *others[: capacity - len(starting)]]}
