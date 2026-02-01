"""Balancing coordinator for bestiarium_devices."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .const import (
    BESTIARIUM_MODE_SELECT_ENTITY_ID,
    BINARY_SENSOR_GREEN,
    BINARY_SENSOR_OVERLOAD,
    BINARY_SENSOR_RED,
    BINARY_SENSOR_YELLOW,
    CONF_SWITCH_ENTITY_ID,
    DOMAIN,
    GREEN_INTERVAL_SECONDS,
    LOAD_PRIORITY_ALWAYS_ON,
    LOAD_PRIORITY_OFF,
    LOGGER,
    RED_INTERVAL_SECONDS,
)
from .helpers import (
    get_current_priority,
    get_device_id_from_entry,
    get_device_name_from_entry,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

# Notification ID for missing sensors warning
NOTIFICATION_ID_MISSING_SENSORS = "bestiarium_devices_missing_sensors"


class ActiveZone(StrEnum):
    """Active power load zone for balancing."""

    NONE = "none"
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    OVERLOAD = "overload"


class BalancingCoordinator:
    """Coordinator for device consumption balancing based on power load zones."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the balancing coordinator."""
        self._hass = hass

        # Current active zone
        self._active_zone: ActiveZone = ActiveZone.NONE

        # Reference to cancel periodic job callback
        self._job_cancel: Callable[[], None] | None = None

        # List of unsubscribe callbacks for state change listeners
        self._subscriptions: list[Callable[[], None]] = []

        # Flag to track if coordinator is running
        self._running: bool = False

    @property
    def active_zone(self) -> ActiveZone:
        """Return the current active zone."""
        return self._active_zone

    @property
    def is_running(self) -> bool:
        """Return True if the coordinator is running."""
        return self._running

    async def async_start(self) -> None:
        """Start the balancing coordinator."""
        if self._running:
            LOGGER.warning("BalancingCoordinator is already running")
            return

        LOGGER.debug("Starting BalancingCoordinator")
        self._running = True

        # Check if binary sensors exist before subscribing
        if not self._check_binary_sensors_exist():
            LOGGER.warning(
                "Power load binary sensors not found. "
                "Balancing functionality will be disabled. "
                "Ensure bestiarium_mode is configured with AC load limits."
            )
            await self._send_missing_sensors_notification()
            # Keep running but without subscriptions - will just be inactive
            return

        # Subscribe to zone binary sensor state changes
        zone_sensors = [
            BINARY_SENSOR_GREEN,
            BINARY_SENSOR_YELLOW,
            BINARY_SENSOR_RED,
            BINARY_SENSOR_OVERLOAD,
        ]
        unsubscribe = async_track_state_change_event(
            self._hass,
            zone_sensors,
            self._on_zone_changed,
        )
        self._subscriptions.append(unsubscribe)

        # Subscribe to mode select state changes
        mode_unsubscribe = async_track_state_change_event(
            self._hass,
            [BESTIARIUM_MODE_SELECT_ENTITY_ID],
            self._on_mode_changed,
        )
        self._subscriptions.append(mode_unsubscribe)

        # Determine initial active zone (but don't execute strategy per Q7)
        self._active_zone = self._determine_active_zone()
        LOGGER.debug("Initial active zone: %s", self._active_zone)

        # Start periodic job based on initial zone (per Q7: wait for first interval)
        # Note: For initial overload, we still execute immediately as it's emergency
        if self._active_zone == ActiveZone.OVERLOAD:
            # Even on startup, overload is an emergency - execute immediately
            self._hass.async_create_task(self._execute_overload_strategy())
        else:
            # Start periodic job (will wait for interval before first execution)
            self._start_periodic_job(self._active_zone)

    async def async_stop(self) -> None:
        """Stop the balancing coordinator."""
        if not self._running:
            LOGGER.warning("BalancingCoordinator is not running")
            return

        LOGGER.debug("Stopping BalancingCoordinator")

        # Stop any running periodic job
        if self._job_cancel:
            self._job_cancel()
            self._job_cancel = None

        # Unsubscribe from all state change listeners
        for unsubscribe in self._subscriptions:
            unsubscribe()
        self._subscriptions.clear()

        self._active_zone = ActiveZone.NONE
        self._running = False

    def _check_binary_sensors_exist(self) -> bool:
        """Check if the power load binary sensors exist."""
        # Check if at least one of the zone sensors exists
        # If bestiarium_mode is configured with AC load limits, all 4 should exist
        for sensor_id in [
            BINARY_SENSOR_GREEN,
            BINARY_SENSOR_YELLOW,
            BINARY_SENSOR_RED,
            BINARY_SENSOR_OVERLOAD,
        ]:
            state = self._hass.states.get(sensor_id)
            if state is not None:
                return True
        return False

    async def _send_missing_sensors_notification(self) -> None:
        """Send a persistent notification about missing binary sensors."""
        try:
            await self._hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Bestiarium Devices - Balancing Disabled",
                    "message": (
                        "Power load binary sensors not found. "
                        "Device consumption balancing is disabled.\n\n"
                        "To enable balancing, configure AC load limits in "
                        "the Bestiarium Mode integration."
                    ),
                    "notification_id": NOTIFICATION_ID_MISSING_SENSORS,
                },
            )
        except Exception:  # noqa: BLE001
            # Service may not be available (e.g., during testing)
            LOGGER.debug("Could not send persistent notification")

    def _determine_active_zone(self) -> ActiveZone:
        """Determine which zone is currently active based on binary sensor states."""
        # Check sensors in priority order (overload is highest priority)
        if self._is_sensor_on(BINARY_SENSOR_OVERLOAD):
            return ActiveZone.OVERLOAD
        if self._is_sensor_on(BINARY_SENSOR_RED):
            return ActiveZone.RED
        if self._is_sensor_on(BINARY_SENSOR_YELLOW):
            return ActiveZone.YELLOW
        if self._is_sensor_on(BINARY_SENSOR_GREEN):
            return ActiveZone.GREEN
        return ActiveZone.NONE

    def _is_sensor_on(self, entity_id: str) -> bool:
        """Check if a binary sensor is ON."""
        state = self._hass.states.get(entity_id)
        return state is not None and state.state == STATE_ON

    def _stop_periodic_job(self) -> None:
        """Stop the current periodic job if running."""
        if self._job_cancel:
            LOGGER.debug("Stopping periodic job")
            self._job_cancel()
            self._job_cancel = None

    def _start_periodic_job(self, zone: ActiveZone) -> None:
        """
        Start the appropriate periodic job for the given zone.

        Per Q7: Job starts immediately but waits for first interval before executing.
        """
        # Stop any existing job first (per Q3: restart timer from zero)
        self._stop_periodic_job()

        if zone == ActiveZone.GREEN:
            LOGGER.info(
                "Starting green strategy periodic job (every %d seconds)",
                GREEN_INTERVAL_SECONDS,
            )

            async def green_job(_now: object) -> None:
                """Execute green strategy on interval."""
                await self._execute_green_strategy()

            self._job_cancel = async_track_time_interval(
                self._hass,
                green_job,
                timedelta(seconds=GREEN_INTERVAL_SECONDS),
            )
        elif zone == ActiveZone.RED:
            LOGGER.info(
                "Starting red strategy periodic job (every %d seconds)",
                RED_INTERVAL_SECONDS,
            )

            async def red_job(_now: object) -> None:
                """Execute red strategy on interval."""
                await self._execute_red_strategy()

            self._job_cancel = async_track_time_interval(
                self._hass,
                red_job,
                timedelta(seconds=RED_INTERVAL_SECONDS),
            )
        elif zone == ActiveZone.YELLOW:
            LOGGER.debug("Yellow zone - no periodic job (waiting)")
        else:
            LOGGER.debug("Zone %s - no periodic job", zone)

    async def _handle_zone_change(self, new_zone: ActiveZone) -> None:
        """
        Handle a zone change by starting/stopping appropriate jobs.

        Per Q3: Always restart timer from zero when zone changes.
        """
        # Handle overload immediately (no periodic job)
        if new_zone == ActiveZone.OVERLOAD:
            self._stop_periodic_job()
            await self._execute_overload_strategy()
            return

        # Start the appropriate periodic job for the zone
        self._start_periodic_job(new_zone)

        # When entering red zone, run strategy once immediately (plus periodic job)
        if new_zone == ActiveZone.RED:
            await self._execute_red_strategy()

    @callback
    def _on_zone_changed(self, event: Event) -> None:
        """Handle zone binary sensor state changes."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")

        LOGGER.debug(
            "Zone sensor changed: %s from %s to %s",
            entity_id,
            old_state.state if old_state else "None",
            new_state.state if new_state else "None",
        )

        # Determine the new active zone
        new_zone = self._determine_active_zone()

        if new_zone != self._active_zone:
            LOGGER.info(
                "Active zone changed from %s to %s",
                self._active_zone,
                new_zone,
            )
            self._active_zone = new_zone
            # Handle zone change (stop/start periodic jobs, execute overload)
            self._hass.async_create_task(self._handle_zone_change(new_zone))

    @callback
    def _on_mode_changed(self, event: Event) -> None:
        """Handle mode select state changes."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")

        old_mode = old_state.state if old_state else "None"
        new_mode = new_state.state if new_state else "None"

        LOGGER.info("Mode changed from %s to %s", old_mode, new_mode)

        # Apply mode priorities: turn off OFF, turn on ALWAYS_ON
        self._hass.async_create_task(self._apply_mode_priorities())

    async def _apply_mode_priorities(self) -> None:
        """Apply mode-specific priorities (turn off/on devices based on priority)."""
        entries = self._hass.config_entries.async_entries(DOMAIN)

        for entry in entries:
            device_id = get_device_id_from_entry(entry)
            device_name = get_device_name_from_entry(entry)
            switch_entity_id = entry.data.get(CONF_SWITCH_ENTITY_ID)

            if not switch_entity_id:
                LOGGER.debug(
                    "No switch entity for device %s (%s), skipping",
                    device_name,
                    device_id,
                )
                continue

            priority = get_current_priority(self._hass, device_id)

            if priority == LOAD_PRIORITY_OFF:
                LOGGER.debug(
                    "Device %s (%s) has LOAD_PRIORITY_OFF, turning off",
                    device_name,
                    device_id,
                )
                try:
                    await self._hass.services.async_call(
                        "switch",
                        "turn_off",
                        {"entity_id": switch_entity_id},
                    )
                except Exception as err:  # noqa: BLE001
                    LOGGER.warning(
                        "Failed to turn off device %s (%s): %s",
                        device_name,
                        device_id,
                        err,
                    )
            elif priority == LOAD_PRIORITY_ALWAYS_ON:
                LOGGER.debug(
                    "Device %s (%s) has LOAD_PRIORITY_ALWAYS_ON, turning on",
                    device_name,
                    device_id,
                )
                try:
                    await self._hass.services.async_call(
                        "switch",
                        "turn_on",
                        {"entity_id": switch_entity_id},
                    )
                except Exception as err:  # noqa: BLE001
                    LOGGER.warning(
                        "Failed to turn on device %s (%s): %s",
                        device_name,
                        device_id,
                        err,
                    )

    async def _execute_green_strategy(self) -> None:
        """Execute green strategy: turn on highest priority device that is OFF."""
        entries = self._hass.config_entries.async_entries(DOMAIN)

        # Build list of (priority, switch_entity_id, device_id, device_name)
        candidates: list[tuple[int, str, str, str]] = []

        for entry in entries:
            device_id = get_device_id_from_entry(entry)
            device_name = get_device_name_from_entry(entry)
            switch_entity_id = entry.data.get(CONF_SWITCH_ENTITY_ID)

            if not switch_entity_id:
                continue

            # Check if switch is OFF
            state = self._hass.states.get(switch_entity_id)
            if state is None or state.state != STATE_OFF:
                continue

            # Get current priority
            priority = get_current_priority(self._hass, device_id)

            if priority == LOAD_PRIORITY_OFF:
                continue

            candidates.append((priority, switch_entity_id, device_id, device_name))

        if not candidates:
            LOGGER.debug("Green strategy: No devices to turn on")
            return

        # Sort by priority descending (highest priority first)
        candidates.sort(key=lambda x: x[0], reverse=True)

        # Turn on the highest priority device
        priority, switch_entity_id, device_id, device_name = candidates[0]
        LOGGER.info(
            "Green strategy: Turning on device %s (%s) (priority %d)",
            device_name,
            device_id,
            priority,
        )
        try:
            await self._hass.services.async_call(
                "switch",
                "turn_on",
                {"entity_id": switch_entity_id},
            )
        except Exception as err:  # noqa: BLE001
            LOGGER.warning(
                "Green strategy: failed to turn on device %s (%s): %s",
                device_name,
                device_id,
                err,
            )

    async def _execute_red_strategy(self) -> None:
        """Execute red strategy: turn off lowest priority device that is ON."""
        entries = self._hass.config_entries.async_entries(DOMAIN)

        # Build list of (priority, switch_entity_id, device_id, device_name)
        candidates: list[tuple[int, str, str, str]] = []

        for entry in entries:
            device_id = get_device_id_from_entry(entry)
            device_name = get_device_name_from_entry(entry)
            switch_entity_id = entry.data.get(CONF_SWITCH_ENTITY_ID)

            if not switch_entity_id:
                continue

            # Check if switch is ON
            state = self._hass.states.get(switch_entity_id)
            if state is None or state.state != STATE_ON:
                continue

            # Get current priority
            priority = get_current_priority(self._hass, device_id)

            # ALWAYS_ON devices should NOT be turned off by red strategy
            if priority == LOAD_PRIORITY_ALWAYS_ON:
                continue

            candidates.append((priority, switch_entity_id, device_id, device_name))

        if not candidates:
            LOGGER.debug("Red strategy: No devices to turn off")
            return

        # Sort by priority ascending (lowest priority first)
        candidates.sort(key=lambda x: x[0])

        # Turn off the lowest priority device
        priority, switch_entity_id, device_id, device_name = candidates[0]
        LOGGER.info(
            "Red strategy: Turning off device %s (%s) (priority %d)",
            device_name,
            device_id,
            priority,
        )
        try:
            await self._hass.services.async_call(
                "switch",
                "turn_off",
                {"entity_id": switch_entity_id},
            )
        except Exception as err:  # noqa: BLE001
            LOGGER.warning(
                "Red strategy: failed to turn off device %s (%s): %s",
                device_name,
                device_id,
                err,
            )

    async def _execute_overload_strategy(self) -> None:
        """Execute overload: EMERGENCY - turn off ALL devices including ALWAYS_ON."""
        LOGGER.warning("OVERLOAD: Executing emergency shutdown of all devices")
        entries = self._hass.config_entries.async_entries(DOMAIN)

        devices_turned_off = 0

        for entry in entries:
            device_id = get_device_id_from_entry(entry)
            device_name = get_device_name_from_entry(entry)
            switch_entity_id = entry.data.get(CONF_SWITCH_ENTITY_ID)

            if not switch_entity_id:
                continue

            # Turn off ALL devices - no exceptions, even ALWAYS_ON
            # This is an emergency shutdown
            LOGGER.info(
                "OVERLOAD: Turning off device %s (%s) (emergency shutdown)",
                device_name,
                device_id,
            )
            try:
                await self._hass.services.async_call(
                    "switch",
                    "turn_off",
                    {"entity_id": switch_entity_id},
                )
                devices_turned_off += 1
            except Exception as err:  # noqa: BLE001
                LOGGER.warning(
                    "OVERLOAD: failed to turn off device %s (%s): %s",
                    device_name,
                    device_id,
                    err,
                )

        LOGGER.warning(
            "OVERLOAD: Emergency shutdown complete - %d devices turned off",
            devices_turned_off,
        )


# Export constants for use in tests
__all__ = [
    "BINARY_SENSOR_GREEN",
    "BINARY_SENSOR_OVERLOAD",
    "BINARY_SENSOR_RED",
    "BINARY_SENSOR_YELLOW",
    "GREEN_INTERVAL_SECONDS",
    "RED_INTERVAL_SECONDS",
    "ActiveZone",
    "BalancingCoordinator",
]
