"""Tests for the pure power-consumption logic (runs, memory, estimate)."""

# ruff: noqa: S101, PLR2004

from datetime import UTC, datetime, timedelta

from custom_components.bestiarium_devices.consumption import (
    ESTIMATE_HISTORY,
    ESTIMATE_LARGE_DEVICE,
    ESTIMATE_MANUAL,
    ESTIMATE_MEMORY,
    SOURCE_HISTORY,
    SOURCE_RUN,
    SOURCE_STATISTICS,
    PowerRecord,
    add_run,
    estimate,
    records_from_hourly_max,
    runs_from_states,
    stable_power,
)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    """Return T0 plus seconds."""
    return T0 + timedelta(seconds=seconds)


def record(power: float, source: str = SOURCE_RUN, minute: int = 0) -> PowerRecord:
    """Build a one-minute record."""
    return PowerRecord(
        start=T0 + timedelta(minutes=minute),
        duration_s=60,
        power_w=power,
        source=source,
    )


class TestStablePower:
    """The highest level held for at least 30 seconds."""

    def test_short_spike_does_not_count(self) -> None:
        """Criterion 5: a 10 s spike above a steady level gives the steady level."""
        samples = [(at(0), 1800), (at(20), 2600), (at(30), 1800)]
        assert stable_power(samples, end=at(90)) == 1800

    def test_steps_take_the_highest_held_level(self) -> None:
        """1000 W then 2000 W for 40 s each -> 2000 W."""
        samples = [(at(0), 1000), (at(40), 2000)]
        assert stable_power(samples, end=at(80)) == 2000

    def test_level_held_across_small_changes(self) -> None:
        """2050 then 2000 for 20 s each: 2000 is held for 40 s."""
        samples = [(at(0), 2050), (at(20), 2000), (at(40), 500)]
        assert stable_power(samples, end=at(45)) == 2000

    def test_run_shorter_than_30_seconds_has_no_value(self) -> None:
        """A 29 s run has no stable level."""
        assert stable_power([(at(0), 2000)], end=at(29)) is None


class TestRunsFromStates:
    """Reconstructing runs from relay and power state histories."""

    def test_relay_on_without_consumption_is_not_a_run(self) -> None:
        """A heated boiler: relay on, 0 W."""
        relay = [(at(0), True)]
        power = [(at(0), 0.0)]
        assert runs_from_states(relay, power, source=SOURCE_RUN) == []

    def test_run_while_relay_on_and_consuming(self) -> None:
        """The run spans the time the relay is on and power is drawn."""
        relay = [(at(0), True), (at(100), False)]
        power = [(at(0), 0.0), (at(10), 2400.0), (at(100), 0.0)]
        runs = runs_from_states(relay, power, source=SOURCE_HISTORY)
        assert runs == [
            PowerRecord(
                start=at(10), duration_s=90, power_w=2400, source=SOURCE_HISTORY
            )
        ]

    def test_power_dropping_to_zero_ends_the_run(self) -> None:
        """A boiler cycling on its own thermostat makes separate runs."""
        relay = [(at(0), True)]
        power = [
            (at(0), 1500.0),
            (at(60), 0.0),
            (at(120), 1600.0),
            (at(200), 0.0),
        ]
        runs = runs_from_states(relay, power, source=SOURCE_RUN)
        assert [(r.start, r.power_w) for r in runs] == [(at(0), 1500), (at(120), 1600)]

    def test_unavailable_power_ends_the_run_and_unfinished_run_is_skipped(
        self,
    ) -> None:
        """Unavailable power ends a run; a run still going at the end is skipped."""
        relay = [(at(0), True)]
        power = [(at(0), 900.0), (at(50), None), (at(60), 950.0)]
        runs = runs_from_states(relay, power, source=SOURCE_RUN)
        assert [r.power_w for r in runs] == [900]

    def test_standby_below_10_w_is_not_consumption(self) -> None:
        """Standby power of a smart socket is not a run."""
        relay = [(at(0), True)]
        power = [(at(0), 5.0), (at(300), 0.0)]
        assert runs_from_states(relay, power, source=SOURCE_RUN) == []


class TestHourlyStatistics:
    """Imported records from hourly maxima."""

    def test_consecutive_working_hours_make_one_record(self) -> None:
        """Adjacent working hours merge; idle hours split."""
        hour = timedelta(hours=1)
        rows = [
            (T0, 2100.0),
            (T0 + hour, 2500.0),
            (T0 + 2 * hour, 3.0),
            (T0 + 5 * hour, 1800.0),
        ]
        assert records_from_hourly_max(rows) == [
            PowerRecord(
                start=T0, duration_s=7200, power_w=2500, source=SOURCE_STATISTICS
            ),
            PowerRecord(
                start=T0 + 5 * hour,
                duration_s=3600,
                power_w=1800,
                source=SOURCE_STATISTICS,
            ),
        ]


class TestMemory:
    """Adding runs to the 10-record memory."""

    def test_eleventh_run_evicts_the_oldest(self) -> None:
        """Criterion 5."""
        records = [record(100 + i, minute=i) for i in range(10)]
        new = record(999, minute=20)
        result = add_run(records, new)
        assert len(result) == 10
        assert result[0].power_w == 101
        assert result[-1] == new

    def test_imported_records_are_evicted_first(self) -> None:
        """A full memory of imported records and one real run keeps the real run."""
        records = [
            record(100, minute=0),
            *(record(500, SOURCE_HISTORY, minute=i) for i in range(1, 10)),
        ]
        result = add_run(records, record(200, minute=20))
        assert len(result) == 10
        assert [r.source for r in result].count(SOURCE_HISTORY) == 8
        assert result[0] == records[0]
        assert records[1] not in result

    def test_imported_records_dropped_after_three_real_runs(self) -> None:
        """The third real run drops the imported hints."""
        records = [
            record(2600, SOURCE_HISTORY),
            record(1800, minute=1),
            record(1800, minute=2),
        ]
        result = add_run(records, record(1800, minute=3))
        assert {r.source for r in result} == {SOURCE_RUN}


class TestEstimate:
    """The expected power of a device."""

    def test_three_real_runs_use_statistics_only(self) -> None:
        """Criterion 6: after the 3rd run (300 W) the manual 1000 W no longer counts."""
        records = [record(300, minute=i) for i in range(3)]
        assert estimate(records, manual_w=1000) == (300, ESTIMATE_MEMORY)

    def test_fewer_than_three_runs_take_the_larger_manual(self) -> None:
        """Criterion 6: 2 runs of 300 W and manual 1000 W -> 1000 W."""
        records = [record(300, minute=i) for i in range(2)]
        assert estimate(records, manual_w=1000) == (1000, ESTIMATE_MANUAL)

    def test_fewer_than_three_runs_take_the_larger_learned(self) -> None:
        """Criterion 6: 1 run of 2400 W and manual 1000 W -> 2400 W."""
        assert estimate([record(2400)], manual_w=1000) == (2400, ESTIMATE_MEMORY)

    def test_nothing_known_is_a_large_device(self) -> None:
        """Criterion 7."""
        assert estimate([], manual_w=None) == (2000, ESTIMATE_LARGE_DEVICE)

    def test_manual_only(self) -> None:
        """No runs, manual value set -> manual value."""
        assert estimate([], manual_w=700) == (700, ESTIMATE_MANUAL)

    def test_imported_only(self) -> None:
        """Review: 0 real runs with an imported 2600 W -> 2600 W."""
        assert estimate([record(2600, SOURCE_STATISTICS)], manual_w=None) == (
            2600,
            ESTIMATE_HISTORY,
        )

    def test_imported_beats_two_real_runs(self) -> None:
        """Review: 2 real runs of 1800 W and an imported 2600 W -> 2600 W."""
        records = [
            record(2600, SOURCE_HISTORY),
            record(1800, minute=1),
            record(1800, minute=2),
        ]
        assert estimate(records, manual_w=None) == (2600, ESTIMATE_HISTORY)

    def test_imported_ignored_from_three_real_runs(self) -> None:
        """Review: 3 real runs of 1800 W and an imported 2600 W -> 1800 W."""
        records = [
            record(2600, SOURCE_HISTORY),
            *(record(1800, minute=i) for i in range(1, 4)),
        ]
        assert estimate(records, manual_w=None) == (1800, ESTIMATE_MEMORY)


def test_record_round_trips_through_storage() -> None:
    """A record survives serialization."""
    rec = record(1234.5, SOURCE_HISTORY)
    assert PowerRecord.from_dict(rec.as_dict()) == rec
