"""
Set up the Bestiarium test stand in the development Home Assistant.

Run it against the running development HA (`make develop`) with a long-lived
access token of that HA in HA_TEST_ENV_TOKEN (a Windows user variable, passed
into the devcontainer by `remoteEnv` in .devcontainer.json):

    make stand

It adds Bestiarium Mock and Bestiarium Mode if they are missing, recreates the
Bestiarium Devices entries of the test sockets and the Bestiarium Climate
thermostats, the scenario scripts and the "Bestiarium stand" dashboard. Running
it again brings the stand back to the same shape. It refuses any Home
Assistant that does not run on this machine.
"""

# A command-line script: it prints its progress. The stand dashboard and
# scripts are for the owner, in Ukrainian.
# ruff: noqa: T201

from __future__ import annotations

import asyncio
import os
import sys
from urllib.parse import urlparse

import aiohttp

from scripts.stand.dashboard import check_zones, write_dashboard
from scripts.stand.entries import (
    ensure_mock,
    ensure_mode,
    normalize_mock,
    recreate_climate,
    recreate_devices,
    remove_broken_automations,
)
from scripts.stand.ha import HomeAssistant, StandError, registry
from scripts.stand.layout import TOKEN_ENV
from scripts.stand.scenarios import write_scripts

HA_URL = os.environ.get("BESTIARIUM_STAND_URL", "http://localhost:8123")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


async def setup_stand(url: str, token: str) -> None:
    """Set up the whole stand."""
    async with aiohttp.ClientSession() as session:
        ha = HomeAssistant(session, url, token)
        await ha.connect()
        try:
            await ensure_mock(ha)
            reg = await registry(ha)
            await normalize_mock(ha, reg)
            await remove_broken_automations(ha, reg)
            reg = await registry(ha)
            await ensure_mode(ha, reg)
            await recreate_devices(ha, reg)
            await recreate_climate(ha, reg)
            reg = await registry(ha)
            await write_dashboard(ha, reg, await write_scripts(ha, reg))
            await check_zones(ha, reg)
        finally:
            await ha.close()


def main() -> int:
    """Check the target and the token, then set up the stand."""
    if urlparse(HA_URL).hostname not in LOCAL_HOSTS:
        print(f"Refusing {HA_URL}: the stand is set up only on this machine.")
        return 1
    if not (token := os.environ.get(TOKEN_ENV)):
        print(
            f"{TOKEN_ENV} is empty: set it (a Windows user variable) to a "
            "long-lived access token of the development HA, then restart VS Code "
            "and reopen the folder in the container."
        )
        return 1
    print(f"Setting up the Bestiarium stand on {HA_URL}")
    try:
        asyncio.run(setup_stand(HA_URL, token))
    except (StandError, aiohttp.ClientError) as err:
        print(f"Stand setup failed: {err}")
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
