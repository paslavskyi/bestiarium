"""Balancing coordinator for bestiarium_devices."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_entity_registry_updated_event,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_OVERLOAD_FIRST_QUEUE,
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
    ZONE_SENSOR_UNIQUE_ID,
    DeviceSwitchedData,
    ModeAppliedData,
)
from .control import (
    DeviceControl,
    Direction,
    ResponseMemory,
    async_get_response_memory,
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

        self._power_memory: PowerMemory  # loaded by async_start
        self._response_memory: ResponseMemory  # loaded by async_start

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
        self._power_memory = await async_get_power_memory(self._hass)
        self._response_memory = await async_get_response_memory(self._hass)

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

    async def _async_command(self, entry: ConfigEntry, direction: Direction) -> bool:
        """
        Send a device's relay a command; return whether HA accepted it.

        A refused command is logged and the strategy goes on with the others.
        """
        try:
            await self._hass.services.async_call(
                "switch", direction, {"entity_id": entry.data[CONF_SWITCH_ENTITY_ID]}
            )
        except HomeAssistantError as err:
            LOGGER.warning(
                "Failed to %s device %s: %s",
                direction.replace("_", " "),
                get_device_name_from_entry(entry),
                err,
            )
            return False
        return True

    def _fire_switched(
        self, entry: ConfigEntry, direction: Direction, strategy: str, priority: int
    ) -> None:
        """Fire the event of a device the balancer turned on or off."""
        self._hass.bus.async_fire(
            EVENT_DEVICE_TURNED_ON
            if direction is Direction.TURN_ON
            else EVENT_DEVICE_TURNED_OFF,
            DeviceSwitchedData(
                strategy=strategy,
                device_id=get_device_id_from_entry(entry),
                device_name=get_device_name_from_entry(entry),
                switch_entity_id=entry.data[CONF_SWITCH_ENTITY_ID],
                priority=priority,
            ),
        )

    async def _async_strategy_command(  # noqa: PLR0913
        self,
        entry: ConfigEntry,
        control: DeviceControl,
        direction: Direction,
        strategy: str,
        priority: int,
        now: datetime,
        *,
        step_s: float,
    ) -> None:
        """
        Send a strategy's command and wait for the device's answer.

        Only the first attempt of a series is logged at info, the repeats at
        debug: a device that does not respond gives no line a step.
        """
        LOGGER.log(
            logging.INFO if control.first_attempt(direction) else logging.DEBUG,
            "%s strategy: Turning %s device %s (%s) (priority %d)",
            strategy.title(),
            "on" if direction is Direction.TURN_ON else "off",
            get_device_name_from_entry(entry),
            get_device_id_from_entry(entry),
            priority,
        )
        control.command_sent(direction, now, step_s=step_s, red=strategy == "red")
        if await self._async_command(entry, direction):
            self._fire_switched(entry, direction, strategy, priority)

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
        """
        Apply mode-specific priorities (turn off/on devices based on priority).

        A rare human action: every device gets its command, a deferred one
        too, without waiting for the answer or counting attempts.
        """
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
                if await self._async_command(entry, Direction.TURN_OFF):
                    devices_turned_off.append(device_id)
            elif priority == LOAD_PRIORITY_ALWAYS_ON and balancer_may_turn_on(entry):
                LOGGER.debug(
                    "Device %s (%s) has LOAD_PRIORITY_ALWAYS_ON, turning on",
                    device_name,
                    device_id,
                )
                if await self._async_command(entry, Direction.TURN_ON):
                    devices_turned_on.append(device_id)

        if devices_turned_off or devices_turned_on:
            mode_state = get_mode_state(self._hass)
            mode = mode_state.state if mode_state else ""
            self._hass.bus.async_fire(
                EVENT_MODE_APPLIED,
                ModeAppliedData(
                    mode=mode,
                    devices_turned_off=devices_turned_off,
                    devices_turned_on=devices_turned_on,
                ),
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
        priority = get_current_priority(self._hass, entry)

        if not is_allowed(entry):
            if state.state == STATE_OFF:
                return
            direction = Direction.TURN_OFF
        elif (
            self._active_zone == ActiveZone.GREEN
            and state.state == STATE_OFF
            and priority != LOAD_PRIORITY_OFF
            and balancer_may_turn_on(entry)
            and self._fits(entry)
        ):
            direction = Direction.TURN_ON
        else:
            return

        # A person's action: a refused command reaches them as an error
        await self._hass.services.async_call(
            "switch", direction, {"entity_id": switch_entity_id}
        )
        self._fire_switched(entry, direction, "allowed", priority)

    async def _execute_green_strategy(self) -> None:
        """
        Execute green strategy: turn on the most important OFF device that fits.

        A device that waits for the answer to a command or is deferred is not
        a candidate (docs/concepts/stability.md).
        """
        now = dt_util.utcnow()
        entries = self._hass.config_entries.async_entries(DOMAIN)

        candidates: list[tuple[int, ConfigEntry, DeviceControl]] = []

        for entry in entries:
            switch_entity_id = entry.data.get(CONF_SWITCH_ENTITY_ID)
            control = self.control(entry)

            if not switch_entity_id or control is None:
                continue

            # Check if switch is OFF
            state = self._hass.states.get(switch_entity_id)
            if state is None or state.state != STATE_OFF:
                continue

            # Get current priority
            priority = get_current_priority(self._hass, entry)

            if priority == LOAD_PRIORITY_OFF or not balancer_may_turn_on(entry):
                continue

            if not control.may_command(Direction.TURN_ON, now):
                continue

            candidates.append((priority, entry, control))

        candidates.sort(key=lambda x: x[0], reverse=True)
        fitting = next(
            (
                candidate
                for candidate in candidates
                if candidate[0] == LOAD_PRIORITY_ALWAYS_ON or self._fits(candidate[1])
            ),
            None,
        )
        if fitting is None:
            LOGGER.debug("Green strategy: No devices to turn on")
            return

        priority, entry, control = fitting
        await self._async_strategy_command(
            entry,
            control,
            Direction.TURN_ON,
            "green",
            priority,
            now,
            step_s=GREEN_INTERVAL_SECONDS,
        )

    async def _execute_red_strategy(self) -> None:
        """
        Execute red strategy: turn off lowest priority device that is ON.

        While a device may still answer its turn-off command (its response
        time, at most the red zone's limit), no other device is turned off.
        A deferred device is not a candidate.
        """
        now = dt_util.utcnow()
        entries = self._hass.config_entries.async_entries(DOMAIN)
        if any(
            control.waiting(Direction.TURN_OFF, now)
            for control in self._controls.values()
        ):
            LOGGER.debug("Red strategy: waiting for a device to turn off")
            return

        candidates: list[tuple[int, ConfigEntry, DeviceControl]] = []

        for entry in entries:
            switch_entity_id = entry.data.get(CONF_SWITCH_ENTITY_ID)
            control = self.control(entry)

            if not switch_entity_id or control is None:
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

            if not control.may_command(Direction.TURN_OFF, now):
                continue

            candidates.append((priority, entry, control))

        if not candidates:
            LOGGER.debug("Red strategy: No devices to turn off")
            return

        # Sort by priority ascending (lowest priority first)
        candidates.sort(key=lambda x: x[0])

        # Turn off the lowest priority device
        priority, entry, control = candidates[0]
        await self._async_strategy_command(
            entry,
            control,
            Direction.TURN_OFF,
            "red",
            priority,
            now,
            step_s=RED_INTERVAL_SECONDS,
        )

    async def _turn_off_devices(
        self,
        entries: list[ConfigEntry],
    ) -> int:
        """
        Turn off switches for the given config entries. Returns count turned off.

        An emergency: every device gets the command, a deferred one too,
        without waiting for the answer or counting attempts.
        """
        count = 0
        for entry in entries:
            if not entry.data.get(CONF_SWITCH_ENTITY_ID):
                continue
            LOGGER.info(
                "OVERLOAD: Turning off device %s (%s)",
                get_device_name_from_entry(entry),
                get_device_id_from_entry(entry),
            )
            if await self._async_command(entry, Direction.TURN_OFF):
                count += 1
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
