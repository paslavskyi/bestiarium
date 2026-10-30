"""Switch platform for bestiarium_mock: the switch of each mock socket."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, HOME_POWER_SWITCHES
from .entity import MockHomePowerEntity, MockIrAcEntity, MockSocketEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import MockDevicesCoordinator
    from .data import BestiariumMockConfigEntry
    from .faults import MockSocket
    from .home_power import MockHomePower
    from .ir_ac import MockIrAc


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumMockConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch platform."""
    coordinator: MockDevicesCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MockDeviceSwitch(socket, None) for socket in coordinator.sockets.values()
    )
    async_add_entities(
        MockIrKnobSwitch(socket, knob)
        for socket in coordinator.sockets.values()
        if socket.spec.has_ir
        for knob in ("ir_lost", "ir_hangs")
    )
    async_add_entities(
        MockIrAcKnobSwitch(ac, knob)
        for ac in coordinator.ir_acs.values()
        for knob in ("ir_lost", "ignores_off")
    )
    async_add_entities(
        MockHomePowerSwitch(coordinator.home_power, key) for key in HOME_POWER_SWITCHES
    )


class MockIrAcKnobSwitch(MockIrAcEntity, SwitchEntity, RestoreEntity):
    """A test knob of the stand's IR air conditioner: "IR lost", "Ignores off"."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, ac: MockIrAc, knob: str) -> None:
        """Initialize the knob."""
        super().__init__(ac, knob)
        self._knob = knob

    @property
    def is_on(self) -> bool:
        """Return whether the knob is on."""
        return getattr(self._ac, self._knob)

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn the knob on."""
        self._ac.set_knob(self._knob, on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn the knob off."""
        self._ac.set_knob(self._knob, on=False)

    async def async_added_to_hass(self) -> None:
        """Restore the knob."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._ac.set_knob(self._knob, on=last_state.state == STATE_ON)


class MockIrKnobSwitch(MockSocketEntity, SwitchEntity, RestoreEntity):
    """
    A test knob of the air conditioner's IR, checked by the stand's IR scripts.

    "IR lost": the IR does not reach the air conditioner. "IR hangs": its "on"
    never ends (a cloud call that never returns). The socket (the AC's power)
    still works.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_is_on = False

    def __init__(self, socket: MockSocket, knob: str) -> None:
        """Initialize the knob."""
        super().__init__(socket, knob)
        self._attr_translation_key = knob

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Break the IR."""
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Get the IR back."""
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore the knob."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._attr_is_on = last_state.state == STATE_ON


class MockDeviceSwitch(MockSocketEntity, SwitchEntity, RestoreEntity):
    """The socket's switch: shows the reported state, passes commands to it."""

    _attr_translation_key = "device_switch"

    @property
    def available(self) -> bool:
        """Return whether the socket is reachable."""
        return self._socket.available

    @property
    def is_on(self) -> bool:
        """Return the state the socket reports."""
        return self._socket.reported_on

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Send the socket a turn on command."""
        self._socket.command(on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Send the socket a turn off command."""
        self._socket.command(on=False)

    async def async_added_to_hass(self) -> None:
        """Restore the socket's state."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._socket.restore(on=last_state.state == STATE_ON)


class MockHomePowerSwitch(MockHomePowerEntity, SwitchEntity, RestoreEntity):
    """A relay of the stand's home, or whether its grid is there."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, home_power: MockHomePower, key: str) -> None:
        """Initialize the switch."""
        super().__init__(home_power, key)
        self._key = key

    @property
    def is_on(self) -> bool:
        """Return whether the relay is closed or the grid is there."""
        return self._home_power.switches[self._key]

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Close the relay, or bring the grid back."""
        self._home_power.set_switch(self._key, on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Open the relay, or cut the grid."""
        self._home_power.set_switch(self._key, on=False)

    async def async_added_to_hass(self) -> None:
        """Restore the switch."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._home_power.set_switch(self._key, on=last_state.state == STATE_ON)
