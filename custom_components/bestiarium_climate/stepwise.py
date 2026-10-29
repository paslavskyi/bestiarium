"""
The stepwise "off" of a thermostat's switch (DEV-010, FR-027).

A switch that runs while its thermostat wants it off gets an "off" at once,
then again an interval apart, as many as the attempts; one interval after the
last, the event "out of control"; one more, its socket is cut, if it has one.
Then nothing more: the thermostat never turns the socket back on. It ends when
the switch is off or wanted on again; the next time starts from the first
"off". Kept in memory only: after a restart it starts again.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime, timedelta


class Step(StrEnum):
    """What the stepwise "off" does next."""

    ATTEMPT = "attempt"
    REPORT = "report"
    CUT = "cut"


@dataclass
class StepwiseOff:
    """The steps taken so far; none while the switch is not on unwanted."""

    attempts: int = 0
    last_attempt_at: datetime | None = None
    reported_at: datetime | None = None
    cut: bool = False
    # The entry to the red zone asks for the next attempt now
    forced: bool = False

    @property
    def active(self) -> bool:
        """Return whether an "off" of it was sent."""
        return self.attempts > 0

    def next(
        self, attempts: int, interval: timedelta, *, socket: bool
    ) -> tuple[Step, datetime | None] | None:
        """Return the next step and when it is due (None: at once), or None."""
        if self.last_attempt_at is None:
            return Step.ATTEMPT, None
        if self.attempts < attempts:
            return Step.ATTEMPT, None if self.forced else (
                self.last_attempt_at + interval
            )
        if self.reported_at is None:
            return Step.REPORT, self.last_attempt_at + interval
        if socket and not self.cut:
            return Step.CUT, self.reported_at + interval
        return None

    def failed(self, now: datetime, interval: timedelta) -> int:
        """Return the attempts whose interval is over with the switch still on."""
        if self.last_attempt_at is None:
            return 0
        return self.attempts - (now < self.last_attempt_at + interval)

    def attempt(self, now: datetime) -> None:
        """Count an "off" sent now."""
        self.attempts += 1
        self.last_attempt_at = now
        self.forced = False
