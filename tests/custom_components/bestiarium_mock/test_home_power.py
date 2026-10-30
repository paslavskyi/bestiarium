"""The stand's home power: what the power flow card reads (DEV-014)."""

# ruff: noqa: S101

from collections.abc import Callable

import pytest
from homeassistant.const import STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.bestiarium_mock.const import DOMAIN

from .conftest import mock_entry

EntityId = Callable[[str, str], str]

DEFAULTS = {
    ("select", "mode"): "Звичайний",
    ("switch", "inverter_home"): "on",
    ("switch", "grid_inverter"): "on",
    ("switch", "grid_present"): "on",
    ("number", "grid_power"): "0",
    ("number", "sun_power"): "0",
    ("number", "battery_flow"): "0",
    ("number", "battery_charge"): "91",
    ("number", "battery_hours_left"): "7.5",
}
TO_FULL = ("sensor", "mock_home_power_battery_hours_to_full")


def _state(hass: HomeAssistant, entity_id: EntityId, domain: str, key: str) -> str:
    return hass.states.get(entity_id(domain, f"mock_home_power_{key}")).state


async def _set(
    hass: HomeAssistant, entity_id: EntityId, key: str, value: float
) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id("number", f"mock_home_power_{key}"), "value": value},
        blocking=True,
    )


@pytest.mark.usefixtures("stand")
async def test_home_power_defaults(hass: HomeAssistant, entity_id: EntityId) -> None:
    """A calm home on the stand: the relays on, the grid there, nothing flowing."""
    for (domain, key), state in DEFAULTS.items():
        assert _state(hass, entity_id, domain, key) == state, key
        registry_entry = er.async_get(hass).async_get(
            entity_id(domain, f"mock_home_power_{key}")
        )
        assert registry_entry.entity_category is EntityCategory.CONFIG
    assert hass.states.get(entity_id(*TO_FULL)).state == STATE_UNKNOWN


@pytest.mark.usefixtures("stand")
async def test_to_full_is_unknown_unless_charging(
    hass: HomeAssistant, entity_id: EntityId
) -> None:
    """As in the home: an estimate only while the battery charges."""
    to_full = entity_id(*TO_FULL)
    await _set(hass, entity_id, "battery_flow", 0.6)
    assert hass.states.get(to_full).state == STATE_UNKNOWN
    # 9 % of 10 kWh at 0.9 kW
    await _set(hass, entity_id, "battery_flow", -0.9)
    assert hass.states.get(to_full).state == "1.0"
    await _set(hass, entity_id, "battery_charge", 50)
    assert hass.states.get(to_full).state == "5.5"


async def test_home_power_knobs_survive_a_restart(
    hass: HomeAssistant,
    enable_custom_integrations: None,  # noqa: ARG001
    entity_id: EntityId,
) -> None:
    """The relays, the numbers and the mode come back after a restart."""
    # The entity_ids the stand gives them: <domain>.<unique_id>
    registry = er.async_get(hass)
    for domain, key in (
        ("select", "mode"),
        ("switch", "grid_inverter"),
        ("number", "sun_power"),
    ):
        unique_id = f"mock_home_power_{key}"
        registry.async_get_or_create(
            domain, DOMAIN, unique_id, suggested_object_id=unique_id
        )
    mock_restore_cache_with_extra_data(
        hass,
        [
            (State("select.mock_home_power_mode", "Автономний"), {}),
            (State("switch.mock_home_power_grid_inverter", "off"), {}),
            (
                State("number.mock_home_power_sun_power", "1.8"),
                {
                    "native_value": 1.8,
                    "native_min_value": 0,
                    "native_max_value": 10,
                    "native_step": 0.1,
                    "native_unit_of_measurement": "kW",
                },
            ),
        ],
    )
    entry = mock_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _state(hass, entity_id, "select", "mode") == "Автономний"
    assert _state(hass, entity_id, "switch", "grid_inverter") == "off"
    assert _state(hass, entity_id, "number", "sun_power") == "1.8"


@pytest.mark.usefixtures("stand")
async def test_home_power_device(hass: HomeAssistant) -> None:
    """The knobs are one device of the stand."""
    entry = er.async_get(hass).async_get(
        er.async_get(hass).async_get_entity_id("select", DOMAIN, "mock_home_power_mode")
    )
    device = dr.async_get(hass).async_get(entry.device_id)
    assert device.name == "Mock Home Power"
