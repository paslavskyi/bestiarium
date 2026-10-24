"""The scenario scripts of the stand: one button each on its dashboard."""

# A command-line script: it prints its progress. The stand dashboard and
# scripts are for the owner, in Ukrainian.
# ruff: noqa: T201

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from custom_components.bestiarium_devices.const import LOAD_PRIORITY_ALWAYS_ON
from custom_components.bestiarium_mock.const import MOCK_DEVICES, FaultProfile

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
