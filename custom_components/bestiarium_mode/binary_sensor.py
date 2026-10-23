"""Binary sensor platform for bestiarium_mode: the power load zones."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL_ZONE_CHANGED
from .zones import Zone, ZoneTracker

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BestiariumModeConfigEntry

ZONE_ICONS = {
    Zone.GREEN: "mdi:check-circle",
    Zone.YELLOW: "mdi:alert-circle",
    Zone.RED: "mdi:close-circle",
    Zone.OVERLOAD: "mdi:flash-alert",
}


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: BestiariumModeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the four zone sensors, if the AC load limits are configured."""
    if (tracker := entry.runtime_data) is not None:
        async_add_entities(PowerLoadBinarySensor(tracker, entry, z) for z in ZONE_ICONS)


class PowerLoadBinarySensor(BinarySensorEntity):
    """
    On while its zone is the current one.

    In the pause before green all four are off; with no power data they are
    unavailable.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, tracker: ZoneTracker, entry: BestiariumModeConfigEntry, zone: Zone
    ) -> None:
        """Show one zone of the tracker."""
        self._tracker = tracker
        self._entry = entry
        self._zone = zone
        self._attr_unique_id = f"power_load_{zone}"
        self._attr_translation_key = f"power_load_{zone}"
        self._attr_suggested_object_id = f"power_load_{zone}"
        self._attr_icon = ZONE_ICONS[zone]

    @property
    def is_on(self) -> bool:
        """Return whether the zone is the current one."""
        return self._tracker.zone is self._zone

    @property
    def available(self) -> bool:
        """Return False while there is no power data."""
        return self._tracker.zone is not Zone.NO_DATA

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the thresholds; green also tells whether it is on pause."""
        config = self._tracker.config
        attrs: dict[str, Any] = {
            "zone": self._zone.value,
            "yellow_threshold": config.yellow_threshold,
            "red_threshold": config.red_threshold,
            "limit_w": config.limit_w,
        }
        if self._zone is Zone.GREEN:
            attrs["recovery_pending"] = self._tracker.pause_pending
        return attrs

    async def async_added_to_hass(self) -> None:
        """Follow the zone."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ZONE_CHANGED.format(self._entry.entry_id),
                self.async_write_ha_state,
            )
        )
