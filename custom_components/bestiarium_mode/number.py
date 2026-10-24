"""Number platform for bestiarium_mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import UnitOfPower

from .const import (
    CONF_AC_LOAD_LIMIT_W,
    CONF_AC_LOAD_LIMITS,
    EVENT_LOAD_LIMIT_CHANGED,
    LOAD_LIMIT_MAX_W,
    LOAD_LIMIT_MIN_W,
)
from .data import power_load_monitoring_configured

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BestiariumModeConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: BestiariumModeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the load limit number when power load monitoring is configured."""
    if power_load_monitoring_configured(entry):
        async_add_entities([PowerLoadLimitNumber(entry)])


class PowerLoadLimitNumber(NumberEntity):
    """
    Current AC load limit, changeable at runtime.

    The limit lives in the config entry data, so it survives restarts and the
    reconfigure form shows the same value.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "power_load_limit"
    _attr_unique_id = "power_load_limit"
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = LOAD_LIMIT_MIN_W
    _attr_native_max_value = LOAD_LIMIT_MAX_W
    _attr_native_step = 1

    def __init__(self, entry: BestiariumModeConfigEntry) -> None:
        """Initialize the load limit number."""
        self._entry = entry

    @property
    def native_value(self) -> int:
        """Return the current load limit."""
        return self._entry.data[CONF_AC_LOAD_LIMITS][CONF_AC_LOAD_LIMIT_W]

    async def async_set_native_value(self, value: float) -> None:
        """Store the new limit and let the zone sensors recalculate."""
        limit = int(value)
        ac_load_limits = self._entry.data[CONF_AC_LOAD_LIMITS]
        previous_limit = ac_load_limits[CONF_AC_LOAD_LIMIT_W]
        if limit == previous_limit:
            return

        self.hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                CONF_AC_LOAD_LIMITS: {**ac_load_limits, CONF_AC_LOAD_LIMIT_W: limit},
            },
        )
        self.async_write_ha_state()
        # Zone sensors recalculate inside this event's dispatch, so listeners
        # see only the final zone combination, never an intermediate one.
        self.hass.bus.async_fire(
            EVENT_LOAD_LIMIT_CHANGED,
            {"limit_w": limit, "previous_limit_w": previous_limit},
        )
