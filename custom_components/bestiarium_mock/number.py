"""Number platform for bestiarium_mock: the test knobs of the mock devices."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfPower, UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    FAULT_PARAMS,
    LOGGER,
    MOCK_THERMOMETERS,
    MockThermometerSpec,
)
from .entity import MockSocketEntity

if TYPE_CHECKING:
    from dataclasses import Field

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import MockDevicesCoordinator
    from .data import BestiariumMockConfigEntry
    from .faults import MockSocket


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumMockConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number platform."""
    LOGGER.debug("Setting up mock device number entities")

    coordinator: MockDevicesCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []

    for socket in coordinator.sockets.values():
        if socket.spec.has_variable_power:
            entities.append(MockPowerNumber(socket, "mock_power"))
        entities.extend(MockFaultParamNumber(socket, param) for param in FAULT_PARAMS)

    # Temperature number inputs for thermometers
    for thermo_spec in MOCK_THERMOMETERS:
        number = MockTemperatureNumber(entry, thermo_spec, coordinator)
        coordinator.register_temperature_number(thermo_spec.device_id, number)
        entities.append(number)

    async_add_entities(entities)


class MockPowerNumber(MockSocketEntity, RestoreNumber):
    """The power a variable-power load draws when on."""

    _attr_translation_key = "mock_power"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_step = 1
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, socket: MockSocket, key: str) -> None:
        """Take the range from the socket's spec."""
        super().__init__(socket, key)
        self._attr_native_min_value = socket.spec.power_min_w
        self._attr_native_max_value = socket.spec.power_max_w

    @property
    def native_value(self) -> float:
        """Return the power the load draws when on."""
        return self._socket.power_w

    async def async_set_native_value(self, value: float) -> None:
        """Set the power the load draws when on."""
        self._socket.set_power(value)

    async def async_added_to_hass(self) -> None:
        """Restore the power."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_number_data()) is not None:
            self._socket.power_w = last.native_value


class MockFaultParamNumber(MockSocketEntity, RestoreNumber):
    """One fault parameter; editable only in the CUSTOM fault profile."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1

    def __init__(self, socket: MockSocket, param: Field) -> None:
        """Show the parameter with its range and unit."""
        super().__init__(socket, param.name)
        self._key = param.name
        self._attr_translation_key = param.name
        self._attr_native_min_value = param.metadata["min"]
        self._attr_native_max_value = param.metadata["max"]
        self._attr_native_unit_of_measurement = param.metadata["unit"]

    @property
    def native_value(self) -> float:
        """Return the parameter in effect."""
        return getattr(self._socket.params, self._key)

    async def async_set_native_value(self, value: float) -> None:
        """Change the parameter of the CUSTOM profile."""
        self._socket.set_param(self._key, value)

    async def async_added_to_hass(self) -> None:
        """Restore the parameter into the CUSTOM profile."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_number_data()) is not None:
            self._socket.restore_param(self._key, last.native_value)


class MockTemperatureNumber(NumberEntity, RestoreEntity):
    """Number entity to manually set mock temperature for thermometers."""

    _attr_has_entity_name = True
    _attr_translation_key = "mock_temperature"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:thermometer"

    def __init__(
        self,
        entry: BestiariumMockConfigEntry,
        thermo_spec: MockThermometerSpec,
        coordinator: MockDevicesCoordinator,
    ) -> None:
        """Initialize the mock temperature number entity."""
        super().__init__()
        self._entry = entry
        self._thermo_spec = thermo_spec
        self._coordinator = coordinator

        # Set min/max from thermometer spec
        self._attr_native_min_value = thermo_spec.temp_min_c
        self._attr_native_max_value = thermo_spec.temp_max_c
        self._attr_native_step = 0.1

        # Default to the thermometer's default temperature
        self._attr_native_value = thermo_spec.default_temp_c

        # Entity attributes
        self._attr_unique_id = f"mock_{thermo_spec.device_id}_mock_temperature"

        # Device info - group under same device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"mock_{thermo_spec.device_id}")},
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set the native value."""
        self._attr_native_value = value
        self.async_write_ha_state()
        self._coordinator.notify_temperature_number_changed(self._thermo_spec.device_id)

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is not None:
            try:
                value = float(last_state.state)
                if self._attr_native_min_value <= value <= self._attr_native_max_value:
                    self._attr_native_value = value
            except (ValueError, TypeError):
                pass
