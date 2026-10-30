"""Sensor platform for bestiarium_mock."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfPower, UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from ..bestiarium_ir.kelon168 import Kelon168Fan  # noqa: TID252
from .const import (
    DOMAIN,
    LOGGER,
    MOCK_GRID,
    MOCK_THERMOMETERS,
    IrFrameOutcome,
    MockThermometerSpec,
)
from .entity import MockIrAcEntity, MockSocketEntity
from .ir_ac import STATES

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import MockDevicesCoordinator
    from .data import BestiariumMockConfigEntry
    from .readings import MockReading


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumMockConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    LOGGER.debug("Setting up mock device power sensors")

    coordinator: MockDevicesCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        MockDevicePowerSensor(socket, "power")
        for socket in coordinator.sockets.values()
    ]

    # Temperature sensors for each mock thermometer
    for thermo_spec in MOCK_THERMOMETERS:
        sensor = MockThermometerTemperatureSensor(
            entry, thermo_spec, coordinator.readings[thermo_spec.device_id]
        )
        coordinator.register_temperature_sensor(thermo_spec.device_id, sensor)
        entities.append(sensor)

    # Grid power sensor
    grid_sensor = MockGridPowerSensor(entry, coordinator.readings[MOCK_GRID.device_id])
    coordinator.register_grid_power_sensor(grid_sensor)
    entities.append(grid_sensor)

    for ac in coordinator.ir_acs.values():
        entities.append(MockIrAcStateSensor(ac, "ac_state"))
        entities.append(MockIrAcLastFrameSensor(ac, "last_frame"))

    async_add_entities(entities)


class MockDevicePowerSensor(MockSocketEntity, SensorEntity):
    """Power the load of a mock socket draws; it drops out with the socket."""

    _attr_translation_key = "power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    @property
    def available(self) -> bool:
        """Return whether the socket is reachable."""
        return self._socket.available

    @property
    def native_value(self) -> float:
        """Return the power the load draws."""
        return self._socket.power


class MockGridPowerSensor(SensorEntity):
    """Power sensor for the mock grid (total power consumption)."""

    _attr_has_entity_name = True
    _attr_translation_key = "grid_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, entry: BestiariumMockConfigEntry, reading: MockReading) -> None:
        """Initialize the grid power sensor."""
        super().__init__()
        self._entry = entry
        self._reading = reading
        self._power: float = 0

        # Entity attributes
        self._attr_unique_id = f"mock_{MOCK_GRID.device_id}_power"

        # Device info for grid
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"mock_{MOCK_GRID.device_id}")},
            translation_key=MOCK_GRID.device_id,
            manufacturer="Bestiarium",
            model="Mock Grid Monitor",
        )

    @property
    def available(self) -> bool:
        """Return False while the reading's profile hides the data."""
        return self._reading.available

    @property
    def native_value(self) -> float:
        """Return the power HA sees."""
        return self._reading.shown(self._power)

    async def async_added_to_hass(self) -> None:
        """Follow the reading's profile."""
        self.async_on_remove(self._reading.add_listener(self.async_write_ha_state))

    def set_power(self, power: float) -> None:
        """Set the power value (called by coordinator)."""
        self._power = power
        self.async_write_ha_state()


class MockThermometerTemperatureSensor(SensorEntity):
    """Temperature sensor for a mock thermometer."""

    _attr_has_entity_name = True
    _attr_translation_key = "temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        entry: BestiariumMockConfigEntry,
        thermo_spec: MockThermometerSpec,
        reading: MockReading,
    ) -> None:
        """Initialize the temperature sensor."""
        super().__init__()
        self._entry = entry
        self._thermo_spec = thermo_spec
        self._reading = reading
        self._temperature: float = thermo_spec.default_temp_c

        # Entity attributes
        self._attr_unique_id = f"mock_{thermo_spec.device_id}_temperature"

        # Device info - group under same device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"mock_{thermo_spec.device_id}")},
            translation_key=thermo_spec.device_id,
            manufacturer="Bestiarium",
            model="Mock Thermometer",
        )

    @property
    def available(self) -> bool:
        """Return False while the reading's profile hides the data."""
        return self._reading.available

    @property
    def native_value(self) -> float:
        """Return the temperature HA sees."""
        return self._reading.shown(self._temperature)

    async def async_added_to_hass(self) -> None:
        """Follow the reading's profile."""
        self.async_on_remove(self._reading.add_listener(self.async_write_ha_state))

    def set_temperature(self, temperature: float) -> None:
        """Set the temperature value (called by coordinator)."""
        self._temperature = temperature
        self.async_write_ha_state()


class MockIrAcStateSensor(MockIrAcEntity, SensorEntity, RestoreEntity):
    """What the stand's air conditioner does: off or its mode; restored."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options: ClassVar[list[str]] = ["off", *STATES.values()]

    @property
    def native_value(self) -> str:
        """Return the unit's state."""
        return self._ac.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the target, the fan speed and the display."""
        return {
            "temperature": self._ac.temperature,
            "fan": self._ac.fan.value,
            "display": self._ac.display_on,
        }

    async def async_added_to_hass(self) -> None:
        """Restore the unit."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None or last.state not in self._attr_options:
            return
        attributes = last.attributes
        mode = next(
            (mode for mode, state in STATES.items() if state == last.state),
            self._ac.mode,
        )
        self._ac.restore(
            power=last.state != "off",
            mode=mode,
            temperature=int(attributes.get("temperature", self._ac.temperature)),
            fan=Kelon168Fan(attributes.get("fan", self._ac.fan)),
            display_on=bool(attributes.get("display", True)),
        )


class MockIrAcLastFrameSensor(MockIrAcEntity, SensorEntity):
    """What the stand's air conditioner did with the last frame it heard."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options: ClassVar[list[str]] = [outcome.value for outcome in IrFrameOutcome]

    @property
    def native_value(self) -> str | None:
        """Return the outcome of the last frame."""
        return self._ac.last_outcome

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the frame: its button and the state it carries."""
        if (frame := self._ac.last_frame) is None:
            return None
        return {
            "action": frame.action.name.lower(),
            "power": frame.power,
            "mode": STATES[frame.mode],
            "temperature": frame.temperature,
            "fan": frame.fan.value,
        }
