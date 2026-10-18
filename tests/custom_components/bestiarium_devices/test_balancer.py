"""Tests for the Bestiarium Devices balancing coordinator."""

# ruff: noqa: S101

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.bestiarium_devices.balancer import (
    BINARY_SENSOR_GREEN,
    BINARY_SENSOR_OVERLOAD,
    BINARY_SENSOR_RED,
    BINARY_SENSOR_YELLOW,
    ActiveZone,
    BalancingCoordinator,
)
from custom_components.bestiarium_devices.const import (
    BESTIARIUM_MODE_SELECT_ENTITY_ID,
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_OVERLOAD_FIRST_QUEUE,
    CONF_SWITCH_ENTITY_ID,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    DOMAIN_BESTIARIUM_MODE,
    EVENT_DEVICE_TURNED_OFF,
    EVENT_DEVICE_TURNED_ON,
    EVENT_MODE_APPLIED,
    EVENT_OVERLOAD_CLEARED,
    EVENT_OVERLOAD_FIRST_QUEUE,
    EVENT_OVERLOAD_SECOND_QUEUE,
    EVENT_ZONE_CHANGED,
    GREEN_INTERVAL_SECONDS,
    LOAD_PRIORITY_ALWAYS_ON,
    LOAD_PRIORITY_OFF,
    RED_INTERVAL_SECONDS,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

EXPECTED_TURN_OFF_CALLS = 2
MIN_SERVICE_CALL_ARGS = 3


@pytest.fixture
def zone_binary_sensors(hass: HomeAssistant) -> None:
    """Register zone binary sensors so coordinator can start (one must exist)."""
    hass.states.async_set(BINARY_SENSOR_GREEN, STATE_OFF)
    hass.states.async_set(BINARY_SENSOR_YELLOW, STATE_OFF)
    hass.states.async_set(BINARY_SENSOR_RED, STATE_OFF)
    hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_OFF)


@pytest.fixture
def zone_green_on(hass: HomeAssistant, zone_binary_sensors: None) -> None:  # noqa: ARG001
    """Set green zone sensor ON so coordinator has an active zone."""
    hass.states.async_set(BINARY_SENSOR_GREEN, STATE_ON)


@pytest.fixture
def mode_select_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create Bestiarium Mode config entry and set select entity state."""
    entry = MockConfigEntry(
        domain=DOMAIN_BESTIARIUM_MODE,
        unique_id="bestiarium_mode",
        data={
            "options": [
                {"name": "Eco", "description": "Energy saving", "icon": "mdi:leaf"},
                {"name": "Boost", "description": "High priority", "icon": "mdi:flash"},
            ],
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
        assert coordinator.active_zone == ActiveZone.NONE

    async def test_coordinator_initial_active_zone_when_green_on(
        self,
        hass: HomeAssistant,
        zone_green_on: None,  # noqa: ARG002
    ) -> None:
        """Test coordinator detects green zone when green sensor is ON."""
        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
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
        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
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
        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            assert coordinator.is_running
            assert coordinator.active_zone == ActiveZone.NONE
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
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_OFF,
                },
            },
        )
        entry.add_to_hass(hass)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._apply_mode_priorities()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_ALWAYS_ON,
                },
            },
        )
        entry.add_to_hass(hass)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._apply_mode_priorities()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 7000},
            },
        )
        entry_high.add_to_hass(hass)
        hass.states.async_set("switch.high", STATE_OFF)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._execute_green_strategy()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.already_on", STATE_ON)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._execute_green_strategy()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_ALWAYS_ON,
                },
            },
        )
        entry_always.add_to_hass(hass)
        hass.states.async_set("switch.always_priority", STATE_OFF)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._execute_green_strategy()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 3000},
            },
        )
        entry_low.add_to_hass(hass)
        hass.states.async_set("switch.low", STATE_ON)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._execute_red_strategy()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.already_off", STATE_OFF)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._execute_red_strategy()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_ALWAYS_ON,
                },
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.always_on", STATE_ON)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._execute_red_strategy()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_ALWAYS_ON,
                },
            },
        )
        entry_always.add_to_hass(hass)
        hass.states.async_set("switch.always_on", STATE_ON)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._execute_overload_strategy()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": LOAD_PRIORITY_ALWAYS_ON,
                },
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.always_on", STATE_ON)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ) as mock_call:
                await coordinator._execute_red_strategy()  # noqa: SLF001
                turn_off_after_red = [
                    c for c in mock_call.call_args_list if c[0][1] == "turn_off"
                ]
                assert len(turn_off_after_red) == 0

                mock_call.reset_mock()
                await coordinator._execute_overload_strategy()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: False,
            },
        )
        entry_second.add_to_hass(hass)
        hass.states.async_set("switch.second", STATE_ON)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with (
                patch(
                    "custom_components.bestiarium_devices.balancer.asyncio.sleep",
                    new_callable=AsyncMock,
                ),
                patch(
                    "homeassistant.core.ServiceRegistry.async_call",
                    new_callable=AsyncMock,
                ) as mock_call,
            ):
                await coordinator._execute_overload_strategy()  # noqa: SLF001
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

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            mock_call = AsyncMock(side_effect=clear_overload_on_first_turn_off)
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                mock_call,
            ):
                await coordinator._execute_overload_strategy()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: False,
            },
        )
        entry_second.add_to_hass(hass)
        hass.states.async_set("switch.second", STATE_ON)

        async def clear_overload_after_sleep(_seconds: float) -> None:
            hass.states.async_set(BINARY_SENSOR_OVERLOAD, STATE_OFF)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with (
                patch(
                    "custom_components.bestiarium_devices.balancer.asyncio.sleep",
                    side_effect=clear_overload_after_sleep,
                ),
                patch(
                    "homeassistant.core.ServiceRegistry.async_call",
                    new_callable=AsyncMock,
                ) as mock_call,
            ):
                await coordinator._execute_overload_strategy()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: True,
            },
        )
        entry2.add_to_hass(hass)
        hass.states.async_set("switch.first1", STATE_ON)
        hass.states.async_set("switch.first2", STATE_ON)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with (
                patch(
                    "custom_components.bestiarium_devices.balancer.asyncio.sleep",
                    new_callable=AsyncMock,
                ),
                patch(
                    "homeassistant.core.ServiceRegistry.async_call",
                    new_callable=AsyncMock,
                ) as mock_call,
            ):
                await coordinator._execute_overload_strategy()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
                CONF_OVERLOAD_FIRST_QUEUE: False,
            },
        )
        entry2.add_to_hass(hass)
        hass.states.async_set("switch.a", STATE_ON)
        hass.states.async_set("switch.b", STATE_ON)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            sleep_mock = AsyncMock()
            with (
                patch(
                    "custom_components.bestiarium_devices.balancer.asyncio.sleep",
                    sleep_mock,
                ),
                patch(
                    "homeassistant.core.ServiceRegistry.async_call",
                    new_callable=AsyncMock,
                ) as mock_call,
            ):
                await coordinator._execute_overload_strategy()  # noqa: SLF001
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

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with (
                patch(
                    "custom_components.bestiarium_devices.balancer.asyncio.sleep",
                    new_callable=AsyncMock,
                ),
                patch(
                    "homeassistant.core.ServiceRegistry.async_call",
                    new_callable=AsyncMock,
                ) as mock_call,
            ):
                await coordinator._execute_overload_strategy()  # noqa: SLF001
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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.device", STATE_OFF)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.device", STATE_ON)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.device", STATE_OFF)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 5000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.device", STATE_ON)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
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


class TestEventFiring:
    """Tests for automation event firing."""

    async def test_zone_changed_event(
        self,
        hass: HomeAssistant,
        zone_binary_sensors: None,  # noqa: ARG002
        mode_select_entry: MockConfigEntry,  # noqa: ARG002
    ) -> None:
        """Zone change fires zone_changed with previous_zone and zone."""
        hass.states.async_set(BINARY_SENSOR_GREEN, STATE_ON)
        events: list = []

        def record(event: Event) -> None:
            events.append({"event_type": event.event_type, "data": dict(event.data)})

        hass.bus.async_listen(EVENT_ZONE_CHANGED, record)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            hass.states.async_set(BINARY_SENSOR_RED, STATE_ON)
            hass.states.async_set(BINARY_SENSOR_GREEN, STATE_OFF)
            await hass.async_block_till_done()

            assert len(events) == 1
            assert events[0]["event_type"] == EVENT_ZONE_CHANGED
            assert events[0]["data"]["previous_zone"] == "green"
            assert events[0]["data"]["zone"] == "red"
        finally:
            await coordinator.async_stop()

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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 6000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.test", STATE_OFF)
        events: list = []

        def record(event: Event) -> None:
            events.append({"event_type": event.event_type, "data": dict(event.data)})

        hass.bus.async_listen(EVENT_DEVICE_TURNED_ON, record)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ):
                await coordinator._execute_green_strategy()  # noqa: SLF001
            await hass.async_block_till_done()

            assert len(events) == 1
            assert events[0]["event_type"] == EVENT_DEVICE_TURNED_ON
            assert events[0]["data"]["strategy"] == "green"
            assert events[0]["data"]["device_name"] == "Test Device"
            assert events[0]["data"]["switch_entity_id"] == "switch.test"
            assert events[0]["data"]["priority"] == 6000  # noqa: PLR2004
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
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 2000},
            },
        )
        entry.add_to_hass(hass)
        hass.states.async_set("switch.low", STATE_ON)
        events: list = []

        def record(event: Event) -> None:
            events.append({"event_type": event.event_type, "data": dict(event.data)})

        hass.bus.async_listen(EVENT_DEVICE_TURNED_OFF, record)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ):
                await coordinator._execute_red_strategy()  # noqa: SLF001
            await hass.async_block_till_done()

            assert len(events) == 1
            assert events[0]["event_type"] == EVENT_DEVICE_TURNED_OFF
            assert events[0]["data"]["strategy"] == "red"
            assert events[0]["data"]["device_name"] == "Low Priority"
            assert events[0]["data"]["switch_entity_id"] == "switch.low"
            assert events[0]["data"]["priority"] == 2000  # noqa: PLR2004
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

        coordinator = BalancingCoordinator(hass)
        with patch(
            "homeassistant.core.ServiceRegistry.async_call",
            AsyncMock(side_effect=clear_overload_on_first_turn_off),
        ):
            await coordinator._execute_overload_strategy()  # noqa: SLF001
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

        coordinator = BalancingCoordinator(hass)
        with (
            patch(
                "custom_components.bestiarium_devices.balancer.asyncio.sleep",
                new_callable=AsyncMock,
            ),
            patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ),
        ):
            await coordinator._execute_overload_strategy()  # noqa: SLF001
        await hass.async_block_till_done()

        second_queue_events = [
            e for e in events if e["event_type"] == EVENT_OVERLOAD_SECOND_QUEUE
        ]
        assert len(second_queue_events) == 1
        assert second_queue_events[0]["data"]["first_queue_count"] == 1
        assert second_queue_events[0]["data"]["second_queue_count"] == 1
        assert "Second" in second_queue_events[0]["data"]["device_names"]

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

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            with patch(
                "homeassistant.core.ServiceRegistry.async_call",
                new_callable=AsyncMock,
            ):
                await coordinator._apply_mode_priorities()  # noqa: SLF001
            await hass.async_block_till_done()

            assert len(events) == 1
            assert events[0]["event_type"] == EVENT_MODE_APPLIED
            assert events[0]["data"]["mode"] == "Eco"
            assert len(events[0]["data"]["devices_turned_off"]) == 1
            assert len(events[0]["data"]["devices_turned_on"]) == 1
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
    async def test_zone_changed_events_from_power_sensor(
        self,
        hass: HomeAssistant,
        power_readings: list[str],
        expected_zone_changes: list[tuple[str, str]],
    ) -> None:
        """Power crossing thresholds fires one zone_changed per real transition."""
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

        zone_changes: list[tuple[str, str]] = []

        @callback
        def record(event: Event) -> None:
            zone_changes.append((event.data["previous_zone"], event.data["zone"]))

        hass.bus.async_listen(EVENT_ZONE_CHANGED, record)

        coordinator = BalancingCoordinator(hass)
        await coordinator.async_start()
        try:
            for power in power_readings:
                hass.states.async_set("sensor.power_meter", power)
                await hass.async_block_till_done()

            assert zone_changes == expected_zone_changes
        finally:
            await coordinator.async_stop()
