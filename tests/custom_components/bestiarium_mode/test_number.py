"""Tests for the Bestiarium Mode power load limit number entity."""

# ruff: noqa: S101

from unittest.mock import patch

import pytest
from homeassistant.components.number import (
    ATTR_VALUE,
    SERVICE_SET_VALUE,
)
from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_mode.const import (
    CONF_AC_LOAD_LIMIT_W,
    CONF_AC_LOAD_LIMITS,
    DOMAIN,
    EVENT_LOAD_LIMIT_CHANGED,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

POWER_SENSOR = "sensor.grid_main_load_em0_power"
LIMIT_ENTITY = "number.power_load_limit"
ZONES = ("green", "yellow", "red", "overload")

# The owner's production config entry data.
OWNER_ENTRY_DATA = {
    "options": [{"name": "Grid on"}],
    CONF_AC_LOAD_LIMITS: {
        "ac_power_sensor": POWER_SENSOR,
        CONF_AC_LOAD_LIMIT_W: 5000,
        "ac_yellow_zone_percent": 60,
        "ac_red_zone_percent": 85,
        "ac_overload_recovery_seconds": 30,
    },
}


async def _setup_mode(hass: HomeAssistant, power: str) -> MockConfigEntry:
    hass.states.async_set(POWER_SENSOR, power)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="bestiarium_mode", data=OWNER_ENTRY_DATA
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _zone_entity_ids(hass: HomeAssistant) -> dict[str, str]:
    registry = er.async_get(hass)
    return {
        zone: registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"power_load_{zone}"
        )
        for zone in ZONES
    }


def _zones_on(hass: HomeAssistant) -> set[str]:
    return {
        zone
        for zone, entity_id in _zone_entity_ids(hass).items()
        if hass.states.get(entity_id).state == STATE_ON
    }


async def _set_limit(hass: HomeAssistant, value: float) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: LIMIT_ENTITY, ATTR_VALUE: value},
        blocking=True,
    )
    await hass.async_block_till_done()


async def test_limit_entity_from_existing_entry(hass: HomeAssistant) -> None:
    """An existing entry gets a config number with its configured limit."""
    await _setup_mode(hass, "1000")

    registry_entry = er.async_get(hass).async_get(LIMIT_ENTITY)
    assert registry_entry is not None
    assert registry_entry.unique_id == "power_load_limit"
    # Operated by automations: on dashboards, no config category (DEV-008 B)
    assert registry_entry.entity_category is None

    state = hass.states.get(LIMIT_ENTITY)
    assert float(state.state) == 5000  # noqa: PLR2004
    assert state.attributes["min"] == 1
    assert state.attributes["max"] == 100000  # noqa: PLR2004
    assert state.attributes["unit_of_measurement"] == "W"


async def test_no_limit_entity_without_load_limits(hass: HomeAssistant) -> None:
    """Without AC load limits there is no limit entity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"options": [{"name": "Grid on"}]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(LIMIT_ENTITY) is None


async def test_red_zone_at_4500_with_5000_limit(hass: HomeAssistant) -> None:
    """With the owner's 85 % red threshold, 4500 W of 5000 W is red."""
    await _setup_mode(hass, "4500")

    assert _zones_on(hass) == {"red"}


async def test_raising_limit_recalculates_zones_without_power_change(
    hass: HomeAssistant,
) -> None:
    """Raising the limit moves 6000 W from overload to yellow at once."""
    entry = await _setup_mode(hass, "6000")
    assert _zones_on(hass) == {"overload"}

    await _set_limit(hass, 8800)

    assert _zones_on(hass) == {"yellow"}
    assert float(hass.states.get(LIMIT_ENTITY).state) == 8800  # noqa: PLR2004
    assert entry.data[CONF_AC_LOAD_LIMITS][CONF_AC_LOAD_LIMIT_W] == 8800  # noqa: PLR2004
    zones = _zone_entity_ids(hass)
    assert hass.states.get(zones["red"]).attributes["red_threshold"] == 7480  # noqa: PLR2004


async def test_limit_change_does_not_reload_entry(hass: HomeAssistant) -> None:
    """Changing the limit updates zones in place, without reloading the entry."""
    await _setup_mode(hass, "1000")

    with (
        patch.object(hass.config_entries, "async_reload") as reload,
        patch.object(hass.config_entries, "async_schedule_reload") as schedule,
    ):
        await _set_limit(hass, 8800)

    reload.assert_not_called()
    schedule.assert_not_called()


async def test_same_limit_is_not_written(hass: HomeAssistant) -> None:
    """Setting the current limit again neither updates the entry nor fires."""
    await _setup_mode(hass, "1000")
    events: list[Event] = []

    @callback
    def record(event: Event) -> None:
        events.append(event)

    hass.bus.async_listen(EVENT_LOAD_LIMIT_CHANGED, record)

    with patch.object(hass.config_entries, "async_update_entry") as update:
        await _set_limit(hass, 5000)

    update.assert_not_called()
    assert events == []


async def test_limit_change_fires_event(hass: HomeAssistant) -> None:
    """A limit change fires load_limit_changed with the new and previous limit."""
    await _setup_mode(hass, "1000")
    events: list[Event] = []

    @callback
    def record(event: Event) -> None:
        events.append(event)

    hass.bus.async_listen(EVENT_LOAD_LIMIT_CHANGED, record)

    await _set_limit(hass, 8800)

    assert [event.data for event in events] == [
        {"limit_w": 8800, "previous_limit_w": 5000}
    ]


async def test_limit_survives_reload(hass: HomeAssistant) -> None:
    """After a reload (as after an HA restart) the last limit is in effect."""
    entry = await _setup_mode(hass, "4500")
    await _set_limit(hass, 8800)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert float(hass.states.get(LIMIT_ENTITY).state) == 8800  # noqa: PLR2004
    red = hass.states.get(_zone_entity_ids(hass)["red"])
    assert red.attributes["red_threshold"] == 7480  # noqa: PLR2004 - 85 % of 8800
    assert red.state == STATE_OFF


@pytest.mark.parametrize("value", [1, 1234, 7777, 100000])
async def test_any_limit_in_range_is_accepted(hass: HomeAssistant, value: int) -> None:
    """Every value within the config flow range is applied the same way."""
    entry = await _setup_mode(hass, "1000")

    await _set_limit(hass, value)

    assert entry.data[CONF_AC_LOAD_LIMITS][CONF_AC_LOAD_LIMIT_W] == value
    assert float(hass.states.get(LIMIT_ENTITY).state) == value
    overload = hass.states.get(_zone_entity_ids(hass)["overload"])
    assert overload.state == (STATE_ON if value <= 1000 else STATE_OFF)  # noqa: PLR2004


@pytest.mark.parametrize("value", [0, 100001])
async def test_limit_out_of_range_is_rejected(hass: HomeAssistant, value: int) -> None:
    """Values outside the config flow range are rejected; the limit stays."""
    entry = await _setup_mode(hass, "1000")

    with pytest.raises(ServiceValidationError):
        await _set_limit(hass, value)

    assert entry.data[CONF_AC_LOAD_LIMITS][CONF_AC_LOAD_LIMIT_W] == 5000  # noqa: PLR2004
