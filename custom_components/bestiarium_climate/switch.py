"""Switch platform for bestiarium_climate."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_COOLING_ACTION,
    CONF_HEATING_ACTION,
    CONF_MIN_POWER,
    CONF_OFF_ACTION,
    CONF_POWER_ENTITY,
    CONF_SWITCH_MODE,
    DOMAIN,
    LOGGER,
    SUBENTRY_TYPE_SWITCH,
    ClimateMode,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .data import BestiariumClimateConfigEntry


async def _execute_action(
    hass: HomeAssistant, action: dict[str, Any] | list[Any]
) -> None:
    """Execute a single action dict (service call)."""
    if isinstance(action, list):
        if not action:
            return
        action = action[0]
    if not isinstance(action, dict):
        return
    action_str = action.get("action")
    if not action_str or "." not in action_str:
        LOGGER.warning("Invalid action: missing or malformed 'action'")
        return
    domain, service = action_str.split(".", 1)
    target = action.get("target") or {}
    data = dict(action.get("data") or {})
    if target:
        data.update(target)
    await hass.services.async_call(domain, service, data, blocking=False)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BestiariumClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switch entities from switch subentries."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_TYPE_SWITCH:
            async_add_entities(
                [VirtualSwitch(hass, entry, subentry)],
                config_subentry_id=subentry.subentry_id,
            )


class VirtualSwitch(SwitchEntity):
    """Virtual switch that runs actions and reflects power sensor state."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BestiariumClimateConfigEntry,
        subentry: ConfigSubentry,
    ) -> None:
        """Initialize the virtual switch."""
        self._hass = hass
        self._entry = entry
        self._subentry = subentry

        self._attr_unique_id = subentry.subentry_id
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Bestiarium",
            model="Virtual Switch",
            entry_type=DeviceEntryType.SERVICE,
        )

        self._mode = subentry.data.get(CONF_SWITCH_MODE, ClimateMode.HEATING)
        self._heating_action = subentry.data.get(CONF_HEATING_ACTION)
        self._cooling_action = subentry.data.get(CONF_COOLING_ACTION)
        self._off_action = subentry.data.get(CONF_OFF_ACTION)
        self._power_entity = subentry.data.get(CONF_POWER_ENTITY) or ""
        self._min_power = float(subentry.data.get(CONF_MIN_POWER) or 0.0)

        self._attr_is_on = False
        self._unsub: Any = None

    @property
    def is_on(self) -> bool:
        """Return true if switch is on (from power sensor)."""
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn the switch on: run heating or cooling action by mode."""
        if self._mode == ClimateMode.HEATING and self._heating_action:
            await _execute_action(self._hass, self._heating_action)
        elif self._mode == ClimateMode.COOLING and self._cooling_action:
            await _execute_action(self._hass, self._cooling_action)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002
        """Turn the switch off: run off action."""
        if self._off_action:
            await _execute_action(self._hass, self._off_action)
        self.async_write_ha_state()

    def _update_from_power(self, state: Any) -> None:
        """Update is_on from power sensor state."""
        if state is None or state.state in ("unknown", "unavailable", ""):
            self._attr_is_on = False
            return
        try:
            power = float(state.state)
        except (TypeError, ValueError):
            self._attr_is_on = False
            return
        self._attr_is_on = power >= self._min_power

    @callback
    def _async_power_changed(self, event: Any) -> None:
        """Handle power sensor state change."""
        new_state = event.data.get("new_state")
        self._update_from_power(new_state)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to power sensor and set initial state."""
        if self._power_entity:
            state = self._hass.states.get(self._power_entity)
            self._update_from_power(state)
            self._unsub = async_track_state_change_event(
                self._hass,
                [self._power_entity],
                self._async_power_changed,
            )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from power sensor."""
        if self._unsub:
            self._unsub()
            self._unsub = None
