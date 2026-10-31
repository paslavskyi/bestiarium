"""Bestiarium Mode serves the power flow card to HA's frontend (DEV-014, DEV-015)."""

# ruff: noqa: S101

from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.bestiarium_mode.card import CARD_FILES
from custom_components.bestiarium_mode.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

FRONTEND = (
    Path(__file__).parents[3] / "custom_components" / "bestiarium_mode" / "frontend"
)
ADD_JS = "custom_components.bestiarium_mode.card.add_extra_js_url"
CARD_JS = "/api/bestiarium_mode/frontend/power-flow-card.js"


@pytest.fixture
async def client(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> TestClient:
    """Set up Mode with the frontend of HA; return a client without a token."""
    hass.config.components.add("frontend")
    assert await async_setup_component(hass, "http", {})
    with patch(ADD_JS):
        assert await async_setup_component(hass, DOMAIN, {})
    return await hass_client_no_auth()


async def test_the_card_is_loaded_from_a_stable_url_under_api(
    hass: HomeAssistant,
) -> None:
    """
    Every page of HA loads the card from one URL that never changes.

    HA's service worker never answers /api/ from its cache, so a page served
    from that cache still asks the server for the card it has now.
    """
    hass.config.components.add("frontend")
    assert await async_setup_component(hass, "http", {})
    with patch(ADD_JS) as add_js:
        assert await async_setup_component(hass, DOMAIN, {})
    add_js.assert_called_once_with(hass, CARD_JS)


@pytest.mark.parametrize("name", CARD_FILES)
async def test_the_card_files_are_served_uncached(
    client: TestClient, name: str
) -> None:
    """The browser asks again on every page load: no stale card after an update."""
    response = await client.get(f"/api/bestiarium_mode/frontend/{name}")
    assert response.status == HTTPStatus.OK
    assert await response.read() == (FRONTEND / name).read_bytes()
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.content_type == "text/javascript"
    assert response.headers["ETag"]


async def test_an_unchanged_file_is_not_sent_again(client: TestClient) -> None:
    """Asking again costs a 304, not the file."""
    etag = (await client.get(CARD_JS)).headers["ETag"]
    response = await client.get(CARD_JS, headers={"If-None-Match": etag})
    assert response.status == HTTPStatus.NOT_MODIFIED


@pytest.mark.parametrize(
    "path",
    [
        "card.py",
        "__init__.py",
        "power-flow-card.js.map",
        "..%2Fcard.py",
        "..%2F..%2Fbestiarium_mode%2Fconst.py",
        "%2E%2E%2Fcard.py",
        "../card.py",
        "../../bestiarium_mode/const.py",
        "power-flow-card.js%00",
    ],
)
async def test_only_the_card_files_are_served(client: TestClient, path: str) -> None:
    """
    Served without a token: nothing but the two files of the card.

    HA's own security filter answers an encoded `..` with 400 before routing.
    """
    response = await client.get(f"/api/bestiarium_mode/frontend/{path}")
    assert response.status in (HTTPStatus.BAD_REQUEST, HTTPStatus.NOT_FOUND)


@pytest.mark.parametrize("name", CARD_FILES)
async def test_the_digest_path_of_dev_014_serves_the_current_files(
    client: TestClient, name: str
) -> None:
    """A page cached before DEV-015 asks the old digest, and the model beside it."""
    response = await client.get(f"/bestiarium_mode/frontend/0123abcd/{name}")
    assert response.status == HTTPStatus.OK
    assert await response.read() == (FRONTEND / name).read_bytes()
    assert response.headers["Cache-Control"] == "no-cache"


async def test_a_changed_file_is_served_at_once(
    client: TestClient, tmp_path: Path
) -> None:
    """No copy in memory: the next request gets what is on the disk."""
    for name in CARD_FILES:
        (tmp_path / name).write_bytes(b"one")
    with patch("custom_components.bestiarium_mode.card.CARD_DIR", tmp_path):
        assert await (await client.get(CARD_JS)).read() == b"one"
        (tmp_path / "power-flow-card.js").write_bytes(b"two")
        assert await (await client.get(CARD_JS)).read() == b"two"


async def test_without_the_frontend_nothing_is_registered(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """An HA without its frontend has no dashboards: Mode works as before."""
    assert await async_setup_component(hass, "http", {})
    with patch(ADD_JS) as add_js:
        assert await async_setup_component(hass, DOMAIN, {})
    add_js.assert_not_called()
    client = await hass_client_no_auth()
    assert (await client.get(CARD_JS)).status == HTTPStatus.NOT_FOUND
