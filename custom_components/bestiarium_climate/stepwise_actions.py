"""
What the stepwise "off" and the entry to red send (DEV-010).

The control decides the steps (stepwise.py); this module carries them out:
the "off"s outside the attempts at the entry to red or overload, the event
"out of control", the cut of the socket and the Repairs issue of a socket
gone. Every command goes the balancer's one path (`send`): none is counted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    EVENT_POWER_CUT,
    EVENT_SWITCH_OUT_OF_CONTROL,
    ISSUE_SOCKET_GONE,
    LOGGER,
    PowerCutData,
    SwitchOutOfControlData,
)
from .devices import switch_power
from .repairs import set_issue
from .stepwise import Step
from .switch import virtual_switch

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable
    from datetime import datetime

    from homeassistant.core import HomeAssistant

    from .control import ThermostatControl


class StepwiseActions:
    """The actions of the stepwise "off" of one climate entry."""

    def __init__(self, hass: HomeAssistant, send: Callable[..., None]) -> None:
        """Send by the balancer's one path of the commands (its _send)."""
        self._hass = hass
        self._send = send
        self._gone_sockets: set[str] = set()

    def shed(self, controls: Iterable[ThermostatControl], now: datetime) -> None:
        """
        Turn off what runs unwanted at the entry to red or overload, once.

        A stepwise "off" under way makes its next attempt now; any other switch
        that reads off (its power sensor may be late), or is on with no
        attempt left, gets an "off" outside the attempts: no attempt, no
        answer awaited, no change of the control state (FR-029).
        """
        for control in controls:
            if control.force_off_attempt(now):
                continue
            if control.blind_off(now):
                self._send(control, False, now, counted=False)  # noqa: FBT003

    def take_step(self, control: ThermostatControl, now: datetime) -> datetime | None:
        """Take the step of the stepwise "off" due now; return when the next is."""
        step = control.off_step(now)
        if step is Step.REPORT:
            LOGGER.info(
                "%s: %s still runs after %s attempts to turn it off",
                control.name,
                control.switch,
                control.off_attempts,
            )
            self._hass.bus.async_fire(
                EVENT_SWITCH_OUT_OF_CONTROL,
                SwitchOutOfControlData(
                    thermostat=self._thermostat_id(control),
                    switch_entity_id=control.switch,
                    power=switch_power(self._hass, control.switch),
                    attempts=control.off_attempts,
                ),
            )
            control.reported(now)
        elif step is Step.CUT and (socket := control.socket) is not None:
            control.cut(now)
            self._send(control, False, now, counted=False, target=socket)  # noqa: FBT003
        due = control.off_step_at()
        return due if due is not None and due > now else None

    async def async_cut(self, control: ThermostatControl, socket: str) -> None:
        """Cut the socket of a switch that does not turn off; never turn it on."""
        try:
            await self._hass.services.async_call(
                "switch", "turn_off", {"entity_id": socket}, blocking=True
            )
        except HomeAssistantError as err:
            LOGGER.warning("%s: socket %s refused: %s", control.name, socket, err)
            return
        LOGGER.info(
            "%s: cut the power of %s at %s", control.name, control.switch, socket
        )
        self._hass.bus.async_fire(
            EVENT_POWER_CUT,
            PowerCutData(
                thermostat=self._thermostat_id(control),
                switch_entity_id=control.switch,
                socket_entity_id=socket,
            ),
        )

    def report_gone_sockets(self, controls: Collection[ThermostatControl]) -> None:
        """
        Report a socket that is gone from HA, as a Repairs issue of its switch.

        Checked once HA has started; until fixed, the stepwise "off" of that
        switch only reports.
        """
        if not self._hass.is_running:
            return
        gone = {
            socket
            for control in controls
            if (socket := control.socket) is not None
            and self._hass.states.get(socket) is None
        }
        if gone == self._gone_sockets:
            return
        self._gone_sockets = gone
        for control in controls:
            if (found := virtual_switch(self._hass, control.switch)) is None:
                continue
            entry, subentry = found
            set_issue(
                self._hass,
                f"{ISSUE_SOCKET_GONE}_{subentry.subentry_id}",
                ISSUE_SOCKET_GONE,
                control.socket in gone,
                placeholders={"switch": subentry.title, "socket": control.socket or ""},
                data={"entry_id": entry.entry_id, "subentry_id": subentry.subentry_id},
            )

    def _thermostat_id(self, control: ThermostatControl) -> str | None:
        return er.async_get(self._hass).async_get_entity_id(
            "climate", DOMAIN, control.subentry_id
        )
