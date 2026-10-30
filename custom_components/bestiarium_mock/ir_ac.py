"""
The stand's IR air conditioner: a Hisense that hears its emitter.

It recognises a frame with the Kelon168 module of Bestiarium IR (the one its
air conditioners send with) and behaves as the real unit: a frame sets the
whole state; while the display is off, a frame other than "off" only lights
it; the display button switches it. Its power is its mock socket's: it draws
by its state, and a socket cut turns it off and leaves it deaf. Knobs: "IR
lost" (it hears nothing), "Ignores off".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback

from ..bestiarium_ir.kelon168 import (  # noqa: TID252
    Kelon168Action,
    Kelon168Command,
    Kelon168Fan,
    Kelon168Mode,
)
from .const import IR_AC_POWER_W, LOGGER, IrFrameOutcome

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import CALLBACK_TYPE

    from .const import MockIrAcSpec
    from .faults import MockSocket

STATES: dict[Kelon168Mode, str] = {
    Kelon168Mode.HEAT: "heat",
    Kelon168Mode.COOL: "cool",
    Kelon168Mode.AUTO: "auto",
    Kelon168Mode.DRY: "dry",
    Kelon168Mode.FAN: "fan_only",
}


class MockIrAc:
    """The unit: its state, its display, its knobs and the last frame it heard."""

    def __init__(self, spec: MockIrAcSpec, socket: MockSocket) -> None:
        """Start off, the display on."""
        self.spec = spec
        self._socket = socket
        self.power = False
        self.mode = Kelon168Mode.HEAT
        self.temperature = 24
        self.fan = Kelon168Fan.AUTO
        self.display_on = True
        self.ir_lost = False
        self.ignores_off = False
        self.last_frame: Kelon168Command | None = None
        self.last_outcome: IrFrameOutcome | None = None
        self._listeners: list[Callable[[], None]] = []
        # The socket's own power (5 W) is the unit's off
        self._unsub_socket = socket.add_listener(self._socket_changed)

    @property
    def state(self) -> str:
        """Return the unit's state: off or its mode."""
        return STATES[self.mode] if self.power else "off"

    @callback
    def add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call the listener on every change; return its removal."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def stop(self) -> None:
        """Stop following the socket."""
        self._unsub_socket()

    @callback
    def restore(
        self,
        *,
        power: bool,
        mode: Kelon168Mode,
        temperature: int,
        fan: Kelon168Fan,
        display_on: bool,
    ) -> None:
        """Restore the state the unit had before a restart."""
        self.power, self.mode, self.temperature = power, mode, temperature
        self.fan, self.display_on = fan, display_on
        self._draw()

    @callback
    def set_knob(self, knob: str, *, on: bool) -> None:
        """Set the knob "ir_lost" or "ignores_off"."""
        setattr(self, knob, on)
        self._changed()

    @callback
    def hear(self, timings: list[int]) -> None:
        """Hear a signal of the emitter."""
        if self.ir_lost or not self._socket.physical_on:
            LOGGER.debug("%s hears nothing", self.spec.device_id)
            return
        frame = Kelon168Command.from_raw_timings(timings)
        self.last_frame = frame
        self.last_outcome = self._carry_out(frame)
        self._draw()

    def _carry_out(self, frame: Kelon168Command | None) -> IrFrameOutcome:
        if frame is None:
            return IrFrameOutcome.NOT_RECOGNISED
        if not frame.power:
            if self.ignores_off:
                return IrFrameOutcome.OFF_IGNORED
            self.power = False
            return IrFrameOutcome.APPLIED
        if not self.display_on:
            self.display_on = True
            return IrFrameOutcome.DISPLAY_LIT
        if frame.action is Kelon168Action.LIGHT:
            self.display_on = False
            return IrFrameOutcome.APPLIED
        self.power = True
        self.mode, self.temperature, self.fan = frame.mode, frame.temperature, frame.fan
        return IrFrameOutcome.APPLIED

    @callback
    def _socket_changed(self) -> None:
        """Turn the unit off when its socket is cut."""
        if not self._socket.physical_on and self.power:
            self.power = False
            self._draw()

    def _draw(self) -> None:
        """Draw the power of the state from the socket; tell the listeners."""
        self._socket.set_power(IR_AC_POWER_W[self.state])
        self._changed()

    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener()
