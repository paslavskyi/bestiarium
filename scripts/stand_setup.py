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
# ruff: noqa: T201, RUF001

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import aiohttp

from custom_components.bestiarium_devices.const import LOAD_PRIORITY_ALWAYS_ON
from custom_components.bestiarium_mock.const import (
    MOCK_DEVICES,
    FaultProfile,
)

HA_URL = os.environ.get("BESTIARIUM_STAND_URL", "http://localhost:8123")
TOKEN_ENV = "HA_TEST_ENV_TOKEN"  # noqa: S105 - the name of the variable
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

MOCK = "bestiarium_mock"
MODE = "bestiarium_mode"
DEVICES = "bestiarium_devices"
CLIMATE = "bestiarium_climate"

DASHBOARD = "bestiarium-stand"
SCRIPT_PREFIX = "bestiarium_stand_"
ZONES = ("green", "yellow", "red", "overload")
ZONE_CHECK_SECONDS = 15
# Bestiarium Devices entries of the test sockets removed in DEV-007: without
# their devices they have no entities left to recognise them by
RETIRED_TEST_DEVICES = frozenset(
    {
        "Mock AC",
        "Mock Kettle",
        "Mock Router",
        "Mock Heating",
        "Mock Washing Machine",
        "Mock Dishwasher",
        *(f"Mock Socket {number}" for number in range(1, 5)),
    }
)
# A test entity_id in an automation, such as switch.mock_socket_3_switch
TEST_ENTITY_ID = re.compile(r"\b[a-z_]+\.mock_\w+")


@dataclass(frozen=True)
class StandDevice:
    """How a test socket is added to Bestiarium Devices."""

    mock_id: str
    priority: int
    first_queue: bool = False
    thermostat_driven: bool = False


# Added in this order: of two devices with the same priority the balancer
# picks the one added first, so the commode socket goes before the bedside one
STAND_DEVICES = (
    StandDevice("commode_socket", 5200),
    StandDevice("bedside_socket", 5200),
    StandDevice("boiler", 5000, first_queue=True),
    StandDevice("slow_cloud_socket", 4000),
    StandDevice("tuya_socket", 3500),
    StandDevice("night_light", 3000),
    StandDevice("dead_socket", 1000),
    StandDevice("cooker", LOAD_PRIORITY_ALWAYS_ON),
    StandDevice("fridge", LOAD_PRIORITY_ALWAYS_ON),
    StandDevice("floor_heating_1", 2000, first_queue=True, thermostat_driven=True),
    StandDevice("floor_heating_2", 2000, first_queue=True, thermostat_driven=True),
    StandDevice("floor_heating_3", 2000, first_queue=True, thermostat_driven=True),
)
# Thermostat name, thermometer, floor heating socket
THERMOSTATS = (
    ("Stand floor 1", "thermo_1", "floor_heating_1"),
    ("Stand floor 2", "thermo_2", "floor_heating_2"),
    ("Stand floor 3", "thermo_3", "floor_heating_3"),
)
MODES = ("Grid on", "Night")
LOAD_LIMIT_W = 5000
YELLOW_PERCENT = 75
RED_PERCENT = 90


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


async def ensure_mock(ha: HomeAssistant) -> None:
    """Add Bestiarium Mock if it is missing."""
    if await ha.entries(MOCK):
        return
    await ha.flow("flow", MOCK, {})
    print("  added Bestiarium Mock")


async def normalize_mock(ha: HomeAssistant, reg: Registry) -> None:
    """
    Give every test device its own name and every test entity `<domain>.<unique_id>`.

    Otherwise entity_ids built from a renamed device or its area (such as
    `sensor.none_power` or `select.kukhnia_mock_boiler_1_fault_profile`) make
    one stand differ from another.
    """
    for device in reg.devices:
        if any(i[0] == MOCK for i in device["identifiers"]) and (
            device["name_by_user"] or device["area_id"]
        ):
            await ha.ws(
                "config/device_registry/update",
                device_id=device["id"],
                name_by_user=None,
                area_id=None,
            )
    renamed = 0
    for entity in reg.entities:
        if entity["platform"] != MOCK:
            continue
        domain = entity["entity_id"].split(".")[0]
        changes: dict[str, Any] = {"name": None} if entity.get("name") else {}
        if entity["entity_id"] != (wanted := f"{domain}.{entity['unique_id']}"):
            changes["new_entity_id"] = wanted
        if changes:
            await ha.ws(
                "config/entity_registry/update",
                entity_id=entity["entity_id"],
                **changes,
            )
            renamed += 1
    if renamed:
        print(f"  renamed {renamed} test entities to <domain>.<unique_id>")


async def remove_broken_automations(ha: HomeAssistant, reg: Registry) -> None:
    """
    Remove the automations that broke because test entities are gone.

    An automation that fails to validate is unavailable; its id is the
    unique_id of its entity. Only one that refers to a test entity that no
    longer exists is removed; any other broken automation is left as it is.
    """
    states = {state["entity_id"]: state for state in await ha.rest("GET", "states")}
    for entity in reg.entities:
        if entity["platform"] != "automation" or (
            states.get(entity["entity_id"], {}).get("state") != "unavailable"
        ):
            continue
        path = f"config/automation/config/{entity['unique_id']}"
        config = json.dumps(await ha.rest("GET", path))
        if any(ref not in states for ref in TEST_ENTITY_ID.findall(config)):
            await ha.rest("DELETE", path)
            print(f"  removed the automation of old test devices {entity['entity_id']}")
        else:
            print(f"  kept the broken automation {entity['entity_id']}: not a test one")


async def ensure_mode(ha: HomeAssistant, reg: Registry) -> None:
    """
    Add Bestiarium Mode on the test grid if it is missing.

    An existing one keeps its modes and limits but watches the test grid by its
    current entity_id, which normalize_mock may just have changed.
    """
    grid = reg.mock("sensor", "mock_grid_power")
    if entries := await ha.entries(MODE):
        await ha.reconfigure(
            entries[0],
            {"next_step_id": "configure_ac_load_limits"},
            {"ac_power_sensor": grid},
            {"next_step_id": "complete"},
        )
        print(f"  Bestiarium Mode kept, watching {grid}")
        return
    steps: list[dict[str, Any]] = []
    for mode in MODES:
        steps += [{"next_step_id": "add"}, {"name": mode, "icon": "mdi:home"}]
    steps += [
        {"next_step_id": "configure_ac_load_limits"},
        {
            "ac_power_sensor": grid,
            "ac_load_limit_w": LOAD_LIMIT_W,
            "ac_yellow_zone_percent": YELLOW_PERCENT,
            "ac_red_zone_percent": RED_PERCENT,
            "ac_overload_recovery_seconds": 30,
        },
        {"next_step_id": "complete"},
    ]
    await ha.flow("flow", MODE, *steps)
    print("  added Bestiarium Mode")


def _belongs_to_stand(entry: dict[str, Any], reg: Registry) -> bool:
    """
    Return whether a Bestiarium Devices entry is a stand device.

    It is one if an entity of it sits on a test device, or if it has no
    entities at all and is named after a retired test socket (its device went
    with the DEV-007 migration). Anything else is kept: entries of other
    devices, standalone ones, disabled ones.
    """
    test_devices = {
        device["id"]
        for device in reg.devices
        if any(i[0] == MOCK for i in device["identifiers"])
    }
    entities = [e for e in reg.entities if e["config_entry_id"] == entry["entry_id"]]
    if entities:
        return any(entity["device_id"] in test_devices for entity in entities)
    return entry["disabled_by"] is None and entry["title"] in RETIRED_TEST_DEVICES


async def recreate_devices(ha: HomeAssistant, reg: Registry) -> None:
    """Replace the Bestiarium Devices entries of the stand with fresh ones."""
    for entry in await ha.entries(DEVICES):
        if _belongs_to_stand(entry, reg):
            await ha.remove_entry(entry)
    for device in STAND_DEVICES:
        await ha.flow(
            "flow",
            DEVICES,
            {"next_step_id": "extend_device"},
            {"device_id": reg.mock_device(device.mock_id)["id"]},
            {
                "default": device.priority,
                "overload_first_queue": device.first_queue,
                "thermostat_driven": device.thermostat_driven,
            },
        )
        print(f"  added {DEVICES}: {device.mock_id} (priority {device.priority})")


async def recreate_climate(ha: HomeAssistant, reg: Registry) -> None:
    """
    Replace the stand's Bestiarium Climate entry and its thermostats.

    The stand's entry is the one with the stand thermostats; others are kept.
    """
    stand_thermostats = {name for name, _, _ in THERMOSTATS}
    for entry in await ha.entries(CLIMATE):
        subentries = await ha.ws(
            "config_entries/subentries/list", entry_id=entry["entry_id"]
        )
        if stand_thermostats & {subentry["title"] for subentry in subentries}:
            await ha.remove_entry(entry)
    result = await ha.flow(
        "flow",
        CLIMATE,
        {
            "capacity": 2,
            "min_working_time": {"hours": 0, "minutes": 10, "seconds": 0},
            "recovery_timeout": {"hours": 1, "minutes": 0, "seconds": 0},
        },
    )
    entry_id = result["result"]["entry_id"]
    for name, thermometer, socket in THERMOSTATS:
        await ha.flow(
            "subentries/flow",
            [entry_id, "thermostat"],
            {
                "name": name,
                "temperature_sensor": reg.mock(
                    "sensor", f"mock_{thermometer}_temperature"
                ),
                "target_switch": reg.mock("switch", f"mock_{socket}"),
                "thermostat_mode": "heating",
                "cold_tolerance": 0.3,
                "hot_tolerance": 0.3,
            },
            {},
        )
        print(f"  added {CLIMATE} thermostat: {name}")


def _scripts(reg: Registry) -> dict[str, dict[str, Any]]:
    """Return the scenario scripts, by object id."""

    def switch(*mock_ids: str) -> list[str]:
        return [reg.mock("switch", f"mock_{mock_id}") for mock_id in mock_ids]

    def profile(mock_id: str, fault_profile: FaultProfile) -> dict[str, Any]:
        return {
            "action": "select.select_option",
            "target": {
                "entity_id": reg.mock("select", f"mock_{mock_id}_fault_profile")
            },
            "data": {"option": fault_profile.value},
        }

    def cooker(watts: int) -> dict[str, Any]:
        return {
            "action": "number.set_value",
            "target": {"entity_id": reg.mock("number", "mock_cooker_mock_power")},
            "data": {"value": watts},
        }

    def turn(service: str, *mock_ids: str) -> dict[str, Any]:
        return {
            "action": f"switch.{service}",
            "target": {"entity_id": switch(*mock_ids)},
        }

    def wait(seconds: int) -> dict[str, Any]:
        return {"delay": {"seconds": seconds}}

    reset = {"action": f"script.{SCRIPT_PREFIX}reset"}
    all_sockets = [spec.device_id for spec in MOCK_DEVICES]
    # The balancer keeps 'always on' devices on, so the reset leaves them on
    always_on = [
        device.mock_id
        for device in STAND_DEVICES
        if device.priority == LOAD_PRIORITY_ALWAYS_ON
    ]
    return {
        "reset": {
            "alias": "Стенд: скинути",
            "icon": "mdi:restore",
            "sequence": [
                *(profile(mock_id, FaultProfile.RELIABLE) for mock_id in all_sockets),
                cooker(400),
                turn("turn_off", *(s for s in all_sockets if s not in always_on)),
                turn("turn_on", *always_on),
                wait(2),
            ],
        },
        "night_case": {
            "alias": "Стенд: нічний випадок",
            "icon": "mdi:weather-night",
            "sequence": [
                reset,
                profile("commode_socket", FaultProfile.STUCK_STATE),
                turn("turn_on", "commode_socket"),
            ],
        },
        "red_zone_dead_socket": {
            "alias": "Стенд: червона зона з мертвою розеткою",
            "icon": "mdi:power-plug-off",
            "sequence": [
                reset,
                turn("turn_on", "dead_socket", "night_light", "slow_cloud_socket"),
                wait(2),
                profile("slow_cloud_socket", FaultProfile.SLOW_CLOUD),
                profile("dead_socket", FaultProfile.UNRESPONSIVE),
                cooker(3100),
            ],
        },
        "slow_cloud": {
            "alias": "Стенд: повільна хмара",
            "icon": "mdi:cloud-clock",
            "sequence": [
                reset,
                turn("turn_on", "commode_socket", "bedside_socket", "boiler"),
                profile("slow_cloud_socket", FaultProfile.SLOW_CLOUD),
            ],
        },
        "unstable_tuya": {
            "alias": "Стенд: нестабільна Tuya",
            "icon": "mdi:wifi-strength-alert-outline",
            "sequence": [
                reset,
                profile("tuya_socket", FaultProfile.UNSTABLE),
                turn("turn_on", "tuya_socket"),
            ],
        },
    }


async def write_scripts(ha: HomeAssistant, reg: Registry) -> list[str]:
    """Write the scenario scripts; return their entity_ids in dashboard order."""
    scripts = _scripts(reg)
    for name, config in scripts.items():
        await ha.rest("POST", f"config/script/config/{SCRIPT_PREFIX}{name}", config)
    entity_ids = [f"script.{SCRIPT_PREFIX}{name}" for name in scripts]
    loaded = {state["entity_id"] for state in await ha.rest("GET", "states")}
    if not loaded.issuperset(entity_ids):
        msg = (
            "The scenario scripts were written but HA did not load them: "
            "configuration.yaml needs `script: !include scripts.yaml`"
        )
        raise StandError(msg)
    print(f"  wrote {len(scripts)} scenario scripts")
    return entity_ids


def _tile(entity_id: str, **options: Any) -> dict[str, Any]:
    return {"type": "tile", "entity": entity_id, **options}


def _dashboard(reg: Registry, scripts: list[str]) -> dict[str, Any]:
    zones = [
        reg.entity_id("binary_sensor", MODE, f"power_load_{zone}") for zone in ZONES
    ]
    grid = [
        reg.mock("sensor", "mock_grid_power"),
        reg.mock("binary_sensor", "mock_grid_alarm"),
    ]
    mode_select = reg.entity_id("select", MODE, "select")
    run = {"action": "perform-action", "perform_action": "script.turn_on"}
    sections: list[dict[str, Any]] = [
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "Мережа і зони"},
                *(_tile(entity_id) for entity_id in grid),
                _tile(mode_select, features=[{"type": "select-options"}]),
                *(_tile(entity_id) for entity_id in zones),
            ],
        },
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "Сценарії"},
                *(
                    _tile(
                        script,
                        tap_action={**run, "target": {"entity_id": script}},
                        icon_tap_action={**run, "target": {"entity_id": script}},
                    )
                    for script in scripts
                ),
            ],
        },
    ]
    logged = [*zones, mode_select]
    for spec in MOCK_DEVICES:
        device = reg.mock_device(spec.device_id)
        switch = reg.mock("switch", f"mock_{spec.device_id}")
        control_state = reg.control(device["id"], "control_state")
        logged += [switch, control_state, reg.control(device["id"], "control_events")]
        open_page = {
            "action": "navigate",
            "navigation_path": f"/config/devices/device/{device['id']}",
        }
        sections.append(
            {
                "type": "grid",
                "cards": [
                    {
                        "type": "heading",
                        "heading": device["name"],
                        "tap_action": open_page,
                    },
                    _tile(switch, tap_action=open_page),
                    _tile(reg.mock("sensor", f"mock_{spec.device_id}_power")),
                    _tile(reg.mock("select", f"mock_{spec.device_id}_fault_profile")),
                    _tile(control_state),
                ],
            }
        )
    sections.append(
        {
            "type": "grid",
            "column_span": 2,
            "cards": [
                {"type": "heading", "heading": "Журнал"},
                {
                    "type": "logbook",
                    "hours_to_show": 3,
                    "target": {"entity_id": logged},
                    "grid_options": {"columns": "full"},
                },
            ],
        }
    )
    return {
        "views": [
            {
                "title": "Стенд",
                "path": "stand",
                "type": "sections",
                "max_columns": 4,
                "sections": sections,
            }
        ]
    }


async def write_dashboard(ha: HomeAssistant, reg: Registry, scripts: list[str]) -> None:
    """Create the stand dashboard if missing and write its content."""
    dashboards = await ha.ws("lovelace/dashboards/list")
    if not any(board["url_path"] == DASHBOARD for board in dashboards):
        await ha.ws(
            "lovelace/dashboards/create",
            url_path=DASHBOARD,
            title="Стенд Bestiarium",
            icon="mdi:test-tube",
            show_in_sidebar=True,
            require_admin=False,
            mode="storage",
        )
    await ha.ws(
        "lovelace/config/save", url_path=DASHBOARD, config=_dashboard(reg, scripts)
    )
    print(f"  wrote the dashboard {ha.url}/{DASHBOARD}")


async def check_zones(ha: HomeAssistant, reg: Registry) -> None:
    """Fail unless the zone sensors of Bestiarium Mode have a state."""
    zones = [reg.entity_id("binary_sensor", MODE, f"power_load_{z}") for z in ZONES]
    for _ in range(ZONE_CHECK_SECONDS):
        states = [await ha.rest("GET", f"states/{zone}") for zone in zones]
        if all(state["state"] in ("on", "off") for state in states):
            print("  the load zones of Bestiarium Mode are available")
            return
        await asyncio.sleep(1)
    msg = (
        "The load zones of Bestiarium Mode are unavailable: it does not read "
        f"the test grid {reg.mock('sensor', 'mock_grid_power')}"
    )
    raise StandError(msg)


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
