"""Custom types for bestiarium_mock."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import MockDevicesCoordinator


type BestiariumMockConfigEntry = ConfigEntry[MockDevicesCoordinator]
