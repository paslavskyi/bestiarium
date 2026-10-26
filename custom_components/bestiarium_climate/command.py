"""
The commands to a thermostat's switch and their series (DEC-014, review 24).

A command is an attempt of its direction's series when it is sent, and a
failed attempt once its window (a cycle, from its end) is over without an
answer. Only the switch's answer ends a series, never a change of what is
wanted; a power cut ends the series "on" too (protection, not a fault), and
a deferral ends all of them. One path closes a series: its commands are
closed with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .const import BALANCING_INTERVAL_SECONDS

if TYPE_CHECKING:
    import asyncio

# One attempt a balancing cycle; a little less, as the timers jitter
ATTEMPT_INTERVAL = timedelta(seconds=BALANCING_INTERVAL_SECONDS - 5)


def direction(on: bool) -> str:  # noqa: FBT001
    """Return the name of a direction in the memory."""
    return "on" if on else "off"


@dataclass(eq=False)
class SwitchCommand:
    """One command to the switch: its direction, its task, its times."""

    on: bool
    sent_at: datetime
    # The deferral's "off": its answer ends no "Not responding"
    deferral: bool = False
    task: asyncio.Task[None] | None = None
    ended_at: datetime | None = None
    # Its series is closed: the switch answered, the power was cut or the
    # switch was deferred
    closed: bool = False

    @property
    def running(self) -> bool:
        """Return whether its task has not ended yet."""
        return self.ended_at is None

    def in_window(self, now: datetime) -> bool:
        """Return whether it runs, or its window after its end is not over."""
        return self.ended_at is None or now < self.ended_at + ATTEMPT_INTERVAL

    def waiting(self, now: datetime) -> bool:
        """Return whether it waits for its answer: open, and in its window."""
        return not self.closed and self.in_window(now)


class SwitchCommands:
    """The commands to one switch and the series of each direction."""

    def __init__(self, kept: dict[str, int]) -> None:
        """Start with the series kept across a restart; no command yet."""
        self._series = {on: kept.get(direction(on), 0) for on in (True, False)}
        # The commands running or in their window, and the last of each direction
        self._commands: list[SwitchCommand] = []
        # The "on"s of the start the balancer wants now: its first, and how many
        self._start: SwitchCommand | None = None
        self._start_ons = 0

    @property
    def kept(self) -> dict[str, int]:
        """Return the series to keep across a restart."""
        return {direction(on): n for on, n in self._series.items()}

    def latest(self, on: bool | None = None) -> SwitchCommand | None:  # noqa: FBT001
        """Return the last command, or the last of a direction."""
        return next(
            (c for c in reversed(self._commands) if on is None or c.on is on), None
        )

    def new_start(self) -> None:
        """Count the "on"s of a new start: the balancer wants the switch on now."""
        self._start, self._start_ons = None, 0

    @property
    def start(self) -> SwitchCommand | None:
        """Return the start's first command while it is the only "on" since."""
        return self._start if self._start_ons == 1 else None

    @property
    def start_sent(self) -> bool:
        """Return whether an "on" was sent for the start the balancer wants now."""
        return self._start_ons > 0

    def waiting(self, now: datetime, *, on: bool) -> bool:
        """Return whether a command of a direction waits for its answer."""
        return any(c.on is on and c.waiting(now) for c in self._commands)

    def in_flight(self, now: datetime, *, on: bool) -> bool:
        """Return whether a command of a direction runs or is in its window."""
        return any(c.on is on and c.in_window(now) for c in self._commands)

    def failed(self, now: datetime, *, on: bool) -> int:
        """
        Return the failed attempts of a direction's series.

        A command is a failed attempt once its window is over without an
        answer (DEC-014, rule 3), an overtaken one too: the ones still waiting
        are not failed yet.
        """
        waiting = sum(
            c.on is on and not c.deferral and c.waiting(now) for c in self._commands
        )
        return self._series[on] - waiting

    def hung(self, now: datetime, budget: timedelta) -> bool:
        """Return whether a command not answered runs longer than the budget."""
        return any(
            c.running and not c.closed and now - c.sent_at >= budget
            for c in self._commands
        )

    def next_window_end(self, now: datetime) -> datetime | None:
        """Return when the next window of a command waiting for its answer ends."""
        ends = [
            c.ended_at + ATTEMPT_INTERVAL
            for c in self._commands
            if c.ended_at is not None and c.waiting(now)
        ]
        return min(ends, default=None)

    def send(self, on: bool, now: datetime, *, deferral: bool) -> SwitchCommand:  # noqa: FBT001
        """
        Count a command sent now; the one still running the other way is cancelled.

        A command is an attempt of its direction's series when it is sent; the
        deferral's "off" is none.
        """
        latest = self.latest()
        if latest is not None and latest.running and latest.task is not None:
            latest.task.cancel()
        command = SwitchCommand(on, now, deferral=deferral)
        self._commands.append(command)
        if not deferral:
            self._series[on] += 1
        if on:
            self._start_ons += 1
            self._start = self._start or command
        return command

    def ended(self, command: SwitchCommand, now: datetime) -> None:
        """
        Note the end of a command, whatever ended it.

        Only that command: an overtaken one that ends late frees or restamps
        none newer. The ended ones are forgotten once their window is over,
        but the last of each direction.
        """
        command.ended_at = now
        self._commands = [
            c for c in self._commands if c.in_window(now) or c is self.latest(c.on)
        ]

    def cancel(self) -> None:
        """Cancel the commands still running (the entry unloads)."""
        for command in self._commands:
            if command.running and command.task is not None:
                command.task.cancel()

    def answer(self, actual: bool) -> SwitchCommand | None:  # noqa: FBT001
        """
        Close the series the switch answered: it is in the state of a direction.

        Return the command it answered, if one was open; None when nothing
        changed or the series closed without one (after a restart).
        """
        command = self.latest(actual)
        if command is not None and not command.closed:
            self.close(on=actual)
            return command
        if self._series[actual]:
            self.close(on=actual)
        return None

    def close(self, *, on: bool) -> bool:
        """Close a direction's series and its commands; return whether it was open."""
        was_open = bool(self._series[on]) or any(
            c.on is on and not c.closed for c in self._commands
        )
        for c in self._commands:
            c.closed = c.closed or c.on is on
        self._series[on] = 0
        return was_open

    def reset(self) -> None:
        """Close every series and command: the switch is deferred."""
        for on in (True, False):
            self.close(on=on)
