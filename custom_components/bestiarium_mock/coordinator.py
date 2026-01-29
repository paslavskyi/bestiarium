"""Coordinator for bestiarium_mock."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    GRID_ALARM_THRESHOLD_SECONDS,
    GRID_MAX_POWER_W,
    GRID_RANDOM_MAX_W,
    GRID_RANDOM_MIN_W,
    GRID_UPDATE_INTERVAL_SECONDS,
    LOGGER,
    MOCK_DEVICES,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .binary_sensor import MockDeviceStateSensor, MockGridAlarmSensor
    from .data import BestiariumMockConfigEntry
    from .number import MockPowerNumber
    from .sensor import MockDevicePowerSensor, MockGridPowerSensor
    from .switch import MockDeviceSwitch, MockEnabledSwitch


class MockDevicesCoordinator:
    """Coordinator for mock devices state management and grid calculations."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumMockConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry

        # Entity references (populated during platform setup)
        self._device_switches: dict[str, MockDeviceSwitch] = {}
        self._mock_enabled_switches: dict[str, MockEnabledSwitch] = {}
        self._power_sensors: dict[str, MockDevicePowerSensor] = {}
        self._power_numbers: dict[str, MockPowerNumber] = {}
        self._state_sensors: dict[str, MockDeviceStateSensor] = {}
        self._grid_power_sensor: MockGridPowerSensor | None = None
        self._grid_alarm_sensor: MockGridAlarmSensor | None = None

        # Grid state
        self._grid_random_power: float = 0
        self._overload_start_time: datetime | None = None
        self._unsub_timer: callable | None = None

    def register_device_switch(self, device_id: str, switch: MockDeviceSwitch) -> None:
        """Register a device switch."""
        self._device_switches[device_id] = switch

    def register_mock_enabled_switch(
        self, device_id: str, switch: MockEnabledSwitch
    ) -> None:
        """Register a mock enabled switch."""
        self._mock_enabled_switches[device_id] = switch

    def register_power_sensor(
        self, device_id: str, sensor: MockDevicePowerSensor
    ) -> None:
        """Register a power sensor."""
        self._power_sensors[device_id] = sensor

    def register_power_number(self, device_id: str, number: MockPowerNumber) -> None:
        """Register a power number entity."""
        self._power_numbers[device_id] = number

    def register_state_sensor(
        self, device_id: str, sensor: MockDeviceStateSensor
    ) -> None:
        """Register a state sensor."""
        self._state_sensors[device_id] = sensor

    def register_grid_power_sensor(self, sensor: MockGridPowerSensor) -> None:
        """Register the grid power sensor."""
        self._grid_power_sensor = sensor

    def register_grid_alarm_sensor(self, sensor: MockGridAlarmSensor) -> None:
        """Register the grid alarm sensor."""
        self._grid_alarm_sensor = sensor

    async def async_start(self) -> None:
        """Start the coordinator."""
        LOGGER.debug("Starting mock devices coordinator")

        # Initial update
        self._update_all_devices()
        self._update_grid()

        # Start periodic grid updates
        self._unsub_timer = async_track_time_interval(
            self.hass,
            self._async_update_grid_periodic,
            timedelta(seconds=GRID_UPDATE_INTERVAL_SECONDS),
        )

    async def async_stop(self) -> None:
        """Stop the coordinator."""
        LOGGER.debug("Stopping mock devices coordinator")
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def notify_switch_changed(self, device_id: str) -> None:
        """Handle switch state change."""
        self._update_device(device_id)
        self._update_grid()

    @callback
    def notify_mock_enabled_changed(self, device_id: str) -> None:
        """Handle mock enabled switch change."""
        self._update_device(device_id)
        self._update_grid()

    @callback
    def notify_power_number_changed(self, device_id: str) -> None:
        """Handle power number change."""
        self._update_device(device_id)
        self._update_grid()

    def _update_device(self, device_id: str) -> None:
        """Update a single device's power and state."""
        device_switch = self._device_switches.get(device_id)
        mock_enabled = self._mock_enabled_switches.get(device_id)
        power_sensor = self._power_sensors.get(device_id)
        state_sensor = self._state_sensors.get(device_id)

        if not all([device_switch, mock_enabled, power_sensor, state_sensor]):
            return

        # Calculate power based on switch states
        power = self._calculate_device_power(device_id)

        # Update sensors
        power_sensor.set_power(power)
        state_sensor.set_state(is_on=power > 0)

    def _update_all_devices(self) -> None:
        """Update all devices."""
        for device_spec in MOCK_DEVICES:
            self._update_device(device_spec.device_id)

    def _calculate_device_power(self, device_id: str) -> float:
        """Calculate power for a device based on its state."""
        device_switch = self._device_switches.get(device_id)
        mock_enabled = self._mock_enabled_switches.get(device_id)

        if not device_switch or not mock_enabled:
            return 0

        # If switch is off or mock is disabled, power is 0
        if not device_switch.is_on or not mock_enabled.is_on:
            return 0

        # Get expected power (from number entity if available, else from spec)
        power_number = self._power_numbers.get(device_id)
        if power_number and power_number.native_value is not None:
            return float(power_number.native_value)

        # Find device spec to get default power
        for device_spec in MOCK_DEVICES:
            if device_spec.device_id == device_id:
                return float(device_spec.power_w)

        return 0

    def _update_grid(self) -> None:
        """Update grid power and alarm state."""
        if not self._grid_power_sensor or not self._grid_alarm_sensor:
            return

        # Calculate total device power
        total_device_power = sum(
            self._calculate_device_power(device_spec.device_id)
            for device_spec in MOCK_DEVICES
        )

        # Add random background power
        total_power = total_device_power + self._grid_random_power

        # Update grid power sensor
        self._grid_power_sensor.set_power(total_power)

        # Update alarm state
        self._update_grid_alarm(total_power)

    def _update_grid_alarm(self, total_power: float) -> None:
        """Update grid alarm based on power and time over limit."""
        if not self._grid_alarm_sensor:
            return

        now = datetime.now(UTC)

        if total_power > GRID_MAX_POWER_W:
            # Over limit
            if self._overload_start_time is None:
                self._overload_start_time = now
            else:
                # Check if over limit for threshold duration
                elapsed = (now - self._overload_start_time).total_seconds()
                if elapsed >= GRID_ALARM_THRESHOLD_SECONDS:
                    self._grid_alarm_sensor.set_alarm(is_alarm=True)
        else:
            # Under limit - reset
            self._overload_start_time = None
            self._grid_alarm_sensor.set_alarm(is_alarm=False)

    @callback
    def _async_update_grid_periodic(self, now: datetime) -> None:  # noqa: ARG002
        """Periodic update for grid random power."""
        # Update random power component (using standard random for mock data)
        self._grid_random_power = random.uniform(  # noqa: S311
            GRID_RANDOM_MIN_W, GRID_RANDOM_MAX_W
        )
        self._update_grid()
