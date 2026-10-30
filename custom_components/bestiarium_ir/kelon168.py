"""
The Kelon168 IR protocol of Hisense air conditioners (remote DG11R2-01).

A frame is 21 bytes, sent least significant bit first in three sections of
6, 8 and 7 bytes; the first has a header, each but the last ends with a mark
and a long space, the last with a mark.

  0-1   83 06
  2     fan speed (bits 0-1) | power command (bit 2)
  3     mode (bits 0-2) | (temperature - 16) << 4
  6     0x80 | clock hours (bits 0-4) | display (bit 5, the light command)
  7     clock minutes
  13    xor of bytes 2..12
  15    the remote's button (Kelon168Action)
  16    fan speed (bit 1)
  18    0x28 | on (bit 4)
  20    xor of bytes 14..19

The layout and the fan speeds follow IRremoteESP8266 (ir_Kelon.h); the
timings are those of the codes working in the home (kelon_gen.py, FEAT-025),
which reproduce the codes learned from the real remote bit for bit. The unit
ignores the clock, but drops a frame with the hour 00: commands carry 23:40.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Self, override

from infrared_protocols.commands import Command

if TYPE_CHECKING:
    from collections.abc import Sequence

MIN_TEMP = 16
MAX_TEMP = 30
# The temperature of the unit's own auto mode
AUTO_TEMP = 23

FRAME_LENGTH = 21
_SECTIONS = ((0, 6), (6, 14), (14, 21))
_PREAMBLE = b"\x83\x06"

_HEADER_MARK = 8354
_HEADER_SPACE = 4516
_BIT_MARK = 540
_ZERO_SPACE = 561
_ONE_SPACE = 1729
_SECTION_GAP = 8354

_TOLERANCE = 0.35
_BIT_TOLERANCE = 350
# Wider than any bit's space, shorter than the gap between sections
_MIN_GAP = 4000

_POWER_COMMAND = 0x04
_DISPLAY = 0x20
_CLOCK_BASE = 0x80
_ON = 0x10
_MODEL = 0x28


class Kelon168Mode(IntEnum):
    """The mode field, bits 0-2 of byte 3."""

    HEAT = 0
    AUTO = 1
    COOL = 2
    DRY = 3
    FAN = 4


class Kelon168Fan(StrEnum):
    """The fan speed; the values are the fan modes of HA."""

    AUTO = "auto"
    MIN = "min"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


# The fan speed in the frame: bits 0-1 of byte 2, bit 1 of byte 16
_FAN_BITS: dict[Kelon168Fan, tuple[int, int]] = {
    Kelon168Fan.AUTO: (0, 0),
    Kelon168Fan.MIN: (3, 0),
    Kelon168Fan.LOW: (3, 1),
    Kelon168Fan.MEDIUM: (2, 0),
    Kelon168Fan.HIGH: (1, 1),
    Kelon168Fan.MAX: (1, 0),
}
_BITS_FAN = {bits: fan for fan, bits in _FAN_BITS.items()}


class Kelon168Action(IntEnum):
    """The button of the remote that sent the frame, byte 15."""

    LIGHT = 0x00
    POWER = 0x01
    TEMP = 0x02
    MODE = 0x06
    IFEEL = 0x0D
    FAN = 0x11


def _xor(data: bytes | bytearray) -> int:
    result = 0
    for byte in data:
        result ^= byte
    return result


def _is_close(actual: int, expected: int) -> bool:
    return abs(actual - expected) <= expected * _TOLERANCE


class Kelon168Command(Command):
    """
    A frame of the remote: the whole state of the unit and the button pressed.

    The state is absolute: a frame sets it whatever the unit did before.
    "power" is the state after the frame; the power command flag and the
    display bit go with the buttons POWER and LIGHT.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        mode: Kelon168Mode,
        temperature: int,
        fan: Kelon168Fan = Kelon168Fan.AUTO,
        power: bool = True,
        action: Kelon168Action = Kelon168Action.TEMP,
        clock: tuple[int, int] = (23, 40),
        modulation: int = 38000,
    ) -> None:
        """Initialize the frame; the temperature is 16-30 °C."""
        super().__init__(modulation=modulation)
        if not MIN_TEMP <= temperature <= MAX_TEMP:
            msg = f"temperature {temperature} out of range {MIN_TEMP}..{MAX_TEMP}"
            raise ValueError(msg)
        self.mode = mode
        self.temperature = temperature
        self.fan = fan
        self.power = power
        self.action = action
        self.clock = clock

    def frame(self) -> bytes:
        """Return the 21 bytes of the frame."""
        frame = bytearray(FRAME_LENGTH)
        frame[0:2] = _PREAMBLE
        fan_low, fan_high = _FAN_BITS[self.fan]
        frame[2] = fan_low | (
            _POWER_COMMAND if self.action is Kelon168Action.POWER else 0
        )
        frame[3] = self.mode | (self.temperature - MIN_TEMP) << 4
        hours, minutes = self.clock
        frame[6] = (
            _CLOCK_BASE
            | hours
            | (_DISPLAY if self.action is Kelon168Action.LIGHT else 0)
        )
        frame[7] = minutes
        frame[13] = _xor(frame[2:13])
        frame[15] = self.action
        frame[16] = fan_high << 1
        frame[18] = _MODEL | (_ON if self.power else 0)
        frame[20] = _xor(frame[14:20])
        return bytes(frame)

    @override
    def get_raw_timings(self) -> list[int]:
        """Return the timings: marks positive, spaces negative."""
        frame = self.frame()
        timings = [_HEADER_MARK, -_HEADER_SPACE]
        for number, (start, end) in enumerate(_SECTIONS):
            for byte in frame[start:end]:
                for bit in range(8):
                    timings += [
                        _BIT_MARK,
                        -(_ONE_SPACE if byte >> bit & 1 else _ZERO_SPACE),
                    ]
            timings.append(_BIT_MARK)
            if number < len(_SECTIONS) - 1:
                timings.append(-_SECTION_GAP)
        return timings

    @classmethod
    def from_frame(cls, frame: bytes) -> Self | None:
        """Return the command of the frame, or None if this module would not send it."""
        if len(frame) != FRAME_LENGTH or frame[0:2] != _PREAMBLE:
            return None
        try:
            command = cls(
                mode=Kelon168Mode(frame[3] & 0x07),
                temperature=(frame[3] >> 4) + MIN_TEMP,
                fan=_BITS_FAN[(frame[2] & 0x03, frame[16] >> 1 & 1)],
                power=bool(frame[18] & _ON),
                action=Kelon168Action(frame[15]),
                clock=(frame[6] & 0x1F, frame[7] & 0x3F),
            )
        except (ValueError, KeyError):
            return None
        # Checksums, and no bit this module does not know (sleep, swing, timers)
        return command if command.frame() == frame else None

    @classmethod
    def from_raw_timings(cls, timings: Sequence[int]) -> Self | None:
        """Return the command of the timings (signed or not), or None."""
        durations = [abs(timing) for timing in timings]
        if (
            len(durations) < 2  # noqa: PLR2004
            or not _is_close(durations[0], _HEADER_MARK)
            or not _is_close(durations[1], _HEADER_SPACE)
        ):
            return None
        frame = bytearray(FRAME_LENGTH)
        position = 2
        for number, (start, end) in enumerate(_SECTIONS):
            for index in range(start * 8, end * 8):
                if position + 1 >= len(durations):
                    return None
                bit = _decode_bit(durations[position], durations[position + 1])
                if bit is None:
                    return None
                frame[index // 8] |= bit << index % 8
                position += 2
            if position >= len(durations) or not _is_bit_mark(durations[position]):
                return None
            position += 1
            if number < len(_SECTIONS) - 1:
                if position >= len(durations) or durations[position] < _MIN_GAP:
                    return None
                position += 1
        return cls.from_frame(bytes(frame))


def _is_bit_mark(mark: int) -> bool:
    return abs(mark - _BIT_MARK) <= _BIT_TOLERANCE


def _decode_bit(mark: int, space: int) -> int | None:
    if not _is_bit_mark(mark):
        return None
    if abs(space - _ZERO_SPACE) <= _BIT_TOLERANCE:
        return 0
    if abs(space - _ONE_SPACE) <= _BIT_TOLERANCE:
        return 1
    return None
