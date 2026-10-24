"""Balancing coordinator for bestiarium_devices."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
from homeassistant.helpers.event import (
    async_track_entity_registry_updated_event,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started

from .const import (
    DOMAIN,
    EVENT_ZONE_CHANGED,
    GREEN_INTERVAL_SECONDS,
    ISSUE_NO_LOAD_ZONES,
    LOGGER,
    MODE_SELECT_UNIQUE_ID,
    RED_INTERVAL_SECONDS,
    ZONE_SENSOR_UNIQUE_ID,
    ZoneChangedData,
)
from .control import DeviceControl, ResponseMemory, async_get_response_memory
from .helpers import mode_entity_id
from .power_memory import async_get_power_memory
from .strategies import Strategies

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import (
        CALLBACK_TYPE,
        EventStateChangedData,
        HomeAssistant,
        State,
    )
    from homeassistant.helpers.entity_registry import EventEntityRegistryUpdatedData


class ActiveZone(StrEnum):
    """
    Active power load zone for balancing.

    Pause (green on pause) and no data run no strategy.
    """

    NO_DATA = "no_data"
    PAUSE = "pause"
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
        self._active_zone: ActiveZone = ActiveZone.NO_DATA

        # Reference to cancel periodic job callback
        self._job_cancel: Callable[[], None] | None = None
        # The job that repeats the mode's turn-offs
        self._mode_job_cancel: Callable[[], None] | None = None

        # Unsubscribe callbacks for bestiarium_mode entity listeners
        self._subscriptions: list[Callable[[], None]] = []

        # Flag to track if coordinator is running
        self._running: bool = False
        # Whether it follows the zone sensors (they exist)
        self._active = False
        self._unsubscribe_start: list[Callable[[], None]] = []

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

        # The zone sensors may come later: when the AC load limits are set
        self._unsubscribe_start = [
            self._hass.bus.async_listen(
                EVENT_ENTITY_REGISTRY_UPDATED, self._on_entity_registry_updated
            )
        ]
        if self._zone_entity_ids():
            self._activate()
        self._unsubscribe_start.append(async_at_started(self._hass, self._on_started))

    @callback
    def _on_entity_registry_updated(
        self, event: Event[EventEntityRegistryUpdatedData]
    ) -> None:
        """Come alive once a zone sensor is created; follow each new mode entity."""
        entity_id = event.data["entity_id"]
        if event.data["action"] != "create":
            return
        if self._active and entity_id in self._mode_entity_ids():
            self._unsubscribe()
            self._subscribe()
        elif entity_id in self._zone_entity_ids():
            self._activate()

    @callback
    def _on_started(self, _hass: HomeAssistant) -> None:
        """Tell the owner, once HA has started, that there are no zones."""
        if not self._active:
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                ISSUE_NO_LOAD_ZONES,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_NO_LOAD_ZONES,
            )

    def _activate(self) -> None:
        """Follow the zones and run the strategy of the current one."""
        self._active = True
        ir.async_delete_issue(self._hass, DOMAIN, ISSUE_NO_LOAD_ZONES)
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
        if self._mode_job_cancel:
            self._mode_job_cancel()
            self._mode_job_cancel = None

        self._unsubscribe()
        for unsubscribe in self._unsubscribe_start:
            unsubscribe()
        self._unsubscribe_start = []
        self._active = False
        for control in self._controls.values():
            control.async_stop()
        self._controls.clear()

        self._active_zone = ActiveZone.NO_DATA
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

    def _zone_entity_ids(self) -> list[str]:
        return [e for zone in ZONES if (e := self._zone_entity_id(zone))]

    def _mode_entity_ids(self) -> list[str]:
        """Return the entity_ids of the zone sensors and the mode select."""
        select = mode_entity_id(self._hass, "select", MODE_SELECT_UNIQUE_ID)
        return [*self._zone_entity_ids(), *([select] if select else [])]

    def _subscribe(self) -> None:
        """
        Listen to the bestiarium_mode zone sensors and mode select.

        The entities are tracked by their current entity_ids, so the listeners
        are re-created whenever one of them is renamed or created.
        """
        zone_entity_ids = self._zone_entity_ids()
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

    def _determine_active_zone(self) -> ActiveZone:
        """
        Return the zone whose sensor is on.

        None on: the pause before green while the sensors are available, no
        data when they are not.
        """
        for zone in reversed(ZONES):
            if self._is_sensor_on(zone):
                return zone
        green = self._zone_state(ActiveZone.GREEN)
        if green is not None and green.state == STATE_OFF:
            return ActiveZone.PAUSE
        return ActiveZone.NO_DATA

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
                await self._async_red()

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
            await self._async_red()

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
                ZoneChangedData(previous_zone=previous_zone.value, zone=new_zone.value),
            )
            # Handle zone change (stop/start periodic jobs, execute overload)
            self._hass.async_create_task(self._handle_zone_change(new_zone))

    @callback
    def _on_mode_changed(self, event: Event[EventStateChangedData]) -> None:
        """
        Apply a mode a person or an automation selected.

        A select that goes and comes back (a reload of the mode entry) is no
        change: applying it again would turn off a device a person turned on.
        """
        old, new = _mode(event.data["old_state"]), _mode(event.data["new_state"])
        if old is None or new is None or old == new:
            return
        LOGGER.info("Mode changed from %s to %s", old, new)
        # Apply mode priorities: turn off OFF, turn on ALWAYS_ON
        self._hass.async_create_task(self._async_apply_mode())

    async def _async_apply_mode(self) -> None:
        """Apply the mode, then repeat its turn-offs until they are done."""
        await self._strategies.async_apply_mode()
        self._repeat_mode_duties()

    def _repeat_mode_duties(self) -> None:
        """Run the job that repeats the mode's turn-offs, while a duty is open."""
        if self._mode_job_cancel is None and any(
            control.mode_off for control in self._controls.values()
        ):
            self._mode_job_cancel = async_track_time_interval(
                self._hass, self._mode_job, timedelta(seconds=RED_INTERVAL_SECONDS)
            )

    async def _async_red(self) -> None:
        """Run the red strategy; its turn-off of a device off by mode is a duty."""
        await self._strategies.async_red()
        self._repeat_mode_duties()

    async def _mode_job(self, _now: object) -> None:
        if not await self._strategies.async_mode_duties() and self._mode_job_cancel:
            self._mode_job_cancel()
            self._mode_job_cancel = None

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


def _mode(state: State | None) -> str | None:
    """Return the mode a select state names; None while it is gone."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    return state.state
