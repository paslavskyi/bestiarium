"""
The link of a Hisense air conditioner to its emitter.

Its climate entity and its display switch send frames through it. Which
button of the remote a frame carries is decided here, in one table: only the
"temperature" button was checked in the home (the home's scripts send every
state with it), the others follow IRremoteESP8266 until checked on Stasi's
air conditioner.
"""

from __future__ import annotations

from asyncio import Lock, sleep
from enum import StrEnum
from time import monotonic
from typing import TYPE_CHECKING

from homeassistant.components.infrared import async_send_command

from .const import FRAME_PAUSE_SECONDS
from .kelon168 import Kelon168Action

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant

    from .kelon168 import Kelon168Command


class Change(StrEnum):
    """What a frame changes on the unit."""

    TURN_ON = "turn_on"
    MODE = "mode"
    TEMPERATURE = "temperature"
    FAN = "fan"
    OWN_AUTO = "own_auto"
    OFF = "off"
    DISPLAY = "display"


# The button of each change (DEV-012 review, U1)
ACTIONS: dict[Change, Kelon168Action] = {
    Change.TURN_ON: Kelon168Action.TEMP,  # checked at home
    Change.MODE: Kelon168Action.MODE,
    Change.TEMPERATURE: Kelon168Action.TEMP,  # checked at home
    Change.FAN: Kelon168Action.FAN,
    Change.OWN_AUTO: Kelon168Action.IFEEL,
    Change.OFF: Kelon168Action.POWER,  # checked at home
    Change.DISPLAY: Kelon168Action.LIGHT,  # checked at home
}


class HisenseLink:
    """
    Sends the frames of one air conditioner through its emitter.

    The unit does not carry out a frame sooner than 3 s after the previous
    one: frames wait their turn, in order.

    The display is assumed (design 10): while it is off, a frame only lights
    it, so a frame of a running state goes twice; "off" works with the
    display off (the home, 2026-10-28) and goes once. The display of a unit
    that is off goes out by itself: switching it sends nothing (DEV-012
    review, U3).
    """

    def __init__(self, hass: HomeAssistant, emitter: str) -> None:
        """Link to the emitter's entity."""
        self.hass = hass
        self.emitter = emitter
        self.display_on = True
        # The display frame of the state, None while the unit is off; set by
        # the climate entity
        self.display_frame: Callable[[], Kelon168Command | None] = lambda: None
        self._turn = Lock()
        self._last_sent: float | None = None
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call the listener when the display changes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _set_display(self, *, on: bool) -> None:
        self.display_on = on
        for listener in self._listeners:
            listener()

    async def send(self, command: Kelon168Command, context: Context | None) -> None:
        """Send a frame in its turn; twice while the display is off."""
        async with self._turn:
            await self._send(command, context)
            if command.power and not self.display_on:
                self._set_display(on=True)
                await self._send(command, context)

    async def set_display(self, *, on: bool, context: Context | None) -> None:
        """Switch the display; a display frame only while the unit runs."""
        async with self._turn:
            if on is self.display_on:
                return
            if (frame := self.display_frame()) is not None:
                await self._send(frame, context)
            self._set_display(on=on)

    async def _send(self, command: Kelon168Command, context: Context | None) -> None:
        if (
            self._last_sent is not None
            and (wait := self._last_sent + FRAME_PAUSE_SECONDS - monotonic()) > 0
        ):
            await sleep(wait)
        try:
            await async_send_command(self.hass, self.emitter, command, context=context)
        finally:
            self._last_sent = monotonic()
