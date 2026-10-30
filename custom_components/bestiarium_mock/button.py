"""Button platform for bestiarium_mock: the remote of the stand's IR conditioners."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .const import DOMAIN
from .entity import MockIrAcEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import MockDevicesCoordinator
    from .data import BestiariumMockConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumMockConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the remote's "off" of each IR air conditioner."""
    coordinator: MockDevicesCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MockIrAcRemoteOffButton(ac, "remote_off") for ac in coordinator.ir_acs.values()
    )


class MockIrAcRemoteOffButton(MockIrAcEntity, ButtonEntity):
    """A test knob: the unit turned off with its remote, no frame of the emitter."""

    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        """Turn the unit off as its remote does."""
        self._ac.remote_off()
