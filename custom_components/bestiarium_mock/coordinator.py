"""Coordinator for bestiarium_mock."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    GRID_RANDOM_MAX_W,
    GRID_RANDOM_MIN_W,
    GRID_UPDATE_INTERVAL_SECONDS,
    IR_AC_ROOM_INTERVAL_SECONDS,
    LOGGER,
    MOCK_DEVICES,
    MOCK_GRID,
    MOCK_IR_ACS,
    MOCK_THERMOMETERS,
)
from .faults import MockSocket
from .ir_ac import MockIrAc
from .readings import MockReading

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import BestiariumMockConfigEntry
    from .number import MockTemperatureNumber
    from .sensor import MockGridPowerSensor, MockThermometerTemperatureSensor


class MockDevicesCoordinator:
    """Coordinator for the mock sockets, thermometers and grid."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumMockConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry

        # The grid's background noise; tests can seed it
        self.rng = random.Random()  # noqa: S311
        self.sockets: dict[str, MockSocket] = {
            spec.device_id: MockSocket(hass, spec) for spec in MOCK_DEVICES
        }
        self._unsub_sockets = [
            socket.add_listener(self._update_grid) for socket in self.sockets.values()
        ]
        self.ir_acs: dict[str, MockIrAc] = {
            spec.device_id: MockIrAc(spec, self.sockets[spec.socket])
            for spec in MOCK_IR_ACS
        }
        self._grid_power_sensor: MockGridPowerSensor | None = None
        # What HA sees of the thermometers and the grid, by device id
        self.readings: dict[str, MockReading] = {
            device_id: MockReading(hass)
            for device_id in (
                *(spec.device_id for spec in MOCK_THERMOMETERS),
                MOCK_GRID.device_id,
            )
        }

        # Thermometer entity references
        self._temperature_sensors: dict[str, MockThermometerTemperatureSensor] = {}
        self._temperature_numbers: dict[str, MockTemperatureNumber] = {}

        # Grid state
        self._grid_random_power: float = 0
        self._unsub_timer: callable | None = None
        self._unsub_rooms: callable | None = None

    def register_grid_power_sensor(self, sensor: MockGridPowerSensor) -> None:
        """Register the grid power sensor."""
        self._grid_power_sensor = sensor

    def register_temperature_sensor(
        self, device_id: str, sensor: MockThermometerTemperatureSensor
    ) -> None:
        """Register a temperature sensor."""
        self._temperature_sensors[device_id] = sensor

    def register_temperature_number(
        self, device_id: str, number: MockTemperatureNumber
    ) -> None:
        """Register a temperature number entity."""
        self._temperature_numbers[device_id] = number

    async def async_start(self) -> None:
        """Start the coordinator."""
        LOGGER.debug("Starting mock devices coordinator")

        for socket in self.sockets.values():
            socket.start()

        # Initial update
        self._update_all_thermometers()
        self._update_grid()

        # Start periodic grid updates
        self._unsub_timer = async_track_time_interval(
            self.hass,
            self._async_update_grid_periodic,
            timedelta(seconds=GRID_UPDATE_INTERVAL_SECONDS),
        )
        self._unsub_rooms = async_track_time_interval(
            self.hass,
            self._async_follow_rooms,
            timedelta(seconds=IR_AC_ROOM_INTERVAL_SECONDS),
        )

    async def async_stop(self) -> None:
        """Stop the coordinator."""
        LOGGER.debug("Stopping mock devices coordinator")
        for socket in self.sockets.values():
            socket.stop()
        for ac in self.ir_acs.values():
            ac.stop()
        for reading in self.readings.values():
            reading.stop()
        for unsub in self._unsub_sockets:
            unsub()
        for unsub in (self._unsub_timer, self._unsub_rooms):
            if unsub:
                unsub()
        self._unsub_timer = self._unsub_rooms = None

    @callback
    def notify_temperature_number_changed(self, device_id: str) -> None:
        """Handle temperature number change."""
        self._update_thermometer(device_id)

    def _update_thermometer(self, device_id: str) -> None:
        """Update a single thermometer's temperature."""
        temp_sensor = self._temperature_sensors.get(device_id)
        temp_number = self._temperature_numbers.get(device_id)

        if not temp_sensor or not temp_number:
            return

        # Get temperature from number input
        if temp_number.native_value is not None:
            temp_sensor.set_temperature(float(temp_number.native_value))

    def _update_all_thermometers(self) -> None:
        """Update all thermometers."""
        for thermo_spec in MOCK_THERMOMETERS:
            self._update_thermometer(thermo_spec.device_id)

    @callback
    def _async_follow_rooms(self, _now: datetime) -> None:
        """Let each air conditioner with a room warm or cool it a step."""
        for ac in self.ir_acs.values():
            if (room := ac.spec.room) is None:
                continue
            number = self._temperature_numbers[room]
            temperature = float(number.native_value)
            if (moved := ac.follow_room(temperature)) != temperature:
                number.set_temperature(moved)

    @callback
    def _update_grid(self) -> None:
        """Update the grid power."""
        if not self._grid_power_sensor:
            return

        # The grid sees what the loads draw, whatever their switches report
        total_device_power = sum(socket.power for socket in self.sockets.values())

        # Add random background power
        total_power = total_device_power + self._grid_random_power

        self._grid_power_sensor.set_power(total_power)

    @callback
    def _async_update_grid_periodic(self, now: datetime) -> None:  # noqa: ARG002
        """Periodic update for grid random power."""
        self._grid_random_power = self.rng.uniform(GRID_RANDOM_MIN_W, GRID_RANDOM_MAX_W)
        self._update_grid()
