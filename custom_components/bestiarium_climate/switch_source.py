"""
Where a thermostat reads its switch and sends it commands (DR-9f67ab61).

Chosen once, when the thermostat is made: a switch by its own state (the old
thermostat, a universal one without a power sensor), or a switch read by its
power sensor (a universal one with it). A source reads on or off and when
that last changed, or nothing while the switch is gone, and tells its owner
when what it reads changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_AIR_CONDITIONER,
    CONF_POWER_ENTITY,
    CONF_TARGET_SWITCH,
    DEFAULT_UNIVERSAL_MIN_POWER,
)
from .power_reading import PowerReading, PowerThresholds

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import (
        CALLBACK_TYPE,
        Event,
        EventStateChangedData,
        HomeAssistant,
    )


def controlled_entity(data: Mapping[str, Any]) -> str | None:
    """Return what a thermostat controls: its air conditioner or its switch."""
    return data.get(CONF_AIR_CONDITIONER) or data.get(CONF_TARGET_SWITCH)


@dataclass(frozen=True)
class SwitchState:
    """What a source reads: on or off, and when that last changed."""

    on: bool
    changed_at: datetime


class SwitchSource:
    """A switch: what it reads, and its commands."""

    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        """Follow nothing yet; call start."""
        self._hass = hass
        self.entity_id = entity_id
        self._on_change: Callable[[bool], None] | None = None
        self._shown: SwitchState | None = None
        self._unsubscribe: list[CALLBACK_TYPE] = []

    @callback
    def start(self, on_change: Callable[[bool], None]) -> None:
        """Follow the switch; tell on_change(was_on) each change of what it reads."""
        self._on_change = on_change
        self._unsubscribe.append(
            async_track_state_change_event(
                self._hass, [self.entity_id], self._entity_changed
            )
        )
        self._shown = self.state()

    @callback
    def stop(self) -> None:
        """Stop following."""
        while self._unsubscribe:
            self._unsubscribe.pop()()

    def state(self) -> SwitchState | None:
        """Return what the switch reads now; None while it is gone."""
        raise NotImplementedError

    async def async_turn(self, *, on: bool) -> None:
        """Turn the switch on or off."""
        await self._hass.services.async_call(
            "switch",
            "turn_on" if on else "turn_off",
            {"entity_id": self.entity_id},
            blocking=True,
        )

    @callback
    def _entity_changed(self, _event: Event[EventStateChangedData]) -> None:
        self._tell()

    def _tell(self) -> None:
        """Tell the owner if what the switch reads changed."""
        shown, self._shown = self._shown, self.state()
        if shown != self._shown and self._on_change is not None:
            self._on_change(shown is not None and shown.on)


class EntitySwitch(SwitchSource):
    """A switch read by its own state: gone while missing, unavailable or unknown."""

    def state(self) -> SwitchState | None:
        """Return the switch's own state."""
        state = self._hass.states.get(self.entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return SwitchState(on=state.state == STATE_ON, changed_at=state.last_changed)


class PoweredSwitch(SwitchSource):
    """
    A switch read by its power sensor.

    Gone while the switch is missing or unavailable, or the power data is
    gone. An optimistic switch (a template switch over scenes) is unknown
    until its first command: no reason to be gone.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        name: str,
        power_entity: str,
        thresholds: PowerThresholds,
    ) -> None:
        """Read the switch by the power sensor; call start."""
        super().__init__(hass, entity_id)
        self._reading = PowerReading(hass, name, power_entity, thresholds, self._tell)

    @callback
    def start(self, on_change: Callable[[bool], None]) -> None:
        """Follow the switch and its power."""
        # Read by power before HA's start is seen: on a reload it is at once
        self._reading.start()
        self._unsubscribe.append(self._reading.stop)
        super().start(on_change)

    def state(self) -> SwitchState | None:
        """Return the switch as its power reads it."""
        state = self._hass.states.get(self.entity_id)
        reading = self._reading
        if state is None or state.state == STATE_UNAVAILABLE or not reading.available:
            return None
        return SwitchState(on=reading.is_on, changed_at=reading.last_changed)


def switch_source(
    hass: HomeAssistant, subentry: ConfigSubentry, name: str
) -> SwitchSource:
    """Return the source of a thermostat's switch, by its subentry."""
    data = subentry.data
    switch = data[CONF_TARGET_SWITCH]
    if power := data.get(CONF_POWER_ENTITY):
        return PoweredSwitch(
            hass,
            switch,
            name,
            power,
            PowerThresholds.of(data, min_power=DEFAULT_UNIVERSAL_MIN_POWER),
        )
    return EntitySwitch(hass, switch)
