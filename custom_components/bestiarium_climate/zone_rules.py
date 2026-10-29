"""
The zone's rules: who heats after this cycle (the owner's decision 32).

A pure function of what the balancer sees, without timers: the balancer
applies its arrangement (wants the heaters on or off, tells the rotation in a
deficit) and decides again after `retry_in`. Who should heat by the capacity
alone is rotation.arrange; here the zone's rules apply to its changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .const import LOGGER, RED_STEP_SECONDS, START_STEP_SECONDS, Zone
from .rotation import Heater, arrange

# The zones in which the balancer starts heaters
STARTING_ZONES = (Zone.GREEN, Zone.NONE)


@dataclass(frozen=True)
class Candidate:
    """A thermostat that wants to work, as the zone's rules see it."""

    key: str
    name: str
    # Lower first: the less worked, then the colder room
    rank: tuple[float, float]
    # The balancer has it on
    running: bool
    starting: bool
    paused_until: datetime | None
    # Its switch heats (or is starting); out of green one that does not goes off
    heating: bool
    temperature_diff: float
    # What its start adds to the load, and where that comes from
    power: float
    power_source: str
    # What it draws now: what its "off" frees
    measured: float


@dataclass(frozen=True)
class ZoneInput:
    """The zone and what the rules of the last cycles left."""

    zone: Zone
    capacity: int
    now: datetime
    # The load and the red threshold of bestiarium_mode, in W; None: unknown
    load_and_threshold: tuple[float, float] | None
    # The last start: in green one at a time, a step apart
    started_at: datetime | None
    # The last "off" of the red zone: one a step
    red_off_at: datetime | None


@dataclass(frozen=True)
class Arrangement:
    """Who heats after this cycle, and when to decide again."""

    heaters: frozenset[str]
    # Turned off by the rotation: told to it in a deficit
    freed: frozenset[str]
    retry_in: timedelta | None
    # A deficit: the rotation is told of its changes
    rotated: bool
    started_at: datetime | None
    red_off_at: datetime | None


def arrange_zone(candidates: list[Candidate], state: ZoneInput) -> Arrangement:
    """
    Return who heats after this cycle by the zone's rules.

    Green (or no load limits): its "off"s at once, even before the new heater
    fits, and its starts one at a time, if they fit with the power of the
    ones turned off freed. Yellow: its "off"s only. The pause and no data: no
    rotation. Red: the heaters go off one by one; overload: all at once. Out
    of green a heater that does not heat is not wanted on (B1).
    """
    if state.zone is Zone.RED:
        return _red(candidates, state)
    return _Arranging(sorted(candidates, key=lambda c: c.rank), state).arrange()


def _red(candidates: list[Candidate], state: ZoneInput) -> Arrangement:
    """Red zone: every 5 s the heater whose room needs it least goes off."""
    heating = [c for c in candidates if c.heating]
    heaters = {c.key for c in heating}
    red_off_at, retry_in = state.red_off_at, None
    step = timedelta(seconds=RED_STEP_SECONDS)
    if heating and (red_off_at is None or state.now - red_off_at >= step):
        off = min(heating, key=lambda c: c.temperature_diff)
        LOGGER.info("%s: turned off in the red zone", off.name)
        heaters.discard(off.key)
        red_off_at = state.now
        if len(heating) > 1:
            retry_in = step
    elif heating and red_off_at is not None:
        retry_in = step - (state.now - red_off_at)
    return Arrangement(
        heaters=frozenset(heaters),
        freed=frozenset(),
        retry_in=retry_in,
        rotated=False,
        started_at=state.started_at,
        red_off_at=red_off_at,
    )


@dataclass
class _Arranging:
    """One arrangement out of red: the retries and starts it collects."""

    ranked: list[Candidate]
    state: ZoneInput
    retries: list[timedelta] = field(default_factory=list)
    started_at: datetime | None = field(init=False)
    # In green whether a start may go in this cycle (one at a time)
    gate_open: bool = field(init=False)

    def __post_init__(self) -> None:
        """Start from the last start; in green a start waits for the one under way."""
        self.started_at = self.state.started_at
        zone = self.state.zone
        self.gate_open = zone is Zone.NONE or (
            zone is Zone.GREEN and not any(c.starting for c in self.ranked)
        )

    def arrange(self) -> Arrangement:
        zone, ranked = self.state.zone, self.ranked
        deficit = self.state.capacity < len(ranked)
        if zone is Zone.OVERLOAD:
            # Not in the devices' emergency queue: climate turns off all at
            # once; no rotation, so no rotation events
            return self._result(set(), {c.key for c in ranked}, rotated=False)
        arranged, unfit = self._arranged()
        for c in ranked:
            # Decide again the moment a pause ends
            if not c.running and c.paused_until is not None:
                self.retries.append(c.paused_until - self.state.now)
        if zone not in STARTING_ZONES:
            heaters = {
                c.key
                for c in ranked
                if c.heating and (zone is not Zone.YELLOW or c.key in arranged)
            }
            freed = {c.key for c in ranked if c.heating and c.key not in heaters}
            return self._result(heaters, freed, rotated=deficit)
        freed = {c.key for c in ranked if c.running and c.key not in arranged}
        # While one does not fit, the next step decides again
        if unfit:
            self.retries.append(timedelta(seconds=START_STEP_SECONDS))
        heaters = {c.key for c in ranked if c.running and c.key in arranged}
        for c in ranked:
            if c.key in arranged and not c.running and self._start():
                heaters.add(c.key)
        return self._result(heaters, freed, rotated=deficit)

    def _result(
        self, heaters: set[str], freed: set[str], *, rotated: bool
    ) -> Arrangement:
        return Arrangement(
            heaters=frozenset(heaters),
            freed=frozenset(freed),
            retry_in=min(self.retries, default=None),
            rotated=rotated,
            started_at=self.started_at,
            red_off_at=None,
        )

    def _arranged(self) -> tuple[set[str], bool]:
        """
        Return who should heat, and whether a waiting one does not fit.

        The owner's decision (SRC-140): a waiting heater that does not fit
        even with the power of the running one it would replace freed (load
        minus that one's measured power plus its own over the red threshold;
        an unknown load or threshold does not fit) takes no place, as one in
        its pause: the running one goes on heating. Without load limits all
        fit.
        """
        ranked = self.ranked
        heaters = {
            c.key: Heater(
                c.key,
                c.rank,
                running=c.running,
                starting=c.starting,
                paused=c.paused_until is not None,
            )
            for c in ranked
        }
        unfit: set[str] = set()
        while True:
            arranged = arrange(
                (h for key, h in heaters.items() if key not in unfit),
                self.state.capacity,
            )
            new = [c for c in ranked if c.key in arranged and not c.running]
            out = [c for c in reversed(ranked) if c.running and c.key not in arranged]
            # Each new one takes the place of the worst-ranked going out
            freed = {
                c.key: out[place].measured if place < len(out) else 0.0
                for place, c in enumerate(new)
            }
            if self.state.zone is Zone.NONE:
                return arranged, False
            if (
                no_fit := next(
                    (c for c in new if not self._fits(c, freed[c.key])), None
                )
            ) is None:
                return arranged, bool(unfit)
            unfit.add(no_fit.key)

    def _start(self) -> bool:
        """Return whether an arranged heater (it fits) starts now: one at a time."""
        if not self.gate_open:
            self.retries.append(timedelta(seconds=START_STEP_SECONDS))
            return False
        on = self._may_start()
        self.gate_open = not on
        return on

    def _may_start(self) -> bool:
        """
        Return whether a heater that fits may start now: one at a time.

        The next start waits a step, so that the load shows the last one.
        Without load limits (no zone) only the capacity counts.
        """
        step = timedelta(seconds=START_STEP_SECONDS)
        now = self.state.now
        if self.state.zone is Zone.NONE:
            return True
        if self.started_at is not None and now - self.started_at < step:
            self.retries.append(step - (now - self.started_at))
            return False
        self.started_at = now
        self.retries.append(step)
        return True

    def _fits(self, candidate: Candidate, freed: float) -> bool:
        """
        Return whether a heater's start fits: the rule of the devices' balancer.

        The load, less the measured power freed by the one it takes the place
        of, plus the heater's power stays at or below the red threshold
        (DEV-006, SRC-140); an unknown load or threshold does not fit.
        """
        if self.state.load_and_threshold is None:
            LOGGER.debug(
                "%s does not fit: load or red threshold unknown", candidate.name
            )
            return False
        load, threshold = self.state.load_and_threshold
        if load - freed + candidate.power > threshold:
            LOGGER.debug(
                "%s does not fit: %s W - %s W + %s W (%s) > %s W",
                candidate.name,
                load,
                freed,
                candidate.power,
                candidate.power_source,
                threshold,
            )
            return False
        return True
