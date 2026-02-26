"""Fixtures for bestiarium_climate tests."""

from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.bestiarium_climate.const import (
    CONF_CAPACITY,
    CONF_MIN_WORKING_TIME,
    CONF_RECOVERY_TIMEOUT,
    DOMAIN,
    SUBENTRY_TYPE_SWITCH,
    SUBENTRY_TYPE_THERMOSTAT,
    ClimateMode,
)


def _make_subentry(
    subentry_id: str,
    subentry_type: str,
    title: str,
    data: dict,
) -> SimpleNamespace:
    """Create a minimal subentry-like object for entity tests."""
    obj = SimpleNamespace()
    obj.subentry_id = subentry_id
    obj.subentry_type = subentry_type
    obj.title = title
    obj.data = data
    return obj


@pytest.fixture
async def climate_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create a bestiarium_climate config entry (no subentries)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CAPACITY: 2,
            CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 20, "seconds": 0},
            CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
        },
    )
    entry.add_to_hass(hass)
    if not hasattr(entry, "subentries"):
        entry.subentries = {}
    yield entry
    # Unload so BalancingCoordinator timer is stopped (no lingering timer at teardown)
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.fixture
def switch_subentry() -> SimpleNamespace:
    """One switch subentry for entity tests."""
    return _make_subentry(
        subentry_id="switch_01",
        subentry_type=SUBENTRY_TYPE_SWITCH,
        title="Virtual Sw",
        data={
            "name": "Virtual Sw",
            "mode": ClimateMode.HEATING,
            "power_entity": "sensor.power",
            "min_power": 50.0,
            "heating_action": {
                "action": "switch.turn_on",
                "target": {"entity_id": "switch.heater"},
            },
            "cooling_action": None,
            "off_action": {
                "action": "switch.turn_off",
                "target": {"entity_id": "switch.heater"},
            },
        },
    )


@pytest.fixture
def thermostat_subentry() -> SimpleNamespace:
    """One thermostat subentry (temperature_sensor=sensor.temp, target_switch=switch.room)."""
    return _make_subentry(
        subentry_id="thermo_01",
        subentry_type=SUBENTRY_TYPE_THERMOSTAT,
        title="Room Thermostat",
        data={
            "name": "Room Thermostat",
            "temperature_sensor": "sensor.temp",
            "target_switch": "switch.room",
            "thermostat_mode": ClimateMode.HEATING,
            "cold_tolerance": 0.3,
            "hot_tolerance": 0.3,
        },
    )


@pytest.fixture
async def climate_entry_with_switch(
    hass: HomeAssistant, switch_subentry: SimpleNamespace
) -> MockConfigEntry:
    """Entry with one switch subentry (subentries set before add_to_hass)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CAPACITY: 2,
            CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 20, "seconds": 0},
            CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
        },
    )
    entry.subentries = {switch_subentry.subentry_id: switch_subentry}
    entry.add_to_hass(hass)
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.fixture
async def climate_entry_with_thermostat(
    hass: HomeAssistant, thermostat_subentry: SimpleNamespace
) -> MockConfigEntry:
    """Entry with one thermostat subentry (subentries set before add_to_hass)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CAPACITY: 2,
            CONF_MIN_WORKING_TIME: {"hours": 0, "minutes": 20, "seconds": 0},
            CONF_RECOVERY_TIMEOUT: {"hours": 1, "minutes": 0, "seconds": 0},
        },
    )
    entry.subentries = {thermostat_subentry.subentry_id: thermostat_subentry}
    entry.add_to_hass(hass)
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
