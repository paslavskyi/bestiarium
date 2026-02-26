"""Custom types for bestiarium_climate."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


type BestiariumClimateConfigEntry = ConfigEntry[dict[str, Any]]


class SwitchConfigData(TypedDict, total=False):
    """Configuration data for a switch subentry."""

    name: str
    mode: str
    heating_action: dict[str, Any]
    cooling_action: dict[str, Any]
    off_action: dict[str, Any]
    power_entity: str
    min_power: float


class ThermostatConfigDataRequired(TypedDict):
    """Required fields for thermostat subentry config."""

    name: str
    temperature_sensor: str
    target_switch: str


class ThermostatConfigData(ThermostatConfigDataRequired, total=False):
    """Configuration data for a thermostat subentry."""

    thermostat_mode: str
    min_temp: float
    max_temp: float
    cold_tolerance: float
    hot_tolerance: float
    min_cycle_duration: dict[str, int]
    away_temp: float
    comfort_temp: float
    eco_temp: float
    home_temp: float
    sleep_temp: float
    activity_temp: float
