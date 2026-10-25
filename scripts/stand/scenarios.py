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
from .layout import MODE, RED_W, SCRIPT_PREFIX, STAND_DEVICES, YELLOW_W

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
    return {
        "reset": {
            "alias": "Стенд: скинути",
            "icon": "mdi:restore",
            "sequence": [
                *(profile(mock_id, FaultProfile.RELIABLE) for mock_id in all_sockets),
                *(reading(device, ReadingProfile.RELIABLE) for device in readings),
                *heat("off"),
                {
                    "action": "number.set_value",
                    "target": {"entity_id": capacity},
                    "data": {"value": 2},
                },
                mode("Grid on"),
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
                profile("floor_heating_1", FaultProfile.UNRESPONSIVE),
                *heat("heat"),
            ],
        },
        "thermostats_no_temperature": {
            "alias": "Стенд: термостати — немає температури",
            "icon": "mdi:thermometer-off",
            "sequence": [
                reset,
                *heat("heat"),
                wait(90),
                reading("thermo_2", ReadingProfile.NO_DATA),
            ],
        },
        "climate_zones": {
            "alias": "Стенд: клімат за зонами — старт (далі навантаження кнопками)",
            "icon": "mdi:radiator",
            "sequence": [reset, *heat("heat")],
        },
        "climate_fit": {
            "alias": "Стенд: клімат — вміщається лише один обігрівач",
            "icon": "mdi:scale-balance",
            # 2300 W: one heater fits under the red threshold, two do not
            "sequence": [reset, grid_at(2300, 0), *heat("heat")],
        },
        "climate_margin": {
            "alias": "Стенд: клімат — з'явився запас (навантаження 2300 Вт з обігрівачем)",
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
