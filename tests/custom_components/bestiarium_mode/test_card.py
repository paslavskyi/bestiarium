"""Bestiarium Mode serves the power flow card to the frontend of HA (DEV-014)."""

# ruff: noqa: S101

import re
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.bestiarium_mode.card import CARD_URL, async_register_card
from custom_components.bestiarium_mode.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

FRONTEND = (
    Path(__file__).parents[3] / "custom_components" / "bestiarium_mode" / "frontend"
)
ADD_JS = "custom_components.bestiarium_mode.card.add_extra_js_url"


@pytest.fixture
def frontend(hass: HomeAssistant) -> Mock:
    """Pretend the frontend of HA is loaded; return its HTTP server."""
    hass.config.components.add("frontend")
    hass.http = Mock(async_register_static_paths=AsyncMock())
    return hass.http


async def test_card_is_served_and_loaded_with_the_frontend(
    hass: HomeAssistant, frontend: Mock
) -> None:
    """
    The folder is a static path; every page of HA loads the card's module.

    The whole folder is under the version: the card's relative import of the
    model is versioned too, so a long cache never pairs a new card with an old
    model.
    """
    with patch(ADD_JS) as add_js:
        assert await async_setup_component(hass, DOMAIN, {})

    add_js.assert_called_once()
    url = add_js.call_args.args[1]
    version = re.fullmatch(
        r"/bestiarium_mode/frontend/([0-9a-f]{8})/power-flow-card\.js", url
    )[1]
    # The model, imported relatively, is under the same version: a long cache
    frontend.async_register_static_paths.assert_awaited_once_with(
        [StaticPathConfig(f"{CARD_URL}/{version}", str(FRONTEND), cache_headers=True)]
    )


@pytest.mark.usefixtures("frontend")
async def test_the_version_follows_the_files(hass: HomeAssistant) -> None:
    """A new card in an update of Bestiarium is a new URL: no stale cache."""
    urls = []
    for content in (b"one", b"two"):
        with (
            patch(ADD_JS) as add_js,
            patch(
                "custom_components.bestiarium_mode.card.Path.read_bytes",
                return_value=content,
            ),
        ):
            await async_register_card(hass)
        urls.append(add_js.call_args.args[1])
    assert urls[0] != urls[1]


async def test_without_the_frontend_nothing_is_registered(hass: HomeAssistant) -> None:
    """An HA without its frontend has no dashboards: Mode works as before."""
    with patch(ADD_JS) as add_js:
        assert await async_setup_component(hass, DOMAIN, {})
    add_js.assert_not_called()
