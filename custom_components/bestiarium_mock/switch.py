"""Switch platform for bestiarium_mock."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, LOGGER, MOCK_DEVICES, MockDeviceSpec

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
    """Set up the switch platform."""
    LOGGER.debug("Setting up mock device switches")

    coordinator: MockDevicesCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []

    for device_spec in MOCK_DEVICES:
        # Main device switch
        device_switch = MockDeviceSwitch(entry, device_spec, coordinator)
        coordinator.register_device_switch(device_spec.device_id, device_switch)
        entities.append(device_switch)

        # Mock enabled switch
        mock_enabled = MockEnabledSwitch(entry, device_spec, coordinator)
        coordinator.register_mock_enabled_switch(device_spec.device_id, mock_enabled)
        entities.append(mock_enabled)

    async_add_entities(entities)


class MockDeviceSwitch(SwitchEntity, RestoreEntity):
    """Main on/off switch for a mock device."""

    _attr_has_entity_name = True
    _attr_translation_key = "device_switch"

    def __init__(
        self,
        entry: BestiariumMockConfigEntry,
        device_spec: MockDeviceSpec,
        coordinator: MockDevicesCoordinator,
    ) -> None:
        """Initialize the mock device switch."""
        super().__init__()
        self._entry = entry
        self._device_spec = device_spec
        self._coordinator = coordinator
        self._attr_is_on = False

        # Entity attributes
        self._attr_unique_id = f"mock_{device_spec.device_id}"

        # Device info - group entities under one device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"mock_{device_spec.device_id}")},
            name=device_spec.name,
            manufacturer="Bestiarium",
            model="Mock Smart Socket",
        )

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn the switch on."""
        self._attr_is_on = True
        self.async_write_ha_state()
        self._coordinator.notify_switch_changed(self._device_spec.device_id)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn the switch off."""
        self._attr_is_on = False
        self.async_write_ha_state()
        self._coordinator.notify_switch_changed(self._device_spec.device_id)

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is not None:
            self._attr_is_on = last_state.state == "on"


class MockEnabledSwitch(SwitchEntity, RestoreEntity):
    """Switch to enable/disable mock behavior for a device."""

    _attr_has_entity_name = True
    _attr_translation_key = "mock_enabled"
    _attr_icon = "mdi:test-tube"

    def __init__(
        self,
        entry: BestiariumMockConfigEntry,
        device_spec: MockDeviceSpec,
        coordinator: MockDevicesCoordinator,
    ) -> None:
        """Initialize the mock enabled switch."""
        super().__init__()
        self._entry = entry
        self._device_spec = device_spec
        self._coordinator = coordinator
        self._attr_is_on = True  # Default to enabled

        # Entity attributes
        self._attr_unique_id = f"mock_{device_spec.device_id}_mock_enabled"

        # Device info - group under same device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"mock_{device_spec.device_id}")},
        )

    @property
    def is_on(self) -> bool:
        """Return true if mock behavior is enabled."""
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Enable mock behavior."""
        self._attr_is_on = True
        self.async_write_ha_state()
        self._coordinator.notify_mock_enabled_changed(self._device_spec.device_id)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Disable mock behavior."""
        self._attr_is_on = False
        self.async_write_ha_state()
        self._coordinator.notify_mock_enabled_changed(self._device_spec.device_id)

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is not None:
            self._attr_is_on = last_state.state == "on"
