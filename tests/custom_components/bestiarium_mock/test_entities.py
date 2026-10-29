"""Tests for the Bestiarium Mock Devices entities."""

# ruff: noqa: S101

from collections.abc import Callable

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.bestiarium_mock.const import (
    DOMAIN,
    FAULT_PARAMS,
    MOCK_DEVICES,
    MOCK_GRID,
    MOCK_THERMOMETERS,
    FaultProfile,
)
from custom_components.bestiarium_mock.coordinator import MockDevicesCoordinator

EntityId = Callable[[str, str], str]

pytestmark = pytest.mark.usefixtures("stand")


def _category(hass: HomeAssistant, entity_id: str) -> EntityCategory | None:
    return er.async_get(hass).async_get(entity_id).entity_category


async def test_socket_entities(hass: HomeAssistant, entity_id: EntityId) -> None:
    """
    A socket's switch and power have no category; its knobs are config.

    The tester's truth of the load (state) is to be seen: diagnostic.
    """
    for spec in MOCK_DEVICES:
        mock_id = f"mock_{spec.device_id}"
        for domain, unique_id in (("switch", mock_id), ("sensor", f"{mock_id}_power")):
            assert _category(hass, entity_id(domain, unique_id)) is None
        state = entity_id("binary_sensor", f"{mock_id}_state")
        assert _category(hass, state) == EntityCategory.DIAGNOSTIC
        knobs = [("select", f"{mock_id}_fault_profile")] + [
            ("number", f"{mock_id}_{param.name}") for param in FAULT_PARAMS
        ]
        if spec.has_variable_power:
            knobs.append(("number", f"{mock_id}_mock_power"))
        for domain, unique_id in knobs:
            assert (
                _category(hass, entity_id(domain, unique_id)) is EntityCategory.CONFIG
            )


async def test_no_mock_enabled_switch(hass: HomeAssistant) -> None:
    """The fault profile replaced the 'Mock Enabled' switch."""
    assert not [
        entity
        for entity in er.async_get(hass).entities.values()
        if entity.platform == DOMAIN and entity.unique_id.endswith("_mock_enabled")
    ]


async def test_thermometer_and_grid_entities(
    hass: HomeAssistant, entity_id: EntityId
) -> None:
    """The thermometer knob is config; the measurements and grid are not."""
    for spec in MOCK_THERMOMETERS:
        temperature = entity_id("sensor", f"mock_{spec.device_id}_temperature")
        knob = entity_id("number", f"mock_{spec.device_id}_mock_temperature")
        assert _category(hass, temperature) is None
        assert _category(hass, knob) is EntityCategory.CONFIG
    assert _category(hass, entity_id("sensor", "mock_grid_power")) is None


async def test_initial_states(hass: HomeAssistant, entity_id: EntityId) -> None:
    """Sockets start off, drawing nothing, with their scenario's profile."""
    for spec in MOCK_DEVICES:
        mock_id = f"mock_{spec.device_id}"
        assert hass.states.get(entity_id("switch", mock_id)).state == STATE_OFF
        assert hass.states.get(entity_id("sensor", f"{mock_id}_power")).state == "0"
        profile = hass.states.get(entity_id("select", f"{mock_id}_fault_profile"))
        assert profile.state == spec.profile


async def test_reliable_socket_turns_on_and_off(
    hass: HomeAssistant, entity_id: EntityId, advance: Callable
) -> None:
    """A reliable socket follows its commands within a second."""
    switch = entity_id("switch", "mock_boiler")
    power = entity_id("sensor", "mock_boiler_power")
    state = entity_id("binary_sensor", "mock_boiler_state")

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch})
    await advance(1)
    assert hass.states.get(switch).state == STATE_ON
    assert hass.states.get(power).state == "2000"
    assert hass.states.get(state).state == STATE_ON

    await hass.services.async_call("switch", "turn_off", {"entity_id": switch})
    await advance(1)
    assert hass.states.get(switch).state == STATE_OFF
    assert hass.states.get(power).state == "0"
    assert hass.states.get(state).state == STATE_OFF


async def test_cooker_power_number(
    hass: HomeAssistant, entity_id: EntityId, advance: Callable
) -> None:
    """The cooker's power knob sets what it draws when on."""
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id("number", "mock_cooker_mock_power"), "value": 3000},
        blocking=True,
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id("switch", "mock_cooker")}
    )
    await advance(1)

    power = hass.states.get(entity_id("sensor", "mock_cooker_power"))
    assert float(power.state) == 3000  # noqa: PLR2004


async def test_names_are_translated() -> None:
    """Every device, profile and fault parameter has a name in both languages."""
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    import custom_components.bestiarium_mock as mock  # noqa: PLC0415

    for language in ("en", "uk"):
        translations = json.loads(
            (
                Path(mock.__file__).parent / "translations" / f"{language}.json"
            ).read_text(encoding="utf-8")
        )
        states = translations["entity"]["select"]["fault_profile"]["state"]
        assert set(states) == set(FaultProfile)
        assert set(translations["entity"]["number"]) >= {p.name for p in FAULT_PARAMS}
        assert set(translations["device"]) == {
            spec.device_id for spec in (*MOCK_DEVICES, *MOCK_THERMOMETERS, MOCK_GRID)
        }


async def test_unload_entry(hass: HomeAssistant, stand: MockDevicesCoordinator) -> None:
    """Unloading stops the stand."""
    assert await hass.config_entries.async_unload(stand.entry.entry_id)
    assert stand.entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize("device", ["thermo_2", "grid"])
async def test_reading_profiles(
    hass: HomeAssistant, entity_id: EntityId, device: str
) -> None:
    """A thermometer or the grid: no data is unavailable; frozen keeps the value."""
    knob = entity_id("select", f"mock_{device}_reading_profile")
    unit = "temperature" if device.startswith("thermo") else "power"
    sensor = entity_id("sensor", f"mock_{device}_{unit}")
    assert _category(hass, knob) is EntityCategory.CONFIG

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": knob, "option": "no_data"},
        blocking=True,
    )
    assert hass.states.get(sensor).state == "unavailable"

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": knob, "option": "frozen"},
        blocking=True,
    )
    frozen = hass.states.get(sensor).state
    if device.startswith("thermo"):
        await hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": entity_id("number", f"mock_{device}_mock_temperature"),
                "value": 30,
            },
            blocking=True,
        )
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == frozen


async def test_the_devices_of_the_universal_thermostats(
    hass: HomeAssistant, entity_id: EntityId
) -> None:
    """DEV-011: a free heater, a fourth AC's power with its IR knobs, a fifth room."""
    for domain, unique_id in (
        ("switch", "mock_heater"),
        ("sensor", "mock_heater_power"),
        ("sensor", "mock_ac_4_power"),
        ("switch", "mock_ac_4_ir_lost"),
        ("sensor", "mock_thermo_5_temperature"),
    ):
        assert hass.states.get(entity_id(domain, unique_id)) is not None
    heater = next(spec for spec in MOCK_DEVICES if spec.device_id == "heater")
    assert heater.power_w == 1500  # noqa: PLR2004
