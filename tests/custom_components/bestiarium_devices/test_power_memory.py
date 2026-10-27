"""Tests for the power memory: storage, live runs, recorder import."""

# ruff: noqa: S101, PLR2004

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    flush_store,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_ESTIMATED_POWER,
    CONF_LOAD_PRIORITIES,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    SIGNAL_EXPECTED_POWER_CHANGED,
)
from custom_components.bestiarium_devices.consumption import (
    ESTIMATE_HISTORY,
    ESTIMATE_LARGE_DEVICE,
    ESTIMATE_MEMORY,
    SOURCE_HISTORY,
    SOURCE_RUN,
    SOURCE_STATISTICS,
    PowerRecord,
)
from custom_components.bestiarium_devices.power_memory import (
    DATA_POWER_MEMORY,
    STORAGE_KEY,
    PowerMemory,
    async_get_power_memory,
    async_import_history,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

RELAY = "switch.boiler"
POWER = "sensor.boiler_power"


def _device_entry(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: "Boiler",
            CONF_SWITCH_ENTITY_ID: RELAY,
            CONF_POWER_SENSOR_ENTITY_ID: POWER,
            CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
        },
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


def _run(power: float, source: str = SOURCE_RUN, minute: int = 0) -> PowerRecord:
    return PowerRecord(
        start=dt_util.utcnow() - timedelta(hours=1) + timedelta(minutes=minute),
        duration_s=60,
        power_w=power,
        source=source,
    )


def _power_attrs() -> dict[str, Any]:
    return {
        "device_class": "power",
        "unit_of_measurement": "W",
        "state_class": "measurement",
    }


class TestStorage:
    """The memory lives in a Store and survives restarts."""

    async def test_runs_survive_a_restart(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """Criterion 8: the stored runs come back after a restart."""
        entry = _device_entry(hass)
        memory = await async_get_power_memory(hass)
        for minute in range(3):
            memory.add_run(entry.entry_id, _run(1500 + minute, minute=minute))
        await flush_store(memory._store)  # noqa: SLF001

        stored = hass_storage[STORAGE_KEY]["data"]
        assert len(stored[entry.entry_id]["runs"]) == 3

        restarted = PowerMemory(hass)
        await restarted.async_load()
        assert restarted.records(entry.entry_id) == memory.records(entry.entry_id)
        assert restarted.expected_power(entry) == (1502, ESTIMATE_MEMORY)

    async def test_reset_clears_runs_and_keeps_history_imported(
        self, hass: HomeAssistant
    ) -> None:
        """Criterion 9: reset empties the memory and blocks a second import."""
        entry = _device_entry(hass)
        memory = await async_get_power_memory(hass)
        memory.set_imported(entry.entry_id, [_run(2600, SOURCE_HISTORY)])
        memory.add_run(entry.entry_id, _run(1800, minute=5))

        memory.reset(entry.entry_id)

        assert memory.records(entry.entry_id) == []
        assert memory.filled_from_history(entry.entry_id)
        assert memory.expected_power(entry) == (2000, ESTIMATE_LARGE_DEVICE)

    async def test_manual_value_counts_until_three_runs(
        self, hass: HomeAssistant
    ) -> None:
        """Criterion 6 through the entry options."""
        entry = _device_entry(hass, **{CONF_ESTIMATED_POWER: 1000})
        memory = await async_get_power_memory(hass)
        assert memory.expected_power(entry) == (1000, "manual")
        for minute in range(3):
            memory.add_run(entry.entry_id, _run(300, minute=minute))
        assert memory.expected_power(entry) == (300, ESTIMATE_MEMORY)

    async def test_zero_manual_value_is_not_set(self, hass: HomeAssistant) -> None:
        """0 W in the number means no manual value."""
        entry = _device_entry(hass, **{CONF_ESTIMATED_POWER: 0})
        memory = await async_get_power_memory(hass)
        assert memory.expected_power(entry) == (2000, ESTIMATE_LARGE_DEVICE)

    async def test_listeners_are_told_about_changes(self, hass: HomeAssistant) -> None:
        """Adding, resetting and importing signal the entry's expected power."""
        entry = _device_entry(hass)
        memory = await async_get_power_memory(hass)
        calls: list[None] = []
        unsubscribe = async_dispatcher_connect(
            hass,
            SIGNAL_EXPECTED_POWER_CHANGED.format(entry.entry_id),
            callback(lambda: calls.append(None)),
        )
        memory.add_run(entry.entry_id, _run(1500))
        memory.set_imported(entry.entry_id, [])
        memory.reset(entry.entry_id)
        unsubscribe()
        memory.add_run(entry.entry_id, _run(1500))
        assert len(calls) == 3

    async def test_removed_entry_is_forgotten(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """Removing a device entry drops its memory from the store."""
        entry = _device_entry(hass)
        memory = await async_get_power_memory(hass)
        memory.add_run(entry.entry_id, _run(1500))
        memory.remove(entry.entry_id)
        await flush_store(memory._store)  # noqa: SLF001
        assert entry.entry_id not in hass_storage[STORAGE_KEY]["data"]


class TestLiveRuns:
    """Runs seen live through the relay and power sensor."""

    async def _setup(self, hass: HomeAssistant, *, relay_on: bool) -> MockConfigEntry:
        hass.states.async_set(RELAY, STATE_ON if relay_on else STATE_OFF)
        hass.states.async_set(POWER, "0", _power_attrs())
        entry = _device_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    async def test_run_held_for_40_seconds_is_remembered(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """Relay on, 2400 W for 40 s, relay off -> one run of 2400 W."""
        entry = await self._setup(hass, relay_on=False)
        hass.states.async_set(RELAY, STATE_ON)
        hass.states.async_set(POWER, "2400", _power_attrs())
        freezer.tick(timedelta(seconds=40))
        hass.states.async_set(RELAY, STATE_OFF)
        hass.states.async_set(POWER, "0", _power_attrs())
        await hass.async_block_till_done()

        memory = hass.data[DATA_POWER_MEMORY]
        [run] = memory.records(entry.entry_id)
        assert (run.power_w, run.source, run.duration_s) == (2400, SOURCE_RUN, 40)
        await hass.config_entries.async_unload(entry.entry_id)

    async def test_relay_on_at_zero_watts_is_not_a_run(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """A heated boiler: relay on for minutes at 0 W leaves no record."""
        entry = await self._setup(hass, relay_on=True)
        freezer.tick(timedelta(minutes=5))
        hass.states.async_set(RELAY, STATE_OFF)
        await hass.async_block_till_done()
        assert hass.data[DATA_POWER_MEMORY].records(entry.entry_id) == []
        await hass.config_entries.async_unload(entry.entry_id)

    async def test_unavailable_power_ends_the_run(
        self, hass: HomeAssistant, freezer: FrozenDateTimeFactory
    ) -> None:
        """The sensor going unavailable ends the run."""
        entry = await self._setup(hass, relay_on=True)
        hass.states.async_set(POWER, "1800", _power_attrs())
        freezer.tick(timedelta(seconds=45))
        hass.states.async_set(POWER, STATE_UNAVAILABLE)
        await hass.async_block_till_done()
        [run] = hass.data[DATA_POWER_MEMORY].records(entry.entry_id)
        assert run.power_w == 1800
        await hass.config_entries.async_unload(entry.entry_id)


# Before the module's fixtures: the recorder must start before hass does
@pytest.mark.usefixtures("recorder_mock")
class TestImportFromRecorder:
    """Criterion 10: first fill of the memory from the recorder."""

    async def _record_run(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        watts: str,
        seconds: int = 120,
    ) -> None:
        hass.states.async_set(RELAY, STATE_ON)
        hass.states.async_set(POWER, watts, _power_attrs())
        freezer.tick(timedelta(seconds=seconds))
        hass.states.async_set(POWER, "0", _power_attrs())
        hass.states.async_set(RELAY, STATE_OFF)
        freezer.tick(timedelta(minutes=10))

    async def test_runs_rebuilt_from_state_history(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Recorded relay and power states become 'history' records."""
        hass.states.async_set(RELAY, STATE_OFF)
        hass.states.async_set(POWER, "0", _power_attrs())
        for watts in ("2300", "2400", "2350"):
            await self._record_run(hass, freezer, watts)
        await async_wait_recording_done(hass)

        entry = _device_entry(hass)
        memory = await async_get_power_memory(hass)
        await async_import_history(hass, memory, entry)

        records = memory.records(entry.entry_id)
        assert [(r.power_w, r.source) for r in records] == [
            (2300, SOURCE_HISTORY),
            (2400, SOURCE_HISTORY),
            (2350, SOURCE_HISTORY),
        ]
        assert memory.filled_from_history(entry.entry_id)
        assert memory.expected_power(entry) == (2400, ESTIMATE_HISTORY)

    async def test_statistics_fill_up_to_three_records(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """One history run + hourly maxima before it -> 3 records in all."""
        now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        old = now - timedelta(days=30)
        hour = timedelta(hours=1)
        async_import_statistics(
            hass,
            {
                "mean_type": StatisticMeanType.ARITHMETIC,
                "has_sum": False,
                "name": None,
                "source": "recorder",
                "statistic_id": POWER,
                "unit_class": "power",
                "unit_of_measurement": "W",
            },
            [
                {"start": old, "max": 2100.0, "mean": 1000.0, "min": 0.0},
                {"start": old + hour, "max": 2500.0, "mean": 1000.0, "min": 0.0},
                {"start": old + 3 * hour, "max": 2.0, "mean": 1.0, "min": 0.0},
                {"start": old + 5 * hour, "max": 1900.0, "mean": 900.0, "min": 0.0},
                {"start": old + 7 * hour, "max": 2200.0, "mean": 900.0, "min": 0.0},
            ],
        )
        hass.states.async_set(RELAY, STATE_OFF)
        hass.states.async_set(POWER, "0", _power_attrs())
        await self._record_run(hass, freezer, "1700")
        await async_wait_recording_done(hass)

        entry = _device_entry(hass)
        memory = await async_get_power_memory(hass)
        await async_import_history(hass, memory, entry)

        records = memory.records(entry.entry_id)
        assert [(r.power_w, r.source) for r in records] == [
            (1900, SOURCE_STATISTICS),
            (2200, SOURCE_STATISTICS),
            (1700, SOURCE_HISTORY),
        ]

    async def test_empty_recorder_leaves_memory_empty(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Nothing recorded -> no records, the import is not repeated."""
        entry = _device_entry(hass)
        memory = await async_get_power_memory(hass)
        await async_import_history(hass, memory, entry)
        assert memory.records(entry.entry_id) == []
        assert memory.filled_from_history(entry.entry_id)

    async def test_setup_imports_once(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Setting up an old entry fills its memory; the next setup does not."""
        hass.states.async_set(RELAY, STATE_OFF)
        hass.states.async_set(POWER, "0", _power_attrs())
        await self._record_run(hass, freezer, "2400")
        await async_wait_recording_done(hass)

        entry = _device_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        memory = hass.data[DATA_POWER_MEMORY]
        assert [r.power_w for r in memory.records(entry.entry_id)] == [2400]

        memory.reset(entry.entry_id)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert memory.records(entry.entry_id) == []
        await hass.config_entries.async_unload(entry.entry_id)

    async def test_run_seen_during_import_is_kept(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A live run recorded before the import ends stays next to the import."""
        entry = _device_entry(hass)
        memory = await async_get_power_memory(hass)
        live = _run(900, minute=50)
        imported = _run(2600, SOURCE_HISTORY)
        memory.add_run(entry.entry_id, live)
        memory.set_imported(entry.entry_id, [imported])
        assert memory.records(entry.entry_id) == [imported, live]
