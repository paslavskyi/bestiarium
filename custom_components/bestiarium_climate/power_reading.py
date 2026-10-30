"""
A switch's on/off read from a power sensor.

On at or above the minimum power; on stays on down to the minimum minus the
hysteresis. A change is shown only after it has lasted the delay; no power
data (unavailable, unknown, not a number) only after the tolerance, and data
back at once. The virtual switch and the universal thermostat read this way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

from homeassistant.core import CoreState, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MIN_POWER,
    CONF_POWER_DELAY,
    CONF_POWER_HYSTERESIS,
    CONF_POWER_TOLERANCE,
    DEFAULT_POWER_DELAY_SECONDS,
    DEFAULT_POWER_HYSTERESIS_PERCENT,
    DEFAULT_POWER_TOLERANCE_SECONDS,
    LOGGER,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from homeassistant.core import (
        CALLBACK_TYPE,
        Event,
        EventStateChangedData,
        HomeAssistant,
        State,
    )


@dataclass(frozen=True)
class PowerThresholds:
    """When the power reads on, and how long a change must last."""

    min_power: float
    hysteresis_percent: int
    delay_seconds: int
    tolerance_seconds: int

    @classmethod
    def of(cls, data: Mapping[str, Any], *, min_power: float = 0.0) -> Self:
        """Return the thresholds of a subentry; a missing one is its default."""
        return cls(
            min_power=float(data.get(CONF_MIN_POWER, min_power)),
            hysteresis_percent=data.get(
                CONF_POWER_HYSTERESIS, DEFAULT_POWER_HYSTERESIS_PERCENT
            ),
            delay_seconds=data.get(CONF_POWER_DELAY, DEFAULT_POWER_DELAY_SECONDS),
            tolerance_seconds=data.get(
                CONF_POWER_TOLERANCE, DEFAULT_POWER_TOLERANCE_SECONDS
            ),
        )


class PowerReading:
    """On or off by a power sensor; tells its owner when the shown state changes."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        power_entity: str,
        thresholds: PowerThresholds,
        on_change: Callable[[], None],
    ) -> None:
        """Read nothing yet; call start."""
        self._hass = hass
        self.name = name
        self.power_entity = power_entity
        self._on_power = thresholds.min_power
        self._off_power = thresholds.min_power * (
            1 - thresholds.hysteresis_percent / 100
        )
        self._delay = thresholds.delay_seconds
        self._tolerance = thresholds.tolerance_seconds
        self._on_change = on_change
        self.available = True
        self.is_on = False
        self.last_changed: datetime = dt_util.utcnow()
        # The reading waiting to be shown, and its timer
        self._candidate: bool | None = None
        self._pending: CALLBACK_TYPE | None = None
        self._unsubscribe: CALLBACK_TYPE | None = None

    @callback
    def start(self) -> None:
        """Show the power sensor's state and follow it."""
        self._show(on=self._reading(self._hass.states.get(self.power_entity)))
        self._unsubscribe = async_track_state_change_event(
            self._hass, [self.power_entity], self._async_power_changed
        )

    @callback
    def stop(self) -> None:
        """Stop following."""
        self._cancel_pending()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _reading(self, state: State | None) -> bool | None:
        """Return on or off by the power, with the hysteresis; None without data."""
        if state is None:
            return None
        try:
            power = float(state.state)
        except ValueError:
            return None
        on = self.available and self.is_on
        return power >= (self._off_power if on else self._on_power)

    def _show(self, *, on: bool | None) -> bool:
        """
        Show the reading; return whether the shown state changed.

        Once HA runs, one info line when the data goes or is back.
        """
        available = on is not None
        if available != self.available and self._hass.state is CoreState.running:
            LOGGER.info(
                "%s: power data from %s %s",
                self.name,
                self.power_entity,
                "is back" if available else "is gone",
            )
        changed = (available, bool(on)) != (self.available, self.is_on)
        self.available = available
        self.is_on = bool(on)
        if changed:
            self.last_changed = dt_util.utcnow()
        return changed

    def _show_and_tell(self, *, on: bool | None) -> None:
        if self._show(on=on):
            self._on_change()

    @callback
    def _async_power_changed(self, event: Event[EventStateChangedData]) -> None:
        """Show data back at once, a change after the delay, none after tolerance."""
        reading = self._reading(event.data["new_state"])
        shown = self.is_on if self.available else None
        if reading == shown:
            self._cancel_pending()
        elif shown is None:
            self._cancel_pending()
            self._show_and_tell(on=reading)
        elif self._pending is None or reading != self._candidate:
            self._cancel_pending()
            self._candidate = reading
            wait = self._tolerance if reading is None else self._delay
            self._pending = async_call_later(self._hass, wait, self._async_apply)

    @callback
    def _async_apply(self, _now: datetime) -> None:
        """Show the reading that has lasted its wait."""
        self._pending = None
        self._show_and_tell(on=self._reading(self._hass.states.get(self.power_entity)))

    def _cancel_pending(self) -> None:
        if self._pending is not None:
            self._pending()
            self._pending = None
