"""The Tuya/Zosung code format of the TS1201."""

import pytest

from custom_components.bestiarium_ir.tuya import encode_code

from .tuya_codes import all_home_codes, decode_code, load_vectors


def test_decode_then_encode_gives_the_home_code_back() -> None:
    """Every code of the home's packages is compressed bit for bit."""
    codes = all_home_codes(load_vectors())
    assert len(codes) == 61
    for code in codes:
        assert encode_code(decode_code(code)) == code


def test_signs_are_dropped() -> None:
    """Marks and spaces are both durations in the code."""
    assert encode_code([8354, -4516, 540]) == encode_code([8354, 4516, 540])


def test_a_timing_over_16_bits_is_refused() -> None:
    """The format holds 16-bit durations."""
    with pytest.raises(ValueError, match="70000"):
        encode_code([540, -70000, 540])
