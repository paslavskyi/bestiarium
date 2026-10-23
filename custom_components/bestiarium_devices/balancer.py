"""Balancing coordinator for bestiarium_devices."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, callback
from homeassistant.helpers.event import (
    async_track_entity_registry_updated_event,
    async_track_state_change_event,
    async_track_time_interval,
)

from .const import (
    EVENT_ZONE_CHANGED,
    GREEN_INTERVAL_SECONDS,
    LOGGER,
    MODE_SELECT_UNIQUE_ID,
    RED_INTERVAL_SECONDS,
    ZONE_SENSOR_UNIQUE_ID,
)
from .control import DeviceControl, ResponseMemory, async_get_response_memory
from .helpers import mode_entity_id
from .power_memory import async_get_power_memory
from .strategies import Strategies

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State
    from homeassistant.helpers.entity_registry import EventEntityRegistryUpdatedData

# Notification ID for missing sensors warning
NOTIFICATION_ID_MISSING_SENSORS = "bestiarium_devices_missing_sensors"


class ActiveZone(StrEnum):
    """Active power load zone for balancing."""

    NONE = "none"
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    OVERLOAD = "overload"


ZONES = (ActiveZone.GREEN, ActiveZone.YELLOW, ActiveZone.RED, ActiveZone.OVERLOAD)


class BalancingCoordinator:
    """Coordinator for device consumption balancing based on power load zones."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the balancing coordinator."""
        self._hass = hass

        # Current active zone
        self._active_zone: ActiveZone = ActiveZone.NONE

        # Reference to cancel periodic job callback
        self._job_cancel: Callable[[], None] | None = None

        # Unsubscribe callbacks for bestiarium_mode entity listeners
        self._subscriptions: list[Callable[[], None]] = []

        # Flag to track if coordinator is running
        self._running: bool = False

        self._response_memory: ResponseMemory  # loaded by async_start
        self._strategies: Strategies  # made by async_start

        # The control state of each device, by entry_id
        self._controls: dict[str, DeviceControl] = {}

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
        self._response_memory = await async_get_response_memory(self._hass)
        self._strategies = Strategies(
            self._hass,
            self._controls,
            await async_get_power_memory(self._hass),
            self._overload_cleared,
        )

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

        self._subscribe()

        # Determine initial active zone (but don't execute strategy per Q7)
        self._active_zone = self._determine_active_zone()
        LOGGER.debug("Initial active zone: %s", self._active_zone)

        # Start periodic job based on initial zone (per Q7: wait for first interval)
        # Note: For initial overload, we still execute immediately as it's emergency
        if self._active_zone == ActiveZone.OVERLOAD:
            # Even on startup, overload is an emergency - execute immediately
            self._hass.async_create_task(self._strategies.async_overload())
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

        self._unsubscribe()
        for control in self._controls.values():
            control.async_stop()
        self._controls.clear()

        self._active_zone = ActiveZone.NONE
        self._running = False

    def control(self, entry: ConfigEntry) -> DeviceControl | None:
        """
        Return the control state of a device that is set up.

        Disabled, unloading and removed entries have none, and the strategies
        leave them alone.
        """
        return self._controls.get(entry.entry_id)

    @callback
    def async_add_device(self, entry: ConfigEntry) -> CALLBACK_TYPE:
        """Follow a device entry; return the call that stops following it."""
        control = DeviceControl(self._hass, entry, self._response_memory)
        control.async_start()
        self._controls[entry.entry_id] = control

        @callback
        def remove() -> None:
            if control := self._controls.pop(entry.entry_id, None):
                control.async_stop()

        return remove

    def _zone_entity_id(self, zone: ActiveZone) -> str | None:
        """Return the current entity_id of a zone binary sensor."""
        return mode_entity_id(
            self._hass, "binary_sensor", ZONE_SENSOR_UNIQUE_ID.format(zone=zone)
        )

    def _zone_state(self, zone: ActiveZone) -> State | None:
        """Return the state of a zone binary sensor, if it has one."""
        entity_id = self._zone_entity_id(zone)
        return self._hass.states.get(entity_id) if entity_id else None

    def _subscribe(self) -> None:
        """
        Listen to the bestiarium_mode zone sensors and mode select.

        The entities are tracked by their current entity_ids, so the listeners
        are re-created whenever one of them is renamed.
        """
        zone_entity_ids = [
            entity_id for zone in ZONES if (entity_id := self._zone_entity_id(zone))
        ]
        select_entity_id = mode_entity_id(self._hass, "select", MODE_SELECT_UNIQUE_ID)
        mode_entity_ids = [select_entity_id] if select_entity_id else []
        self._subscriptions = [
            async_track_state_change_event(
                self._hass, zone_entity_ids, self._on_zone_changed
            ),
            async_track_state_change_event(
                self._hass, mode_entity_ids, self._on_mode_changed
            ),
            async_track_entity_registry_updated_event(
                self._hass,
                zone_entity_ids + mode_entity_ids,
                self._on_mode_entity_registry_updated,
            ),
        ]

    def _unsubscribe(self) -> None:
        """Remove all bestiarium_mode entity listeners."""
        for unsubscribe in self._subscriptions:
            unsubscribe()
        self._subscriptions.clear()

    @callback
    def _on_mode_entity_registry_updated(
        self, event: Event[EventEntityRegistryUpdatedData]
    ) -> None:
        """Follow a renamed bestiarium_mode entity to its new entity_id."""
        if "old_entity_id" not in event.data:
            return
        LOGGER.debug(
            "bestiarium_mode entity renamed from %s to %s",
            event.data["old_entity_id"],
            event.data["entity_id"],
        )
        self._unsubscribe()
        self._subscribe()

    def _check_binary_sensors_exist(self) -> bool:
        """Check if any power load zone binary sensor exists."""
        return any(self._zone_state(zone) is not None for zone in ZONES)

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
        for zone in reversed(ZONES):
            if self._is_sensor_on(zone):
                return zone
        return ActiveZone.NONE

    def _is_sensor_on(self, zone: ActiveZone) -> bool:
        """Check if a zone binary sensor is ON."""
        state = self._zone_state(zone)
        return state is not None and state.state == STATE_ON

    def _overload_cleared(self) -> bool:
        """
        Return True only if the overload sensor reports OFF.

        Fail-safe for the emergency shutdown: a missing, unknown or
        unavailable sensor means the overload may still be going on.
        """
        state = self._zone_state(ActiveZone.OVERLOAD)
        return state is not None and state.state == STATE_OFF

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
                await self._strategies.async_green()

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
                await self._strategies.async_red()

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
            await self._strategies.async_overload()
            return

        # Start the appropriate periodic job for the zone
        self._start_periodic_job(new_zone)

        # When entering red zone, run strategy once immediately (plus periodic job)
        if new_zone == ActiveZone.RED:
            await self._strategies.async_red()

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
            previous_zone = self._active_zone
            LOGGER.info(
                "Active zone changed from %s to %s",
                previous_zone,
                new_zone,
            )
            self._active_zone = new_zone
            self._hass.bus.async_fire(
                EVENT_ZONE_CHANGED,
                {"previous_zone": previous_zone.value, "zone": new_zone.value},
            )
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
        self._hass.async_create_task(self._strategies.async_apply_mode())

    async def async_allowed_changed(self, entry: ConfigEntry) -> None:
        """Apply a change of a device's 'Allowed' switch to its relay."""
        await self._strategies.async_allowed_changed(
            entry, green=self._active_zone == ActiveZone.GREEN
        )


# Export constants for use in tests
__all__ = [
    "GREEN_INTERVAL_SECONDS",
    "RED_INTERVAL_SECONDS",
    "ActiveZone",
    "BalancingCoordinator",
]
