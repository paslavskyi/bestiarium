"""
The power load zone of the home: one zone at a time.

Up at once; down only below the threshold minus a margin; green only after a
pause; no data only after a tolerance (docs/concepts/stability.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    CONF_AC_LOAD_LIMIT_W,
    CONF_AC_LOAD_LIMITS,
    CONF_AC_OVERLOAD_RECOVERY_SECONDS,
    CONF_AC_POWER_SENSOR,
    CONF_AC_POWER_TOLERANCE_SECONDS,
    CONF_AC_RED_ZONE_PERCENT,
    CONF_AC_YELLOW_ZONE_PERCENT,
    CONF_AC_ZONE_HYSTERESIS_PERCENT,
    DEFAULT_OVERLOAD_RECOVERY_SECONDS,
    DEFAULT_POWER_TOLERANCE_SECONDS,
    DEFAULT_ZONE_HYSTERESIS_PERCENT,
    EVENT_LOAD_LIMIT_CHANGED,
    LOGGER,
    SIGNAL_ZONE_CHANGED,
)

if TYPE_CHECKING:
    from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State

    from .data import BestiariumModeConfigEntry


class Zone(StrEnum):
    """The power load zone; the zone sensors show the four with a threshold."""

    NO_DATA = "no_data"
    PAUSE = "pause"  # green on pause: below yellow, not green yet
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    OVERLOAD = "overload"


# How high a zone is: up is at once, down needs the margin
_LEVEL = {
    Zone.NO_DATA: -1,
    Zone.PAUSE: 0,
    Zone.GREEN: 0,
    Zone.YELLOW: 1,
    Zone.RED: 2,
    Zone.OVERLOAD: 3,
}


@dataclass(frozen=True)
class ZoneConfig:
    """The thresholds of the zones and the parameters of their changes."""

    power_sensor_id: str
    yellow_threshold: float
    red_threshold: float
    limit_w: float
    margin_w: float
    pause_seconds: float
    tolerance_seconds: float

    @classmethod
    def of(cls, entry: BestiariumModeConfigEntry) -> ZoneConfig:
        """Read the AC load limits of the entry, with the defaults."""
        limits: dict[str, Any] = entry.data[CONF_AC_LOAD_LIMITS]
        limit_w = limits[CONF_AC_LOAD_LIMIT_W]
        return cls(
            power_sensor_id=limits[CONF_AC_POWER_SENSOR],
            yellow_threshold=limit_w
            * limits.get(CONF_AC_YELLOW_ZONE_PERCENT, 75)
            / 100,
            red_threshold=limit_w * limits.get(CONF_AC_RED_ZONE_PERCENT, 90) / 100,
            limit_w=limit_w,
            margin_w=limit_w
            * limits.get(
                CONF_AC_ZONE_HYSTERESIS_PERCENT, DEFAULT_ZONE_HYSTERESIS_PERCENT
            )
            / 100,
            pause_seconds=limits.get(
                CONF_AC_OVERLOAD_RECOVERY_SECONDS, DEFAULT_OVERLOAD_RECOVERY_SECONDS
            ),
            tolerance_seconds=limits.get(
                CONF_AC_POWER_TOLERANCE_SECONDS, DEFAULT_POWER_TOLERANCE_SECONDS
            ),
        )

    def zone_of(self, power: float) -> Zone:
        """Return the zone of a load by the thresholds alone."""
        if power >= self.limit_w:
            return Zone.OVERLOAD
        if power >= self.red_threshold:
            return Zone.RED
        if power >= self.yellow_threshold:
            return Zone.YELLOW
        return Zone.GREEN


def _watts(state: State | None) -> float | None:
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


class ZoneTracker:
    """The current zone of a mode entry, told by a dispatcher signal."""

    def __init__(self, hass: HomeAssistant, entry: BestiariumModeConfigEntry) -> None:
        """Follow the power sensor of the entry; call async_start."""
        self._hass = hass
        self._entry = entry
        self.config = ZoneConfig.of(entry)
        self.zone = Zone.NO_DATA
        self._data_lost = False  # no data longer than the tolerance
        self._cancel_pause: CALLBACK_TYPE | None = None
        self._cancel_tolerance: CALLBACK_TYPE | None = None
        self._unsubscribe: list[CALLBACK_TYPE] = []

    @callback
    def async_start(self) -> None:
        """Read the load now and follow its changes and the load limit."""
        self._unsubscribe = [
            async_track_state_change_event(
                self._hass, [self.config.power_sensor_id], self._on_power
            ),
            self._hass.bus.async_listen(EVENT_LOAD_LIMIT_CHANGED, self._on_limit),
        ]
        self._update()

    @callback
    def async_stop(self) -> None:
        """Stop following the load."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe = []
        self._cancel_timers()

    @property
    def pause_pending(self) -> bool:
        """Return whether green waits for the end of its pause."""
        return self.zone is Zone.PAUSE

    @callback
    def _on_power(self, _event: Event) -> None:
        self._update()

    @callback
    def _on_limit(self, _event: Event) -> None:
        self.config = ZoneConfig.of(self._entry)
        self._update()
        self._changed()  # the thresholds the zone sensors show

    def _update(self) -> None:
        power = _watts(self._hass.states.get(self.config.power_sensor_id))
        if power is None:
            self._stop_pause()
            if self._cancel_tolerance is None and not self._data_lost:
                self._cancel_tolerance = async_call_later(
                    self._hass, self.config.tolerance_seconds, self._on_tolerance
                )
            return
        if self._cancel_tolerance:
            self._cancel_tolerance()
            self._cancel_tolerance = None
        if self._data_lost:
            self._data_lost = False
            LOGGER.info("Power load data is back")
        config = self.config
        up = config.zone_of(power)
        down = config.zone_of(power + config.margin_w)
        if _LEVEL[up] > _LEVEL[self.zone]:
            self._set(Zone.PAUSE if up is Zone.GREEN else up)
        elif _LEVEL[down] < _LEVEL[self.zone]:
            self._set(Zone.PAUSE if down is Zone.GREEN else down)
        if self.zone is Zone.PAUSE:
            # Green after a pause below the yellow threshold minus the margin
            if down is Zone.GREEN:
                self._start_pause()
            else:
                self._stop_pause()

    def _set(self, zone: Zone) -> None:
        if zone is not self.zone:
            if self.zone is Zone.PAUSE:
                self._stop_pause()
            self.zone = zone
            self._changed()

    def _changed(self) -> None:
        async_dispatcher_send(
            self._hass, SIGNAL_ZONE_CHANGED.format(self._entry.entry_id)
        )

    def _start_pause(self) -> None:
        if self._cancel_pause is None:
            self._cancel_pause = async_call_later(
                self._hass, self.config.pause_seconds, self._on_pause_over
            )

    def _stop_pause(self) -> None:
        if self._cancel_pause:
            self._cancel_pause()
            self._cancel_pause = None

    @callback
    def _on_pause_over(self, _now: object) -> None:
        self._cancel_pause = None
        self._set(Zone.GREEN)

    @callback
    def _on_tolerance(self, _now: object) -> None:
        self._cancel_tolerance = None
        self._data_lost = True
        LOGGER.warning(
            "No power load data from %s for %s s",
            self.config.power_sensor_id,
            self.config.tolerance_seconds,
        )
        self._set(Zone.NO_DATA)

    def _cancel_timers(self) -> None:
        self._stop_pause()
        if self._cancel_tolerance:
            self._cancel_tolerance()
            self._cancel_tolerance = None
