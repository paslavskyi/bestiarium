"""Switch platform for bestiarium_devices: the per-device 'Allowed' switch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity

from .const import CONF_ALLOWED, DATA_COORDINATOR, DOMAIN
from .entity import BestiariumDeviceEntity
from .helpers import is_allowed

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BestiariumDevicesConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the 'Allowed' switch of a device entry."""
    async_add_entities([AllowedSwitch(hass, entry)])


class AllowedSwitch(BestiariumDeviceEntity, SwitchEntity):
    """
    Whether the balancer may turn the device on.

    The state lives in the config entry options, so it survives restarts and
    the balancer reads it synchronously with the rest of the entry.
    """

    _key = "allowed"

    @property
    def is_on(self) -> bool:
        """Return whether the balancer may turn the device on."""
        return is_allowed(self._entry)

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Allow the balancer to turn the device on."""
        await self._async_set_allowed(allowed=True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Forbid the balancer to turn the device on; turn it off now."""
        await self._async_set_allowed(allowed=False)

    async def _async_set_allowed(self, *, allowed: bool) -> None:
        if allowed == self.is_on:
            return
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_ALLOWED: allowed}
        )
        self.async_write_ha_state()
        await self.hass.data[DOMAIN][DATA_COORDINATOR].async_allowed_changed(
            self._entry
        )
