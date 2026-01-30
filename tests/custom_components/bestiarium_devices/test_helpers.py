"""Tests for the Bestiarium Devices helper functions."""

# ruff: noqa: S101

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    DEVICE_TYPE_EXTENDED,
    DOMAIN,
    LOAD_PRIORITY_DEFAULT,
)
from custom_components.bestiarium_devices.helpers import (
    build_priority_form_key,
    extract_priorities_from_user_input,
    get_current_priority,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


class TestBuildPriorityFormKey:
    """Tests for build_priority_form_key function."""

    def test_builds_form_key(self) -> None:
        """Test building form key for mode priority."""
        result = build_priority_form_key("Eco")
        assert result == "priority_Eco"

    def test_builds_form_key_with_spaces(self) -> None:
        """Test building form key for mode with spaces."""
        result = build_priority_form_key("Night Mode")
        assert result == "priority_Night Mode"


class TestExtractPrioritiesFromUserInput:
    """Tests for extract_priorities_from_user_input function."""

    def test_extracts_all_provided_priorities(self) -> None:
        """Test extracting all provided priorities."""
        user_input = {
            CONF_DEFAULT_PRIORITY: 3000,
            build_priority_form_key("Eco"): 1000,
            build_priority_form_key("Boost"): 9000,
        }
        mode_names = ["Eco", "Boost"]

        result = extract_priorities_from_user_input(user_input, mode_names)

        assert result == {
            CONF_DEFAULT_PRIORITY: 3000,
            "Eco": 1000,
            "Boost": 9000,
        }

    def test_omits_all_mode_priorities_when_none_provided(self) -> None:
        """Test that when no mode priorities are provided, only default is stored."""
        user_input = {
            CONF_DEFAULT_PRIORITY: 4000,
            # No mode priorities provided
        }
        mode_names = ["Eco", "Boost"]

        result = extract_priorities_from_user_input(user_input, mode_names)

        assert result[CONF_DEFAULT_PRIORITY] == 4000  # noqa: PLR2004
        assert "Eco" not in result
        assert "Boost" not in result

    def test_omits_mode_priority_when_not_provided(self) -> None:
        """Test that omitted mode priorities are left out of the config."""
        user_input = {
            CONF_DEFAULT_PRIORITY: 4000,
            build_priority_form_key("Eco"): 2000,
            # Boost not provided
        }
        mode_names = ["Eco", "Boost"]

        result = extract_priorities_from_user_input(user_input, mode_names)

        assert result[CONF_DEFAULT_PRIORITY] == 4000  # noqa: PLR2004
        assert result["Eco"] == 2000  # noqa: PLR2004
        assert "Boost" not in result

    def test_uses_default_for_missing_default_priority(self) -> None:
        """Test that missing default priority uses LOAD_PRIORITY_DEFAULT."""
        user_input = {
            build_priority_form_key("Eco"): 1000,
        }
        mode_names = ["Eco"]

        result = extract_priorities_from_user_input(user_input, mode_names)

        assert result[CONF_DEFAULT_PRIORITY] == LOAD_PRIORITY_DEFAULT
        assert result["Eco"] == 1000  # noqa: PLR2004


class TestGetCurrentPriority:
    """Tests for get_current_priority function (reads from config entry data)."""

    async def test_returns_mode_specific_priority(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test returns mode-specific priority from config entry when available."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
                CONF_DEVICE_ID: "test_device",
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 5000,
                    "Eco": 2000,
                    "Boost": 8000,
                },
            },
        )
        entry.add_to_hass(hass)

        result = get_current_priority(hass, "test_device")

        assert result == 2000  # noqa: PLR2004

    async def test_falls_back_to_default_when_mode_not_in_priorities(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to default when current mode has no specific priority."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
                CONF_DEVICE_ID: "test_device",
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 4000,
                    "Boost": 8000,
                },
            },
        )
        entry.add_to_hass(hass)

        result = get_current_priority(hass, "test_device")

        assert result == 4000  # noqa: PLR2004

    async def test_falls_back_to_constant_when_no_entry_for_device(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to LOAD_PRIORITY_DEFAULT when no config entry for device."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        # No config entry for test_device

        result = get_current_priority(hass, "test_device")

        assert result == LOAD_PRIORITY_DEFAULT

    async def test_falls_back_to_constant_when_mode_select_missing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to LOAD_PRIORITY_DEFAULT when mode select is missing."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
                CONF_DEVICE_ID: "test_device",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 6000},
            },
        )
        entry.add_to_hass(hass)

        result = get_current_priority(hass, "test_device")

        assert result == LOAD_PRIORITY_DEFAULT

    async def test_falls_back_to_constant_when_mode_select_unknown(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to LOAD_PRIORITY_DEFAULT when mode select is unknown."""
        hass.states.async_set("select.bestiarium_mode", "unknown")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
                CONF_DEVICE_ID: "test_device",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 6000},
            },
        )
        entry.add_to_hass(hass)

        result = get_current_priority(hass, "test_device")

        assert result == LOAD_PRIORITY_DEFAULT

    async def test_falls_back_to_constant_when_mode_select_unavailable(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to LOAD_PRIORITY_DEFAULT when mode select unavailable."""
        hass.states.async_set("select.bestiarium_mode", "unavailable")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
                CONF_DEVICE_ID: "test_device",
                CONF_LOAD_PRIORITIES: {CONF_DEFAULT_PRIORITY: 6000},
            },
        )
        entry.add_to_hass(hass)

        result = get_current_priority(hass, "test_device")

        assert result == LOAD_PRIORITY_DEFAULT

    async def test_returns_default_priority_when_entry_has_empty_priorities(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test returns LOAD_PRIORITY_DEFAULT when entry has no priorities dict."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_EXTENDED,
                CONF_DEVICE_ID: "test_device",
                # No CONF_LOAD_PRIORITIES
            },
        )
        entry.add_to_hass(hass)

        result = get_current_priority(hass, "test_device")

        assert result == LOAD_PRIORITY_DEFAULT

    async def test_virtual_device_uses_entry_id_as_device_id(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test get_current_priority finds virtual device by entry_id."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_DEVICE_TYPE: "virtual",
                "device_name": "My Virtual",
                CONF_LOAD_PRIORITIES: {
                    CONF_DEFAULT_PRIORITY: 3000,
                    "Eco": 1500,
                },
            },
        )
        entry.add_to_hass(hass)
        # For virtual devices, device_id is entry.entry_id
        device_id = entry.entry_id

        result = get_current_priority(hass, device_id)

        assert result == 1500  # noqa: PLR2004
