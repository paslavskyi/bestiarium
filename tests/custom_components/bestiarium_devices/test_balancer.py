"""Tests for the Bestiarium Devices balancing coordinator."""

# ruff: noqa: S101

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.bestiarium_devices.balancer import (
    ActiveZone,
    BalancingCoordinator,
)
from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_OVERLOAD_FIRST_QUEUE,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    DOMAIN_BESTIARIUM_MODE,
    EVENT_DATA,
    EVENT_DEVICE_TURNED_OFF,
    EVENT_DEVICE_TURNED_ON,
    EVENT_MODE_APPLIED,
    EVENT_OVERLOAD_CLEARED,
    EVENT_OVERLOAD_FIRST_QUEUE,
    EVENT_OVERLOAD_SECOND_QUEUE,
    GREEN_INTERVAL_SECONDS,
    ISSUE_NO_LOAD_ZONES,
    LOAD_PRIORITY_ALWAYS_ON,
    LOAD_PRIORITY_OFF,
    MODE_SELECT_UNIQUE_ID,
    RED_INTERVAL_SECONDS,
)
from custom_components.bestiarium_devices.helpers import get_current_priority

from .conftest import device_strategies, started_coordinator
from .mode_entities import (
    AC_LOAD_LIMITS,
    BESTIARIUM_MODE_SELECT_ENTITY_ID,
    BINARY_SENSOR_GREEN,
    BINARY_SENSOR_OVERLOAD,
    BINARY_SENSOR_RED,
    BINARY_SENSOR_YELLOW,
    GRID_POWER_SENSOR,
    RED_SENSOR_ATTRIBUTES,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

EXPECTED_TURN_OFF_CALLS = 2
MIN_SERVICE_CALL_ARGS = 3


@pytest.fixture
def zone_binary_sensors(hass: HomeAssistant, mode_entities: None) -> None:  # noqa: ARG001
    """Register zone binary sensors so coordinator can start (one must exist)."""
    hass.states.async_set(BINARY_SENSOR_GREEN, STATE_OFF)
    hass.states.async_set(BINARY_SENSOR_YELLOW, STATE_OFF)
    hass.states.async_set(BINARY_SENSOR_RED, STATE_OFF, RED_SENSOR_ATTRIBUTES)
    hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_OFF)


@pytest.fixture
def zone_green_on(hass: HomeAssistant, zone_binary_sensors: None) -> None:  # noqa: ARG001
    """Set green zone sensor ON so coordinator has an active zone."""
    hass.states.async_set(BINARY_SENSOR_GREEN, STATE_ON)


@pytest.fixture
def mode_select_entry(hass: HomeAssistant, mode_entities: None) -> MockConfigEntry:  # noqa: ARG001
    """Create a Bestiarium Mode config entry; the grid draws nothing yet."""
    hass.states.async_set(GRID_POWER_SENSOR, "0")
    entry = MockConfigEntry(
        domain=DOMAIN_BESTIARIUM_MODE,
        unique_id="bestiarium_mode",
        data={
            "options": [
                {"name": "Eco", "description": "Energy saving", "icon": "mdi:leaf"},
                {"name": "Boost", "description": "High priority", "icon": "mdi:flash"},
            ],
            "ac_load_limits": AC_LOAD_LIMITS,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def bestiarium_devices_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create a bestiarium_devices config entry for strategy tests."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: "Test Device",
            CONF_SWITCH_ENTITY_ID: "switch.test_device",
            CONF_POWER_SENSOR_ENTITY_ID: "sensor.test_device_power",
            CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
        },
    )
    entry.add_to_hass(hass)
    return entry


class TestBalancingCoordinatorInitAndCleanup:
    """Tests for coordinator initialization and cleanup."""

    async def test_coordinator_starts_and_stops(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
    ) -> None:
        """Test coordinator starts and stops cleanly."""
        coordinator = BalancingCoordinator(hass)
        assert not coordinator.is_running

        await coordinator.async_start()
        assert coordinator.is_running

        await coordinator.async_stop()
        assert not coordinator.is_running
        assert coordinator.active_zone == ActiveZone.NO_DATA

    async def test_coordinator_initial_active_zone_when_green_on(
        self,
        hass: HomeAssistant,
        zone_green_on: None,  # noqa: ARG002
    ) -> None:
        """Test coordinator detects green zone when green sensor is ON."""
        coordinator = await started_coordinator(hass)
        try:
            assert coordinator.active_zone == ActiveZone.GREEN
        finally:
            await coordinator.async_stop()

    async def test_coordinator_double_start_logs_warning(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
    ) -> None:
        """Test starting coordinator twice does not duplicate subscriptions."""
        coordinator = await started_coordinator(hass)
        await coordinator.async_start()  # Second start should return early
        assert coordinator.is_running
        await coordinator.async_stop()

    async def test_coordinator_stop_when_not_running(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test stopping coordinator when not running does not raise."""
        coordinator = BalancingCoordinator(hass)
        await coordinator.async_stop()
        assert not coordinator.is_running

    async def test_coordinator_missing_sensors_still_running(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Coordinator still running when sensors missing (no subscriptions)."""
        coordinator = await started_coordinator(hass)
        try:
            assert coordinator.is_running
            assert coordinator.active_zone == ActiveZone.NO_DATA
        finally:
            await coordinator.async_stop()


class TestModeChangeHandling:
    """Tests for mode change handling (LOAD_PRIORITY_OFF / LOAD_PRIORITY_ALWAYS_ON)."""

    async def test_priority_off_device_turns_off_on_mode_change(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """LOAD_PRIORITY_OFF device turns off when mode priorities are applied."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Eco")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Off Device",
                CONF_SWITCH_ENTITY_ID: "switch.off_device",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.off_device_power",
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_OFF,
                },
            },
        )
        entry.add_to_hass(hass)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._strategies.async_apply_mode()  # noqa: SLF001
                mock_call.assert_any_call(
                    "switch",
                    "turn_off",
                    {"entity_id": "switch.off_device"},
                )
        finally:
            await coordinator.async_stop()

    async def test_priority_always_on_device_turns_on_on_mode_change(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """LOAD_PRIORITY_ALWAYS_ON device turns on when mode priorities are applied."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Eco")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Always On Device",
                CONF_SWITCH_ENTITY_ID: "switch.always_on_device",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.always_on_device_power",
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_ALWAYS_ON,
                },
            },
        )
        entry.add_to_hass(hass)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._strategies.async_apply_mode()  # noqa: SLF001
                mock_call.assert_any_call(
                    "switch",
                    "turn_on",
                    {"entity_id": "switch.always_on_device"},
                )
        finally:
            await coordinator.async_stop()


class TestGreenStrategy:
    """Tests for Green strategy (turn on highest-priority OFF device)."""

    async def test_highest_priority_device_turns_on(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Green strategy turns on the highest-priority device that is OFF."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Eco")
        # Low priority device OFF
        entry_low = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Low",
                CONF_SWITCH_ENTITY_ID: "switch.low",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.low_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 3000},
            },
        )
        entry_low.add_to_hass(hass)
        hass.states.async_set("switch.low", STATE_OFF)
        # High priority device OFF
        entry_high = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "High",
                CONF_SWITCH_ENTITY_ID: "switch.high",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.high_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 7000},
            },
        )
        entry_high.add_to_hass(hass)
        hass.states.async_set("switch.high", STATE_OFF)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._strategies.async_green()  # noqa: SLF001
                mock_call.assert_called_once_with(
                    "switch",
                    "turn_on",
                    {"entity_id": "switch.high"},
                )
        finally:
            await coordinator.async_stop()

    async def test_already_on_devices_skipped(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Green strategy does not turn on devices that are already ON."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Eco")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Already On",
                CONF_SWITCH_ENTITY_ID: "switch.already_on",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.already_on_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.already_on", STATE_ON)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._strategies.async_green()  # noqa: SLF001
                turn_on_calls = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_on"
                ]
                assert len(turn_on_calls) == 0
        finally:
            await coordinator.async_stop()

    async def test_off_skipped_always_on_off_turned_on(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Green skips OFF priority; if ALWAYS_ON device is OFF, turn it on and exit."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Eco")
        # Device with OFF in Eco - switch OFF, should not be turned on
        entry_off = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Off Priority",
                CONF_SWITCH_ENTITY_ID: "switch.off_priority",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.off_priority_power",
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_OFF,
                },
            },
        )
        entry_off.add_to_hass(hass)
        hass.states.async_set("switch.off_priority", STATE_OFF)
        # ALWAYS_ON device OFF - green turns it on and exits
        entry_always = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Always On Priority",
                CONF_SWITCH_ENTITY_ID: "switch.always_priority",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.always_priority_power",
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_ALWAYS_ON,
                },
            },
        )
        entry_always.add_to_hass(hass)
        hass.states.async_set("switch.always_priority", STATE_OFF)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._strategies.async_green()  # noqa: SLF001
                turn_on_calls = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_on"
                ]
                assert len(turn_on_calls) == 1
                assert turn_on_calls[0][0][2]["entity_id"] == "switch.always_priority"
        finally:
            await coordinator.async_stop()


class TestRedStrategy:
    """Tests for Red strategy (turn off lowest-priority ON device)."""

    async def test_lowest_priority_device_turns_off(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Red strategy turns off the lowest-priority device that is ON."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Eco")
        entry_high = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "High",
                CONF_SWITCH_ENTITY_ID: "switch.high",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.high_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 7000},
            },
        )
        entry_high.add_to_hass(hass)
        hass.states.async_set("switch.high", STATE_ON)
        entry_low = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Low",
                CONF_SWITCH_ENTITY_ID: "switch.low",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.low_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 3000},
            },
        )
        entry_low.add_to_hass(hass)
        hass.states.async_set("switch.low", STATE_ON)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._strategies.async_red()  # noqa: SLF001
                mock_call.assert_called_once_with(
                    "switch",
                    "turn_off",
                    {"entity_id": "switch.low"},
                )
        finally:
            await coordinator.async_stop()

    async def test_already_off_devices_skipped(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Red strategy does not turn off devices that are already OFF."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Eco")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Already Off",
                CONF_SWITCH_ENTITY_ID: "switch.already_off",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.already_off_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.already_off", STATE_OFF)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._strategies.async_red()  # noqa: SLF001
                turn_off_calls = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_off"
                ]
                assert len(turn_off_calls) == 0
        finally:
            await coordinator.async_stop()

    async def test_always_on_priority_device_skipped(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Red strategy skips ALWAYS_ON devices (does not turn them off)."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Eco")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Always On",
                CONF_SWITCH_ENTITY_ID: "switch.always_on",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.always_on_power",
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_ALWAYS_ON,
                },
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.always_on", STATE_ON)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._strategies.async_red()  # noqa: SLF001
                turn_off_calls = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_off"
                ]
                assert len(turn_off_calls) == 0
        finally:
            await coordinator.async_stop()


class TestOverloadStrategy:
    """Tests for Overload strategy (emergency shutdown - all devices off)."""

    async def test_all_devices_turn_off_including_always_on(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Overload turns off ALL devices including ALWAYS_ON (emergency shutdown)."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Eco")
        entry_normal = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Normal",
                CONF_SWITCH_ENTITY_ID: "switch.normal",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.normal_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            },
        )
        entry_normal.add_to_hass(hass)
        hass.states.async_set("switch.normal", STATE_ON)
        entry_always = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Always On",
                CONF_SWITCH_ENTITY_ID: "switch.always_on",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.always_on_power",
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_ALWAYS_ON,
                },
            },
        )
        entry_always.add_to_hass(hass)
        hass.states.async_set("switch.always_on", STATE_ON)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._strategies.async_overload()  # noqa: SLF001
                turn_off_calls = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_off"
                ]
                expected_devices = {"switch.normal", "switch.always_on"}
                assert len(turn_off_calls) == len(expected_devices)
                entity_ids = {c[0][2]["entity_id"] for c in turn_off_calls}
                assert entity_ids == expected_devices
        finally:
            await coordinator.async_stop()

    async def test_red_skips_always_on_contrast_with_overload(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Red skips ALWAYS_ON; overload turns it off (contrast)."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Eco")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Always On",
                CONF_SWITCH_ENTITY_ID: "switch.always_on",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.always_on_power",
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_ALWAYS_ON,
                },
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.always_on", STATE_ON)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._strategies.async_red()  # noqa: SLF001
                turn_off_after_red = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_off"
                ]
                assert len(turn_off_after_red) == 0

                mock_call.reset_mock()
                await coordinator._strategies.async_overload()  # noqa: SLF001
                turn_off_after_overload = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_off"
                ]
                assert len(turn_off_after_overload) == 1
                assert (
                    turn_off_after_overload[0][0][2]["entity_id"] == "switch.always_on"
                )
        finally:
            await coordinator.async_stop()


class TestOverloadTwoStepStrategy:
    """Tests for two-step overload strategy."""

    async def test_two_step_overload_first_then_second_after_wait(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Turn off first-queue devices, then after 5s turn off second queue."""
        hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_ON)
        entry_first = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "First Queue",
                CONF_SWITCH_ENTITY_ID: "switch.first",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.first_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: True,
            },
        )
        entry_first.add_to_hass(hass)
        hass.states.async_set("switch.first", STATE_ON)
        entry_second = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Second Queue",
                CONF_SWITCH_ENTITY_ID: "switch.second",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.second_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: False,
            },
        )
        entry_second.add_to_hass(hass)
        hass.states.async_set("switch.second", STATE_ON)

        coordinator = await started_coordinator(hass)
        try:
            with (
                patch(
                    "custom_components.bestiarium_devices.strategies.asyncio.sleep",
                    new_callable=AsyncMock,
                ),
                patch(
                    "homeassistant.core.ServiceRegistry.async_call",
                    new_callable=AsyncMock,
                ) as mock_call,
            ):
                await coordinator._strategies.async_overload()  # noqa: SLF001
            turn_off_calls = [
                c for c in mock_call.call_args_list if c[0][1] == "turn_off"
            ]
            assert len(turn_off_calls) == EXPECTED_TURN_OFF_CALLS
            assert turn_off_calls[0][0][2]["entity_id"] == "switch.first"
            assert turn_off_calls[1][0][2]["entity_id"] == "switch.second"
        finally:
            await coordinator.async_stop()

    async def test_overload_cleared_after_first_queue_skips_second(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Overload clears after first shutdown; second queue stays on."""
        hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_ON)
        entry_first = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "First",
                CONF_SWITCH_ENTITY_ID: "switch.first",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.first_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: True,
            },
        )
        entry_first.add_to_hass(hass)
        hass.states.async_set("switch.first", STATE_ON)
        entry_second = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Second",
                CONF_SWITCH_ENTITY_ID: "switch.second",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.second_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: False,
            },
        )
        entry_second.add_to_hass(hass)
        hass.states.async_set("switch.second", STATE_ON)

        async def clear_overload_on_first_turn_off(
            *args: object,
        ) -> None:
            if len(args) >= MIN_SERVICE_CALL_ARGS and args[1] == "turn_off":
                data = args[2]
                entity_id = data.get("entity_id") if isinstance(data, dict) else None
                if entity_id == "switch.first":
                    hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_OFF)

        coordinator = await started_coordinator(hass)
        try:
            mock_call = AsyncMock(side_effect=clear_overload_on_first_turn_off)
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                mock_call,
            ):
                await coordinator._strategies.async_overload()  # noqa: SLF001
            turn_off_calls = [
                c for c in mock_call.call_args_list if c[0][1] == "turn_off"
            ]
            assert len(turn_off_calls) == 1
            assert turn_off_calls[0][0][2]["entity_id"] == "switch.first"
        finally:
            await coordinator.async_stop()

    async def test_overload_cleared_during_wait_skips_second_queue(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """If overload clears during 5s wait, second queue is not turned off."""
        hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_ON)
        entry_first = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "First",
                CONF_SWITCH_ENTITY_ID: "switch.first",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.first_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: True,
            },
        )
        entry_first.add_to_hass(hass)
        hass.states.async_set("switch.first", STATE_ON)
        entry_second = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Second",
                CONF_SWITCH_ENTITY_ID: "switch.second",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.second_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: False,
            },
        )
        entry_second.add_to_hass(hass)
        hass.states.async_set("switch.second", STATE_ON)

        async def clear_overload_after_sleep(_seconds: float) -> None:
            hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_OFF)

        coordinator = await started_coordinator(hass)
        try:
            with (
                patch(
                    "custom_components.bestiarium_devices.strategies.asyncio.sleep",
                    side_effect=clear_overload_after_sleep,
                ),
                patch(
                    "homeassistant.core.ServiceRegistry.async_call",
                    new_callable=AsyncMock,
                ) as mock_call,
            ):
                await coordinator._strategies.async_overload()  # noqa: SLF001
                turn_off_calls = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_off"
                ]
                assert len(turn_off_calls) == 1
                assert turn_off_calls[0][0][2]["entity_id"] == "switch.first"
        finally:
            await coordinator.async_stop()

    async def test_all_devices_in_first_queue_second_queue_no_op(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """All devices in first queue: second queue is a no-op."""
        hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_ON)
        entry1 = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "First1",
                CONF_SWITCH_ENTITY_ID: "switch.first1",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.first1_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: True,
            },
        )
        entry1.add_to_hass(hass)
        entry2 = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "First2",
                CONF_SWITCH_ENTITY_ID: "switch.first2",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.first2_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: True,
            },
        )
        entry2.add_to_hass(hass)
        hass.states.async_set("switch.first1", STATE_ON)
        hass.states.async_set("switch.first2", STATE_ON)

        coordinator = await started_coordinator(hass)
        try:
            with (
                patch(
                    "custom_components.bestiarium_devices.strategies.asyncio.sleep",
                    new_callable=AsyncMock,
                ),
                patch(
                    "homeassistant.core.ServiceRegistry.async_call",
                    new_callable=AsyncMock,
                ) as mock_call,
            ):
                await coordinator._strategies.async_overload()  # noqa: SLF001
                turn_off_calls = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_off"
                ]
                assert len(turn_off_calls) == EXPECTED_TURN_OFF_CALLS
                entity_ids = {c[0][2]["entity_id"] for c in turn_off_calls}
                assert entity_ids == {"switch.first1", "switch.first2"}
        finally:
            await coordinator.async_stop()

    async def test_no_first_queue_devices_turn_off_all_immediately(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """No first-queue devices: all turned off immediately, no 5s wait."""
        hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_ON)
        entry1 = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "A",
                CONF_SWITCH_ENTITY_ID: "switch.a",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.a_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: False,
            },
        )
        entry1.add_to_hass(hass)
        entry2 = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "B",
                CONF_SWITCH_ENTITY_ID: "switch.b",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.b_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: False,
            },
        )
        entry2.add_to_hass(hass)
        hass.states.async_set("switch.a", STATE_ON)
        hass.states.async_set("switch.b", STATE_ON)

        coordinator = await started_coordinator(hass)
        try:
            sleep_mock = AsyncMock()
            with (
                patch(
                    "custom_components.bestiarium_devices.strategies.asyncio.sleep",
                    sleep_mock,
                ),
                patch(
                    "homeassistant.core.ServiceRegistry.async_call",
                    new_callable=AsyncMock,
                ) as mock_call,
            ):
                await coordinator._strategies.async_overload()  # noqa: SLF001
                turn_off_calls = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_off"
                ]
                assert len(turn_off_calls) == EXPECTED_TURN_OFF_CALLS
                entity_ids = {c[0][2]["entity_id"] for c in turn_off_calls}
                assert entity_ids == {"switch.a", "switch.b"}
                sleep_mock.assert_not_called()
        finally:
            await coordinator.async_stop()

    async def test_mixed_queues_overload_persists_all_turned_off(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Mixed queues with persistent overload: all devices eventually turn off."""
        hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_ON)
        entry_first = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "First",
                CONF_SWITCH_ENTITY_ID: "switch.first",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.first_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: True,
            },
        )
        entry_first.add_to_hass(hass)
        entry_second = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Second",
                CONF_SWITCH_ENTITY_ID: "switch.second",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.second_power",
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_ALWAYS_ON,
                },
                CONF_OVERLOAD_FIRST_QUEUE: False,
            },
        )
        entry_second.add_to_hass(hass)
        hass.states.async_set("switch.first", STATE_ON)
        hass.states.async_set("switch.second", STATE_ON)

        coordinator = await started_coordinator(hass)
        try:
            with (
                patch(
                    "custom_components.bestiarium_devices.strategies.asyncio.sleep",
                    new_callable=AsyncMock,
                ),
                patch(
                    "homeassistant.core.ServiceRegistry.async_call",
                    new_callable=AsyncMock,
                ) as mock_call,
            ):
                await coordinator._strategies.async_overload()  # noqa: SLF001
                turn_off_calls = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_off"
                ]
                assert len(turn_off_calls) == EXPECTED_TURN_OFF_CALLS
                assert turn_off_calls[0][0][2]["entity_id"] == "switch.first"
                assert turn_off_calls[1][0][2]["entity_id"] == "switch.second"
        finally:
            await coordinator.async_stop()


class TestZoneTransitions:
    """Tests for zone change handling and periodic jobs."""

    async def test_green_sensor_on_starts_60s_periodic_job(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """When green zone is ON, green strategy runs on 60s interval."""
        hass.states.async_set(BINARY_SENSOR_GREEN, STATE_ON)
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Device",
                CONF_SWITCH_ENTITY_ID: "switch.device",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.device_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.device", STATE_OFF)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                # Advance past first green interval so periodic job runs
                async_fire_time_changed(
                    hass,
                    dt_util.utcnow() + timedelta(seconds=GREEN_INTERVAL_SECONDS + 5),
                )
                await hass.async_block_till_done()
                turn_on_calls = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_on"
                ]
                assert len(turn_on_calls) >= 1
                assert turn_on_calls[0][0][2]["entity_id"] == "switch.device"
        finally:
            await coordinator.async_stop()

    async def test_red_sensor_on_starts_5s_periodic_job(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """When red zone is ON, red strategy runs on 5s interval."""
        hass.states.async_set(BINARY_SENSOR_RED, STATE_ON)
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Device",
                CONF_SWITCH_ENTITY_ID: "switch.device",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.device_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.device", STATE_ON)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                # Red periodic runs after first interval (no immediate run on startup)
                async_fire_time_changed(
                    hass,
                    dt_util.utcnow() + timedelta(seconds=RED_INTERVAL_SECONDS + 1),
                )
                await hass.async_block_till_done()
                turn_off_calls = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_off"
                ]
                assert len(turn_off_calls) >= 1
                assert turn_off_calls[0][0][2]["entity_id"] == "switch.device"
        finally:
            await coordinator.async_stop()

    async def test_zone_change_stops_previous_job_and_starts_new_one(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Switching zone stops old periodic job and starts the new one."""
        hass.states.async_set(BINARY_SENSOR_GREEN, STATE_ON)
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Device",
                CONF_SWITCH_ENTITY_ID: "switch.device",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.device_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.device", STATE_OFF)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                # Let green run once
                async_fire_time_changed(
                    hass,
                    dt_util.utcnow() + timedelta(seconds=GREEN_INTERVAL_SECONDS + 5),
                )
                await hass.async_block_till_done()
                turn_on_after_green = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_on"
                ]
                assert len(turn_on_after_green) == 1

                # Switch to red: green job stops, red runs immediately.
                # Mock does not update hass state; set switch ON so red has a device.
                hass.states.async_set("switch.device", STATE_ON)
                hass.states.async_set(BINARY_SENSOR_GREEN, STATE_OFF)
                hass.states.async_set(BINARY_SENSOR_RED, STATE_ON)
                await hass.async_block_till_done()
                turn_off_after_red = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_off"
                ]
                assert len(turn_off_after_red) >= 1

                # Advance time; old green job must not run (no second turn_on)
                async_fire_time_changed(
                    hass,
                    dt_util.utcnow() + timedelta(seconds=GREEN_INTERVAL_SECONDS + 10),
                )
                await hass.async_block_till_done()
                turn_on_total = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_on"
                ]
                assert len(turn_on_total) == 1
        finally:
            await coordinator.async_stop()

    async def test_overload_executes_immediately(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """When overload zone is ON, overload strategy runs immediately (no wait)."""
        hass.states.async_set(BINARY_SENSOR_GREEN, STATE_ON)
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Device",
                CONF_SWITCH_ENTITY_ID: "switch.device",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.device_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.device", STATE_ON)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                hass.states.async_set(BINARY_SENSOR_GREEN, STATE_OFF)
                hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_ON)
                await hass.async_block_till_done()
                turn_off_calls = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_off"
                ]
                assert len(turn_off_calls) == 1
                assert turn_off_calls[0][0][2]["entity_id"] == "switch.device"
        finally:
            await coordinator.async_stop()


def _assert_contract(events: list[dict]) -> None:
    """Each payload has exactly the fields of its event's TypedDict."""
    for event in events:
        assert set(event["data"]) == set(
            EVENT_DATA[event["event_type"]].__annotations__
        )


class TestEventFiring:
    """Tests for automation event firing."""

    async def test_device_turned_on_event(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Green strategy fires device_turned_on with device details."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Eco")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Test Device",
                CONF_SWITCH_ENTITY_ID: "switch.test",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.test_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 6000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.test", STATE_OFF)
        events: list = []

        def record(event: Event) -> None:
            events.append({"event_type": event.event_type, "data": dict(event.data)})

        hass.bus.async_listen(EVENT_DEVICE_TURNED_ON, record)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ):
                await coordinator._strategies.async_green()  # noqa: SLF001
            await hass.async_block_till_done()

            assert len(events) == 1
            assert events[0]["event_type"] == EVENT_DEVICE_TURNED_ON
            assert events[0]["data"]["strategy"] == "green"
            assert events[0]["data"]["device_name"] == "Test Device"
            assert events[0]["data"]["switch_entity_id"] == "switch.test"
            assert events[0]["data"]["priority"] == 6000  # noqa: PLR2004
            _assert_contract(events)
        finally:
            await coordinator.async_stop()

    async def test_device_turned_off_event(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Red strategy fires device_turned_off with device details."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Eco")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Low Priority",
                CONF_SWITCH_ENTITY_ID: "switch.low",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.low_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 2000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.low", STATE_ON)
        events: list = []

        def record(event: Event) -> None:
            events.append({"event_type": event.event_type, "data": dict(event.data)})

        hass.bus.async_listen(EVENT_DEVICE_TURNED_OFF, record)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ):
                await coordinator._strategies.async_red()  # noqa: SLF001
            await hass.async_block_till_done()

            assert len(events) == 1
            assert events[0]["event_type"] == EVENT_DEVICE_TURNED_OFF
            assert events[0]["data"]["strategy"] == "red"
            assert events[0]["data"]["device_name"] == "Low Priority"
            assert events[0]["data"]["switch_entity_id"] == "switch.low"
            assert events[0]["data"]["priority"] == 2000  # noqa: PLR2004
            _assert_contract(events)
        finally:
            await coordinator.async_stop()

    async def test_overload_first_queue_and_cleared_events(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Overload first queue fires overload_first_queue, then overload_cleared."""
        hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_ON)
        entry_first = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "First",
                CONF_SWITCH_ENTITY_ID: "switch.first",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.first_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: True,
            },
        )
        entry_first.add_to_hass(hass)
        entry_second = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Second",
                CONF_SWITCH_ENTITY_ID: "switch.second",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.second_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: False,
            },
        )
        entry_second.add_to_hass(hass)
        hass.states.async_set("switch.first", STATE_ON)
        hass.states.async_set("switch.second", STATE_ON)

        events: list = []

        def record(event: Event) -> None:
            events.append({"event_type": event.event_type, "data": dict(event.data)})

        hass.bus.async_listen(EVENT_OVERLOAD_FIRST_QUEUE, record)
        hass.bus.async_listen(EVENT_OVERLOAD_CLEARED, record)

        async def clear_overload_on_first_turn_off(*args: object) -> None:
            if len(args) >= MIN_SERVICE_CALL_ARGS and args[1] == "turn_off":
                data = args[2]
                entity_id = data.get("entity_id") if isinstance(data, dict) else None
                if entity_id == "switch.first":
                    hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_OFF)
                    await hass.async_block_till_done()

        strategies = await device_strategies(hass)
        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            AsyncMock(side_effect=clear_overload_on_first_turn_off),
        ):
            await strategies.async_overload()
        await hass.async_block_till_done()

        first_queue_events = [
            e for e in events if e["event_type"] == EVENT_OVERLOAD_FIRST_QUEUE
        ]
        cleared_events = [
            e for e in events if e["event_type"] == EVENT_OVERLOAD_CLEARED
        ]

        assert len(first_queue_events) == 1
        assert first_queue_events[0]["data"]["devices_turned_off"] == 1
        assert first_queue_events[0]["data"]["overload_cleared"] is True
        assert "First" in first_queue_events[0]["data"]["device_names"]

        assert len(cleared_events) == 1
        assert cleared_events[0]["data"]["cleared_after"] == "first_queue"
        assert cleared_events[0]["data"]["first_queue_count"] == 1
        _assert_contract(events)

    async def test_overload_second_queue_event(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Overload second queue fires overload_second_queue with counts."""
        hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_ON)
        entry_first = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "First",
                CONF_SWITCH_ENTITY_ID: "switch.first",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.first_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: True,
            },
        )
        entry_first.add_to_hass(hass)
        entry_second = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Second",
                CONF_SWITCH_ENTITY_ID: "switch.second",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.second_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: False,
            },
        )
        entry_second.add_to_hass(hass)
        hass.states.async_set("switch.first", STATE_ON)
        hass.states.async_set("switch.second", STATE_ON)

        events: list = []

        def record(event: Event) -> None:
            events.append({"event_type": event.event_type, "data": dict(event.data)})

        hass.bus.async_listen(EVENT_OVERLOAD_SECOND_QUEUE, record)

        strategies = await device_strategies(hass)
        with (
            patch(
                "custom_components.bestiarium_devices.strategies.asyncio.sleep",
                new_callable=AsyncMock,
            ),
            patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ),
        ):
            await strategies.async_overload()
        await hass.async_block_till_done()

        second_queue_events = [
            e for e in events if e["event_type"] == EVENT_OVERLOAD_SECOND_QUEUE
        ]
        assert len(second_queue_events) == 1
        assert second_queue_events[0]["data"]["first_queue_count"] == 1
        assert second_queue_events[0]["data"]["second_queue_count"] == 1
        assert "Second" in second_queue_events[0]["data"]["device_names"]
        _assert_contract(events)

    async def test_mode_applied_event(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Applying mode priorities fires mode_applied with device counts."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Eco")
        entry_off = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Off Device",
                CONF_SWITCH_ENTITY_ID: "switch.off",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.off_power",
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_OFF,
                },
            },
        )
        entry_off.add_to_hass(hass)
        entry_always = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Always Device",
                CONF_SWITCH_ENTITY_ID: "switch.always",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.always_power",
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_ALWAYS_ON,
                },
            },
        )
        entry_always.add_to_hass(hass)
        hass.states.async_set("switch.off", STATE_ON)
        hass.states.async_set("switch.always", STATE_OFF)

        events: list = []

        def record(event: Event) -> None:
            events.append({"event_type": event.event_type, "data": dict(event.data)})

        hass.bus.async_listen(EVENT_MODE_APPLIED, record)

        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ):
                await coordinator._strategies.async_apply_mode()  # noqa: SLF001
            await hass.async_block_till_done()

            assert len(events) == 1
            assert events[0]["event_type"] == EVENT_MODE_APPLIED
            assert events[0]["data"]["mode"] == "Eco"
            assert len(events[0]["data"]["devices_turned_off"]) == 1
            assert len(events[0]["data"]["devices_turned_on"]) == 1
            _assert_contract(events)
        finally:
            await coordinator.async_stop()

    @pytest.mark.parametrize(
        ("power_readings", "expected_zone_changes"),
        [
            (["4000"], [("green", "yellow")]),
            (["4600"], [("green", "red")]),
            (["4000", "4600"], [("green", "yellow"), ("yellow", "red")]),
            (["4600", "4000"], [("green", "red"), ("red", "yellow")]),
        ],
    )
    async def test_zone_changes_from_power_sensor(
        self,
        hass: HomeAssistant,
        power_readings: list[str],
        expected_zone_changes: list[tuple[str, str]],
        zone_changes: list[tuple[str, str]],
    ) -> None:
        """Power crossing thresholds: one zone change per real transition."""
        hass.states.async_set("sensor.power_meter", "1000")
        mode_entry = MockConfigEntry(
            domain=DOMAIN_BESTIARIUM_MODE,
            unique_id="bestiarium_mode",
            data={
                "options": [{"name": "Eco", "icon": "mdi:leaf"}],
                "ac_load_limits": {
                    "ac_power_sensor": "sensor.power_meter",
                    "ac_load_limit_w": 5000,
                    "ac_yellow_zone_percent": 75,
                    "ac_red_zone_percent": 90,
                    "ac_overload_recovery_seconds": 10,
                },
            },
        )
        mode_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mode_entry.entry_id)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
        await hass.async_block_till_done()
        assert hass.states.get(BINARY_SENSOR_GREEN).state == STATE_ON

        coordinator = await started_coordinator(hass)
        try:
            for power in power_readings:
                hass.states.async_set("sensor.power_meter", power)
                await hass.async_block_till_done()

            assert zone_changes == expected_zone_changes
        finally:
            await coordinator.async_stop()


POWER_SENSOR = "sensor.grid_main_load_em0_power"


async def _setup_real_mode(
    hass: HomeAssistant,
    power: str,
    limit: int = 5000,
    options: tuple[str, ...] = ("Grid on",),
) -> None:
    """Set up the real bestiarium_mode with the owner's zone settings."""
    hass.states.async_set(POWER_SENSOR, power)
    mode_entry = MockConfigEntry(
        domain=DOMAIN_BESTIARIUM_MODE,
        unique_id="bestiarium_mode",
        data={
            "options": [{"name": name, "icon": "mdi:home"} for name in options],
            "ac_load_limits": {
                "ac_power_sensor": POWER_SENSOR,
                "ac_load_limit_w": limit,
                "ac_yellow_zone_percent": 60,
                "ac_red_zone_percent": 85,
                "ac_overload_recovery_seconds": 30,
            },
        },
    )
    mode_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mode_entry.entry_id)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()


async def test_zone_tells_pause_and_no_data(
    hass: HomeAssistant, zone_changes: list[tuple[str, str]]
) -> None:
    """The balancer's zone: the pause before green and no power data apart."""
    await _setup_real_mode(hass, "3500")
    coordinator = await started_coordinator(hass)
    try:
        hass.states.async_set(POWER_SENSOR, "2000")
        await hass.async_block_till_done()
        assert coordinator.active_zone == ActiveZone.PAUSE

        hass.states.async_set(POWER_SENSOR, STATE_UNAVAILABLE)
        await hass.async_block_till_done()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
        await hass.async_block_till_done()
        assert zone_changes == [("yellow", "pause"), ("pause", "no_data")]
    finally:
        await coordinator.async_stop()


class TestLoadLimitChange:
    """Changing the bestiarium_mode load limit at runtime, seen by the balancer."""

    async def _set_limit(self, hass: HomeAssistant, value: int) -> None:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": "number.power_load_limit", "value": value},
            blocking=True,
        )
        await hass.async_block_till_done()

    @pytest.mark.parametrize(
        ("power", "old_limit", "new_limit", "expected_zone_changes"),
        [
            ("6000", 5000, 8800, [("overload", "yellow")]),
            ("6000", 8800, 5000, [("yellow", "overload")]),
            ("4500", 8800, 5000, [("green", "red")]),
            ("4500", 5000, 7000, [("red", "yellow")]),
        ],
    )
    async def test_limit_change_fires_single_zone_change(  # noqa: PLR0913
        self,
        hass: HomeAssistant,
        power: str,
        old_limit: int,
        new_limit: int,
        expected_zone_changes: list[tuple[str, str]],
        zone_changes: list[tuple[str, str]],
    ) -> None:
        """At constant load, a limit change moves straight to the final zone."""
        await _setup_real_mode(hass, power, old_limit)
        coordinator = await started_coordinator(hass)
        try:
            await self._set_limit(hass, new_limit)

            assert zone_changes == expected_zone_changes
        finally:
            await coordinator.async_stop()

    async def test_raised_limit_waits_for_green_recovery(
        self,
        hass: HomeAssistant,
        zone_changes: list[tuple[str, str]],
    ) -> None:
        """Raising the limit above the load waits out the pause before green."""
        await _setup_real_mode(hass, "4500", 5000)
        coordinator = await started_coordinator(hass)
        try:
            await self._set_limit(hass, 8800)
            assert zone_changes == [("red", "pause")]

            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
            await hass.async_block_till_done()
            assert zone_changes == [("red", "pause"), ("pause", "green")]
        finally:
            await coordinator.async_stop()

    async def test_raised_limit_keeps_devices_on(
        self, hass: HomeAssistant, zone_changes: list[tuple[str, str]]
    ) -> None:
        """With the limit raised to 8800 W, 6000 W turns no device off."""
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Grid on")
        MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
                CONF_DEVICE_NAME: "Heater",
                CONF_SWITCH_ENTITY_ID: "switch.heater",
                CONF_POWER_SENSOR_ENTITY_ID: "sensor.heater_power",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 100},
            },
        ).add_to_hass(hass)
        hass.states.async_set("switch.heater", STATE_ON)
        await _setup_real_mode(hass, "4000", 5000)
        coordinator = await started_coordinator(hass)
        turned_off: list[Event] = []

        @callback
        def record_turned_off(event: Event) -> None:
            turned_off.append(event)

        hass.bus.async_listen(EVENT_DEVICE_TURNED_OFF, record_turned_off)
        try:
            await self._set_limit(hass, 8800)
            hass.states.async_set(POWER_SENSOR, "6000")
            async_fire_time_changed(
                hass, dt_util.utcnow() + timedelta(seconds=RED_INTERVAL_SECONDS + 1)
            )
            await hass.async_block_till_done()
        finally:
            await coordinator.async_stop()

        assert all(zone != "red" for _, zone in zone_changes)
        assert turned_off == []
        assert hass.states.get("switch.heater").state == STATE_ON


def _add_device(
    hass: HomeAssistant,
    name: str,
    switch_entity_id: str,
    *,
    first_queue: bool = False,
    priorities: dict[str, int] | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL,
            CONF_DEVICE_NAME: name,
            CONF_SWITCH_ENTITY_ID: switch_entity_id,
            CONF_POWER_SENSOR_ENTITY_ID: f"{switch_entity_id}_power",
            CONF_LOAD_PRIORITIES: priorities or {CONF_DEFAULT_PRIORITY: 100},
            CONF_OVERLOAD_FIRST_QUEUE: first_queue,
        },
    )
    entry.add_to_hass(hass)
    hass.states.async_set(switch_entity_id, STATE_ON)
    return entry


def _record_events(hass: HomeAssistant, event_type: str) -> list[Event]:
    events: list[Event] = []

    @callback
    def record(event: Event) -> None:
        events.append(event)

    hass.bus.async_listen(event_type, record)
    return events


def _mode_entity_id(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN_BESTIARIUM_MODE, unique_id
    )
    assert entity_id is not None
    return entity_id


class TestModeEntitiesByUniqueId:
    """The balancer finds bestiarium_mode entities whatever their entity_id."""

    async def test_fresh_install_overload_runs_emergency_shutdown(
        self, hass: HomeAssistant
    ) -> None:
        """On a fresh install the overload sensor is binary_sensor.overload."""
        await _setup_real_mode(hass, "1000")
        assert (
            _mode_entity_id(hass, "binary_sensor", "power_load_overload")
            == "binary_sensor.overload"
        )
        _add_device(hass, "Boiler", "switch.boiler", first_queue=True)
        turn_off = async_mock_service(hass, "switch", "turn_off")
        first_queue = _record_events(hass, EVENT_OVERLOAD_FIRST_QUEUE)
        coordinator = await started_coordinator(hass)
        try:
            with patch(
                "custom_components.bestiarium_devices.strategies.asyncio.sleep",
                new_callable=AsyncMock,
            ):
                hass.states.async_set(POWER_SENSOR, "5200")
                await hass.async_block_till_done()
        finally:
            await coordinator.async_stop()

        assert len(first_queue) == 1
        assert [call.data["entity_id"] for call in turn_off] == ["switch.boiler"]

    async def test_renamed_red_sensor_runs_red_strategy(
        self,
        hass: HomeAssistant,
        zone_changes: list[tuple[str, str]],
    ) -> None:
        """After renaming the red zone sensor at runtime, red still sheds load."""
        await _setup_real_mode(hass, "1000")
        _add_device(hass, "Boiler", "switch.boiler")
        turn_off = async_mock_service(hass, "switch", "turn_off")
        coordinator = await started_coordinator(hass)
        try:
            er.async_get(hass).async_update_entity(
                _mode_entity_id(hass, "binary_sensor", "power_load_red"),
                new_entity_id="binary_sensor.zona_chervona",
            )
            await hass.async_block_till_done()

            hass.states.async_set(POWER_SENSOR, "4500")
            await hass.async_block_till_done()
        finally:
            await coordinator.async_stop()

        assert hass.states.get("binary_sensor.zona_chervona").state == STATE_ON
        assert zone_changes == [("green", "red")]
        assert [call.data["entity_id"] for call in turn_off] == ["switch.boiler"]

    async def test_rename_during_active_zone_keeps_zone(
        self,
        hass: HomeAssistant,
        zone_changes: list[tuple[str, str]],
    ) -> None:
        """Renaming the active zone sensor neither changes nor loses the zone."""
        await _setup_real_mode(hass, "4500")
        coordinator = await started_coordinator(hass)
        try:
            er.async_get(hass).async_update_entity(
                _mode_entity_id(hass, "binary_sensor", "power_load_red"),
                new_entity_id="binary_sensor.zona_chervona",
            )
            await hass.async_block_till_done()
            assert zone_changes == []
            assert coordinator.active_zone == ActiveZone.RED

            hass.states.async_set(POWER_SENSOR, "3500")
            await hass.async_block_till_done()
        finally:
            await coordinator.async_stop()

        assert zone_changes == [("red", "yellow")]

    async def test_renamed_mode_select_drives_priorities(
        self, hass: HomeAssistant
    ) -> None:
        """After renaming the mode select, priorities follow the new mode."""
        await _setup_real_mode(hass, "1000", options=("Grid on", "Grid off"))
        heater = _add_device(
            hass,
            "Heater",
            "switch.heater",
            priorities={CONF_DEFAULT_PRIORITY: 400, "Grid off": LOAD_PRIORITY_OFF},
        )
        turn_off = async_mock_service(hass, "switch", "turn_off")
        mode_applied = _record_events(hass, EVENT_MODE_APPLIED)
        coordinator = await started_coordinator(hass)
        try:
            er.async_get(hass).async_update_entity(
                _mode_entity_id(hass, "select", "select"),
                new_entity_id="select.rezhym",
            )
            await hass.async_block_till_done()

            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": "select.rezhym", "option": "Grid off"},
                blocking=True,
            )
            await hass.async_block_till_done()
        finally:
            await coordinator.async_stop()

        assert get_current_priority(hass, heater) == LOAD_PRIORITY_OFF
        assert [event.data["mode"] for event in mode_applied] == ["Grid off"]
        assert [call.data["entity_id"] for call in turn_off] == ["switch.heater"]

    async def test_without_load_limits_a_repair_then_it_comes_alive(
        self, hass: HomeAssistant
    ) -> None:
        """
        Criterion 6: no zone sensors is a Repairs issue, never a notification.

        Once the AC load limits are set, the balancer follows the zones without
        a restart of HA, and the issue is gone.
        """
        mode_entry = MockConfigEntry(
            domain=DOMAIN_BESTIARIUM_MODE,
            unique_id="bestiarium_mode",
            data={"options": [{"name": "Grid on", "icon": "mdi:home"}]},
        )
        mode_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mode_entry.entry_id)
        await hass.async_block_till_done()
        notifications = async_mock_service(hass, "persistent_notification", "create")
        issues = ir.async_get(hass)
        coordinator = await started_coordinator(hass)
        try:
            assert coordinator.active_zone == ActiveZone.NO_DATA
            assert issues.async_get_issue(DOMAIN, ISSUE_NO_LOAD_ZONES) is not None

            hass.states.async_set(POWER_SENSOR, "3500")
            hass.config_entries.async_update_entry(
                mode_entry,
                data={
                    **mode_entry.data,
                    "ac_load_limits": {
                        "ac_power_sensor": POWER_SENSOR,
                        "ac_load_limit_w": 5000,
                        "ac_yellow_zone_percent": 60,
                        "ac_red_zone_percent": 85,
                    },
                },
            )
            await hass.config_entries.async_reload(mode_entry.entry_id)
            await hass.async_block_till_done()

            assert coordinator.active_zone == ActiveZone.YELLOW
            assert issues.async_get_issue(DOMAIN, ISSUE_NO_LOAD_ZONES) is None
        finally:
            await coordinator.async_stop()
        assert notifications == []


async def test_mode_select_alone_does_not_bring_the_balancer_alive(
    hass: HomeAssistant,
) -> None:
    """Review 06, 4: no zone sensors yet: the issue stays, the balancer waits."""
    coordinator = await started_coordinator(hass)
    issues = ir.async_get(hass)
    try:
        assert issues.async_get_issue(DOMAIN, ISSUE_NO_LOAD_ZONES) is not None
        er.async_get(hass).async_get_or_create(
            "select", DOMAIN_BESTIARIUM_MODE, MODE_SELECT_UNIQUE_ID
        )
        await hass.async_block_till_done()

        assert issues.async_get_issue(DOMAIN, ISSUE_NO_LOAD_ZONES) is not None
        assert coordinator.active_zone == ActiveZone.NO_DATA
    finally:
        await coordinator.async_stop()


class TestOverloadFailSafe:
    """An unreadable overload sensor never counts as a cleared overload."""

    @pytest.mark.parametrize("sensor_state", [STATE_UNAVAILABLE, STATE_UNKNOWN, None])
    @pytest.mark.parametrize("lost", ["after_first_queue", "during_wait"])
    async def test_unreadable_overload_sensor_runs_second_queue(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
        sensor_state: str | None,
        lost: str,
    ) -> None:
        """The second queue still shuts down when the sensor cannot be read."""
        hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_ON)
        _add_device(hass, "First", "switch.first", first_queue=True)
        _add_device(hass, "Second", "switch.second")
        second_queue = _record_events(hass, EVENT_OVERLOAD_SECOND_QUEUE)

        def lose_overload_sensor() -> None:
            if sensor_state is None:
                hass.states.async_remove(BINARY_SENSOR_OVERLOAD)
            else:
                hass.states.async_set(BINARY_SENSOR_OVERLOAD, sensor_state)

        async def on_service_call(*args: object) -> None:
            if lost == "after_first_queue" and args[2] == {"entity_id": "switch.first"}:
                lose_overload_sensor()

        async def on_wait(_seconds: float) -> None:
            if lost == "during_wait":
                lose_overload_sensor()

        strategies = await device_strategies(hass)
        with (
            patch(
                "custom_components.bestiarium_devices.strategies.asyncio.sleep",
                side_effect=on_wait,
            ),
            patch(
                "homeassistant.core.ServiceRegistry.async_call",
                AsyncMock(side_effect=on_service_call),
            ) as mock_call,
        ):
            await strategies.async_overload()
        await hass.async_block_till_done()

        turned_off = [c[0][2]["entity_id"] for c in mock_call.call_args_list]
        assert turned_off == ["switch.first", "switch.second"]
        assert len(second_queue) == 1


async def test_removed_load_limits_leave_no_orphan_zones(hass: HomeAssistant) -> None:
    """Review 06 debt: the zone sensors go, the balancer waits, Repairs tells."""
    await _setup_real_mode(hass, "1000")
    coordinator = await started_coordinator(hass)
    mode = hass.config_entries.async_entries(DOMAIN_BESTIARIUM_MODE)[0]
    issues = ir.async_get(hass)
    try:
        assert issues.async_get_issue(DOMAIN, ISSUE_NO_LOAD_ZONES) is None
        hass.config_entries.async_update_entry(
            mode,
            data={key: v for key, v in mode.data.items() if key != "ac_load_limits"},
        )
        await hass.config_entries.async_reload(mode.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        for zone in ("green", "yellow", "red", "overload"):
            unique_id = f"power_load_{zone}"
            assert (
                registry.async_get_entity_id(
                    "binary_sensor", DOMAIN_BESTIARIUM_MODE, unique_id
                )
                is None
            )
        assert coordinator.active_zone == ActiveZone.NO_DATA
        assert issues.async_get_issue(DOMAIN, ISSUE_NO_LOAD_ZONES) is not None
    finally:
        await coordinator.async_stop()
