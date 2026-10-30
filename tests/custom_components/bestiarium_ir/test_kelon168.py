"""The Kelon168 protocol against the codes of the real remote and of the home."""

import pytest
from infrared_protocols.commands.gree_ac import GreeAcCommand, GreeAcMode

from custom_components.bestiarium_ir.kelon168 import (
    Kelon168Action,
    Kelon168Command,
    Kelon168Fan,
    Kelon168Mode,
)
from custom_components.bestiarium_ir.tuya import encode_code

from .tuya_codes import decode_code, load_vectors

VECTORS = load_vectors()
LEARNED = VECTORS["learned"]
HOME = VECTORS["home"]


def _learned(key: str) -> Kelon168Command | None:
    return Kelon168Command.from_raw_timings(decode_code(LEARNED[key]))


@pytest.mark.parametrize("t", [17, 18, 19, 20, 22, 23, 24, 25, 26, 27, 28, 29, 30])
def test_learned_heat_codes(t: int) -> None:
    """A code of the remote is "heat t", fan auto; the generator's frame bit for bit."""
    cmd = _learned(f"heat_{t}_auto")
    assert cmd is not None
    assert (cmd.mode, cmd.temperature, cmd.fan, cmd.power, cmd.action) == (
        Kelon168Mode.HEAT,
        t,
        Kelon168Fan.AUTO,
        True,
        Kelon168Action.TEMP,
    )
    # The remote's clock was at about 21:35
    assert cmd.clock[0] == 21
    assert (
        Kelon168Command(mode=Kelon168Mode.HEAT, temperature=t, clock=cmd.clock).frame()
        == cmd.frame()
    )


def test_learned_21_is_the_power_button() -> None:
    """The code learned as "21 °C" is the power button pressed."""
    cmd = _learned("heat_21_auto")
    assert cmd is not None
    assert (cmd.action, cmd.power, cmd.temperature) == (
        Kelon168Action.POWER,
        True,
        21,
    )


def test_learned_16_is_incomplete() -> None:
    """The code learned as "16 °C" holds a third of the signal."""
    assert _learned("heat_16_auto") is None


def test_learned_display_toggle() -> None:
    """The display button: the light command with the display bit."""
    cmd = _learned("display_toggle")
    assert cmd is not None
    assert (cmd.action, cmd.power, cmd.mode, cmd.temperature, cmd.clock) == (
        Kelon168Action.LIGHT,
        True,
        Kelon168Mode.HEAT,
        23,
        (22, 56),
    )


@pytest.mark.parametrize("t", range(16, 31))
def test_home_codes_bit_for_bit(t: int) -> None:
    """The codes of the home's packages, from the command's timings."""

    def code(**kwargs: object) -> str:
        return encode_code(Kelon168Command(temperature=t, **kwargs).get_raw_timings())

    assert code(mode=Kelon168Mode.HEAT) == HOME["heat"][str(t)]
    assert code(mode=Kelon168Mode.COOL) == HOME["cool"][str(t)]
    assert (
        code(mode=Kelon168Mode.HEAT, action=Kelon168Action.LIGHT)
        == HOME["light_heat"][str(t)]
    )
    assert (
        code(mode=Kelon168Mode.COOL, action=Kelon168Action.LIGHT)
        == HOME["light_cool"][str(t)]
    )


def test_home_off_code() -> None:
    """The home's "off": the power button, off, heat 22 °C."""
    off = Kelon168Command(
        mode=Kelon168Mode.HEAT,
        temperature=22,
        power=False,
        action=Kelon168Action.POWER,
    )
    assert encode_code(off.get_raw_timings()) == HOME["off"]


@pytest.mark.parametrize(
    ("fan", "low_bits", "high_bit"),
    [
        (Kelon168Fan.AUTO, 0, 0),
        (Kelon168Fan.MIN, 3, 0),
        (Kelon168Fan.LOW, 3, 1),
        (Kelon168Fan.MEDIUM, 2, 0),
        (Kelon168Fan.HIGH, 1, 1),
        (Kelon168Fan.MAX, 1, 0),
    ],
)
def test_fan_speeds_in_the_frame(
    fan: Kelon168Fan, low_bits: int, high_bit: int
) -> None:
    """The fan speed: bits 0-1 of byte 2, bit 1 of byte 16 (IRremoteESP8266)."""
    frame = Kelon168Command(
        mode=Kelon168Mode.COOL, temperature=24, fan=fan, action=Kelon168Action.FAN
    ).frame()
    assert frame[2] & 0b11 == low_bits
    assert frame[16] >> 1 & 1 == high_bit
    assert frame[15] == Kelon168Action.FAN


@pytest.mark.parametrize("fan", list(Kelon168Fan))
@pytest.mark.parametrize("mode", list(Kelon168Mode))
def test_round_trip(mode: Kelon168Mode, fan: Kelon168Fan) -> None:
    """Every mode and fan speed is recognised from its own timings."""
    cmd = Kelon168Command(mode=mode, temperature=24, fan=fan, action=Kelon168Action.FAN)
    back = Kelon168Command.from_raw_timings(cmd.get_raw_timings())
    assert back is not None
    assert back.frame() == cmd.frame()
    assert (back.mode, back.fan) == (mode, fan)


def test_timings_are_signed_and_end_with_a_mark() -> None:
    """Marks positive, spaces negative; the frame ends with a mark (343 values)."""
    timings = Kelon168Command(mode=Kelon168Mode.HEAT, temperature=24).get_raw_timings()
    assert len(timings) == 343
    assert timings[:3] == [8354, -4516, 540]
    assert timings[-1] == 540


def test_temperature_out_of_range_is_refused() -> None:
    """16-30 °C."""
    with pytest.raises(ValueError, match="31"):
        Kelon168Command(mode=Kelon168Mode.HEAT, temperature=31)


def test_corrupt_frames_are_rejected() -> None:
    """A cut frame, a wrong bit, another protocol, no preamble: not a command."""
    good = Kelon168Command(mode=Kelon168Mode.COOL, temperature=24).get_raw_timings()
    assert Kelon168Command.from_raw_timings(good[:200]) is None
    flipped = list(good)
    # The space of bit 5 of byte 3 (a temperature bit): the checksum fails
    flipped[61] = -1729 if flipped[61] == -561 else -561
    assert Kelon168Command.from_raw_timings(flipped) is None
    gree = GreeAcCommand(mode=GreeAcMode.COOL, temperature=24).get_raw_timings()
    assert Kelon168Command.from_raw_timings(gree) is None
    assert Kelon168Command.from_frame(bytes(21)) is None
