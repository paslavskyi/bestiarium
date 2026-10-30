"""The scenario scripts of the stand: one button each on its dashboard."""

# A command-line script: it prints its progress. The stand dashboard and
# scripts are for the owner, in Ukrainian.
# ruff: noqa: T201, RUF001

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from custom_components.bestiarium_devices.const import LOAD_PRIORITY_ALWAYS_ON
from custom_components.bestiarium_mock.const import (
    HOME_POWER_MODES,
    HOME_POWER_NUMBERS,
    HOME_POWER_SWITCHES,
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
    AC_THERMOSTAT,
    MODE,
    RED_W,
    SCRIPT_PREFIX,
    STAND_DEVICES,
    STAND_HISENSE,
    THERMOSTATS,
    UNIVERSAL_THERMOSTATS,
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

    def grid_at(watts: int, delta: int) -> list[dict[str, Any]]:
        """
        Set the cooker so that the grid load is watts + delta.

        The other loads are the grid less what the cooker draws now (nothing
        while it is off). A power out of the cooker's range stops the script:
        after the climate scenarios the other loads alone may be above the
        target, and only a reset brings them down (review 39).
        """
        grid = reg.mock("sensor", "mock_grid_power")
        draws = reg.mock("sensor", "mock_cooker_power")
        knob = reg.mock("number", "mock_cooker_mock_power")
        cooker = (
            f"{{{{ {watts + delta} - states('{grid}') | float(0)"
            f" + states('{draws}') | float(0) }}}}"
        )
        out_of_range = (
            f"{{{{ cooker < state_attr('{knob}', 'min')"
            f" or cooker > state_attr('{knob}', 'max') }}}}"
        )
        return [
            {"variables": {"cooker": cooker}},
            {
                "if": [{"condition": "template", "value_template": out_of_range}],
                "then": [
                    {
                        "stop": "Інше навантаження не дає досягти цілі плитою: "
                        "спершу «Стенд: скинути»",
                        "error": True,
                    }
                ],
            },
            {
                "action": "number.set_value",
                "target": {"entity_id": knob},
                "data": {"value": "{{ cooker }}"},
            },
        ]

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
    universal = {name: reg.thermostat(name) for name, _, _ in UNIVERSAL_THERMOSTATS}
    every_thermostat = [
        e["entity_id"] for e in climate if e["entity_id"].startswith("climate.")
    ]
    # The scenarios before DEV-011 heat the virtual thermostats only: "heat"
    # is the manual mode of a universal one and of the air conditioner's
    thermostats = [
        t
        for t in every_thermostat
        if t not in (*universal.values(), reg.thermostat(AC_THERMOSTAT[0]))
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

    (heater, heater_room, _), (cloud, cloud_room, cloud_ac) = UNIVERSAL_THERMOSTATS

    def universal_mode(name: str, hvac_mode: str) -> list[dict[str, Any]]:
        """Put a universal thermostat in a mode, its target 21 °C."""
        return [
            {
                "action": "climate.set_temperature",
                "target": {"entity_id": universal[name]},
                "data": {"temperature": 21},
            },
            {
                "action": "climate.set_hvac_mode",
                "target": {"entity_id": universal[name]},
                "data": {"hvac_mode": hvac_mode},
            },
        ]

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
    # Every air conditioner the stand plays IR to: the cloud AC too
    air_conditioners = [
        *((name, ac) for name, _, ac in THERMOSTATS),
        (UNIVERSAL_THERMOSTATS[1][0], UNIVERSAL_THERMOSTATS[1][2]),
    ]

    # The stand's own loads that a person turns on (not always on, not run by
    # a thermostat): a fault scenario does not allow them, so the load stays
    # green and the faulty air conditioner gets its start
    loads = [
        reg.control(reg.mock_device(device.mock_id)["id"], "allowed")
        for device in STAND_DEVICES
        if device.priority != LOAD_PRIORITY_ALWAYS_ON and not device.thermostat_driven
    ]
    # The stand's loads not allowed (the reset allows them again)
    loads_wait = [{"action": "switch.turn_off", "target": {"entity_id": loads}}]
    # The power sockets of the air conditioners' virtual switches and of the
    # air conditioner thermostat: the stepwise "off" cuts one, the reset
    # allows them again
    ac_sockets = [
        reg.control(reg.mock_device(ac)["id"], "allowed")
        for ac in (*(ac for _, _, ac in THERMOSTATS), AC_THERMOSTAT[3])
    ]

    def room(thermometer: str, celsius: float) -> dict[str, Any]:
        return {
            "action": "number.set_value",
            "target": {
                "entity_id": reg.mock("number", f"mock_{thermometer}_mock_temperature")
            },
            "data": {"value": celsius},
        }

    def faulty_first(ac: int) -> list[dict[str, Any]]:
        """
        Make one air conditioner the first in the queue, the load green.

        Its room is the coldest (15 °C, the others 21 °C; all heat to 25 °C):
        the largest difference goes first, whatever the rooms were before.
        """
        return [
            *loads_wait,
            *(
                room(thermometer, 15 if number == ac else 21)
                for number, (_, thermometer, _) in enumerate(THERMOSTATS, start=1)
            ),
            *heat("heat"),
        ]

    ir_hangs = [reg.mock("switch", f"mock_{ac}_ir_hangs") for _, _, ac in THERMOSTATS]
    ac_powers = [
        reg.mock("number", f"mock_{ac}_mock_power") for _, _, ac in THERMOSTATS
    ]
    # The power knob of the first air conditioner whose switch is on, or ''
    # (SRC-142: by its switch, not by its knob)
    running_ac = (
        "".join(
            f"{{% {'elif' if n else 'if'} is_state('{reg.ir_switch(name)}', 'on') %}}"
            + reg.mock("number", f"mock_{ac}_mock_power")
            for n, (name, _, ac) in enumerate(THERMOSTATS)
        )
        + "{% endif %}"
    )
    irs = {
        f"ir_{ac}_{command}": {
            "alias": f"Стенд: ІЧ {name} — {label}",
            "icon": "mdi:remote",
            "sequence": ir(ac, watts),
        }
        for name, ac in air_conditioners
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
                # The thermostats first: no start in a cycle the reset itself runs
                {
                    "action": "climate.set_hvac_mode",
                    "target": {"entity_id": every_thermostat},
                    "data": {"hvac_mode": "off"},
                },
                *(profile(mock_id, FaultProfile.RELIABLE) for mock_id in all_sockets),
                *(reading(device, ReadingProfile.RELIABLE) for device in readings),
                *(ac_power(ac, AC_STANDBY_W) for _, ac in air_conditioners),
                {
                    "action": "switch.turn_off",
                    "target": {
                        "entity_id": [
                            reg.mock("switch", f"mock_{ac}_ir_{knob}")
                            for _, ac in air_conditioners
                            for knob in ("lost", "hangs")
                        ]
                    },
                },
                *_hisense_reset(reg),
                {
                    "action": "number.set_value",
                    "target": {"entity_id": capacity},
                    "data": {"value": 2},
                },
                mode("Grid on"),
                cooker(400),
                turn("turn_off", *(s for s in all_sockets if s not in always_on)),
                turn("turn_on", *always_on),
                # The loads a fault scenario did not allow, allowed again; the
                # rooms at 20 °C
                {"action": "switch.turn_on", "target": {"entity_id": loads}},
                {"action": "switch.turn_on", "target": {"entity_id": ac_sockets}},
                *(
                    room(thermometer, 20)
                    for _, thermometer, _ in (*THERMOSTATS, *UNIVERSAL_THERMOSTATS)
                ),
                *_ac_reset(reg),
                *_home_power(reg, HOME_POWER_MODES[0]),
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
                *grid_at(YELLOW_W, 100),
                wait(5),
                {
                    "repeat": {
                        "count": 12,
                        "sequence": [
                            *grid_at(YELLOW_W, -100),
                            wait(5),
                            *grid_at(YELLOW_W, 100),
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
                *grid_at(RED_W, 200),
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
        "climate_stuck_on": {
            "alias": "Стенд: клімат — кондиціонер не вимикається",
            "icon": "mdi:power-off",
            # AC 1 runs, then its IR is lost and its room warm: "off" each
            # minute three times, the event, a minute later its socket off
            "sequence": [
                reset,
                *faulty_first(1),
                {
                    "wait_template": (
                        f"{{{{ is_state('{ac_powers[0]}', '{float(AC_RUNNING_W)}') }}}}"
                    ),
                    "timeout": {"minutes": 3},
                },
                {"action": "switch.turn_on", "target": {"entity_id": ir_lost[0]}},
                room(THERMOSTATS[0][1], 30),
            ],
        },
        "climate_bypass_on_pause": {
            "alias": "Стенд: клімат — увімкнули в обхід паузи",
            "icon": "mdi:hand-back-right-off",
            # AC 1 "Not responding" (its IR lost), then a person turns it on
            # with the remote: it is turned off stepwise all the same. Only
            # AC 1 wants heat, capacity 1: no rotation takes it out. Stops if
            # it is not "Not responding" within 5 minutes
            "sequence": [
                reset,
                {"action": "switch.turn_on", "target": {"entity_id": ir_lost[0]}},
                {
                    "action": "number.set_value",
                    "target": {"entity_id": capacity},
                    "data": {"value": 1},
                },
                *faulty_first(1),
                *(room(thermometer, 30) for _, thermometer, _ in THERMOSTATS[1:]),
                {
                    "wait_template": (
                        f"{{{{ is_state('{reg.control_state(THERMOSTATS[0][0])}',"
                        " 'unresponsive') }}"
                    ),
                    "timeout": {"minutes": 5},
                    "continue_on_timeout": False,
                },
                ac_power(THERMOSTATS[0][2], AC_RUNNING_W),
            ],
        },
        "climate_red_entry": {
            "alias": "Стенд: клімат — вхід у червону зону",
            "icon": "mdi:alert-octagon-outline",
            # The thermostats off: the entry to red sends each air
            # conditioner one IR "off" (its power sensor may be late), once.
            # The cooker holds red for a minute: always on, the devices' red
            # strategy never turns it off
            "sequence": [reset, *grid_at(RED_W, 300), wait(60), cooker(400)],
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
            "sequence": [reset, *grid_at(2300, 0), *heat("heat")],
        },
        "climate_switch_glitch": {
            "alias": "Стенд: клімат — збій вимикача працюючого кондиціонера",
            "icon": "mdi:flash-alert",
            # In the zone as it is: the first running air conditioner draws
            # its standby for 15 s, its switch shows "off" for a moment
            "sequence": [
                {"variables": {"ac": running_ac}},
                {"condition": "template", "value_template": "{{ ac != '' }}"},
                {
                    "action": "number.set_value",
                    "target": {"entity_id": "{{ ac }}"},
                    "data": {"value": AC_STANDBY_W},
                },
                wait(15),
                {
                    "action": "number.set_value",
                    "target": {"entity_id": "{{ ac }}"},
                    "data": {"value": AC_RUNNING_W},
                },
            ],
        },
        "climate_margin": {
            "alias": "Стенд: клімат — з'явився запас (плита на мінімум)",
            "icon": "mdi:scale-unbalanced",
            # After climate_fit (load ~3500 W with one AC): the cooker down to
            # its minimum frees what it drew above 400 W, so the second AC
            # fits under the red threshold
            "sequence": [cooker(400)],
        },
        "load_green": {
            "alias": "Стенд: навантаження — зелена",
            "icon": "mdi:circle-slice-2",
            "sequence": grid_at(YELLOW_W, -900),
        },
        "load_yellow": {
            "alias": "Стенд: навантаження — жовта",
            "icon": "mdi:circle-slice-5",
            "sequence": grid_at(YELLOW_W, 200),
        },
        "load_red": {
            "alias": "Стенд: навантаження — червона",
            "icon": "mdi:circle-slice-8",
            "sequence": grid_at(RED_W, 200),
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
        "universal_auto": {
            "alias": "Стенд: універсальний — «авто» гріє й вимикає",
            "icon": "mdi:thermostat-auto",
            # Scenario 1: auto heats the cold room, then stops when it is warm
            "sequence": [
                reset,
                room(heater_room, 18),
                *universal_mode(heater, "auto"),
                wait(120),
                room(heater_room, 22),
            ],
        },
        "universal_manual": {
            "alias": "Стенд: універсальний — ручний поза ємністю й жовтою",
            "icon": "mdi:hand-back-left",
            # Scenario 1: capacity 0, then yellow: the manual heater starts
            # and stops by itself; then off
            "sequence": [
                reset,
                {
                    "action": "number.set_value",
                    "target": {"entity_id": capacity},
                    "data": {"value": 0},
                },
                room(heater_room, 18),
                *universal_mode(heater, "heat"),
                wait(60),
                *grid_at(YELLOW_W, 200),
                wait(60),
                room(heater_room, 22),
                wait(60),
                {
                    "action": "climate.set_hvac_mode",
                    "target": {"entity_id": universal[heater]},
                    "data": {"hvac_mode": "off"},
                },
                cooker(400),
            ],
        },
        "manual_red": {
            "alias": "Стенд: ручний — червона зона вимикає першим",
            "icon": "mdi:alert-octagon",
            # Scenario 2: the manual heater runs; red turns its thermostat
            # off at once (the event zone_entered); it stays off after red
            "sequence": [
                reset,
                room(heater_room, 18),
                *universal_mode(heater, "heat"),
                wait(30),
                *grid_at(RED_W, 300),
                wait(60),
                cooker(400),
            ],
        },
        "manual_on_in_red": {
            "alias": "Стенд: ручний — увімкнули в червоній",
            "icon": "mdi:alert-octagon-outline",
            # Scenario 2: in red, heat goes off at once (the event turned_on)
            "sequence": [
                reset,
                *grid_at(RED_W, 300),
                wait(10),
                room(heater_room, 18),
                *universal_mode(heater, "heat"),
                wait(30),
                cooker(400),
            ],
        },
        "climate_coexistence": {
            "alias": "Стенд: старі й універсальний ділять ємність",
            "icon": "mdi:account-group",
            # Scenario 9: AC 1, AC 2 and the heater want heat, capacity 2:
            # two run; the rotation lets the one waiting in
            "sequence": [
                reset,
                *loads_wait,
                *(room(thermometer, 18) for _, thermometer, _ in THERMOSTATS[:2]),
                room(heater_room, 18),
                {
                    "action": "climate.set_temperature",
                    "target": {"entity_id": thermostats[:2]},
                    "data": {"temperature": 25},
                },
                {
                    "action": "climate.set_hvac_mode",
                    "target": {"entity_id": thermostats[:2]},
                    "data": {"hvac_mode": "heat"},
                },
                *universal_mode(heater, "auto"),
            ],
        },
        "cloud_ac_universal": {
            "alias": "Стенд: хмарний кондиціонер на універсальному",
            "icon": "mdi:cloud-outline",
            # Scenario 10: auto turns the template switch on (IR as the
            # scenes), its power confirms it; warm, it goes off. Again on,
            # then its IR lost: "off" three times, the event, no socket cut
            "sequence": [
                reset,
                room(cloud_room, 18),
                *universal_mode(cloud, "auto"),
                wait(120),
                room(cloud_room, 25),
                wait(90),
                room(cloud_room, 18),
                {
                    "wait_template": (
                        "{{ is_state('"
                        + reg.mock("number", f"mock_{cloud_ac}_mock_power")
                        + f"', '{float(AC_RUNNING_W)}') }}}}"
                    ),
                    "timeout": {"minutes": 3},
                    "continue_on_timeout": False,
                },
                {
                    "action": "switch.turn_on",
                    "target": {
                        "entity_id": reg.mock("switch", f"mock_{cloud_ac}_ir_lost")
                    },
                },
                room(cloud_room, 25),
            ],
        },
        **_hisense_scenarios(reg, reset),
        **_ac_scenarios(reg, reset),
        **_power_scenarios(reg, reset),
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


def _hisense_mode(climate: str, hvac_mode: str, **data: Any) -> dict[str, Any]:
    """Put the stand's Hisense in a mode, with a target when one is given."""
    return {
        "action": "climate.set_temperature" if data else "climate.set_hvac_mode",
        "target": {"entity_id": climate},
        "data": {**data, "hvac_mode": hvac_mode},
    }


def _hisense_reset(reg: Registry) -> list[dict[str, Any]]:
    """
    Return the reset's steps of the stand's Hisense (DEV-012).

    The display on while the unit may still run (a display frame then; of a
    unit that is off, assumed only: DEV-012 review, U3), so the stand's
    display and the one HA assumes agree; then off; its knobs back.
    """
    return [
        step
        for name, mock_id in STAND_HISENSE
        for step in (
            {
                "action": "switch.turn_on",
                "target": {"entity_id": reg.ir_entity(name, "switch")},
            },
            _hisense_mode(reg.ir_entity(name, "climate"), "off"),
            {
                "action": "switch.turn_off",
                "target": {
                    "entity_id": [
                        reg.mock("switch", f"mock_{mock_id}_{knob}")
                        for knob in ("ir_lost", "ignores_off")
                    ]
                },
            },
        )
    ]


def _hisense_scenarios(
    reg: Registry, reset: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """
    Return the scenarios of the stand's Hisense (DEV-012).

    Scenario 5: each manual mode gives its own frame and power, once.
    Scenario 6: with the display off a frame goes twice (the first only
    lights it); "off" goes once.
    """
    name, _ = STAND_HISENSE[0]
    climate = reg.ir_entity(name, "climate")
    display = reg.ir_entity(name, "switch")

    def mode(hvac_mode: str, **data: Any) -> dict[str, Any]:
        return _hisense_mode(climate, hvac_mode, **data)

    def call(service: str, **data: Any) -> dict[str, Any]:
        return {
            "action": f"climate.{service}",
            "target": {"entity_id": climate},
            "data": data,
        }

    # Long enough to see each frame's state and power on the dashboard
    step = {"delay": {"seconds": 10}}
    return {
        "hisense_manual_modes": {
            "alias": "Стенд: Hisense — ручні режими",
            "icon": "mdi:air-conditioner",
            "sequence": [
                reset,
                mode("heat", temperature=24),
                step,
                mode("cool", temperature=22),
                step,
                mode("dry"),
                step,
                mode("fan_only"),
                step,
                call("set_fan_mode", fan_mode="high"),
                step,
                mode("off"),
            ],
        },
        "hisense_display": {
            "alias": "Стенд: Hisense — екран",
            "icon": "mdi:monitor-off",
            "sequence": [
                reset,
                mode("heat", temperature=24),
                step,
                {"action": "switch.turn_off", "target": {"entity_id": display}},
                step,
                call("set_temperature", temperature=26),
                step,
                mode("off"),
            ],
        },
    }


def _auto_mode(reg: Registry) -> str:
    """Return the select "Auto mode" of the stand's air conditioner thermostat."""
    return next(
        e["entity_id"]
        for e in reg.stand_climate()
        if e["entity_id"].startswith("select.")
    )


def _ac_knob(reg: Registry, domain: str, key: str) -> str:
    """Return a knob of the air conditioner under the thermostat."""
    mock_id = dict(STAND_HISENSE)[AC_THERMOSTAT[2]]
    return reg.mock(domain, f"mock_{mock_id}_{key}")


def _ac_reset(reg: Registry) -> list[dict[str, Any]]:
    """
    Return the reset's steps of the air conditioner thermostat (DEV-013).

    Its thermostat is off with the others; here auto heats again, its
    unit's sensor reads the room, its room is at 20 °C.
    """
    return [
        {
            "action": "select.select_option",
            "target": {"entity_id": _auto_mode(reg)},
            "data": {"option": "heating"},
        },
        {
            "action": "number.set_value",
            "target": {"entity_id": _ac_knob(reg, "number", "sensor_offset")},
            "data": {"value": 0},
        },
        {
            "action": "number.set_value",
            "target": {
                "entity_id": reg.mock(
                    "number", f"mock_{AC_THERMOSTAT[1]}_mock_temperature"
                )
            },
            "data": {"value": 20},
        },
    ]


def _ac_scenarios(reg: Registry, reset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    Return the scenarios of the air conditioner thermostat (DEV-013).

    Scenario 3: auto heats to the target plus the margin and the room
    follows; idling at the setpoint holds the place. 4: auto cools. 7: the
    stepwise "off" with its socket; on past an "off" thermostat. 8: the
    remote and the card past the thermostat; manual leaves them alone.
    """
    name, thermometer, hisense, _ = AC_THERMOSTAT
    thermostat = reg.thermostat(name)
    unit = reg.ir_entity(hisense, "climate")

    def room(celsius: float) -> dict[str, Any]:
        return {
            "action": "number.set_value",
            "target": {
                "entity_id": reg.mock("number", f"mock_{thermometer}_mock_temperature")
            },
            "data": {"value": celsius},
        }

    def mode(hvac_mode: str, target: float = 21) -> list[dict[str, Any]]:
        return [
            {
                "action": "climate.set_temperature",
                "target": {"entity_id": thermostat},
                "data": {"temperature": target},
            },
            {
                "action": "climate.set_hvac_mode",
                "target": {"entity_id": thermostat},
                "data": {"hvac_mode": hvac_mode},
            },
        ]

    def knob(service: str, domain: str, key: str, **data: Any) -> dict[str, Any]:
        return {
            "action": service,
            "target": {"entity_id": _ac_knob(reg, domain, key)},
            **({"data": data} if data else {}),
        }

    def wait(seconds: int) -> dict[str, Any]:
        return {"delay": {"seconds": seconds}}

    # Started and confirmed by power: the start's step, the power's delay
    started = wait(60)
    return {
        "ac_auto": {
            "alias": "Стенд: кондиціонер — авто гріє",
            "icon": "mdi:air-conditioner",
            "sequence": [reset, room(18), *mode("auto")],
        },
        "ac_idle": {
            "alias": "Стенд: кондиціонер — простій на уставці",
            "icon": "mdi:power-sleep",
            "sequence": [
                reset,
                knob("number.set_value", "number", "sensor_offset", value=2),
                room(18),
                *mode("auto"),
                # The room reaches 20 °C in about 3.5 minutes, then it idles
                # for 3 minutes holding its place
                wait(420),
                room(21),
            ],
        },
        "ac_auto_cool": {
            "alias": "Стенд: кондиціонер — авто охолоджує",
            "icon": "mdi:snowflake",
            "sequence": [
                reset,
                {
                    "action": "select.select_option",
                    "target": {"entity_id": _auto_mode(reg)},
                    "data": {"option": "cooling"},
                },
                room(26),
                *mode("auto", 23),
            ],
        },
        "ac_stepwise": {
            "alias": "Стенд: кондиціонер — ступінчасте вимкнення",
            "icon": "mdi:power-plug-off",
            "sequence": [
                reset,
                room(18),
                *mode("auto"),
                started,
                knob("switch.turn_on", "switch", "ignores_off"),
                room(23),
            ],
        },
        "ac_off_against": {
            "alias": "Стенд: кондиціонер — увімкнули повз «вимкнено»",
            "icon": "mdi:account-alert",
            "sequence": [
                reset,
                _hisense_mode(unit, "heat", temperature=24),
            ],
        },
        "ac_remote": {
            "alias": "Стенд: кондиціонер — пульт і картка повз термостат",
            "icon": "mdi:remote",
            "sequence": [
                reset,
                room(18),
                *mode("auto"),
                started,
                knob("button.press", "button", "remote_off"),
                # Off by another after a step (a minute), then the pause
                wait(180),
                _hisense_mode(unit, "cool", temperature=20),
            ],
        },
        "ac_manual_alone": {
            "alias": "Стенд: кондиціонер — ручний не втручається",
            "icon": "mdi:hand-back-right",
            "sequence": [
                reset,
                *mode("heat"),
                wait(30),
                _hisense_mode(unit, "cool", temperature=20),
            ],
        },
    }


def _home_power(
    reg: Registry, mode: str, *, off: tuple[str, ...] = (), **numbers: float
) -> list[dict[str, Any]]:
    """
    Set the stand's home power: its mode, relays and numbers (DEV-014).

    Every switch on but those in off; every number its default but those given.
    """
    values = {number.key: number.default for number in HOME_POWER_NUMBERS} | numbers

    def switches(service: str, keys: list[str]) -> list[dict[str, Any]]:
        if not keys:
            return []
        entity_ids = [reg.mock("switch", f"mock_home_power_{key}") for key in keys]
        return [{"action": f"switch.{service}", "target": {"entity_id": entity_ids}}]

    return [
        {
            "action": "select.select_option",
            "target": {"entity_id": reg.mock("select", "mock_home_power_mode")},
            "data": {"option": mode},
        },
        *switches("turn_on", [key for key in HOME_POWER_SWITCHES if key not in off]),
        *switches("turn_off", list(off)),
        *(
            {
                "action": "number.set_value",
                "target": {"entity_id": reg.mock("number", f"mock_home_power_{key}")},
                "data": {"value": value},
            }
            for key, value in values.items()
        ),
    ]


def _power_scenarios(reg: Registry, reset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    Return the scenarios of the power flow card (DEV-014): one per criterion.

    The home's power on the stand is the stand's grid (its sockets, about
    0.5 kW after the reset); the rest is the home power's knobs.
    """
    normal, powerful, solar, autonomous = HOME_POWER_MODES

    def scenario(alias: str, *steps: dict[str, Any]) -> dict[str, Any]:
        return {
            "alias": f"Стенд: живлення — {alias}",
            "icon": "mdi:transmission-tower",
            "sequence": [reset, *steps],
        }

    return {
        "power_normal": scenario(
            "звичайний, надлишок сонця, батарея стоїть",
            *_home_power(reg, normal, grid_power=1000, sun_power=1.8),
        ),
        "power_powerful": scenario(
            "потужний",
            *_home_power(reg, powerful, off=("inverter_home",), grid_power=1000),
        ),
        "power_solar_charging": scenario(
            "потужний + зарядка від сонця",
            *_home_power(
                reg,
                solar,
                off=("inverter_home", "grid_inverter"),
                grid_power=500,
                sun_power=1.8,
                battery_flow=-0.6,
            ),
        ),
        "power_autonomous": scenario(
            "автономний, без сонця, батарея віддає",
            *_home_power(reg, autonomous, off=("grid_inverter",), battery_flow=0.9),
        ),
        "power_outage": scenario(
            "світла немає",
            *_home_power(
                reg, powerful, off=("inverter_home", "grid_present"), battery_flow=1.1
            ),
        ),
        # The review's exception (DEV-014-03, 1): the sensor sees a discharge
        # only once the charge falls; the card shows it at once
        "power_outage_sensor_lags": scenario(
            "автономний, світла немає, датчик батареї ще 0",
            *_home_power(reg, autonomous, off=("grid_inverter", "grid_present")),
        ),
        "power_no_data": scenario(
            "немає даних про споживання дому",
            *_home_power(reg, normal, grid_power=1000, sun_power=1.8),
            {
                "action": "select.select_option",
                "target": {
                    "entity_id": reg.mock(
                        "select", f"mock_{MOCK_GRID.device_id}_reading_profile"
                    )
                },
                "data": {"option": ReadingProfile.NO_DATA.value},
            },
        ),
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
