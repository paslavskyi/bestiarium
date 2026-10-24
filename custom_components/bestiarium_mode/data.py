"""Custom types for bestiarium_mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import CONF_AC_LOAD_LIMIT_W, CONF_AC_LOAD_LIMITS, CONF_AC_POWER_SENSOR

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .zones import ZoneTracker


# The zone tracker, if the AC load limits are configured
type BestiariumModeConfigEntry = ConfigEntry[ZoneTracker | None]


def power_load_monitoring_configured(entry: BestiariumModeConfigEntry) -> bool:
    """Return True if a power sensor and a load limit are configured."""
    ac_load_limits = entry.data.get(CONF_AC_LOAD_LIMITS) or {}
    return bool(
        ac_load_limits.get(CONF_AC_POWER_SENSOR)
        and ac_load_limits.get(CONF_AC_LOAD_LIMIT_W)
    )
