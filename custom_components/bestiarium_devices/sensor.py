"""Sensor platform for bestiarium_devices: expected power and control state."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    CONF_ESTIMATED_POWER,
    DATA_COORDINATOR,
    DOMAIN,
    SIGNAL_CONTROL_CHANGED,
    SIGNAL_EXPECTED_POWER_CHANGED,
)
from .control import ControlState
from .entity import BestiariumDeviceEntity
from .power_memory import PowerMemory, async_get_power_memory

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .control import DeviceControl
    from .data import BestiariumDevicesConfigEntry

# Translated attribute value explaining when the manual value stops counting
MANUAL_POWER_NOTE = "counts_until_real_runs"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumDevicesConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the expected power and control state sensors of a device entry."""
    memory = await async_get_power_memory(hass)
    control = hass.data[DOMAIN][DATA_COORDINATOR].control(entry)
    async_add_entities(
        [
            ExpectedPowerSensor(hass, entry, memory),
            ControlStateSensor(hass, entry, control),
        ]
    )


class ExpectedPowerSensor(BestiariumDeviceEntity, SensorEntity):
    """The power the balancer expects the device to draw when turned on."""

    _key = "expected_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumDevicesConfigEntry,
        memory: PowerMemory,
    ) -> None:
        """Follow the device's power memory."""
        super().__init__(hass, entry)
        self._memory = memory

    async def async_added_to_hass(self) -> None:
        """Update on memory changes and on a new manual value."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_EXPECTED_POWER_CHANGED.format(self._entry.entry_id),
                self.async_write_ha_state,
            )
        )

    @property
    def native_value(self) -> float:
        """Return the expected power."""
        return self._memory.expected_power(self._entry)[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return what the expectation is based on."""
        return {
            "estimate_source": self._memory.expected_power(self._entry)[1],
            "runs": [
                record.as_dict()
                for record in self._memory.records(self._entry.entry_id)
            ],
            "manual_power_w": self._entry.options.get(CONF_ESTIMATED_POWER) or None,
            "manual_power_note": MANUAL_POWER_NOTE,
        }


class ControlStateSensor(BestiariumDeviceEntity, SensorEntity):
    """
    Whether the balancer can control the device, or the problem that stops it.

    A lasting problem is a state (docs/concepts/problem-reporting.md); the
    details change often and stay out of the history.
    """

    _key = "control_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset(
        {
            "direction",
            "failed_attempts",
            "last_attempt",
            "next_attempt",
            "response_time",
            "dropouts_last_hour",
        }
    )

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumDevicesConfigEntry,
        control: DeviceControl,
    ) -> None:
        """Show the device's control state."""
        super().__init__(hass, entry)
        self._control = control

    async def async_added_to_hass(self) -> None:
        """Update on every change of the control state or its details."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CONTROL_CHANGED.format(self._entry.entry_id),
                self.async_write_ha_state,
            )
        )

    @property
    def options(self) -> list[str]:
        """Return the control states."""
        return [state.value for state in ControlState]

    @property
    def native_value(self) -> str:
        """Return the control state."""
        return self._control.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the details of the control state."""
        return self._control.attributes
