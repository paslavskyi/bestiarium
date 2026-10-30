"""The dashboard of the stand and the check that its zones work."""

# A command-line script: it prints its progress. The stand dashboard and
# scripts are for the owner, in Ukrainian.
# ruff: noqa: T201, RUF001

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from custom_components.bestiarium_mock.const import MOCK_DEVICES

from .ha import StandError
from .layout import (
    DASHBOARD,
    MODE,
    STAND_DEVICES,
    STAND_HISENSE,
    THERMOSTATS,
    UNIVERSAL_THERMOSTATS,
    ZONE_CHECK_SECONDS,
    ZONES,
)

if TYPE_CHECKING:
    from .ha import HomeAssistant, Registry


def _tile(entity_id: str, **options: Any) -> dict[str, Any]:
    return {"type": "tile", "entity": entity_id, **options}


def _dashboard(reg: Registry, scripts: list[str]) -> dict[str, Any]:
    zones = [
        reg.entity_id("binary_sensor", MODE, f"power_load_{zone}") for zone in ZONES
    ]
    grid = [
        reg.mock("sensor", "mock_grid_power"),
        reg.mock("select", "mock_grid_reading_profile"),
    ]
    climate = [e for e in reg.stand_climate() if e["device_id"]]
    universal = [
        (reg.thermostat(name), reg.control_state(name), thermometer, socket)
        for name, thermometer, socket in UNIVERSAL_THERMOSTATS
    ]
    new = {
        entity
        for thermostat, control, _, _ in universal
        for entity in (thermostat, control)
    }
    thermostats = [
        (thermostat, reg.mock("sensor", f"mock_{thermometer}_temperature"), thermometer)
        for thermostat, (_, thermometer, _) in zip(
            sorted(
                e["entity_id"]
                for e in climate
                if e["entity_id"].startswith("climate.") and e["entity_id"] not in new
            ),
            THERMOSTATS,
            strict=False,
        )
    ]
    control_states = sorted(
        e["entity_id"]
        for e in climate
        if e["entity_id"].startswith("sensor.") and e["entity_id"] not in new
    )
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
                {"type": "heading", "heading": "Термостати"},
                *(
                    tile
                    for (thermostat, temperature, thermometer), control in zip(
                        thermostats, control_states, strict=False
                    )
                    for tile in (
                        _tile(thermostat),
                        _tile(control),
                        _tile(temperature),
                        _tile(
                            reg.mock("select", f"mock_{thermometer}_reading_profile")
                        ),
                    )
                ),
            ],
        },
        {
            "type": "grid",
            "cards": [
                {"type": "heading", "heading": "Нові термостати"},
                *(
                    tile
                    for thermostat, control, thermometer, socket in universal
                    for tile in (
                        _tile(thermostat),
                        _tile(control),
                        _tile(reg.mock("sensor", f"mock_{thermometer}_temperature")),
                        _tile(reg.mock("sensor", f"mock_{socket}_power")),
                    )
                ),
            ],
        },
        *(
            {
                "type": "grid",
                "cards": [
                    {"type": "heading", "heading": name},
                    _tile(reg.ir_entity(name, "climate")),
                    _tile(reg.ir_entity(name, "switch")),
                    *(
                        _tile(reg.mock(domain, f"mock_{mock_id}_{key}"))
                        for domain, key in (
                            ("sensor", "ac_state"),
                            ("sensor", "last_frame"),
                            ("infrared", "emitter"),
                            ("sensor", "power_power"),
                        )
                    ),
                ],
            }
            for name, mock_id in STAND_HISENSE
        ),
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
    logged = [
        *zones,
        mode_select,
        *control_states,
        *new,
        # Each frame of the Hisense: its emitter and what the unit did with it
        *(
            reg.mock(domain, f"mock_{mock_id}_{key}")
            for _, mock_id in STAND_HISENSE
            for domain, key in (("infrared", "emitter"), ("sensor", "last_frame"))
        ),
    ]
    balanced = {device.mock_id for device in STAND_DEVICES}
    for spec in MOCK_DEVICES:
        device = reg.mock_device(spec.device_id)
        switch = reg.mock("switch", f"mock_{spec.device_id}")
        logged.append(switch)
        # The free socket has no Bestiarium device until one is re-pointed to it
        control = []
        if spec.device_id in balanced:
            control = [_tile(reg.control(device["id"], "control_state"))]
            logged += [
                reg.control(device["id"], "control_state"),
                reg.control(device["id"], "control_events"),
            ]
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
                    *control,
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
