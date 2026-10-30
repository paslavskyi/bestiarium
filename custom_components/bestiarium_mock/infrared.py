"""Infrared platform for bestiarium_mock: the emitter of each IR air conditioner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.infrared import InfraredEmitterEntity

from .const import DOMAIN
from .entity import MockIrAcEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from infrared_protocols.commands import Command

    from .coordinator import MockDevicesCoordinator
    from .data import BestiariumMockConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumMockConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the emitters."""
    coordinator: MockDevicesCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MockIrEmitter(ac, "emitter") for ac in coordinator.ir_acs.values()
    )


class MockIrEmitter(MockIrAcEntity, InfraredEmitterEntity):
    """
    The stand's IR emitter: its signal reaches its air conditioner.

    Its state is the time of the last signal sent (HA's emitter), so the
    logbook shows every frame.
    """

    async def async_send_command(self, command: Command) -> None:
        """Send the signal to the air conditioner."""
        self._ac.hear(command.get_raw_timings())
