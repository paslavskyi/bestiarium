"""Custom types for bestiarium_devices."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


type BestiariumDevicesConfigEntry = ConfigEntry[dict]
