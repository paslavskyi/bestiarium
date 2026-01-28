"""Binary sensor platform for bestiarium_mode."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_AC_LOAD_LIMIT_W,
    CONF_AC_LOAD_LIMITS,
    CONF_AC_POWER_SENSOR,
    CONF_AC_RED_ZONE_PERCENT,
    CONF_AC_YELLOW_ZONE_PERCENT,
    LOGGER,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BestiariumModeConfigEntry


class PowerLoadZone(StrEnum):
    """Power load zone types."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    OVERLOAD = "overload"


@dataclass
class PowerLoadConfig:
    """Configuration for power load sensors."""

    power_sensor_id: str
    yellow_threshold: float
    red_threshold: float
    limit_w: float


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumModeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up the binary sensor platform.

    Creates 4 binary sensors for power load zones (traffic light style).
    Skips entity creation if ac_load_limits config is not present.
    """
    ac_load_limits = entry.data.get(CONF_AC_LOAD_LIMITS)

    # Skip entity creation if AC load limits not configured
    if not ac_load_limits:
        LOGGER.debug("AC load limits not configured, skipping binary sensor creation")
        return

    power_sensor = ac_load_limits.get(CONF_AC_POWER_SENSOR)
    limit_w = ac_load_limits.get(CONF_AC_LOAD_LIMIT_W)
    yellow_percent = ac_load_limits.get(CONF_AC_YELLOW_ZONE_PERCENT, 75)
    red_percent = ac_load_limits.get(CONF_AC_RED_ZONE_PERCENT, 90)

    if not power_sensor or not limit_w:
        LOGGER.debug("Missing power sensor or limit, skipping binary sensor creation")
        return

    # Calculate thresholds and create config
    config = PowerLoadConfig(
        power_sensor_id=power_sensor,
        yellow_threshold=limit_w * yellow_percent / 100,
        red_threshold=limit_w * red_percent / 100,
        limit_w=limit_w,
    )

    entities = [
        PowerLoadBinarySensor(hass, PowerLoadZone.GREEN, config),
        PowerLoadBinarySensor(hass, PowerLoadZone.YELLOW, config),
        PowerLoadBinarySensor(hass, PowerLoadZone.RED, config),
        PowerLoadBinarySensor(hass, PowerLoadZone.OVERLOAD, config),
    ]

    async_add_entities(entities)


class PowerLoadBinarySensor(BinarySensorEntity):
    """Binary sensor for power load zone indication."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        zone: PowerLoadZone,
        config: PowerLoadConfig,
    ) -> None:
        """Initialize the power load binary sensor."""
        super().__init__()
        self.hass = hass
        self._zone = zone
        self._config = config

        # Entity attributes
        self._attr_unique_id = f"power_load_{zone.value}"
        self._attr_translation_key = f"power_load_{zone.value}"

        # Set icon based on zone (Signal/Status style)
        match zone:
            case PowerLoadZone.GREEN:
                self._attr_icon = "mdi:check-circle"
            case PowerLoadZone.YELLOW:
                self._attr_icon = "mdi:alert-circle"
            case PowerLoadZone.RED:
                self._attr_icon = "mdi:close-circle"
            case PowerLoadZone.OVERLOAD:
                self._attr_icon = "mdi:flash-alert"

        # Internal state
        self._is_on: bool | None = None
        self._unsubscribe: Callable[[], None] | None = None

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        return self._is_on

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._is_on is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "zone": self._zone.value,
            "yellow_threshold": self._config.yellow_threshold,
            "red_threshold": self._config.red_threshold,
            "limit_w": self._config.limit_w,
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to power sensor state changes when added to hass."""
        await super().async_added_to_hass()

        # Get initial state
        self._update_from_power_sensor()

        # Subscribe to state changes
        self._unsubscribe = async_track_state_change_event(
            self.hass,
            [self._config.power_sensor_id],
            self._async_power_sensor_changed,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from power sensor state changes when removed."""
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    @callback
    def _async_power_sensor_changed(self, event: Event) -> None:
        """Handle power sensor state changes."""
        new_state = event.data.get("new_state")
        if new_state is None:
            self._is_on = None
        else:
            self._calculate_zone_state(new_state.state)

        self.async_write_ha_state()

    def _update_from_power_sensor(self) -> None:
        """Update state from current power sensor value."""
        state = self.hass.states.get(self._config.power_sensor_id)
        if state is None:
            self._is_on = None
        else:
            self._calculate_zone_state(state.state)

    def _calculate_zone_state(self, power_state: str) -> None:
        """Calculate binary sensor state based on power value and zone."""
        # Handle unavailable/unknown states
        if power_state in (STATE_UNAVAILABLE, STATE_UNKNOWN, None):
            self._is_on = None
            return

        # Try to parse power value
        try:
            power = float(power_state)
        except (ValueError, TypeError):
            LOGGER.debug(
                "Invalid power sensor value '%s' for zone %s",
                power_state,
                self._zone.value,
            )
            self._is_on = None
            return

        # Calculate zone state based on thresholds
        match self._zone:
            case PowerLoadZone.GREEN:
                self._is_on = power < self._config.yellow_threshold
            case PowerLoadZone.YELLOW:
                self._is_on = (
                    self._config.yellow_threshold <= power < self._config.red_threshold
                )
            case PowerLoadZone.RED:
                self._is_on = self._config.red_threshold <= power < self._config.limit_w
            case PowerLoadZone.OVERLOAD:
                self._is_on = power >= self._config.limit_w
