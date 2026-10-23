"""The control settings of a device: in its options, with defaults."""

# ruff: noqa: S101, PLR2004

import json
from dataclasses import fields
from pathlib import Path

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

import custom_components.bestiarium_devices as devices
from custom_components.bestiarium_devices.const import (
    CONF_ALLOWED,
    CONF_ESTIMATED_POWER,
    ControlSettings,
)
from custom_components.bestiarium_devices.control import (
    ControlEvent,
    ControlState,
    Direction,
)

from .conftest import FakeRelays
from .control_home import Home

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

COMMODE = "switch.commode"
BEDSIDE = "switch.bedside"


@pytest.fixture
def home(
    hass: HomeAssistant, relays: FakeRelays, freezer: FrozenDateTimeFactory
) -> Home:
    """Return the test home."""
    return Home(hass, relays, freezer)


async def test_entry_without_options_has_the_defaults(home: Home) -> None:
    """Criterion 9: an entry from before DEV-007 works with the defaults."""
    await home.mode("1000")
    entry = await home.device(COMMODE, 5200)

    settings = ControlSettings.of(entry)

    assert settings == ControlSettings()
    assert (settings.attempts, settings.defer_minutes) == (3, 15)
    assert (settings.tolerance_seconds, settings.hysteresis_seconds) == (60, 60)
    assert settings.red_max_wait_seconds == 30


async def test_options_flow_keeps_the_other_options(home: Home) -> None:
    """Criterion 20: the form shows the defaults and keeps 'Allowed'."""
    await home.mode("1000")
    entry = await home.device(
        COMMODE, 5200, options={CONF_ALLOWED: False, CONF_ESTIMATED_POWER: 300}
    )
    flows = home.hass.config_entries.options

    result = await flows.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await flows.async_configure(
        result["flow_id"],
        {
            "attempts": 1,
            "defer_minutes": 5,
            "tolerance_seconds": 60,
            "hysteresis_seconds": 60,
            "unstable_dropouts_per_hour": 3,
            "advanced": {
                "default_response_seconds": 15,
                "response_margin": 1.5,
                "red_max_wait_seconds": 30,
            },
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ALLOWED] is False
    assert entry.options[CONF_ESTIMATED_POWER] == 300
    assert ControlSettings.of(entry) == ControlSettings(attempts=1, defer_minutes=5)
    assert isinstance(entry.options["attempts"], int)


async def test_settings_apply_without_reload(home: Home) -> None:
    """One attempt is enough once the option says so; no reload needed."""
    await home.mode("1000")
    commode = await home.device(COMMODE, 5200, options={"attempts": 1})
    await home.device(BEDSIDE, 5200)
    home.relays.stuck.add(COMMODE)

    await home.tick(120, step=60)

    assert home.commands(COMMODE) == ["turn_on"]
    assert home.commands(BEDSIDE) == ["turn_on"]
    assert home.state(commode) == ControlState.UNRESPONSIVE


@pytest.mark.parametrize("language", ["en", "uk"])
def test_everything_is_translated(language: str) -> None:
    """Every state, attribute, event and setting has a text in both languages."""
    translations = json.loads(
        (Path(devices.__file__).parent / "translations" / f"{language}.json").read_text(
            encoding="utf-8"
        )
    )
    entity = translations["entity"]
    for key in ("control_state", "control_state_standalone"):
        sensor = entity["sensor"][key]
        assert set(sensor["state"]) == {state.value for state in ControlState}
        assert set(sensor["state_attributes"]["direction"]["state"]) == {
            direction.value for direction in Direction
        }
    for key in ("control_events", "control_events_standalone"):
        event_types = entity["event"][key]["state_attributes"]["event_type"]["state"]
        assert set(event_types) == {event.value for event in ControlEvent}
    step = translations["options"]["step"]["init"]
    named = set(step["data"]) | set(step["sections"]["advanced"]["data"])
    assert named == {setting.name for setting in fields(ControlSettings)}
