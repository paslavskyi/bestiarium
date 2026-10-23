"""Balancing coordinator for bestiarium_devices."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, callback
from homeassistant.helpers.event import (
    async_track_entity_registry_updated_event,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_OVERLOAD_FIRST_QUEUE,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    DOMAIN,
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
    LOGGER,
    MODE_SELECT_UNIQUE_ID,
    OVERLOAD_WAIT_SECONDS,
    RED_INTERVAL_SECONDS,
    RELAY_ON_POWER_W,
    TURN_ON_ATTEMPTS,
    TURN_ON_RETRY_MINUTES,
    ZONE_SENSOR_UNIQUE_ID,
)
from .helpers import (
    balancer_may_turn_on,
    get_current_priority,
    get_device_id_from_entry,
    get_device_name_from_entry,
    get_mode_state,
    is_allowed,
    load_and_red_threshold,
    mode_entity_id,
)
from .power_memory import PowerMemory, async_get_power_memory

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, State
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


@dataclass
class _TurnOnAttempts:
    """Green strategy attempts to turn on a device whose relay stayed off."""

    count: int
    last: datetime
    waiting: bool = False  # the attempts are used up; the next devices go first


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

        self._power_memory: PowerMemory  # loaded by async_start

        # Unanswered green strategy turn-on attempts, by entry_id
        self._turn_on_attempts: dict[str, _TurnOnAttempts] = {}

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
        self._power_memory = await async_get_power_memory(self._hass)

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

        self._unsubscribe()

        self._active_zone = ActiveZone.NONE
        self._running = False

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
        self._hass.async_create_task(self._apply_mode_priorities())

    async def _apply_mode_priorities(self) -> None:
        """Apply mode-specific priorities (turn off/on devices based on priority)."""
        entries = self._hass.config_entries.async_entries(DOMAIN)
        devices_turned_off: list[str] = []
        devices_turned_on: list[str] = []

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

            priority = get_current_priority(self._hass, entry)

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
                    devices_turned_off.append(device_id)
                except Exception as err:  # noqa: BLE001
                    LOGGER.warning(
                        "Failed to turn off device %s (%s): %s",
                        device_name,
                        device_id,
                        err,
                    )
            elif priority == LOAD_PRIORITY_ALWAYS_ON and balancer_may_turn_on(entry):
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
                    devices_turned_on.append(device_id)
                except Exception as err:  # noqa: BLE001
                    LOGGER.warning(
                        "Failed to turn on device %s (%s): %s",
                        device_name,
                        device_id,
                        err,
                    )

        if devices_turned_off or devices_turned_on:
            mode_state = get_mode_state(self._hass)
            mode = mode_state.state if mode_state else ""
            self._hass.bus.async_fire(
                EVENT_MODE_APPLIED,
                {
                    "mode": mode,
                    "devices_turned_off": devices_turned_off,
                    "devices_turned_on": devices_turned_on,
                },
            )

    def _fits(self, entry: ConfigEntry) -> bool:
        """
        Return whether turning the device on keeps the load below the red zone.

        Unknown load or threshold: it does not fit.
        """
        expected, source = self._power_memory.expected_power(entry)
        name = get_device_name_from_entry(entry)
        if (load_and_threshold := load_and_red_threshold(self._hass)) is None:
            LOGGER.debug("%s does not fit: grid load or red threshold unknown", name)
            return False
        load, threshold = load_and_threshold
        if load + expected > threshold:
            LOGGER.debug(
                "%s does not fit: %s W + %s W (%s) > %s W",
                name,
                load,
                expected,
                source,
                threshold,
            )
            return False
        return True

    async def async_allowed_changed(self, entry: ConfigEntry) -> None:
        """
        Apply a change of a device's 'Allowed' switch to its relay.

        Disallowed: turn the relay off in any zone. Allowed again: in the green
        zone turn it on at once if the green strategy could and it fits; else
        it just waits for its turn.
        """
        switch_entity_id = entry.data[CONF_SWITCH_ENTITY_ID]
        state = self._hass.states.get(switch_entity_id)
        if state is None:
            return
        device_id = get_device_id_from_entry(entry)
        priority = get_current_priority(self._hass, entry)

        if not is_allowed(entry):
            if state.state == STATE_OFF:
                return
            service, event = "turn_off", EVENT_DEVICE_TURNED_OFF
        elif (
            self._active_zone == ActiveZone.GREEN
            and state.state == STATE_OFF
            and priority != LOAD_PRIORITY_OFF
            and balancer_may_turn_on(entry)
            and self._fits(entry)
        ):
            service, event = "turn_on", EVENT_DEVICE_TURNED_ON
        else:
            return

        await self._hass.services.async_call(
            "switch", service, {"entity_id": switch_entity_id}
        )
        self._hass.bus.async_fire(
            event,
            {
                "strategy": "allowed",
                "device_id": device_id,
                "device_name": get_device_name_from_entry(entry),
                "switch_entity_id": switch_entity_id,
                "priority": priority,
            },
        )

    def _draws_power(self, entry: ConfigEntry, relay: State) -> bool:
        """
        Return whether a device whose relay reads 'off' is in fact on.

        Its relay state is stale: the meter reported power since the relay last
        switched. A meter that only kept its reading from before does not count.
        """
        power_entity_id = entry.data.get(CONF_POWER_SENSOR_ENTITY_ID)
        power = self._hass.states.get(power_entity_id) if power_entity_id else None
        if power is None or power.last_reported <= relay.last_changed:
            return False
        try:
            watts = float(power.state)
        except ValueError:
            return False
        if watts <= RELAY_ON_POWER_W:
            return False
        LOGGER.debug(
            "%s is on: its relay reads off, its meter %s W",
            get_device_name_from_entry(entry),
            watts,
        )
        return True

    def _waits_for_retry(self, entry: ConfigEntry, relay: State) -> bool:
        """
        Return whether a device that ignored its turn-on attempts waits.

        A relay that switched since the last attempt answered it, and the wait
        ends TURN_ON_RETRY_MINUTES after the last attempt: either way the
        attempts start over.
        """
        attempts = self._turn_on_attempts.get(entry.entry_id)
        if attempts is None:
            return False
        retry_at = attempts.last + timedelta(minutes=TURN_ON_RETRY_MINUTES)
        if relay.last_changed > attempts.last or dt_util.utcnow() >= retry_at:
            del self._turn_on_attempts[entry.entry_id]
            return False
        if attempts.count < TURN_ON_ATTEMPTS:
            return False
        if not attempts.waiting:
            attempts.waiting = True
            LOGGER.warning(
                "Green strategy: %s is still off after %d turn-on attempts; "
                "the next devices go first, it is tried again in %d minutes",
                get_device_name_from_entry(entry),
                attempts.count,
                TURN_ON_RETRY_MINUTES,
            )
        return True

    def _count_turn_on_attempt(self, entry: ConfigEntry) -> None:
        now = dt_util.utcnow()
        attempts = self._turn_on_attempts.setdefault(
            entry.entry_id, _TurnOnAttempts(count=0, last=now)
        )
        attempts.count += 1
        attempts.last = now

    async def _execute_green_strategy(self) -> None:
        """Execute green strategy: turn on the most important OFF device that fits."""
        entries = self._hass.config_entries.async_entries(DOMAIN)

        candidates: list[tuple[int, ConfigEntry]] = []

        for entry in entries:
            switch_entity_id = entry.data.get(CONF_SWITCH_ENTITY_ID)

            if not switch_entity_id:
                continue

            # Check if switch is OFF
            state = self._hass.states.get(switch_entity_id)
            if state is None or state.state != STATE_OFF:
                continue

            # Get current priority
            priority = get_current_priority(self._hass, entry)

            if priority == LOAD_PRIORITY_OFF or not balancer_may_turn_on(entry):
                continue

            if self._draws_power(entry, state) or self._waits_for_retry(entry, state):
                continue

            candidates.append((priority, entry))

        candidates.sort(key=lambda x: x[0], reverse=True)
        fitting = next(
            (
                (priority, entry)
                for priority, entry in candidates
                if priority == LOAD_PRIORITY_ALWAYS_ON or self._fits(entry)
            ),
            None,
        )
        if fitting is None:
            LOGGER.debug("Green strategy: No devices to turn on")
            return

        priority, entry = fitting
        switch_entity_id = entry.data[CONF_SWITCH_ENTITY_ID]
        device_id = get_device_id_from_entry(entry)
        device_name = get_device_name_from_entry(entry)
        LOGGER.info(
            "Green strategy: Turning on device %s (%s) (priority %d)",
            device_name,
            device_id,
            priority,
        )
        self._count_turn_on_attempt(entry)
        try:
            await self._hass.services.async_call(
                "switch",
                "turn_on",
                {"entity_id": switch_entity_id},
            )
            self._hass.bus.async_fire(
                EVENT_DEVICE_TURNED_ON,
                {
                    "strategy": "green",
                    "device_id": device_id,
                    "device_name": device_name,
                    "switch_entity_id": switch_entity_id,
                    "priority": priority,
                },
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
            priority = get_current_priority(self._hass, entry)

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
            self._hass.bus.async_fire(
                EVENT_DEVICE_TURNED_OFF,
                {
                    "strategy": "red",
                    "device_id": device_id,
                    "device_name": device_name,
                    "switch_entity_id": switch_entity_id,
                    "priority": priority,
                },
            )
        except Exception as err:  # noqa: BLE001
            LOGGER.warning(
                "Red strategy: failed to turn off device %s (%s): %s",
                device_name,
                device_id,
                err,
            )

    async def _turn_off_devices(
        self,
        entries: list[ConfigEntry],
    ) -> int:
        """Turn off switches for the given config entries. Returns count turned off."""
        count = 0
        for entry in entries:
            device_id = get_device_id_from_entry(entry)
            device_name = get_device_name_from_entry(entry)
            switch_entity_id = entry.data.get(CONF_SWITCH_ENTITY_ID)
            if not switch_entity_id:
                continue
            LOGGER.info(
                "OVERLOAD: Turning off device %s (%s)",
                device_name,
                device_id,
            )
            try:
                await self._hass.services.async_call(
                    "switch",
                    "turn_off",
                    {"entity_id": switch_entity_id},
                )
                count += 1
            except Exception as err:  # noqa: BLE001
                LOGGER.warning(
                    "OVERLOAD: failed to turn off device %s (%s): %s",
                    device_name,
                    device_id,
                    err,
                )
        return count

    async def _execute_overload_strategy(self) -> None:
        """Run overload handling using a two-step shutdown strategy."""
        LOGGER.warning("OVERLOAD: Executing two-step emergency shutdown")
        entries = self._hass.config_entries.async_entries(DOMAIN)

        first_queue = [
            e for e in entries if e.data.get(CONF_OVERLOAD_FIRST_QUEUE, False)
        ]
        second_queue = [
            e for e in entries if not e.data.get(CONF_OVERLOAD_FIRST_QUEUE, False)
        ]

        if not first_queue:
            # Backward compat: no first-queue devices → turn off all immediately
            n = await self._turn_off_devices(entries)
            LOGGER.warning(
                "OVERLOAD: No first-queue devices; turned off all %d devices",
                n,
            )
            return

        n1 = await self._turn_off_devices(first_queue)
        LOGGER.warning("OVERLOAD: First queue shutdown complete - %d devices off", n1)

        overload_cleared = self._overload_cleared()
        first_device_ids = [get_device_id_from_entry(e) for e in first_queue]
        first_device_names = [get_device_name_from_entry(e) for e in first_queue]

        self._hass.bus.async_fire(
            EVENT_OVERLOAD_FIRST_QUEUE,
            {
                "devices_turned_off": n1,
                "device_ids": first_device_ids,
                "device_names": first_device_names,
                "overload_cleared": overload_cleared,
            },
        )

        if overload_cleared:
            LOGGER.info(
                "OVERLOAD: Overload cleared after first queue; skipping second queue"
            )
            self._hass.bus.async_fire(
                EVENT_OVERLOAD_CLEARED,
                {"cleared_after": "first_queue", "first_queue_count": n1},
            )
            return

        await asyncio.sleep(OVERLOAD_WAIT_SECONDS)

        if self._overload_cleared():
            LOGGER.info("OVERLOAD: Overload cleared during wait; skipping second queue")
            self._hass.bus.async_fire(
                EVENT_OVERLOAD_CLEARED,
                {"cleared_after": "wait_period", "first_queue_count": n1},
            )
            return

        n2 = await self._turn_off_devices(second_queue)
        LOGGER.warning(
            "OVERLOAD: Two-step shutdown complete - first=%d second=%d",
            n1,
            n2,
        )

        second_device_ids = [get_device_id_from_entry(e) for e in second_queue]
        second_device_names = [get_device_name_from_entry(e) for e in second_queue]
        self._hass.bus.async_fire(
            EVENT_OVERLOAD_SECOND_QUEUE,
            {
                "first_queue_count": n1,
                "second_queue_count": n2,
                "device_ids": second_device_ids,
                "device_names": second_device_names,
            },
        )


# Export constants for use in tests
__all__ = [
    "GREEN_INTERVAL_SECONDS",
    "RED_INTERVAL_SECONDS",
    "ActiveZone",
    "BalancingCoordinator",
]
