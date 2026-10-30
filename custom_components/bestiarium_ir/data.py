"""Custom types for bestiarium_ir."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .hisense import HisenseLink


# The links of the air conditioners, by subentry id
type BestiariumIrConfigEntry = ConfigEntry[dict[str, HisenseLink]]
