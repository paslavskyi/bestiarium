"""
The power memory of the devices: runs seen live or imported from the recorder.

One Store holds the runs of every device entry. Runs are tracked live from
the relay and power sensor states; on the first setup of an entry the memory
is filled from the recorder's state history and hourly statistics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import state_changes_during_period
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import STATE_ON
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.singleton import singleton
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .const import (
    CONF_ESTIMATED_POWER,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DOMAIN,
    POWER_MEMORY_SIZE,
    REAL_RUNS_FOR_ESTIMATE,
    SIGNAL_EXPECTED_POWER_CHANGED,
)
from .consumption import (
    SOURCE_HISTORY,
    SOURCE_RUN,
    PowerRecord,
    RunBuilder,
    add_run,
    estimate,
    records_from_hourly_max,
    runs_from_states,
)

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import (
        CALLBACK_TYPE,
        Event,
        EventStateChangedData,
        HomeAssistant,
        State,
    )

STORAGE_KEY = f"{DOMAIN}.consumption"
STORAGE_VERSION = 1
SAVE_DELAY_SECONDS = 10

DATA_POWER_MEMORY: HassKey[PowerMemory] = HassKey(f"{DOMAIN}_power_memory")


@dataclass
class _DeviceMemory:
    records: list[PowerRecord] = field(default_factory=list)
    filled_from_history: bool = False


class PowerMemory:
    """The remembered runs of every device entry, kept in a Store."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Start empty; call async_load to read the store."""
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._devices: dict[str, _DeviceMemory] = {}

    async def async_load(self) -> None:
        """Read the stored runs."""
        stored = await self._store.async_load() or {}
        self._devices = {
            entry_id: _DeviceMemory(
                records=[PowerRecord.from_dict(run) for run in device["runs"]],
                filled_from_history=device["filled_from_history"],
            )
            for entry_id, device in stored.items()
        }

    def records(self, entry_id: str) -> list[PowerRecord]:
        """Return the remembered runs of a device, oldest first."""
        device = self._devices.get(entry_id)
        return list(device.records) if device else []

    def filled_from_history(self, entry_id: str) -> bool:
        """Return whether the recorder import has already run for a device."""
        device = self._devices.get(entry_id)
        return device is not None and device.filled_from_history

    def expected_power(self, entry: ConfigEntry) -> tuple[float, str]:
        """Return the device's expected power and what it is based on."""
        return estimate(
            self.records(entry.entry_id), entry.options.get(CONF_ESTIMATED_POWER)
        )

    def add_run(self, entry_id: str, run: PowerRecord) -> None:
        """Remember a run seen live."""
        device = self._devices.setdefault(entry_id, _DeviceMemory())
        device.records = add_run(device.records, run)
        self._changed(entry_id)

    def set_imported(self, entry_id: str, imported: list[PowerRecord]) -> None:
        """Store the records imported from the recorder next to the live runs."""
        device = self._devices.setdefault(entry_id, _DeviceMemory())
        records = sorted(imported, key=lambda record: record.start)
        for run in device.records:
            if run.is_real:
                records = add_run(records, run)
        device.records = records
        device.filled_from_history = True
        self._changed(entry_id)

    def reset(self, entry_id: str) -> None:
        """Forget a device's runs; its history is not imported again."""
        self._devices[entry_id] = _DeviceMemory(filled_from_history=True)
        self._changed(entry_id)

    def remove(self, entry_id: str) -> None:
        """Forget a removed device entry."""
        self._devices.pop(entry_id, None)
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY_SECONDS)

    def _changed(self, entry_id: str) -> None:
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY_SECONDS)
        async_dispatcher_send(
            self._hass, SIGNAL_EXPECTED_POWER_CHANGED.format(entry_id)
        )

    def _data_to_save(self) -> dict[str, Any]:
        return {
            entry_id: {
                "filled_from_history": device.filled_from_history,
                "runs": [record.as_dict() for record in device.records],
            }
            for entry_id, device in self._devices.items()
        }


@singleton(DATA_POWER_MEMORY, async_=True)
async def async_get_power_memory(hass: HomeAssistant) -> PowerMemory:
    """Return the loaded power memory, shared by all device entries."""
    memory = PowerMemory(hass)
    await memory.async_load()
    return memory


def watts(state: State | None) -> float | None:
    """Return the power a sensor state reports, None if it reports none."""
    try:
        return float(state.state) if state else None
    except ValueError:
        return None


@callback
def async_track_runs(
    hass: HomeAssistant, memory: PowerMemory, entry: ConfigEntry
) -> CALLBACK_TYPE:
    """Remember every run of the device seen from now on."""
    relay = entry.data[CONF_SWITCH_ENTITY_ID]
    power = entry.data[CONF_POWER_SENSOR_ENTITY_ID]
    builder = RunBuilder(SOURCE_RUN)

    @callback
    def update(entity_id: str, state: State | None, time: datetime) -> None:
        if entity_id == relay:
            run = builder.relay(time, on=state is not None and state.state == STATE_ON)
        else:
            run = builder.power(time, watts(state))
        if run:
            memory.add_run(entry.entry_id, run)

    now = dt_util.utcnow()
    for entity_id in (relay, power):
        update(entity_id, hass.states.get(entity_id), now)

    @callback
    def on_state_changed(event: Event[EventStateChangedData]) -> None:
        update(event.data["entity_id"], event.data["new_state"], event.time_fired)

    return async_track_state_change_event(hass, [relay, power], on_state_changed)


async def async_import_history(
    hass: HomeAssistant, memory: PowerMemory, entry: ConfigEntry
) -> None:
    """Fill the device's memory from the recorder, once."""
    recorder = get_instance(hass)
    await recorder.async_db_ready
    start = dt_util.utcnow() - timedelta(days=recorder.keep_days)
    imported = await recorder.async_add_executor_job(
        _read_recorder,
        hass,
        entry.data[CONF_SWITCH_ENTITY_ID],
        entry.data[CONF_POWER_SENSOR_ENTITY_ID],
        start,
    )
    memory.set_imported(entry.entry_id, imported)


def _read_recorder(
    hass: HomeAssistant, relay: str, power: str, start: datetime
) -> list[PowerRecord]:
    """
    Rebuild the device's runs from the recorder.

    The latest runs come from the state history. With fewer than
    REAL_RUNS_FOR_ESTIMATE of them, hourly maxima from before the history
    window make up the difference.
    """
    relay_states = state_changes_during_period(
        hass, start, entity_id=relay, no_attributes=True
    ).get(relay, [])
    power_states = state_changes_during_period(
        hass, start, entity_id=power, no_attributes=True
    ).get(power, [])
    runs = runs_from_states(
        [(state.last_changed, state.state == STATE_ON) for state in relay_states],
        [(state.last_changed, watts(state)) for state in power_states],
        SOURCE_HISTORY,
    )[-POWER_MEMORY_SIZE:]
    missing = REAL_RUNS_FOR_ESTIMATE - len(runs)
    if missing <= 0:
        return runs
    rows = statistics_during_period(
        hass, dt_util.utc_from_timestamp(0), start, {power}, "hour", None, {"max"}
    ).get(power, [])
    hourly = records_from_hourly_max(
        [(dt_util.utc_from_timestamp(row["start"]), row["max"]) for row in rows]
    )
    return hourly[-missing:] + runs
