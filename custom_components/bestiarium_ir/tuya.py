"""
The IR code format of Tuya/Zosung blasters (TS1201): durations, compressed.

A code is the durations in microseconds as 16-bit little-endian words,
compressed with an LZ77 variant and in base64. A header byte below 32 starts
that many plus one literal bytes; above, its top 3 bits are a match length
less 2 (7: plus the next byte) and its low 5 bits with the next byte a
distance less 1 (up to 8192).

The match search is that of the codes working in the home (kelon_gen.py,
FEAT-025): the first longest match from the nearest distance, so the same
timings give the same code bit for bit.
"""

from __future__ import annotations

import base64
import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

MAX_DURATION = 0xFFFF
MAX_DISTANCE = 8192
MAX_MATCH = 264
MIN_MATCH = 3
MAX_LITERALS = 32
SHORT_MATCH_LIMIT = 7


def encode_code(timings: Sequence[int]) -> str:
    """Return the code of the timings; their signs (mark or space) are dropped."""
    durations = [abs(timing) for timing in timings]
    if too_long := [d for d in durations if d > MAX_DURATION]:
        msg = f"durations over {MAX_DURATION} us: {too_long}"
        raise ValueError(msg)
    raw = struct.pack(f"<{len(durations)}H", *durations)
    return base64.b64encode(_compress(raw)).decode()


def _compress(raw: bytes) -> bytes:
    out = bytearray()
    literals = bytearray()

    def flush() -> None:
        nonlocal literals
        while literals:
            chunk, literals = literals[:MAX_LITERALS], literals[MAX_LITERALS:]
            out.append(len(chunk) - 1)
            out.extend(chunk)

    i = 0
    while i < len(raw):
        best_length, best_distance = 0, 0
        for distance in range(1, min(i, MAX_DISTANCE) + 1):
            length = 0
            while (
                i + length < len(raw)
                and length < MAX_MATCH
                and raw[i + length - distance] == raw[i + length]
            ):
                length += 1
            if length > best_length:
                best_length, best_distance = length, distance
        if best_length < MIN_MATCH:
            literals.append(raw[i])
            i += 1
            continue
        flush()
        length, distance = best_length - 2, best_distance - 1
        if length < SHORT_MATCH_LIMIT:
            out += bytes([(length << 5) | (distance >> 8), distance & 0xFF])
        else:
            out += bytes(
                [
                    (SHORT_MATCH_LIMIT << 5) | (distance >> 8),
                    length - SHORT_MATCH_LIMIT,
                    distance & 0xFF,
                ]
            )
        i += best_length
    flush()
    return bytes(out)
