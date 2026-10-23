"""The integrations and config entries of the stand."""

# A command-line script: it prints its progress. The stand dashboard and
# scripts are for the owner, in Ukrainian.
# ruff: noqa: T201

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .layout import (
    CLIMATE,
    DEVICES,
    LOAD_LIMIT_W,
    MOCK,
    MODE,
    MODES,
    RED_PERCENT,
    RETIRED_TEST_DEVICES,
    STAND_DEVICES,
    TEST_ENTITY_ID,
    THERMOSTATS,
    YELLOW_PERCENT,
)

if TYPE_CHECKING:
    from .ha import HomeAssistant, Registry


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
    current entity_id, which normalize_mock may just have changed, and gets
    the stand's modes it lacks (the scenarios select "Night").
    """
    grid = reg.mock("sensor", "mock_grid_power")
    if entries := await ha.entries(MODE):
        select = await ha.rest(
            "GET", f"states/{reg.entity_id('select', MODE, 'select')}"
        )
        missing = [m for m in MODES if m not in select["attributes"]["options"]]
        added: list[dict[str, Any]] = []
        for mode in missing:
            added += [{"next_step_id": "add"}, {"name": mode, "icon": "mdi:home"}]
        await ha.reconfigure(
            entries[0],
            *added,
            {"next_step_id": "configure_ac_load_limits"},
            {"ac_power_sensor": grid},
            {"next_step_id": "complete"},
        )
        print(f"  Bestiarium Mode kept, watching {grid}; modes added: {missing}")
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
                **(
                    {"priority_Night": device.night} if device.night is not None else {}
                ),
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
