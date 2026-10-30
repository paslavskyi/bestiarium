"""
The power flow card, served by Bestiarium Mode (DEV-014).

The power modes are Mode's subject, so the card that shows where the home is
fed from lives here. Its files are a static path; every page of HA loads its
module, so the owner only adds the card to a dashboard. The folder is
served under a digest of its files: an update of Bestiarium is a new URL for
the card and for the model it imports, so both can be cached for long.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

CARD_URL = "/bestiarium_mode/frontend"
CARD_DIR = Path(__file__).parent / "frontend"
CARD_FILES = ("power-flow-card.js", "power-flow-model.js")


def _digest() -> str:
    digest = hashlib.sha256()
    for name in CARD_FILES:
        digest.update((CARD_DIR / name).read_bytes())
    return digest.hexdigest()[:8]


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and load it on every page of HA, if HA has its frontend."""
    if "frontend" not in hass.config.components:
        return
    url = f"{CARD_URL}/{await hass.async_add_executor_job(_digest)}"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(url, str(CARD_DIR), cache_headers=True)]
    )
    add_extra_js_url(hass, f"{url}/power-flow-card.js")
