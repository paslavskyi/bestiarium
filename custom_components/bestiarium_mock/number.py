"""Number platform for bestiarium_mock."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    LOGGER,
    MOCK_DEVICES,
    MOCK_THERMOMETERS,
    MockDeviceSpec,
    MockThermometerSpec,
)

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
    """Set up the number platform."""
    LOGGER.debug("Setting up mock device number entities")

    coordinator: MockDevicesCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []

    # Only create number entities for devices with variable power
    for device_spec in MOCK_DEVICES:
        if device_spec.has_variable_power:
            number = MockPowerNumber(entry, device_spec, coordinator)
            coordinator.register_power_number(device_spec.device_id, number)
            entities.append(number)

    # Temperature number inputs for thermometers
    for thermo_spec in MOCK_THERMOMETERS:
        number = MockTemperatureNumber(entry, thermo_spec, coordinator)
        coordinator.register_temperature_number(thermo_spec.device_id, number)
        entities.append(number)

    async_add_entities(entities)


class MockPowerNumber(NumberEntity, RestoreEntity):
    """Number entity to set expected mock power for variable power devices."""

    _attr_has_entity_name = True
    _attr_translation_key = "mock_power"
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:lightning-bolt"

    def __init__(
        self,
        entry: BestiariumMockConfigEntry,
        device_spec: MockDeviceSpec,
        coordinator: MockDevicesCoordinator,
    ) -> None:
        """Initialize the mock power number entity."""
        super().__init__()
        self._entry = entry
        self._device_spec = device_spec
        self._coordinator = coordinator

        # Set min/max from device spec
        self._attr_native_min_value = float(device_spec.power_min_w or 0)
        self._attr_native_max_value = float(device_spec.power_max_w or 10000)
        self._attr_native_step = 1.0

        # Default to the device's default power
        self._attr_native_value = float(device_spec.power_w)

        # Entity attributes
        self._attr_unique_id = f"mock_{device_spec.device_id}_mock_power"

        # Device info - group under same device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"mock_{device_spec.device_id}")},
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set the native value."""
        self._attr_native_value = value
        self.async_write_ha_state()
        self._coordinator.notify_power_number_changed(self._device_spec.device_id)

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


class MockTemperatureNumber(NumberEntity, RestoreEntity):
    """Number entity to manually set mock temperature for thermometers."""

    _attr_has_entity_name = True
    _attr_translation_key = "mock_temperature"
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
