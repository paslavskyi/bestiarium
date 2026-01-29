"""Tests for the Bestiarium Devices helper functions."""

# ruff: noqa: S101

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bestiarium_devices.const import (
    CONF_DEFAULT_PRIORITY,
    LOAD_PRIORITY_DEFAULT,
)
from custom_components.bestiarium_devices.helpers import (
    build_priority_entity_id,
    build_priority_form_key,
    build_priority_unique_id,
    extract_priorities_from_user_input,
    get_current_priority,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


class TestBuildPriorityUniqueId:
    """Tests for build_priority_unique_id function."""

    def test_builds_default_priority_unique_id(self) -> None:
        """Test building unique ID for default priority."""
        result = build_priority_unique_id("test_device", None)
        assert result == "test_device_priority_default"

    def test_builds_mode_priority_unique_id(self) -> None:
        """Test building unique ID for mode-specific priority."""
        result = build_priority_unique_id("test_device", "Eco")
        assert result == "test_device_priority_Eco"

    def test_builds_mode_priority_unique_id_with_spaces(self) -> None:
        """Test building unique ID for mode with spaces in name."""
        result = build_priority_unique_id("my_device", "Night Mode")
        assert result == "my_device_priority_Night Mode"


class TestBuildPriorityEntityId:
    """Tests for build_priority_entity_id function."""

    def test_builds_default_priority_entity_id(self) -> None:
        """Test building entity ID for default priority."""
        result = build_priority_entity_id("test_device", None)
        assert result == "number.test_device_priority_default"

    def test_builds_mode_priority_entity_id(self) -> None:
        """Test building entity ID for mode-specific priority."""
        result = build_priority_entity_id("test_device", "Eco")
        assert result == "number.test_device_priority_Eco"

    def test_entity_id_uses_unique_id_internally(self) -> None:
        """Test that entity ID is constructed from unique ID."""
        device_id = "device123"
        mode = "Boost"
        entity_id = build_priority_entity_id(device_id, mode)
        unique_id = build_priority_unique_id(device_id, mode)
        assert entity_id == f"number.{unique_id}"


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

    def test_uses_default_for_missing_mode_priorities(self) -> None:
        """Test that missing mode priorities use LOAD_PRIORITY_DEFAULT."""
        user_input = {
            CONF_DEFAULT_PRIORITY: 4000,
            # No mode priorities provided
        }
        mode_names = ["Eco", "Boost"]

        result = extract_priorities_from_user_input(user_input, mode_names)

        assert result[CONF_DEFAULT_PRIORITY] == 4000  # noqa: PLR2004
        assert result["Eco"] == LOAD_PRIORITY_DEFAULT
        assert result["Boost"] == LOAD_PRIORITY_DEFAULT

    def test_uses_default_for_partial_mode_priorities(self) -> None:
        """Test that only missing mode priorities use LOAD_PRIORITY_DEFAULT."""
        user_input = {
            CONF_DEFAULT_PRIORITY: 4000,
            build_priority_form_key("Eco"): 2000,
            # Boost not provided
        }
        mode_names = ["Eco", "Boost"]

        result = extract_priorities_from_user_input(user_input, mode_names)

        assert result[CONF_DEFAULT_PRIORITY] == 4000  # noqa: PLR2004
        assert result["Eco"] == 2000  # noqa: PLR2004
        assert result["Boost"] == LOAD_PRIORITY_DEFAULT

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
    """Tests for get_current_priority function."""

    async def test_returns_mode_specific_priority(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test returns mode-specific priority when available."""
        # Set up mode select
        hass.states.async_set("select.bestiarium_mode", "Eco")
        # Set up mode-specific priority entity
        hass.states.async_set("number.test_device_priority_Eco", "2000")
        # Set up default priority entity
        hass.states.async_set("number.test_device_priority_default", "5000")

        result = get_current_priority(hass, "test_device")

        assert result == 2000  # noqa: PLR2004

    async def test_falls_back_to_default_when_mode_priority_unknown(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to default when mode priority state is unknown."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        hass.states.async_set("number.test_device_priority_Eco", "unknown")
        hass.states.async_set("number.test_device_priority_default", "4000")

        result = get_current_priority(hass, "test_device")

        assert result == 4000  # noqa: PLR2004

    async def test_falls_back_to_default_when_mode_priority_unavailable(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to default when mode priority state is unavailable."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        hass.states.async_set("number.test_device_priority_Eco", "unavailable")
        hass.states.async_set("number.test_device_priority_default", "3500")

        result = get_current_priority(hass, "test_device")

        assert result == 3500  # noqa: PLR2004

    async def test_falls_back_to_default_when_mode_priority_entity_missing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to default when mode priority entity doesn't exist."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        # Mode priority entity not created
        hass.states.async_set("number.test_device_priority_default", "4500")

        result = get_current_priority(hass, "test_device")

        assert result == 4500  # noqa: PLR2004

    async def test_falls_back_to_constant_when_no_entities_exist(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to LOAD_PRIORITY_DEFAULT when no entities exist."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        # No priority entities exist

        result = get_current_priority(hass, "test_device")

        assert result == LOAD_PRIORITY_DEFAULT

    async def test_falls_back_to_constant_when_mode_select_missing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to LOAD_PRIORITY_DEFAULT when mode select is missing."""
        # Mode select entity not created
        hass.states.async_set("number.test_device_priority_default", "6000")

        result = get_current_priority(hass, "test_device")

        assert result == LOAD_PRIORITY_DEFAULT

    async def test_falls_back_to_constant_when_mode_select_unknown(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to LOAD_PRIORITY_DEFAULT when mode select is unknown."""
        hass.states.async_set("select.bestiarium_mode", "unknown")
        hass.states.async_set("number.test_device_priority_default", "6000")

        result = get_current_priority(hass, "test_device")

        assert result == LOAD_PRIORITY_DEFAULT

    async def test_falls_back_to_constant_when_mode_select_unavailable(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to LOAD_PRIORITY_DEFAULT when mode select unavailable."""
        hass.states.async_set("select.bestiarium_mode", "unavailable")
        hass.states.async_set("number.test_device_priority_default", "6000")

        result = get_current_priority(hass, "test_device")

        assert result == LOAD_PRIORITY_DEFAULT

    async def test_handles_invalid_mode_priority_value(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test handles non-numeric mode priority value gracefully."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        hass.states.async_set("number.test_device_priority_Eco", "invalid")
        hass.states.async_set("number.test_device_priority_default", "3000")

        result = get_current_priority(hass, "test_device")

        assert result == 3000  # noqa: PLR2004

    async def test_handles_invalid_default_priority_value(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test handles non-numeric default priority value gracefully."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        hass.states.async_set("number.test_device_priority_Eco", "not_a_number")
        hass.states.async_set("number.test_device_priority_default", "also_invalid")

        result = get_current_priority(hass, "test_device")

        assert result == LOAD_PRIORITY_DEFAULT

    async def test_handles_float_values(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test handles float values by converting to int."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        hass.states.async_set("number.test_device_priority_Eco", "2500.7")

        result = get_current_priority(hass, "test_device")

        assert result == 2500  # noqa: PLR2004

    async def test_falls_back_to_constant_when_default_priority_unknown(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to constant when default priority is also unknown."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        hass.states.async_set("number.test_device_priority_Eco", "unknown")
        hass.states.async_set("number.test_device_priority_default", "unknown")

        result = get_current_priority(hass, "test_device")

        assert result == LOAD_PRIORITY_DEFAULT

    async def test_falls_back_to_default_when_mode_priority_empty_string(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test falls back to default priority when mode priority is empty string."""
        hass.states.async_set("select.bestiarium_mode", "Eco")
        hass.states.async_set("number.test_device_priority_Eco", "")
        hass.states.async_set("number.test_device_priority_default", "4200")

        result = get_current_priority(hass, "test_device")

        assert result == 4200  # noqa: PLR2004
