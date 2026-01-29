"""Tests for the Bestiarium Mock Devices coordinator."""

# ruff: noqa: S101, SLF001, PLR2004

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from custom_components.bestiarium_mock.const import (
    GRID_ALARM_THRESHOLD_SECONDS,
    GRID_MAX_POWER_W,
    MOCK_DEVICES,
)
from custom_components.bestiarium_mock.coordinator import MockDevicesCoordinator


class MockSwitch:
    """Mock switch entity for testing."""

    def __init__(self, *, is_on: bool = False) -> None:
        """Initialize mock switch."""
        self._is_on = is_on

    @property
    def is_on(self) -> bool:
        """Return switch state."""
        return self._is_on

    def turn_on(self) -> None:
        """Turn switch on."""
        self._is_on = True

    def turn_off(self) -> None:
        """Turn switch off."""
        self._is_on = False


class MockPowerSensor:
    """Mock power sensor entity for testing."""

    def __init__(self) -> None:
        """Initialize mock power sensor."""
        self.power: float = 0

    def set_power(self, power: float) -> None:
        """Set power value."""
        self.power = power


class MockStateSensor:
    """Mock state sensor entity for testing."""

    def __init__(self) -> None:
        """Initialize mock state sensor."""
        self.is_on: bool = False

    def set_state(self, *, is_on: bool) -> None:
        """Set state value."""
        self.is_on = is_on


class MockAlarmSensor:
    """Mock alarm sensor entity for testing."""

    def __init__(self) -> None:
        """Initialize mock alarm sensor."""
        self.is_alarm: bool = False

    def set_alarm(self, *, is_alarm: bool) -> None:
        """Set alarm state."""
        self.is_alarm = is_alarm


class MockPowerNumber:
    """Mock power number entity for testing."""

    def __init__(self, value: float) -> None:
        """Initialize mock power number."""
        self._value = value

    @property
    def native_value(self) -> float:
        """Return native value."""
        return self._value


def create_coordinator_with_mocks() -> tuple[
    MockDevicesCoordinator,
    dict[str, MockSwitch],
    dict[str, MockSwitch],
    dict[str, MockPowerSensor],
    dict[str, MockStateSensor],
    MockPowerSensor,
    MockAlarmSensor,
]:
    """Create a coordinator with mock entities for testing."""
    hass = MagicMock()
    entry = MagicMock()

    coordinator = MockDevicesCoordinator(hass, entry)

    device_switches: dict[str, MockSwitch] = {}
    mock_enabled_switches: dict[str, MockSwitch] = {}
    power_sensors: dict[str, MockPowerSensor] = {}
    state_sensors: dict[str, MockStateSensor] = {}

    # Register mock entities for each device
    for device_spec in MOCK_DEVICES:
        device_id = device_spec.device_id

        device_switch = MockSwitch(is_on=False)
        mock_enabled = MockSwitch(is_on=True)
        power_sensor = MockPowerSensor()
        state_sensor = MockStateSensor()

        device_switches[device_id] = device_switch
        mock_enabled_switches[device_id] = mock_enabled
        power_sensors[device_id] = power_sensor
        state_sensors[device_id] = state_sensor

        coordinator.register_device_switch(device_id, device_switch)
        coordinator.register_mock_enabled_switch(device_id, mock_enabled)
        coordinator.register_power_sensor(device_id, power_sensor)
        coordinator.register_state_sensor(device_id, state_sensor)

    # Register grid sensors
    grid_power_sensor = MockPowerSensor()
    grid_alarm_sensor = MockAlarmSensor()
    coordinator.register_grid_power_sensor(grid_power_sensor)
    coordinator.register_grid_alarm_sensor(grid_alarm_sensor)

    return (
        coordinator,
        device_switches,
        mock_enabled_switches,
        power_sensors,
        state_sensors,
        grid_power_sensor,
        grid_alarm_sensor,
    )


class TestPowerCalculation:
    """Tests for power calculation logic."""

    def test_power_zero_when_switch_off(self) -> None:
        """Test that power is 0 when device switch is off."""
        (
            coordinator,
            device_switches,
            mock_enabled_switches,
            power_sensors,
            state_sensors,
            _,
            _,
        ) = create_coordinator_with_mocks()

        # Switch is off, mock enabled
        device_switches["boiler"].turn_off()
        mock_enabled_switches["boiler"].turn_on()

        coordinator._update_device("boiler")

        assert power_sensors["boiler"].power == 0
        assert state_sensors["boiler"].is_on is False

    def test_power_zero_when_mock_disabled(self) -> None:
        """Test that power is 0 when mock is disabled."""
        (
            coordinator,
            device_switches,
            mock_enabled_switches,
            power_sensors,
            state_sensors,
            _,
            _,
        ) = create_coordinator_with_mocks()

        # Switch is on, mock disabled
        device_switches["boiler"].turn_on()
        mock_enabled_switches["boiler"].turn_off()

        coordinator._update_device("boiler")

        assert power_sensors["boiler"].power == 0
        assert state_sensors["boiler"].is_on is False

    def test_power_from_spec_when_switch_on_and_mock_enabled(self) -> None:
        """Test that power comes from device spec when switch on and mock enabled."""
        (
            coordinator,
            device_switches,
            mock_enabled_switches,
            power_sensors,
            state_sensors,
            _,
            _,
        ) = create_coordinator_with_mocks()

        # Switch is on, mock enabled
        device_switches["boiler"].turn_on()
        mock_enabled_switches["boiler"].turn_on()

        coordinator._update_device("boiler")

        # Boiler has 1800W in spec
        assert power_sensors["boiler"].power == 1800
        assert state_sensors["boiler"].is_on is True

    def test_power_from_number_entity_overrides_spec(self) -> None:
        """Test that power number entity overrides device spec."""
        (
            coordinator,
            device_switches,
            mock_enabled_switches,
            power_sensors,
            state_sensors,
            _,
            _,
        ) = create_coordinator_with_mocks()

        # Register a power number for cooker (variable power device)
        power_number = MockPowerNumber(2500.0)
        coordinator.register_power_number("cooker", power_number)

        # Switch is on, mock enabled
        device_switches["cooker"].turn_on()
        mock_enabled_switches["cooker"].turn_on()

        coordinator._update_device("cooker")

        # Should use number value (2500) instead of spec (400)
        assert power_sensors["cooker"].power == 2500
        assert state_sensors["cooker"].is_on is True


class TestGridCalculation:
    """Tests for grid power calculation."""

    def test_grid_power_sum_of_devices(self) -> None:
        """Test that grid power is sum of all device powers."""
        (
            coordinator,
            device_switches,
            mock_enabled_switches,
            _,
            _,
            grid_power_sensor,
            _,
        ) = create_coordinator_with_mocks()

        # Turn on boiler (1800W) and router (20W)
        device_switches["boiler"].turn_on()
        mock_enabled_switches["boiler"].turn_on()
        device_switches["router"].turn_on()
        mock_enabled_switches["router"].turn_on()

        # Set random power to 0 for predictable test
        coordinator._grid_random_power = 0

        coordinator._update_grid()

        # Total should be 1800 + 20 = 1820
        assert grid_power_sensor.power == 1820

    def test_grid_power_includes_random_component(self) -> None:
        """Test that grid power includes random background power."""
        (
            coordinator,
            device_switches,
            _,  # mock_enabled_switches not used
            _,
            _,
            grid_power_sensor,
            _,
        ) = create_coordinator_with_mocks()

        # All devices off
        for switch in device_switches.values():
            switch.turn_off()

        # Set specific random power
        coordinator._grid_random_power = 75.5

        coordinator._update_grid()

        # Total should just be random power
        assert grid_power_sensor.power == 75.5


class TestGridAlarm:
    """Tests for grid alarm logic."""

    def test_alarm_off_when_under_limit(self) -> None:
        """Test that alarm is off when power is under limit."""
        (
            coordinator,
            _,
            _,
            _,
            _,
            _,
            grid_alarm_sensor,
        ) = create_coordinator_with_mocks()

        # Power under limit
        coordinator._update_grid_alarm(4000)

        assert grid_alarm_sensor.is_alarm is False
        assert coordinator._overload_start_time is None

    def test_alarm_starts_timer_when_over_limit(self) -> None:
        """Test that overload timer starts when power exceeds limit."""
        (
            coordinator,
            _,
            _,
            _,
            _,
            _,
            grid_alarm_sensor,
        ) = create_coordinator_with_mocks()

        # Power over limit (first time)
        coordinator._update_grid_alarm(GRID_MAX_POWER_W + 100)

        # Timer should start but alarm not yet triggered
        assert coordinator._overload_start_time is not None
        assert grid_alarm_sensor.is_alarm is False

    def test_alarm_triggers_after_threshold_duration(self) -> None:
        """Test that alarm triggers after being over limit for threshold duration."""
        (
            coordinator,
            _,
            _,
            _,
            _,
            _,
            grid_alarm_sensor,
        ) = create_coordinator_with_mocks()

        # Set overload start time to past threshold
        coordinator._overload_start_time = datetime.now(UTC) - timedelta(
            seconds=GRID_ALARM_THRESHOLD_SECONDS + 1
        )

        # Power still over limit
        coordinator._update_grid_alarm(GRID_MAX_POWER_W + 100)

        assert grid_alarm_sensor.is_alarm is True

    def test_alarm_resets_when_power_drops(self) -> None:
        """Test that alarm resets when power drops below limit."""
        (
            coordinator,
            _,
            _,
            _,
            _,
            _,
            grid_alarm_sensor,
        ) = create_coordinator_with_mocks()

        # Set up overload state
        coordinator._overload_start_time = datetime.now(UTC) - timedelta(
            seconds=GRID_ALARM_THRESHOLD_SECONDS + 1
        )
        grid_alarm_sensor.is_alarm = True

        # Power drops below limit
        coordinator._update_grid_alarm(4000)

        assert coordinator._overload_start_time is None
        assert grid_alarm_sensor.is_alarm is False

    def test_alarm_not_triggered_if_duration_not_reached(self) -> None:
        """Test that alarm doesn't trigger if duration threshold not reached."""
        (
            coordinator,
            _,
            _,
            _,
            _,
            _,
            grid_alarm_sensor,
        ) = create_coordinator_with_mocks()

        # Set overload start time to recent (under threshold)
        coordinator._overload_start_time = datetime.now(UTC) - timedelta(seconds=5)

        # Power still over limit but not long enough
        coordinator._update_grid_alarm(GRID_MAX_POWER_W + 100)

        assert grid_alarm_sensor.is_alarm is False


class TestNotifications:
    """Tests for notification handlers."""

    def test_notify_switch_changed_updates_device_and_grid(self) -> None:
        """Test that switch change notification updates device and grid."""
        (
            coordinator,
            device_switches,
            mock_enabled_switches,
            power_sensors,
            _,
            grid_power_sensor,
            _,
        ) = create_coordinator_with_mocks()

        # Set up initial state
        device_switches["boiler"].turn_on()
        mock_enabled_switches["boiler"].turn_on()
        coordinator._grid_random_power = 0

        # Notify switch changed
        coordinator.notify_switch_changed("boiler")

        # Both device and grid should be updated
        assert power_sensors["boiler"].power == 1800
        assert grid_power_sensor.power == 1800

    def test_notify_mock_enabled_changed_updates_device_and_grid(self) -> None:
        """Test that mock enabled change notification updates device and grid."""
        (
            coordinator,
            device_switches,
            mock_enabled_switches,
            power_sensors,
            _,
            grid_power_sensor,
            _,
        ) = create_coordinator_with_mocks()

        # Set up: switch on, mock disabled
        device_switches["boiler"].turn_on()
        mock_enabled_switches["boiler"].turn_off()
        coordinator._grid_random_power = 0

        # Initial update
        coordinator.notify_mock_enabled_changed("boiler")
        assert power_sensors["boiler"].power == 0

        # Enable mock
        mock_enabled_switches["boiler"].turn_on()
        coordinator.notify_mock_enabled_changed("boiler")

        assert power_sensors["boiler"].power == 1800
        assert grid_power_sensor.power == 1800

    def test_notify_power_number_changed_updates_device_and_grid(self) -> None:
        """Test that power number change notification updates device and grid."""
        (
            coordinator,
            device_switches,
            mock_enabled_switches,
            power_sensors,
            _,
            grid_power_sensor,
            _,
        ) = create_coordinator_with_mocks()

        # Register power number
        power_number = MockPowerNumber(1000.0)
        coordinator.register_power_number("cooker", power_number)

        device_switches["cooker"].turn_on()
        mock_enabled_switches["cooker"].turn_on()
        coordinator._grid_random_power = 0

        coordinator.notify_power_number_changed("cooker")

        assert power_sensors["cooker"].power == 1000
        assert grid_power_sensor.power == 1000
