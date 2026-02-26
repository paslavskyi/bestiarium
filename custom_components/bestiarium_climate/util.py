"""Shared utilities for bestiarium_climate."""

from __future__ import annotations

from datetime import timedelta


def duration_dict_to_timedelta(data: dict[str, int] | None) -> timedelta:
    """Convert config duration dict (hours, minutes, seconds) to timedelta."""
    if not data:
        return timedelta(0)
    return timedelta(
        hours=data.get("hours", 0),
        minutes=data.get("minutes", 0),
        seconds=data.get("seconds", 0),
    )
