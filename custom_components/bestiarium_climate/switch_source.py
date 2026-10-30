"""
Where a thermostat reads its switch and sends it commands (DR-9f67ab61).

Chosen once, when the thermostat is made: a switch by its own state (the old
thermostat, a universal one without a power sensor), a switch read by its
power sensor (a universal one with it), or an air conditioner read by its
power sensor. A source reads on or off and when that last changed, or
nothing while the switch is gone, and tells its owner when what it reads
changes. An air conditioner has a setting too: what "on" sends it, and what
it shows; a switch has none.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_MODE,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_AIR_CONDITIONER,
    CONF_POWER_ENTITY,
    CONF_TARGET_SWITCH,
    DEFAULT_UNIVERSAL_MIN_POWER,
    SUBENTRY_TYPE_AC,
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
class AcSetting:
    """What an air conditioner is set to: its mode, setpoint and fan speed."""

    hvac_mode: HVACMode
    temperature: float | None = None
    fan_mode: str | None = None

    def matches(self, shown: AcSetting | None) -> bool:
        """Return whether an air conditioner shows this; None here is any."""
        return shown is not None and all(
            getattr(self, f.name) in (None, getattr(shown, f.name))
            for f in fields(self)
        )


@dataclass(frozen=True)
class SwitchState:
    """What a source reads: on or off, and when that last changed."""

    on: bool
    changed_at: datetime


class SwitchSource:
    """A switch: what it reads, and its commands."""

    # An air conditioner idles at its setpoint: it draws little a while
    idles: ClassVar[bool] = False

    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        """Follow nothing yet; call start."""
        self._hass = hass
        self.entity_id = entity_id
        self._on_change: Callable[[bool], None] | None = None
        self._shown: SwitchState | None = None
        self._setting: AcSetting | None = None
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
        self._shown, self._setting = self.state(), self.setting()

    @callback
    def stop(self) -> None:
        """Stop following."""
        while self._unsubscribe:
            self._unsubscribe.pop()()

    def state(self) -> SwitchState | None:
        """Return what the switch reads now; None while it is gone."""
        raise NotImplementedError

    def setting(self) -> AcSetting | None:
        """Return what the device shows it is set to; a switch has no setting."""
        return None

    def wanted(self) -> AcSetting | None:
        """Return what "on" sets the device to; a switch has no setting."""
        return None

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
        """Tell the owner if what the switch reads, or its setting, changed."""
        shown, self._shown = self._shown, self.state()
        setting, self._setting = self._setting, self.setting()
        if (shown, setting) != (self._shown, self._setting) and self._on_change:
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


class AirConditioner(PoweredSwitch):
    """
    An air conditioner (any climate of HA) read by its power sensor.

    "On" sets it to what its thermostat wants (the setpoint rounded to its
    step and clamped to its range, so that it shows what was sent); "off" is
    its mode off. Its own state is assumed, or a cloud's: only its power
    tells whether it runs.
    """

    idles = True

    def __init__(  # noqa: PLR0913
        self,
        hass: HomeAssistant,
        entity_id: str,
        name: str,
        power_entity: str,
        thresholds: PowerThresholds,
        setting: Callable[[], AcSetting | None],
    ) -> None:
        """Read the air conditioner by power; `setting`: what its thermostat wants."""
        super().__init__(hass, entity_id, name, power_entity, thresholds)
        self._thermostat_setting = setting

    def setting(self) -> AcSetting | None:
        """Return what the air conditioner shows: mode, setpoint, fan speed."""
        state = self._hass.states.get(self.entity_id)
        if state is None or state.state not in HVACMode:
            return None
        return AcSetting(
            HVACMode(state.state),
            state.attributes.get(ATTR_TEMPERATURE),
            state.attributes.get(ATTR_FAN_MODE),
        )

    def wanted(self) -> AcSetting | None:
        """Return the thermostat's setting as the air conditioner can take it."""
        wanted = self._thermostat_setting()
        state = self._hass.states.get(self.entity_id)
        if wanted is None or state is None:
            return wanted
        attributes = state.attributes
        temperature = wanted.temperature
        if temperature is not None:
            if step := attributes.get(ATTR_TARGET_TEMP_STEP):
                temperature = round(temperature / step) * step
            low = attributes.get(ATTR_MIN_TEMP, temperature)
            high = attributes.get(ATTR_MAX_TEMP, temperature)
            temperature = float(min(max(temperature, low), high))
        fan = wanted.fan_mode
        if fan not in (attributes.get(ATTR_FAN_MODES) or []):
            fan = None
        return AcSetting(wanted.hvac_mode, temperature, fan)

    async def async_turn(self, *, on: bool) -> None:
        """Set the air conditioner to the wanted setting, or off."""
        wanted = self.wanted() if on else None
        if wanted is None:
            await self._call("set_hvac_mode", {ATTR_HVAC_MODE: HVACMode.OFF})
            return
        if wanted.temperature is None:
            await self._call("set_hvac_mode", {ATTR_HVAC_MODE: wanted.hvac_mode})
        else:
            await self._call(
                "set_temperature",
                {
                    ATTR_HVAC_MODE: wanted.hvac_mode,
                    ATTR_TEMPERATURE: wanted.temperature,
                },
            )
        shown = self.setting()
        if wanted.fan_mode is not None and (
            shown is None or shown.fan_mode != wanted.fan_mode
        ):
            await self._call("set_fan_mode", {ATTR_FAN_MODE: wanted.fan_mode})

    async def _call(self, service: str, data: dict[str, Any]) -> None:
        await self._hass.services.async_call(
            "climate", service, {"entity_id": self.entity_id, **data}, blocking=True
        )


def switch_source(
    hass: HomeAssistant,
    subentry: ConfigSubentry,
    name: str,
    *,
    setting: Callable[[], AcSetting | None],
) -> SwitchSource:
    """
    Return the source of a thermostat's switch, by its subentry.

    `setting` is what an air conditioner thermostat wants its air
    conditioner set to.
    """
    data = subentry.data
    if subentry.subentry_type == SUBENTRY_TYPE_AC:
        return AirConditioner(
            hass,
            data[CONF_AIR_CONDITIONER],
            name,
            data[CONF_POWER_ENTITY],
            PowerThresholds.of(data, min_power=DEFAULT_UNIVERSAL_MIN_POWER),
            setting,
        )
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
