"""Fixtures for bestiarium_devices tests."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, ServiceRegistry

from .mode_entities import register_mode_entities


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
