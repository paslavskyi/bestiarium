"""The development HA of the stand, through its REST and websocket APIs."""

# A command-line script: it prints its progress. The stand dashboard and
# scripts are for the owner, in Ukrainian.
# ruff: noqa: T201

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .layout import (
    AC_THERMOSTAT,
    CLIMATE,
    DEVICES,
    IR,
    MOCK,
    THERMOSTATS,
    TOKEN_ENV,
    UNIVERSAL_THERMOSTATS,
)

if TYPE_CHECKING:
    import aiohttp


class StandError(Exception):
    """The stand cannot be set up; the message says why."""


class HomeAssistant:
    """The development HA, through its REST and websocket APIs."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        """Talk to the HA at url with the token."""
        self._session = session
        self.url = url.rstrip("/")
        self._token = token
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._message_id = 0

    async def connect(self) -> None:
        """Open and authenticate the websocket."""
        self._ws = await self._session.ws_connect(f"{self.url}/api/websocket")
        await self._ws.receive_json()
        await self._ws.send_json({"type": "auth", "access_token": self._token})
        if (await self._ws.receive_json())["type"] != "auth_ok":
            msg = f"The token in {TOKEN_ENV} is not accepted by {self.url}"
            raise StandError(msg)

    async def close(self) -> None:
        """Close the websocket."""
        if self._ws:
            await self._ws.close()

    async def ws(self, message_type: str, **data: Any) -> Any:
        """Send a websocket command and return its result."""
        self._message_id += 1
        await self._ws.send_json({"id": self._message_id, "type": message_type, **data})
        while True:
            reply = await self._ws.receive_json()
            if reply.get("id") == self._message_id:
                break
        if not reply["success"]:
            msg = f"{message_type}: {reply['error']['message']}"
            raise StandError(msg)
        return reply["result"]

    async def rest(self, method: str, path: str, body: Any = None) -> Any:
        """Call the REST API and return the JSON reply."""
        async with self._session.request(
            method,
            f"{self.url}/api/{path}",
            json=body,
            headers={"Authorization": f"Bearer {self._token}"},
        ) as response:
            if response.status >= 400:  # noqa: PLR2004
                msg = f"{method} {path}: {response.status} {await response.text()}"
                raise StandError(msg)
            return await response.json()

    async def _run_flow(
        self, kind: str, start: dict[str, Any], steps: tuple[dict[str, Any], ...]
    ) -> dict[str, Any]:
        path = f"config/config_entries/{kind}"
        result = await self.rest("POST", path, start)
        for step in steps:
            result = await self.rest("POST", f"{path}/{result['flow_id']}", step)
            if result.get("errors"):
                msg = f"{start['handler']} {result['step_id']}: {result['errors']}"
                raise StandError(msg)
        return result

    async def flow(
        self, kind: str, handler: str | list[str], *steps: dict[str, Any]
    ) -> dict[str, Any]:
        """Run a config (or subentry) flow through its steps; return the result."""
        result = await self._run_flow(kind, {"handler": handler}, steps)
        if result["type"] != "create_entry":
            msg = f"{handler}: the flow ended with {result['type']}, not an entry"
            raise StandError(msg)
        return result

    async def reconfigure(self, entry: dict[str, Any], *steps: dict[str, Any]) -> None:
        """Run the reconfigure flow of an entry through its steps."""
        start = {"handler": entry["domain"], "entry_id": entry["entry_id"]}
        result = await self._run_flow("flow", start, steps)
        if result.get("reason") != "reconfigure_successful":
            msg = f"{entry['domain']}: reconfigure ended with {result['type']}"
            raise StandError(msg)

    async def entries(self, domain: str) -> list[dict[str, Any]]:
        """Return the config entries of a domain."""
        return await self.rest("GET", f"config/config_entries/entry?domain={domain}")

    async def remove_entry(self, entry: dict[str, Any]) -> None:
        """Remove a config entry."""
        await self.rest("DELETE", f"config/config_entries/entry/{entry['entry_id']}")
        print(f"  removed {entry['domain']}: {entry['title']}")


@dataclass
class Registry:
    """Devices and entities of the stand, by mock id and unique_id."""

    devices: list[dict[str, Any]]
    entities: list[dict[str, Any]]

    def stand_climate(self) -> list[dict[str, Any]]:
        """
        Return the entities of the stand's climate entry, none of another one.

        The stand's entry is the one whose devices are the stand thermostats.
        """
        names = {
            AC_THERMOSTAT[0],
            *(name for name, _, _ in (*THERMOSTATS, *UNIVERSAL_THERMOSTATS)),
        }
        entry_ids = {
            entry_id
            for device in self.devices
            if device.get("name") in names
            for entry_id in device["config_entries"]
        }
        return [
            e
            for e in self.entities
            if e["platform"] == CLIMATE and e["config_entry_id"] in entry_ids
        ]

    def ir_switch(self, thermostat: str) -> str:
        """Return the virtual switch of a stand thermostat, by the thermostat's name."""
        for device in self.devices:
            if device.get("name") != f"{thermostat} IR":
                continue
            for entity in self.stand_climate():
                if entity["device_id"] == device["id"] and entity[
                    "entity_id"
                ].startswith("switch."):
                    return entity["entity_id"]
        msg = f"No IR switch of {thermostat}"
        raise StandError(msg)

    def thermostat(self, thermostat: str) -> str:
        """Return the climate entity of a stand thermostat, by its name."""
        for device in self.devices:
            if device.get("name") != thermostat:
                continue
            for entity in self.stand_climate():
                if entity["device_id"] == device["id"] and entity[
                    "entity_id"
                ].startswith("climate."):
                    return entity["entity_id"]
        msg = f"No thermostat {thermostat}"
        raise StandError(msg)

    def control_state(self, thermostat: str) -> str:
        """Return the "Control state" sensor of a stand thermostat, by its name."""
        for device in self.devices:
            if device.get("name") != thermostat:
                continue
            for entity in self.stand_climate():
                if entity["device_id"] == device["id"] and entity["unique_id"].endswith(
                    "_control_state"
                ):
                    return entity["entity_id"]
        msg = f"No control state of {thermostat}"
        raise StandError(msg)

    def ir_entity(self, name: str, domain: str) -> str:
        """Return an entity of a Bestiarium IR device, by the device's name."""
        devices = {
            device["id"] for device in self.devices if device.get("name") == name
        }
        for entity in self.entities:
            if (
                entity["platform"] == IR
                and entity["device_id"] in devices
                and entity["entity_id"].startswith(f"{domain}.")
            ):
                return entity["entity_id"]
        msg = f"No {domain} of {name} in Bestiarium IR"
        raise StandError(msg)

    def mock_device(self, mock_id: str) -> dict[str, Any]:
        """Return the device of a test socket or thermometer."""
        for device in self.devices:
            if [MOCK, f"mock_{mock_id}"] in device["identifiers"]:
                return device
        msg = f"No test device {mock_id}: is Bestiarium Mock set up?"
        raise StandError(msg)

    def entity_id(self, domain: str, platform: str, unique_id: str) -> str:
        """Return the entity_id of an entity by its domain, platform and unique_id."""
        for entity in self.entities:
            if (
                entity["entity_id"].startswith(f"{domain}.")
                and entity["platform"] == platform
                and entity["unique_id"] == unique_id
            ):
                return entity["entity_id"]
        msg = f"No {domain} {platform} {unique_id}"
        raise StandError(msg)

    def mock(self, domain: str, unique_id: str) -> str:
        """Return the entity_id of a Bestiarium Mock entity."""
        return self.entity_id(domain, MOCK, unique_id)

    def control(self, device_id: str, key: str) -> str:
        """Return the entity_id of a Bestiarium Devices control entity on a device."""
        for entity in self.entities:
            if (
                entity["platform"] == DEVICES
                and entity["device_id"] == device_id
                and entity["unique_id"].endswith(f"_{key}")
            ):
                return entity["entity_id"]
        msg = f"No {key} of Bestiarium Devices on device {device_id}"
        raise StandError(msg)


async def registry(ha: HomeAssistant) -> Registry:
    """Read the device and entity registries."""
    return Registry(
        devices=await ha.ws("config/device_registry/list"),
        entities=await ha.ws("config/entity_registry/list"),
    )
