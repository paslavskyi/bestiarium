"""Sensor platform for bestiarium_climate: the control state of a thermostat."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SIGNAL_CONTROL_CHANGED, ControlState

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .balancer import BalancingCoordinator
    from .control import ThermostatControl
    from .data import BestiariumClimateConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the control state of each thermostat, on its device."""
    coordinator: BalancingCoordinator = hass.data[DOMAIN][entry.entry_id]
    for subentry_id, control in coordinator.controls.items():
        async_add_entities(
            [ControlStateSensor(control)], config_subentry_id=subentry_id
        )


class ControlStateSensor(SensorEntity):
    """
    Whether the balancer can control the thermostat, or what stops it.

    A lasting problem is a state (docs/concepts/problem-reporting.md); the
    details change often and stay out of the history.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "control_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"failed_attempts", "next_attempt"})

    def __init__(self, control: ThermostatControl) -> None:
        """Show the control of one thermostat."""
        self._control = control
        self._attr_options = [state.value for state in ControlState]
        self._attr_unique_id = f"{control.subentry_id}_control_state"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, control.subentry_id)})

    @property
    def native_value(self) -> str:
        """Return the control state."""
        return self._control.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the attempts and the next attempt."""
        return self._control.attributes

    async def async_added_to_hass(self) -> None:
        """Follow the control."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CONTROL_CHANGED.format(self._control.subentry_id),
                self.async_write_ha_state,
            )
        )
