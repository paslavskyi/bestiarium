"""The integrations and config entries of the stand."""

# A command-line script: it prints its progress. The stand dashboard and
# scripts are for the owner, in Ukrainian.
# ruff: noqa: T201

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .layout import (
    AC_THERMOSTAT,
    CLIMATE,
    CLOUD_AC,
    CLOUD_AC_SWITCH,
    DEVICES,
    HEATER_W,
    IR,
    LOAD_LIMIT_W,
    MOCK,
    MODE,
    MODES,
    RED_PERCENT,
    RETIRED_TEST_DEVICES,
    RETIRED_THERMOSTATS,
    SCRIPT_PREFIX,
    STAND_DEVICES,
    STAND_HISENSE,
    TEMPLATE,
    TEST_ENTITY_ID,
    THERMOSTATS,
    UNIVERSAL_THERMOSTATS,
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
            added += [{"next_step_id": "add"}, {"name": mode}]
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
        steps += [{"next_step_id": "add"}, {"name": mode}]
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


async def remove_stand_climate(ha: HomeAssistant) -> None:
    """
    Remove the stand's Bestiarium Climate entry; others are kept.

    The stand's entry is the one with the stand thermostats. It goes before
    the devices are made again: one relay lives in one integration, and an
    old thermostat on a floor relay would refuse its device.
    """
    stand_thermostats = {
        name for name, _, _ in (*THERMOSTATS, *UNIVERSAL_THERMOSTATS)
    } | RETIRED_THERMOSTATS
    for entry in await ha.entries(CLIMATE):
        subentries = await ha.ws(
            "config_entries/subentries/list", entry_id=entry["entry_id"]
        )
        if stand_thermostats & {subentry["title"] for subentry in subentries}:
            await ha.remove_entry(entry)


async def recreate_climate(ha: HomeAssistant, reg: Registry) -> None:
    """Make the stand's Bestiarium Climate entry: air conditioners and thermostats."""
    result = await ha.flow(
        "flow",
        CLIMATE,
        {
            "capacity": 2,
            # Three minutes: a rotation shows on the stand in minutes
            "min_working_time": {"hours": 0, "minutes": 3, "seconds": 0},
            # Five minutes: a deferred switch is tried again within the run
            "recovery_timeout": {"hours": 0, "minutes": 5, "seconds": 0},
        },
    )
    entry_id = result["result"]["entry_id"]
    # Two attempts (minutes): a dead switch is deferred quickly on the stand;
    # the steps of turning off a minute apart: the whole chain in 4 minutes
    await ha.flow(
        "options/flow",
        entry_id,
        {
            "attempts": 2,
            "tolerance_seconds": 60,
            "hysteresis_seconds": 60,
            "off_attempts": 3,
            "off_interval_seconds": 60,
        },
    )
    # The virtual switch of each air conditioner: IR by the stand's scripts,
    # its state by the power of the AC's socket, its power socket the
    # "Allowed" of that socket's device (the recommended choice)
    for name, _thermometer, socket in THERMOSTATS:
        await ha.flow(
            "subentries/flow",
            [entry_id, "switch"],
            {
                "name": f"{name} IR",
                "power_entity": reg.mock("sensor", f"mock_{socket}_power"),
                "min_power": 100,
                "power_tolerance_seconds": 60,
                "power_hysteresis_percent": 20,
                "power_delay_seconds": 10,
                "heating_action": [{"action": f"script.{SCRIPT_PREFIX}ir_{socket}_on"}],
                "off_action": [{"action": f"script.{SCRIPT_PREFIX}ir_{socket}_off"}],
                "power_socket": reg.control(reg.mock_device(socket)["id"], "allowed"),
            },
        )
    subentries = {
        subentry["title"]: subentry["subentry_id"]
        for subentry in await ha.ws("config_entries/subentries/list", entry_id=entry_id)
    }
    entities = await ha.ws("config/entity_registry/list")
    for name, thermometer, _socket in THERMOSTATS:
        switch = next(
            e["entity_id"]
            for e in entities
            if e["platform"] == CLIMATE and e["unique_id"] == subentries[f"{name} IR"]
        )
        await ha.flow(
            "subentries/flow",
            [entry_id, "thermostat"],
            {
                "name": name,
                "temperature_sensor": reg.mock(
                    "sensor", f"mock_{thermometer}_temperature"
                ),
                "target_switch": switch,
                "thermostat_mode": "heating",
                "cold_tolerance": 0.3,
                "hot_tolerance": 0.3,
                # The pause after an "off" and the working time before a
                # rotation (decision 29): a minute, so that a scenario shows
                # its result within 4 minutes of its button
                "min_cycle_duration": {"hours": 0, "minutes": 1, "seconds": 0},
            },
            {},
        )
        print(f"  added {CLIMATE} thermostat: {name} on {switch}")
    cloud_ac = await recreate_cloud_ac_switch(ha)
    # The universal thermostats (DEV-011): a heater on its own socket, and
    # the cloud AC on its template switch; both read by the socket's power
    for name, thermometer, socket in UNIVERSAL_THERMOSTATS:
        switch = (
            cloud_ac if socket == CLOUD_AC else reg.mock("switch", f"mock_{socket}")
        )
        await ha.flow(
            "subentries/flow",
            [entry_id, "universal_thermostat"],
            {
                "name": name,
                "temperature_sensor": reg.mock(
                    "sensor", f"mock_{thermometer}_temperature"
                ),
                "target_switch": switch,
                "thermostat_mode": "heating",
                "cold_tolerance": 0.3,
                "hot_tolerance": 0.3,
                "min_cycle_duration": {"hours": 0, "minutes": 1, "seconds": 0},
                **({"heater_power": HEATER_W} if socket != CLOUD_AC else {}),
            },
            {
                "power_entity": reg.mock("sensor", f"mock_{socket}_power"),
                "min_power": 20,
                "power_tolerance_seconds": 60,
                "power_hysteresis_percent": 20,
                "power_delay_seconds": 10,
            },
            {},
        )
        print(f"  added {CLIMATE} universal thermostat: {name} on {switch}")
    # The air conditioner thermostat (DEV-013) on the second Hisense, read
    # by its socket's power; its socket the "Allowed" of that socket's device
    name, thermometer, hisense, socket = AC_THERMOSTAT
    air_conditioner = reg.ir_entity(hisense, "climate")
    await ha.flow(
        "subentries/flow",
        [entry_id, "ac_thermostat"],
        {
            "name": name,
            "temperature_sensor": reg.mock("sensor", f"mock_{thermometer}_temperature"),
            "air_conditioner": air_conditioner,
            "setpoint_margin": 1,
            "cold_tolerance": 0.3,
            "hot_tolerance": 0.3,
            "min_cycle_duration": {"hours": 0, "minutes": 1, "seconds": 0},
        },
        {
            "power_entity": reg.mock("sensor", f"mock_{socket}_power"),
            "min_power": 20,
            "power_tolerance_seconds": 60,
            "power_hysteresis_percent": 20,
            "power_delay_seconds": 10,
            "power_socket": reg.control(reg.mock_device(socket)["id"], "allowed"),
        },
        {},
    )
    print(f"  added {CLIMATE} air conditioner thermostat: {name} on {air_conditioner}")


async def recreate_cloud_ac_switch(ha: HomeAssistant) -> str:
    """
    Make the template switch of the cloud AC again; return its entity_id.

    As the home's switch over Tuya scenes: no state of its own (optimistic),
    its actions the IR scripts of the fourth AC.
    """
    for entry in await ha.entries(TEMPLATE):
        if entry["title"] == CLOUD_AC_SWITCH:
            await ha.remove_entry(entry)
    result = await ha.flow(
        "flow",
        TEMPLATE,
        {"next_step_id": "switch"},
        {
            "name": CLOUD_AC_SWITCH,
            "turn_on": [{"action": f"script.{SCRIPT_PREFIX}ir_{CLOUD_AC}_on"}],
            "turn_off": [{"action": f"script.{SCRIPT_PREFIX}ir_{CLOUD_AC}_off"}],
        },
    )
    entry_id = result["result"]["entry_id"]
    entities = await ha.ws("config/entity_registry/list")
    switch = next(e["entity_id"] for e in entities if e["config_entry_id"] == entry_id)
    print(f"  added the template switch {switch}")
    return switch


async def recreate_ir(ha: HomeAssistant, reg: Registry) -> None:
    """
    Make the stand's Hisense air conditioners again, each on its mock emitter.

    Bestiarium IR is added if missing; its other subentries (the blasters and
    air conditioners of the development HA) are kept.
    """
    entries = await ha.entries(IR)
    if entries:
        entry_id = entries[0]["entry_id"]
    else:
        entry_id = (await ha.flow("flow", IR))["result"]["entry_id"]
        print("  added Bestiarium IR")
    names = {name for name, _ in STAND_HISENSE}
    for subentry in await ha.ws("config_entries/subentries/list", entry_id=entry_id):
        if subentry["title"] in names:
            await ha.ws(
                "config_entries/subentries/delete",
                entry_id=entry_id,
                subentry_id=subentry["subentry_id"],
            )
    for name, mock_id in STAND_HISENSE:
        emitter = reg.mock("infrared", f"mock_{mock_id}_emitter")
        await ha.flow(
            "subentries/flow",
            [entry_id, "hisense"],
            {"name": name, "emitter": emitter},
        )
        print(f"  added {IR} air conditioner: {name} on {emitter}")
