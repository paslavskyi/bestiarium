"""
A reading of the stand (a thermometer, the grid) that can misbehave.

What HA sees of a sensor: its value, or unavailable. The profile decides:
reliable, unstable (short dropouts), no data (unavailable until the profile
changes), frozen (the value no longer changes).
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_call_later

from .const import (
    READING_DROPOUT_MAX_S,
    READING_DROPOUT_MIN_S,
    READING_DROPOUTS_PER_HOUR,
    ReadingProfile,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant


class MockReading:
    """The fault model of one sensor of the stand."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Start reliable."""
        self._hass = hass
        # Its own generator, so tests can seed each reading
        self.rng = random.Random()  # noqa: S311
        self.profile = ReadingProfile.RELIABLE
        self._dropped = False
        self._frozen: float | None = None
        self._listeners: list[Callable[[], None]] = []
        self._cancel_dropout: CALLBACK_TYPE | None = None

    @property
    def available(self) -> bool:
        """Return whether HA gets a value."""
        return self.profile is not ReadingProfile.NO_DATA and not self._dropped

    def shown(self, value: float) -> float:
        """Return the value HA sees for the true value."""
        return self._frozen if self._frozen is not None else value

    def add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call the listener when what HA sees changes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def set_profile(self, profile: ReadingProfile, value: float) -> None:
        """Switch to a profile; value is the true value now."""
        self.profile = profile
        self._frozen = value if profile is ReadingProfile.FROZEN else None
        self._stop_dropouts()
        if profile is ReadingProfile.UNSTABLE:
            self._schedule_dropout()
        self._changed()

    def stop(self) -> None:
        """Stop the dropouts."""
        self._stop_dropouts()

    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener()

    def _stop_dropouts(self) -> None:
        if self._cancel_dropout:
            self._cancel_dropout()
            self._cancel_dropout = None
        self._dropped = False

    def _schedule_dropout(self) -> None:
        self._cancel_dropout = async_call_later(
            self._hass,
            self.rng.expovariate(READING_DROPOUTS_PER_HOUR / 3600),
            self._drop_out,
        )

    @callback
    def _drop_out(self, _now: datetime) -> None:
        self._dropped = True
        self._changed()
        self._cancel_dropout = async_call_later(
            self._hass,
            self.rng.uniform(READING_DROPOUT_MIN_S, READING_DROPOUT_MAX_S),
            self._come_back,
        )

    @callback
    def _come_back(self, _now: datetime) -> None:
        self._dropped = False
        self._changed()
        self._schedule_dropout()
