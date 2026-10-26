"""The scenario scripts of the stand: one button each on its dashboard."""

# A command-line script: it prints its progress. The stand dashboard and
# scripts are for the owner, in Ukrainian.
# ruff: noqa: T201, RUF001

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from custom_components.bestiarium_devices.const import LOAD_PRIORITY_ALWAYS_ON
from custom_components.bestiarium_mock.const import (
    MOCK_DEVICES,
    MOCK_GRID,
    MOCK_THERMOMETERS,
    FaultProfile,
    ReadingProfile,
)

from .ha import StandError
from .layout import (
    AC_RUNNING_W,
    AC_STANDBY_W,
    MODE,
    RED_W,
    SCRIPT_PREFIX,
    STAND_DEVICES,
    THERMOSTATS,
    YELLOW_W,
)

if TYPE_CHECKING:
    from .ha import HomeAssistant, Registry


def scenario_scripts(reg: Registry) -> dict[str, dict[str, Any]]:
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

    def grid_at(watts: int, delta: int) -> dict[str, Any]:
        """Set the cooker so that the grid load is watts + delta."""
        grid = reg.mock("sensor", "mock_grid_power")
        power = reg.mock("number", "mock_cooker_mock_power")
        value = (
            f"{{{{ {watts + delta} - states('{grid}') | float(0)"
            f" + states('{power}') | float(0) }}}}"
        )
        return {
            "action": "number.set_value",
            "target": {"entity_id": power},
            "data": {"value": value},
        }

    def mode(name: str) -> dict[str, Any]:
        return {
            "action": "select.select_option",
            "target": {"entity_id": reg.entity_id("select", MODE, "select")},
            "data": {"option": name},
        }

    def turn(service: str, *mock_ids: str) -> dict[str, Any]:
        return {
            "action": f"switch.{service}",
            "target": {"entity_id": switch(*mock_ids)},
        }

    def wait(seconds: int) -> dict[str, Any]:
        return {"delay": {"seconds": seconds}}

    climate = reg.stand_climate()
    thermostats = [
        e["entity_id"] for e in climate if e["entity_id"].startswith("climate.")
    ]
    capacity = [e["entity_id"] for e in climate if e["entity_id"].startswith("number.")]

    def heat(hvac_mode: str) -> list[dict[str, Any]]:
        """Every stand thermostat heats to 25 °C (above the thermometers), or off."""
        steps: list[dict[str, Any]] = [
            {
                "action": "climate.set_hvac_mode",
                "target": {"entity_id": thermostats},
                "data": {"hvac_mode": hvac_mode},
            }
        ]
        if hvac_mode == "heat":
            steps.append(
                {
                    "action": "climate.set_temperature",
                    "target": {"entity_id": thermostats},
                    "data": {"temperature": 25},
                }
            )
        return steps

    def ac_power(ac: str, watts: int) -> dict[str, Any]:
        return {
            "action": "number.set_value",
            "target": {"entity_id": reg.mock("number", f"mock_{ac}_mock_power")},
            "data": {"value": watts},
        }

    def ir(ac: str, watts: int) -> list[dict[str, Any]]:
        """
        Play the IR of an air conditioner: it runs (or stands by) if it hears it.

        Its knob "IR lost" is an IR that does not reach it, "IR hangs" an "on"
        that never ends while the knob is on; its socket (the power) still
        works. With its socket off (the load protection) it draws nothing anyway.
        """
        lost = reg.mock("switch", f"mock_{ac}_ir_lost")
        hangs = reg.mock("switch", f"mock_{ac}_ir_hangs")
        starting = (
            [
                {"wait_template": f"{{{{ is_state('{hangs}', 'off') }}}}"},
                # An air conditioner takes a few seconds to start
                wait(5),
            ]
            if watts == AC_RUNNING_W
            else []
        )
        return [
            {"condition": "state", "entity_id": lost, "state": "off"},
            *starting,
            ac_power(ac, watts),
        ]

    def reading(device: str, reading_profile: ReadingProfile) -> dict[str, Any]:
        return {
            "action": "select.select_option",
            "target": {
                "entity_id": reg.mock("select", f"mock_{device}_reading_profile")
            },
            "data": {"option": reading_profile.value},
        }

    reset = {"action": f"script.{SCRIPT_PREFIX}reset"}
    readings = [*(spec.device_id for spec in MOCK_THERMOMETERS), MOCK_GRID.device_id]
    all_sockets = [spec.device_id for spec in MOCK_DEVICES]
    # The balancer keeps 'always on' devices on, so the reset leaves them on
    always_on = [
        device.mock_id
        for device in STAND_DEVICES
        if device.priority == LOAD_PRIORITY_ALWAYS_ON
    ]
    ir_lost = [reg.mock("switch", f"mock_{ac}_ir_lost") for _, _, ac in THERMOSTATS]
    # The stand's own loads that a person turns on (not always on, not run by
    # a thermostat): a fault scenario does not allow them, so the load stays
    # green and the faulty air conditioner gets its start
    loads = [
        reg.control(reg.mock_device(device.mock_id)["id"], "allowed")
        for device in STAND_DEVICES
        if device.priority != LOAD_PRIORITY_ALWAYS_ON and not device.thermostat_driven
    ]
    loads_scene_id = "bestiarium_stand_loads_allowed"
    loads_scene = f"scene.{loads_scene_id}"

    # The stand's loads not allowed, their state kept for the reset
    loads_wait = [
        {
            "action": "scene.create",
            "data": {"scene_id": loads_scene_id, "snapshot_entities": loads},
        },
        {"action": "switch.turn_off", "target": {"entity_id": loads}},
    ]

    def faulty_first(ac: int) -> list[dict[str, Any]]:
        """
        Make one air conditioner the first in the queue, the load green.

        Its thermostat heats to 30 °C, the others to 25 °C: the largest
        difference goes first.
        """
        return [
            *loads_wait,
            *heat("heat"),
            {
                "action": "climate.set_temperature",
                "target": {"entity_id": thermostats[ac - 1]},
                "data": {"temperature": 30},
            },
        ]

    ir_hangs = [reg.mock("switch", f"mock_{ac}_ir_hangs") for _, _, ac in THERMOSTATS]
    irs = {
        f"ir_{ac}_{command}": {
            "alias": f"Стенд: ІЧ {name} — {label}",
            "icon": "mdi:remote",
            "sequence": ir(ac, watts),
        }
        for name, _, ac in THERMOSTATS
        for command, label, watts in (
            ("on", "увімкнути", AC_RUNNING_W),
            ("off", "вимкнути", AC_STANDBY_W),
        )
    }
    return {
        **irs,
        "reset": {
            "alias": "Стенд: скинути",
            "icon": "mdi:restore",
            "sequence": [
                *(profile(mock_id, FaultProfile.RELIABLE) for mock_id in all_sockets),
                *(reading(device, ReadingProfile.RELIABLE) for device in readings),
                *heat("off"),
                *(ac_power(ac, AC_STANDBY_W) for _, _, ac in THERMOSTATS),
                {
                    "action": "switch.turn_off",
                    "target": {"entity_id": [*ir_lost, *ir_hangs]},
                },
                {
                    "action": "number.set_value",
                    "target": {"entity_id": capacity},
                    "data": {"value": 2},
                },
                mode("Grid on"),
                cooker(400),
                turn("turn_off", *(s for s in all_sockets if s not in always_on)),
                turn("turn_on", *always_on),
                # The loads a fault scenario did not allow: as they were before it
                {
                    "action": "scene.turn_on",
                    "target": {"entity_id": loads_scene},
                    "continue_on_error": True,
                },
                {
                    "action": "scene.delete",
                    "target": {"entity_id": loads_scene},
                    "continue_on_error": True,
                },
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
        "zone_flicker": {
            "alias": "Стенд: навантаження коливається біля жовтого порогу",
            "icon": "mdi:sine-wave",
            "sequence": [
                reset,
                grid_at(YELLOW_W, 100),
                wait(5),
                {
                    "repeat": {
                        "count": 12,
                        "sequence": [
                            grid_at(YELLOW_W, -100),
                            wait(5),
                            grid_at(YELLOW_W, 100),
                            wait(5),
                        ],
                    }
                },
                cooker(400),
            ],
        },
        "mode_off": {
            "alias": "Стенд: вимкнено за режимом",
            "icon": "mdi:power-sleep",
            "sequence": [reset, turn("turn_on", "tuya_socket"), wait(3), mode("Night")],
        },
        "mode_off_unresponsive": {
            "alias": "Стенд: вимкнено за режимом — розетка не відповідає",
            "icon": "mdi:power-plug-off-outline",
            "sequence": [
                reset,
                turn("turn_on", "tuya_socket"),
                wait(3),
                profile("tuya_socket", FaultProfile.UNRESPONSIVE),
                mode("Night"),
            ],
        },
        "mode_off_turned_on": {
            "alias": "Стенд: вимкнено за режимом — людина ввімкнула",
            "icon": "mdi:hand-back-right",
            "sequence": [
                reset,
                turn("turn_on", "tuya_socket"),
                wait(3),
                mode("Night"),
                wait(10),
                turn("turn_on", "tuya_socket"),
            ],
        },
        "mode_off_red": {
            "alias": "Стенд: вимкнено за режимом — червона зона",
            "icon": "mdi:alert-octagon",
            "sequence": [
                reset,
                mode("Night"),
                wait(3),
                turn("turn_on", "tuya_socket", "night_light", "boiler"),
                wait(3),
                grid_at(RED_W, 200),
            ],
        },
        "thermostats_dead_switch": {
            "alias": "Стенд: термостати — мертвий вимикач у ротації",
            "icon": "mdi:radiator-off",
            "sequence": [
                reset,
                # The IR of the first air conditioner does not reach it
                {"action": "switch.turn_on", "target": {"entity_id": ir_lost[0]}},
                *faulty_first(1),
            ],
        },
        "thermostats_hung_command": {
            "alias": "Стенд: термостати — команда, що не закінчується",
            "icon": "mdi:timer-sand-paused",
            "sequence": [
                reset,
                # The "on" of the third air conditioner never ends (the first
                # may be deferred by the dead switch scenario)
                {"action": "switch.turn_on", "target": {"entity_id": ir_hangs[2]}},
                *faulty_first(3),
            ],
        },
        "thermostats_no_temperature": {
            "alias": "Стенд: термостати — немає температури",
            "icon": "mdi:thermometer-off",
            "sequence": [
                reset,
                *faulty_first(2),
                wait(90),
                reading("thermo_2", ReadingProfile.NO_DATA),
            ],
        },
        "climate_zones": {
            "alias": "Стенд: клімат за зонами — старт (далі навантаження кнопками)",
            "icon": "mdi:radiator",
            "sequence": [reset, *heat("heat")],
        },
        "climate_rotation": {
            "alias": "Стенд: клімат — ротація, ємність 2 з трьох",
            "icon": "mdi:rotate-3d-variant",
            "sequence": [reset, *loads_wait, *heat("heat")],
        },
        "climate_fit": {
            "alias": "Стенд: клімат — вміщається лише один обігрівач",
            "icon": "mdi:scale-balance",
            # 2300 W: one heater fits under the red threshold, two do not
            "sequence": [reset, grid_at(2300, 0), *heat("heat")],
        },
        "climate_margin": {
            "alias": "Стенд: клімат — з'явився запас (2300 Вт з обігрівачем)",
            "icon": "mdi:scale-unbalanced",
            "sequence": [grid_at(2300, 0)],
        },
        "load_green": {
            "alias": "Стенд: навантаження — зелена",
            "icon": "mdi:circle-slice-2",
            "sequence": [grid_at(YELLOW_W, -900)],
        },
        "load_yellow": {
            "alias": "Стенд: навантаження — жовта",
            "icon": "mdi:circle-slice-5",
            "sequence": [grid_at(YELLOW_W, 200)],
        },
        "load_red": {
            "alias": "Стенд: навантаження — червона",
            "icon": "mdi:circle-slice-8",
            "sequence": [grid_at(RED_W, 200)],
        },
        "grid_no_data": {
            "alias": "Стенд: мережа без даних",
            "icon": "mdi:transmission-tower-off",
            "sequence": [reset, reading("grid", ReadingProfile.NO_DATA)],
        },
        "mode_after_restart": {
            "alias": "Стенд: режим після перезапуску — підготовка",
            "icon": "mdi:restart",
            "sequence": [
                reset,
                mode("Night"),
                wait(3),
                turn("turn_on", "tuya_socket"),
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
    scripts = scenario_scripts(reg)
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
