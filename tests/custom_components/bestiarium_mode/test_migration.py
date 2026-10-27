"""Migration of the mode entry."""

# ruff: noqa: S101

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bestiarium_mode.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_description_and_icon_go(hass: HomeAssistant) -> None:
    """1.2: a mode is its name; the other keys and the entry's settings stay."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        minor_version=1,
        data={
            "options": [
                {"name": "Eco", "description": "Energy saving", "icon": "mdi:leaf"},
                {"name": "Boost", "icon": "mdi:flash"},
            ],
            "other": 1,
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 2  # noqa: PLR2004
    assert entry.data == {"options": [{"name": "Eco"}, {"name": "Boost"}], "other": 1}
