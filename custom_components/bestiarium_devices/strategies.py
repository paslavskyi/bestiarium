"""What the balancer does in each zone; the coordinator decides when."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.exceptions import HomeAssistantError
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
    GREEN_INTERVAL_SECONDS,
    LOAD_PRIORITY_ALWAYS_ON,
    LOAD_PRIORITY_OFF,
    LOGGER,
    OVERLOAD_WAIT_SECONDS,
    RED_INTERVAL_SECONDS,
    DeviceSwitchedData,
    ModeAppliedData,
    OverloadClearedData,
    OverloadFirstQueueData,
    OverloadSecondQueueData,
)
from .control import DeviceControl, Direction
from .helpers import (
    balancer_may_turn_on,
    get_current_priority,
    get_device_id_from_entry,
    get_device_name_from_entry,
    get_mode_state,
    is_allowed,
    load_and_red_threshold,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, State

    from .power_memory import PowerMemory


class Strategies:
    """The strategies of the zones, the mode and the 'Allowed' switch."""

    def __init__(
        self,
        hass: HomeAssistant,
        controls: dict[str, DeviceControl],
        power_memory: PowerMemory,
        overload_cleared: Callable[[], bool],
    ) -> None:
        """Command the devices that have a control (the ones set up)."""
        self._hass = hass
        self._controls = controls
        self._power_memory = power_memory
        self._overload_cleared = overload_cleared

    def _devices(self) -> list[tuple[ConfigEntry, DeviceControl, State | None]]:
        """
        Return the devices set up, with their relay's state, in entry order.

        Of two devices with the same priority the one added first wins, also
        after a reload. Disabled, unloading and removed entries have no
        control and are left alone. A list: the strategies await between
        devices while entries come and go.
        """
        return [
            (entry, control, self._hass.states.get(entry.data[CONF_SWITCH_ENTITY_ID]))
            for entry in self._hass.config_entries.async_entries(DOMAIN)
            if (control := self._controls.get(entry.entry_id))
        ]

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

    async def async_apply_mode(
        self, *, restart: bool = False, only: str | None = None
    ) -> None:
        """
        Apply mode-specific priorities (turn off/on devices based on priority).

        A rare human action: every device gets its command, a deferred one
        too. A device that is on and should be off gets the mode's duty: its
        turn-off is repeated like any balancer command until it is off
        (async_mode_duties).

        After a restart of HA the mode is applied again (the owner's choice:
        restarts are rare), and to a device entry reloaded after the start
        (`only`); only the
        devices that are not off (on, or still unknown) get a turn-off and
        the duty, only the always-on ones that are off a turn-on, and the
        event tells the commands that went through, so the family can turn on
        what it needs.
        """
        devices_turned_off: list[str] = []
        devices_turned_on: list[str] = []
        now = dt_util.utcnow()

        for entry, control, state in self._devices():
            if only is not None and entry.entry_id != only:
                continue
            device_id = get_device_id_from_entry(entry)
            device_name = get_device_name_from_entry(entry)
            priority = get_current_priority(self._hass, entry)
            if priority != LOAD_PRIORITY_OFF:
                control.end_mode_off()
            is_on = state is not None and state.state == STATE_ON
            # A cloud relay may still be unknown at the start: it gets the duty
            maybe_on = state is None or state.state != STATE_OFF

            if priority == LOAD_PRIORITY_OFF:
                LOGGER.debug(
                    "Device %s (%s) has LOAD_PRIORITY_OFF, turning off",
                    device_name,
                    device_id,
                )
                if maybe_on:
                    control.start_mode_off()
                    control.command_sent(
                        Direction.TURN_OFF, now, step_s=RED_INTERVAL_SECONDS, red=False
                    )
                if (maybe_on or not restart) and await self._async_command(
                    entry, Direction.TURN_OFF
                ):
                    devices_turned_off.append(device_id)
            elif priority == LOAD_PRIORITY_ALWAYS_ON and balancer_may_turn_on(entry):
                LOGGER.debug(
                    "Device %s (%s) has LOAD_PRIORITY_ALWAYS_ON, turning on",
                    device_name,
                    device_id,
                )
                if (not is_on or not restart) and await self._async_command(
                    entry, Direction.TURN_ON
                ):
                    devices_turned_on.append(device_id)

        if devices_turned_off or (devices_turned_on and not restart):
            mode_state = get_mode_state(self._hass)
            mode = mode_state.state if mode_state else ""
            self._hass.bus.async_fire(
                EVENT_MODE_APPLIED,
                ModeAppliedData(
                    mode=mode,
                    devices_turned_off=devices_turned_off,
                    devices_turned_on=devices_turned_on,
                    reason="restart" if restart else "selected",
                ),
            )

    async def async_mode_duties(self) -> bool:
        """
        Repeat the mode's turn-off of the devices that are still on.

        Each waits for its answer, is deferred after its attempts and tried
        again, as any balancer command. Return whether a duty is still open.
        """
        now = dt_util.utcnow()
        open_duty = False
        for entry, control, _state in self._devices():
            if not control.mode_off:
                continue
            if get_current_priority(self._hass, entry) != LOAD_PRIORITY_OFF:
                control.end_mode_off()
                continue
            open_duty = True
            if control.may_command(Direction.TURN_OFF, now):
                LOGGER.debug(
                    "Mode: turning off device %s again",
                    get_device_name_from_entry(entry),
                )
                control.command_sent(
                    Direction.TURN_OFF, now, step_s=RED_INTERVAL_SECONDS, red=False
                )
                await self._async_command(entry, Direction.TURN_OFF)
        return open_duty

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

    async def async_allowed_changed(self, entry: ConfigEntry, *, green: bool) -> None:
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
            green
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

    async def async_green(self) -> None:
        """
        Execute green strategy: turn on the most important OFF device that fits.

        A device that waits for the answer to a command or is deferred is not
        a candidate (docs/concepts/stability.md).
        """
        now = dt_util.utcnow()
        candidates: list[tuple[int, ConfigEntry, DeviceControl]] = []

        for entry, control, state in self._devices():
            if state is None or state.state != STATE_OFF:
                continue
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

    async def async_red(self) -> None:
        """
        Execute red strategy: turn off lowest priority device that is ON.

        First every device the mode says off (someone turned it on) is turned
        off at once; the queue goes on at the next step. While a device of the
        queue may still answer its turn-off command (its response time, at
        most the red zone's limit), no other device is turned off; the
        devices that are off by mode do not hold the queue. A deferred device
        is not a candidate.
        """
        now = dt_util.utcnow()
        candidates: list[tuple[int, ConfigEntry, DeviceControl]] = []
        off_by_mode: list[tuple[int, ConfigEntry, DeviceControl]] = []
        waiting = False

        for entry, control, state in self._devices():
            priority = get_current_priority(self._hass, entry)
            if priority != LOAD_PRIORITY_OFF:
                waiting = waiting or control.waiting(Direction.TURN_OFF, now)
            if state is None or state.state != STATE_ON:
                continue
            # ALWAYS_ON devices should NOT be turned off by red strategy
            if priority == LOAD_PRIORITY_ALWAYS_ON:
                continue
            if not control.may_command(Direction.TURN_OFF, now):
                continue
            queue = off_by_mode if priority == LOAD_PRIORITY_OFF else candidates
            queue.append((priority, entry, control))

        if off_by_mode:
            for priority, entry, control in off_by_mode:
                if not control.mode_off:
                    control.start_mode_off()
                await self._async_strategy_command(
                    entry,
                    control,
                    Direction.TURN_OFF,
                    "red",
                    priority,
                    now,
                    step_s=RED_INTERVAL_SECONDS,
                )
            return
        if waiting:
            LOGGER.debug("Red strategy: waiting for a device to turn off")
            return

        if not candidates:
            LOGGER.debug("Red strategy: No devices to turn off")
            return

        # Turn off the lowest priority device
        priority, entry, control = min(candidates, key=lambda x: x[0])
        await self._async_strategy_command(
            entry,
            control,
            Direction.TURN_OFF,
            "red",
            priority,
            now,
            step_s=RED_INTERVAL_SECONDS,
        )

    async def _turn_off_devices(self, entries: list[ConfigEntry]) -> int:
        """
        Turn off switches for the given config entries. Returns count turned off.

        An emergency: every device gets the command, a deferred one too,
        without waiting for the answer or counting attempts.
        """
        count = 0
        for entry in entries:
            LOGGER.info(
                "OVERLOAD: Turning off device %s (%s)",
                get_device_name_from_entry(entry),
                get_device_id_from_entry(entry),
            )
            if await self._async_command(entry, Direction.TURN_OFF):
                count += 1
        return count

    async def async_overload(self) -> None:
        """Run overload handling using a two-step shutdown strategy."""
        LOGGER.warning("OVERLOAD: Executing two-step emergency shutdown")
        entries = [entry for entry, _control, _state in self._devices()]

        first_queue = [e for e in entries if e.data.get(CONF_OVERLOAD_FIRST_QUEUE)]
        second_queue = [e for e in entries if not e.data.get(CONF_OVERLOAD_FIRST_QUEUE)]

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
        self._hass.bus.async_fire(
            EVENT_OVERLOAD_FIRST_QUEUE,
            OverloadFirstQueueData(
                devices_turned_off=n1,
                device_ids=[get_device_id_from_entry(e) for e in first_queue],
                device_names=[get_device_name_from_entry(e) for e in first_queue],
                overload_cleared=overload_cleared,
            ),
        )

        if overload_cleared:
            LOGGER.info(
                "OVERLOAD: Overload cleared after first queue; skipping second queue"
            )
            self._hass.bus.async_fire(
                EVENT_OVERLOAD_CLEARED,
                OverloadClearedData(cleared_after="first_queue", first_queue_count=n1),
            )
            return

        await asyncio.sleep(OVERLOAD_WAIT_SECONDS)

        if self._overload_cleared():
            LOGGER.info("OVERLOAD: Overload cleared during wait; skipping second queue")
            self._hass.bus.async_fire(
                EVENT_OVERLOAD_CLEARED,
                OverloadClearedData(cleared_after="wait_period", first_queue_count=n1),
            )
            return

        n2 = await self._turn_off_devices(second_queue)
        LOGGER.warning(
            "OVERLOAD: Two-step shutdown complete - first=%d second=%d",
            n1,
            n2,
        )
        self._hass.bus.async_fire(
            EVENT_OVERLOAD_SECOND_QUEUE,
            OverloadSecondQueueData(
                first_queue_count=n1,
                second_queue_count=n2,
                device_ids=[get_device_id_from_entry(e) for e in second_queue],
                device_names=[get_device_name_from_entry(e) for e in second_queue],
            ),
        )
