"""
Expected power of a device, learned from its runs.

A run is a stretch where the relay is on and the device draws at least
MIN_CONSUMING_POWER_W. Its power is the highest level held for at least
STABLE_POWER_SECONDS, so short spikes (a compressor start) do not count.
Power sensors report on change: between two reports the power is constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .const import (
    LARGE_DEVICE_POWER_W,
    MIN_CONSUMING_POWER_W,
    POWER_MEMORY_SIZE,
    REAL_RUNS_FOR_ESTIMATE,
    STABLE_POWER_SECONDS,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

SOURCE_RUN = "run"  # seen live
SOURCE_HISTORY = "history"  # imported from the recorder's state history
SOURCE_STATISTICS = "statistics"  # imported from hourly long-term statistics

ESTIMATE_MEMORY = "memory"
ESTIMATE_HISTORY = "history"
ESTIMATE_MANUAL = "manual"
ESTIMATE_LARGE_DEVICE = "large_device_default"


@dataclass(frozen=True)
class PowerRecord:
    """One remembered run."""

    start: datetime
    duration_s: float
    power_w: float
    source: str

    @property
    def is_real(self) -> bool:
        """Return True for a run seen live, as opposed to an imported one."""
        return self.source == SOURCE_RUN

    def as_dict(self) -> dict[str, Any]:
        """Serialize for storage and state attributes."""
        return {
            "start": self.start.isoformat(),
            "duration_s": self.duration_s,
            "power_w": self.power_w,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PowerRecord:
        """Deserialize from storage."""
        return cls(
            start=datetime.fromisoformat(data["start"]),
            duration_s=data["duration_s"],
            power_w=data["power_w"],
            source=data["source"],
        )


def stable_power(
    samples: Sequence[tuple[datetime, float]], end: datetime
) -> float | None:
    """
    Return the highest power held for STABLE_POWER_SECONDS, or None.

    samples are the power reports of one run, the first at its start; the
    last one holds until end.
    """
    durations = [
        ((samples[i + 1][0] if i + 1 < len(samples) else end) - time).total_seconds()
        for i, (time, _) in enumerate(samples)
    ]
    best: float | None = None
    for i in range(len(samples)):
        held, level = 0.0, float("inf")
        for j in range(i, len(samples)):
            level = min(level, samples[j][1])
            held += durations[j]
            if held >= STABLE_POWER_SECONDS:
                best = level if best is None else max(best, level)
                break
    return best


class RunBuilder:
    """Follow a device's relay and power and return each run when it ends."""

    def __init__(self, source: str) -> None:
        """Start with the relay off and no power known."""
        self._source = source
        self._relay_on = False
        self._watts: float | None = None
        self._samples: list[tuple[datetime, float]] = []

    def relay(self, time: datetime, *, on: bool) -> PowerRecord | None:
        """Record a relay change; return the run it ended, if any."""
        self._relay_on = on
        return self._update(time)

    def power(self, time: datetime, watts: float | None) -> PowerRecord | None:
        """Record a power report (None if unknown); return the run it ended."""
        self._watts = watts
        return self._update(time)

    def _update(self, time: datetime) -> PowerRecord | None:
        watts = self._watts
        if self._relay_on and watts is not None and watts >= MIN_CONSUMING_POWER_W:
            if not self._samples or self._samples[-1][1] != watts:
                self._samples.append((time, watts))
            return None
        if not self._samples:
            return None
        samples, self._samples = self._samples, []
        return _finish_run(samples, time, self._source)


def runs_from_states(
    relay: Sequence[tuple[datetime, bool]],
    power: Sequence[tuple[datetime, float | None]],
    source: str,
) -> list[PowerRecord]:
    """Reconstruct the finished runs from relay and power state histories."""
    builder = RunBuilder(source)
    events = sorted(
        [(time, builder.relay, {"on": on}) for time, on in relay]
        + [(time, builder.power, {"watts": watts}) for time, watts in power],
        key=lambda event: event[0],
    )
    finished = (update(time, **value) for time, update, value in events)
    return [run for run in finished if run is not None]


def _finish_run(
    samples: Sequence[tuple[datetime, float]], end: datetime, source: str
) -> PowerRecord | None:
    power_w = stable_power(samples, end)
    if power_w is None:
        return None
    start = samples[0][0]
    return PowerRecord(
        start=start,
        duration_s=(end - start).total_seconds(),
        power_w=power_w,
        source=source,
    )


def records_from_hourly_max(
    rows: Sequence[tuple[datetime, float]],
) -> list[PowerRecord]:
    """Turn hourly maxima into imported records, one per stretch of working hours."""
    records: list[PowerRecord] = []
    stretch: list[tuple[datetime, float]] = []
    for hour, max_w in [*rows, (None, None)]:
        working = max_w is not None and max_w >= MIN_CONSUMING_POWER_W
        if working and stretch and hour - stretch[-1][0] == timedelta(hours=1):
            stretch.append((hour, max_w))
            continue
        if stretch:
            records.append(
                PowerRecord(
                    start=stretch[0][0],
                    duration_s=len(stretch) * 3600,
                    power_w=max(w for _, w in stretch),
                    source=SOURCE_STATISTICS,
                )
            )
        stretch = [(hour, max_w)] if working else []
    return records


def add_run(records: Sequence[PowerRecord], run: PowerRecord) -> list[PowerRecord]:
    """
    Remember a new run.

    Once there are REAL_RUNS_FOR_ESTIMATE real runs, imported records are
    dropped. A full memory evicts the oldest imported record first, then the
    oldest real run.
    """
    result = [*records, run]
    if sum(record.is_real for record in result) >= REAL_RUNS_FOR_ESTIMATE:
        result = [record for record in result if record.is_real]
    while len(result) > POWER_MEMORY_SIZE:
        imported = [record for record in result if not record.is_real]
        result.remove(min(imported or result, key=lambda record: record.start))
    return result


def estimate(
    records: Sequence[PowerRecord], manual_w: float | None
) -> tuple[float, str]:
    """
    Return the expected power and what it is based on.

    From REAL_RUNS_FOR_ESTIMATE real runs only they count. Before that the
    largest of the manual value, the imported records and the real runs wins;
    with nothing known the device counts as large.
    """
    real = [record.power_w for record in records if record.is_real]
    if len(real) >= REAL_RUNS_FOR_ESTIMATE:
        return max(real), ESTIMATE_MEMORY
    imported = [record.power_w for record in records if not record.is_real]
    candidates = [
        (value, source)
        for value, source in (
            (manual_w, ESTIMATE_MANUAL),
            (max(imported, default=None), ESTIMATE_HISTORY),
            (max(real, default=None), ESTIMATE_MEMORY),
        )
        if value
    ]
    if not candidates:
        return LARGE_DEVICE_POWER_W, ESTIMATE_LARGE_DEVICE
    return max(candidates, key=lambda candidate: candidate[0])
