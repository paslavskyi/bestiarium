"""
The power flow card, served by Bestiarium Mode (DEV-014, DEV-015).

The power modes are Mode's subject, so the card that shows where the home is
fed from lives here. Every page of HA loads its module, so the owner only adds
the card to a dashboard.

The card has one URL that never changes, under /api/: HA's service worker
never answers /api/ from its cache, and `no-cache` makes the browser ask
again on every page load (a 304 when nothing changed). So a page served from
the service worker's cache after an update still gets the card the server has
now, and its model beside it; an open page that reconnects after a restart
of HA imports the same URL again, which is the module it already has.

The digest path of DEV-014 serves the same files, for pages cached before
DEV-015; it is transitional.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import hdrs, web
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import HomeAssistantView

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

CARD_URL = "/api/bestiarium_mode/frontend"
CARD_DIR = Path(__file__).parent / "frontend"
CARD_FILES = ("power-flow-card.js", "power-flow-model.js")


class CardView(HomeAssistantView):
    """The files of the card; a module script carries no token."""

    url = f"{CARD_URL}/{{filename}}"
    extra_urls = ["/bestiarium_mode/frontend/{digest}/{filename}"]  # noqa: RUF012
    name = "api:bestiarium_mode:card"
    requires_auth = False

    async def get(
        self,
        request: web.Request,  # noqa: ARG002
        filename: str,
        digest: str | None = None,  # noqa: ARG002
    ) -> web.StreamResponse:
        """Serve one of the card's files; nothing else."""
        if filename not in CARD_FILES:
            raise web.HTTPNotFound
        return web.FileResponse(
            CARD_DIR / filename, headers={hdrs.CACHE_CONTROL: "no-cache"}
        )


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and load it on every page of HA, if HA has its frontend."""
    if "frontend" not in hass.config.components:
        return
    hass.http.register_view(CardView())
    add_extra_js_url(hass, f"{CARD_URL}/power-flow-card.js")
