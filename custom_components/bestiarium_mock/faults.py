"""
A mock smart socket that can misbehave like a real cloud socket.

The socket has a physical state (whether the load is powered, which the power
sensor and the grid follow) and a reported state (what its switch shows in
HA). The fault parameters decide how a command changes the first and whether
it reaches the second.
"""

from __future__ import annotations

import random
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, LOGGER, PROFILE_PARAMS, FaultParams, FaultProfile

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .const import MockDeviceSpec


class MockSocket:
    """A mock smart socket and its fault model."""

    def __init__(self, hass: HomeAssistant, spec: MockDeviceSpec) -> None:
        """Start off, available, with the spec's power and fault profile."""
        self._hass = hass
        self.spec = spec
        # The socket's own generator, so tests can seed each socket
        self.rng = random.Random()  # noqa: S311
        self.physical_on = False
        self.reported_on = False
        self.available = True
        self.power_w: float = spec.power_w
        self.profile = spec.profile
        # The parameters of the CUSTOM profile
        self.custom = PROFILE_PARAMS[spec.profile]
        self._listeners: list[Callable[[], None]] = []
        self._cancel_command: CALLBACK_TYPE | None = None
        self._pending_on: bool | None = None  # the command on its way, if any
        self._cancel_dropout: CALLBACK_TYPE | None = None

    @property
    def params(self) -> FaultParams:
        """Return the fault parameters in effect."""
        if self.profile is FaultProfile.CUSTOM:
            return self.custom
        return PROFILE_PARAMS[self.profile]

    @property
    def power(self) -> float:
        """Return the power the load draws."""
        return self.power_w if self.physical_on else 0

    @callback
    def add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call the listener on every change; return its removal."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener()

    @callback
    def start(self) -> None:
        """Start the dropouts of the fault model."""
        self._schedule_dropout()

    @callback
    def stop(self) -> None:
        """Cancel the pending command and dropout."""
        for cancel in (self._cancel_command, self._cancel_dropout):
            if cancel:
                cancel()
        self._cancel_command = self._cancel_dropout = self._pending_on = None

    @callback
    def restore(self, *, on: bool) -> None:
        """Restore the state the socket had before a restart."""
        self.physical_on = self.reported_on = on

    @callback
    def command(self, *, on: bool) -> None:
        """
        Receive a turn on/off command; the fault model decides what follows.

        A repeat of the command on its way changes nothing, like a real device
        that is already carrying it out; the opposite command replaces it.
        """
        if self._pending_on is on:
            LOGGER.debug("%s already carries out the command", self.spec.device_id)
            return
        params = self.params
        if self.rng.random() * 100 < params.ignore_percent:
            LOGGER.debug("%s ignores the command", self.spec.device_id)
            return
        if self._cancel_command:
            self._cancel_command()
        delay = self.rng.uniform(params.delay_min_s, params.delay_max_s)
        if delay <= 0:
            self._execute(on=on)
            return
        self._pending_on = on
        self._cancel_command = async_call_later(
            self._hass, delay, partial(self._execute_later, on=on)
        )

    @callback
    def _execute_later(self, _now: datetime, *, on: bool) -> None:
        self._execute(on=on)

    @callback
    def _execute(self, *, on: bool) -> None:
        self._cancel_command = self._pending_on = None
        self.physical_on = on
        if self.rng.random() * 100 < self.params.stuck_percent:
            LOGGER.debug(
                "%s switched but keeps reporting its old state", self.spec.device_id
            )
        else:
            self.reported_on = on
        self._changed()

    @callback
    def set_power(self, power_w: float) -> None:
        """Set the power the load draws when on."""
        self.power_w = power_w
        self._changed()

    @callback
    def set_profile(self, profile: FaultProfile) -> None:
        """Switch to a profile; CUSTOM starts from the parameters in effect."""
        if profile is FaultProfile.CUSTOM:
            self.custom = self.params
        self.profile = profile
        self._reschedule_dropout()
        self._changed()

    @callback
    def set_param(self, key: str, value: float) -> None:
        """Change one fault parameter; only the CUSTOM profile has editable ones."""
        if self.profile is not FaultProfile.CUSTOM:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="custom_profile_required",
            )
        self.custom = replace(self.custom, **{key: value})
        self._reschedule_dropout()
        self._changed()

    @callback
    def restore_profile(self, profile: FaultProfile) -> None:
        """Restore the profile the socket had before a restart."""
        self.profile = profile

    @callback
    def restore_param(self, key: str, value: float) -> None:
        """Restore a parameter shown before a restart into the CUSTOM profile."""
        self.custom = replace(self.custom, **{key: value})

    @callback
    def _reschedule_dropout(self) -> None:
        # A running dropout ends as planned and schedules the next one itself
        if self.available:
            self._schedule_dropout()

    @callback
    def _schedule_dropout(self) -> None:
        if self._cancel_dropout:
            self._cancel_dropout()
            self._cancel_dropout = None
        if (rate := self.params.dropouts_per_hour) > 0:
            self._cancel_dropout = async_call_later(
                self._hass, self.rng.expovariate(rate / 3600), self._drop_out
            )

    @callback
    def _drop_out(self, _now: datetime) -> None:
        self.available = False
        self._changed()
        params = self.params
        self._cancel_dropout = async_call_later(
            self._hass,
            self.rng.uniform(params.dropout_min_s, params.dropout_max_s),
            self._come_back,
        )

    @callback
    def _come_back(self, _now: datetime) -> None:
        self._cancel_dropout = None
        self.available = True
        self._changed()
        self._schedule_dropout()
