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
    from homeassistant.core import Context, HomeAssistant

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
    """

    def __init__(self, hass: HomeAssistant, emitter: str) -> None:
        """Link to the emitter's entity."""
        self.hass = hass
        self.emitter = emitter
        self._turn = Lock()
        self._last_sent: float | None = None

    async def send(self, command: Kelon168Command, context: Context | None) -> None:
        """Send a frame in its turn."""
        async with self._turn:
            await self._send(command, context)

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
