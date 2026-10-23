"""The fault profile of a mock socket sets its fault parameters."""

# ruff: noqa: S101, PLR2004

from collections.abc import Callable

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.bestiarium_mock.const import (
    FAULT_PARAMS,
    PROFILE_PARAMS,
    FaultProfile,
)

from .conftest import mock_entry

EntityId = Callable[[str, str], str]


async def _select(hass: HomeAssistant, entity_id: EntityId, profile: str) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": entity_id("select", "mock_boiler_fault_profile"),
            "option": profile,
        },
        blocking=True,
    )


def _param(hass: HomeAssistant, entity_id: EntityId, key: str) -> float:
    return float(hass.states.get(entity_id("number", f"mock_boiler_{key}")).state)


async def _set_param(
    hass: HomeAssistant, entity_id: EntityId, key: str, value: float
) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id("number", f"mock_boiler_{key}"), "value": value},
        blocking=True,
    )


@pytest.mark.usefixtures("stand")
@pytest.mark.parametrize("profile", list(PROFILE_PARAMS))
async def test_profile_sets_all_parameters(
    hass: HomeAssistant, entity_id: EntityId, profile: FaultProfile
) -> None:
    """Each profile shows its parameters on the knobs."""
    await _select(hass, entity_id, profile)

    for param in FAULT_PARAMS:
        expected = getattr(PROFILE_PARAMS[profile], param.name)
        assert _param(hass, entity_id, param.name) == expected


@pytest.mark.usefixtures("stand")
async def test_parameters_editable_only_in_custom(
    hass: HomeAssistant, entity_id: EntityId
) -> None:
    """Outside 'custom' a knob refuses with a translated error."""
    with pytest.raises(ServiceValidationError) as raised:
        await _set_param(hass, entity_id, "ignore_percent", 50)
    assert raised.value.translation_key == "custom_profile_required"
    assert _param(hass, entity_id, "ignore_percent") == 0


@pytest.mark.usefixtures("stand")
async def test_custom_starts_from_the_profile_in_effect(
    hass: HomeAssistant, entity_id: EntityId
) -> None:
    """'Custom' keeps the values shown, then the knobs change them."""
    await _select(hass, entity_id, FaultProfile.SLOW_CLOUD)
    await _select(hass, entity_id, FaultProfile.CUSTOM)
    assert _param(hass, entity_id, "delay_max_s") == 15

    await _set_param(hass, entity_id, "ignore_percent", 50)

    assert _param(hass, entity_id, "ignore_percent") == 50
    assert _param(hass, entity_id, "delay_max_s") == 15


async def test_custom_profile_survives_restart(
    hass: HomeAssistant,
    enable_custom_integrations: None,  # noqa: ARG001
) -> None:
    """The profile and the custom parameters are restored after a restart."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (State("select.mock_boiler_fault_profile", FaultProfile.CUSTOM), {}),
            (
                State("number.mock_boiler_command_ignore_chance", "40"),
                {
                    "native_max_value": 100,
                    "native_min_value": 0,
                    "native_step": 1,
                    "native_unit_of_measurement": "%",
                    "native_value": 40,
                },
            ),
        ],
    )
    entry = mock_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    socket = hass.data[entry.domain][entry.entry_id].sockets["boiler"]
    assert socket.profile is FaultProfile.CUSTOM
    assert socket.params.ignore_percent == 40
