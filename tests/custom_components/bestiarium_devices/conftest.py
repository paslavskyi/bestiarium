"""Fixtures for bestiarium_devices tests."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, ServiceRegistry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_devices.balancer import BalancingCoordinator
from custom_components.bestiarium_devices.const import (
    CONF_DEVICE_ID,
    CONF_RESET_POWER_MEMORY,
    DOMAIN,
    DOMAIN_BESTIARIUM_MODE,
)
from custom_components.bestiarium_devices.control import (
    DeviceControl,
    async_get_response_memory,
)
from custom_components.bestiarium_devices.power_memory import async_get_power_memory
from custom_components.bestiarium_devices.strategies import Strategies

from .mode_entities import register_mode_entities


async def started_coordinator(hass: HomeAssistant) -> BalancingCoordinator:
    """Start a balancer following every device entry, as their setup does."""
    coordinator = BalancingCoordinator(hass)
    await coordinator.async_start()
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator.async_add_device(entry)
    return coordinator


async def device_strategies(hass: HomeAssistant) -> Strategies:
    """Return the strategies over every device entry, with no balancer running."""
    memory = await async_get_response_memory(hass)
    controls = {
        entry.entry_id: DeviceControl(hass, entry, memory)
        for entry in hass.config_entries.async_entries(DOMAIN)
    }
    return Strategies(
        hass,
        controls,
        await async_get_power_memory(hass),
        BalancingCoordinator(hass)._overload_cleared,  # noqa: SLF001
    )


async def keep_device(
    hass: HomeAssistant, result: ConfigFlowResult
) -> ConfigFlowResult:
    """Keep the device in the first step of an extended device's reconfigure."""
    assert result["step_id"] == "pick_device"  # noqa: S101
    device_id = next(
        key.default() for key in result["data_schema"].schema if key == CONF_DEVICE_ID
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: device_id, CONF_RESET_POWER_MEMORY: False}
    )


@pytest.fixture
async def bestiarium_mode_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create a Bestiarium Mode config entry for testing."""
    entry = MockConfigEntry(
        domain=DOMAIN_BESTIARIUM_MODE,
        unique_id="bestiarium_mode",
        data={
            "options": [
                {"name": "Eco", "description": "Energy saving", "icon": "mdi:leaf"},
                {"name": "Boost", "description": "High priority", "icon": "mdi:flash"},
            ],
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mode_entities(hass: HomeAssistant) -> None:
    """Register bestiarium_mode entities as a production install has them."""
    register_mode_entities(hass)


class FakeRelays:
    """Device relays: switch.turn_on/turn_off on them only flips their state."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize with no relays."""
        self._hass = hass
        self.entity_ids: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        # Relays that take the commands but keep their state, like a stale one
        self.stuck: set[str] = set()

    def add(self, entity_id: str, *, on: bool) -> None:
        """Add a relay in the given state."""
        self.entity_ids.add(entity_id)
        self._hass.states.async_set(entity_id, STATE_ON if on else STATE_OFF)

    def is_on(self, entity_id: str) -> bool:
        """Return whether the relay is on."""
        return self._hass.states.get(entity_id).state == STATE_ON


@pytest.fixture
def relays(hass: HomeAssistant) -> Iterator[FakeRelays]:
    """Intercept switch services for device relays; others run normally."""
    fake = FakeRelays(hass)
    original = ServiceRegistry.async_call

    async def async_call(
        registry: ServiceRegistry,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        entity_id = (service_data or {}).get("entity_id")
        if domain == "switch" and entity_id in fake.entity_ids:
            fake.calls.append((service, entity_id))
            if entity_id in fake.stuck:
                return None
            hass.states.async_set(
                entity_id, STATE_ON if service == "turn_on" else STATE_OFF
            )
            return None
        return await original(registry, domain, service, service_data, *args, **kwargs)

    with patch.object(ServiceRegistry, "async_call", async_call):
        yield fake
