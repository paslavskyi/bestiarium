"""
The stand's home power: the relays, the grid, the sun and the battery.

What the power flow card of Bestiarium Mode reads in the owner's home (DEV-014),
as knobs: the scenarios set them together, nothing here follows from the
relays. Only the time to full is computed, unknown unless the battery
charges, as the home's estimate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import (
    HOME_POWER_BATTERY_KWH,
    HOME_POWER_FLOW_THRESHOLD,
    HOME_POWER_MODES,
    HOME_POWER_NUMBERS,
    HOME_POWER_SWITCHES,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import CALLBACK_TYPE


class MockHomePower:
    """The knobs of the home power, by key, and who follows them."""

    def __init__(self) -> None:
        """Start as a calm home."""
        self.mode = HOME_POWER_MODES[0]
        self.switches = dict.fromkeys(HOME_POWER_SWITCHES, True)
        self.numbers = {number.key: number.default for number in HOME_POWER_NUMBERS}
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call the listener on every change; return its removal."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def set_mode(self, mode: str) -> None:
        """Set the name of the mode."""
        self.mode = mode
        self._changed()

    def set_switch(self, key: str, *, on: bool) -> None:
        """Set a relay or the grid."""
        self.switches[key] = on
        self._changed()

    def set_number(self, key: str, value: float) -> None:
        """Set a power, the charge or the hours left."""
        self.numbers[key] = value
        self._changed()

    @property
    def hours_to_full(self) -> float | None:
        """Return the hours to a full battery, by halves; None unless charging."""
        flow = self.numbers["battery_flow"]
        if flow >= -HOME_POWER_FLOW_THRESHOLD:
            return None
        missing_kwh = (
            (100 - self.numbers["battery_charge"]) / 100 * HOME_POWER_BATTERY_KWH
        )
        return round(missing_kwh / -flow * 2) / 2

    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener()
