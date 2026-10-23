# bestiarium_mock — the Bestiarium test stand

## Overview

`bestiarium_mock` (domain `bestiarium_mock`) simulates smart-home devices for
testing the Bestiarium integrations without hardware. It has no dependencies.

1. **12 mock smart sockets**, one per test scenario. Each socket has a
   **fault model** that makes it misbehave like a real cloud socket (ignored
   commands, slow replies, a stale state in HA, short dropouts), chosen with
   a **fault profile**.
2. **4 mock thermometers** with a temperature set by hand.
3. **A grid**: the sum of what the loads draw plus 40–100 W of background,
   and an overload alarm after 10 s above 5000 W.
4. **Singleton**: one config entry (unique ID `bestiarium_mock`).

The stand is set up in the development HA with `make stand`
([`scripts/stand_setup.py`](../scripts/stand_setup.py)), see
[The stand](#the-stand-make-stand).

---

## File Structure

```
custom_components/bestiarium_mock/
├── __init__.py          # Entry setup/unload, migration 1 → 2
├── const.py             # Device set, fault profiles and parameters, grid constants
├── faults.py            # MockSocket — physical and reported state, the fault model
├── coordinator.py       # MockDevicesCoordinator — sockets, thermometers, grid
├── entity.py            # MockSocketEntity — entities of a socket follow it
├── switch.py            # MockDeviceSwitch — the socket's switch
├── sensor.py            # Socket power, grid power, thermometer temperature
├── binary_sensor.py     # Socket "State" (draws power), grid overload alarm
├── number.py            # Fault parameters, cooker power, thermometer temperature
├── select.py            # Fault profile
├── config_flow.py       # Confirmation form listing the device set (VERSION 2)
└── translations/        # en, uk

tests/custom_components/bestiarium_mock/
├── conftest.py          # `stand` fixture (seeded generators), `advance` (clock)
├── test_config_flow.py
├── test_entities.py
├── test_coordinator.py  # grid and alarm
├── test_faults.py       # the fault model
├── test_profiles.py     # profiles, custom parameters, restore
└── test_migration.py

tests/stand/test_stand_setup.py   # the stand script against a test HA
```

---

## The device set

Device names are translated (`device` in the translations, `translation_key`
= `device_id`): English below, Ukrainian on a Ukrainian HA ("Нічник",
"Бойлер", "Розетка за комодом (SmartThings)", …).

| device_id | Name | Power | Default profile | Scenario |
|---|---|---|---|---|
| `night_light` | Mock Night Light | 15 W | Reliable | control: how it should work |
| `boiler` | Mock Boiler | 2000 W | Reliable | headroom (DEV-006), red zone |
| `slow_cloud_socket` | Mock Slow Cloud Socket | 800 W | Slow cloud | adaptive response time |
| `tuya_socket` | Mock Tuya Socket | 300 W | Unstable | tolerance, unstable connection |
| `commode_socket` | Mock Commode Socket (SmartThings) | 8 W | Stuck state | the night of 2026-10-23, with the next one |
| `bedside_socket` | Mock Bedside Socket | 8 W | Reliable | same priority as the commode socket: does its turn come? |
| `dead_socket` | Mock Dead Socket | 500 W | Unresponsive | "not responding", deferral |
| `cooker` | Mock Cooker | 400–4200 W | Reliable | pushes the load to yellow, red, overload |
| `fridge` | Mock Fridge | 100 W | Reliable | "always on" priority in modes |
| `floor_heating_1..3` | Mock Floor Heating 1–3 | 1200 W | Reliable | Bestiarium Climate thermostats (DEV-008) |
| `thermo_1..4` | Mock Thermometer 1–4 | — | — | thermostat inputs |

---

## The fault model (`faults.py`)

A `MockSocket` has two states:

- **physical** — whether the load is powered; the power sensor, the "State"
  binary sensor and the grid follow it;
- **reported** — what its switch shows in HA.

They are equal unless the fault model separates them.

| Parameter (`FaultParams`) | Range | Effect |
|---|---|---|
| `ignore_percent` | 0–100 % | the command is ignored |
| `delay_min_s`, `delay_max_s` | 0–120 s | the command takes effect after a random delay from the range (the latest command wins) |
| `stuck_percent` | 0–100 % | the load switches but HA keeps the old state, until a later command goes through without sticking (like SmartThings until its next poll) |
| `dropouts_per_hour` | 0–60 | on average this many dropouts an hour (exponential gaps) |
| `dropout_min_s`, `dropout_max_s` | 1–600 s | a dropout makes the switch **and** the power sensor `unavailable` for a random time; the load keeps its state |

Each socket has its own `random.Random` (`socket.rng`); the grid noise has the
coordinator's (`coordinator.rng`). Tests seed them (the `stand` fixture) and
move the clock with `async_fire_time_changed`, so every run is the same.

### Fault profiles (`FaultProfile`)

One `select` per socket ("Fault profile", `config`). Every profile but
**Custom** sets all parameters (`PROFILE_PARAMS`):

| Profile | Ignore | Delay | Stuck | Dropouts |
|---|---|---|---|---|
| Reliable | 0 % | 0–1 s | 0 % | none |
| Slow cloud | 0 % | 4–15 s | 0 % | none |
| Unstable | 10 % | 1–5 s | 0 % | 6/h, 5–40 s |
| Stuck state | 0 % | 1–3 s | 100 % | none |
| Unresponsive | 100 % | — | — | none |
| Custom | the values shown when it was chosen, then edited | | | |

The parameter `number` entities (`config`) always show the parameters in
effect. They can be changed only in **Custom**; otherwise the service call
fails with the translated `custom_profile_required` error. The profile and the
parameters survive a restart (`RestoreEntity` / `RestoreNumber`).

---

## Entities

Per socket (device `mock_<device_id>`, model "Mock Smart Socket"):

| Entity | Unique ID | Category | Notes |
|---|---|---|---|
| `switch` "Switch" | `mock_<id>` | — | shows the reported state; commands go to the fault model; unavailable during a dropout |
| `sensor` "Power" | `mock_<id>_power` | — | what the load draws; unavailable during a dropout |
| `binary_sensor` "State" | `mock_<id>_state` | — | whether the load draws power, whatever the switch shows |
| `select` "Fault profile" | `mock_<id>_fault_profile` | config | |
| 7 × `number` fault parameters | `mock_<id>_<parameter>` | config | editable in Custom only |
| `number` "Mock Power" | `mock_cooker_mock_power` | config | cooker only, 400–4200 W |

Per thermometer: `sensor` "Temperature" (`mock_<id>_temperature`) and `number`
"Set Temperature" (`mock_<id>_mock_temperature`, `config`).

Grid: `sensor` "Grid Power" (`mock_grid_power`) and `binary_sensor` "Grid
Overload Alarm" (`mock_grid_alarm`).

Test knobs are `config` entities (see
[settings vs entities](concepts/settings-vs-entities.md)); Bestiarium Devices
never offers them as a device's relay or power sensor.

---

## Migration (`async_migrate_entry`, version 1 → 2)

The DEV-007 device set replaced the old one. The migration removes the
devices (with their entities) that are no longer in the set — Mock AC, Kettle,
Router, Heating, Washing Machine, Dishwasher, Socket 1–4 — and the "Mock
Enabled" switches of the kept ones (the fault profile replaced them).
Unique IDs of the kept devices do not change.

---

## The stand (`make stand`)

`scripts/stand_setup.py` sets up the stand in the development HA through its
REST and websocket APIs, using the real config flows. It needs a long-lived
access token of that HA in `HA_TEST_ENV_TOKEN` and refuses any HA that is not
on this machine. The token is a Windows user environment variable;
`.devcontainer.json` passes it into the devcontainer (`remoteEnv`), so VS Code
must be restarted once after the variable is set. The script never prints it.
Running it again gives the same stand. The scenario scripts load only because
`config/configuration.yaml` has `script: !include scripts.yaml` (`make
develop` creates an empty `scripts.yaml` and `automations.yaml` if missing);
the script fails if HA did not load them.

1. Adds Bestiarium Mock and Bestiarium Mode (modes "Grid on" and "Night",
   limit 5000 W on the mock grid, yellow 75 %, red 90 %) if they are missing.
   An existing Bestiarium Mode is kept as it is. Every test device gets back
   its own name (no `name_by_user`, no area) and every test entity the
   entity_id `<domain>.<unique_id>` (for example `sensor.mock_boiler_power`,
   `select.mock_boiler_fault_profile`), whatever HA generated from old names
   or areas. Automations that no longer load (unavailable, such as those of
   old test devices) are removed.
2. Removes the Bestiarium Devices entries of test devices (and those whose
   device is gone) and adds one per socket:

   | Socket | Priority | Overload first queue | Thermostat-driven |
   |---|---|---|---|
   | commode, bedside | 5200 (commode added first) | | |
   | boiler | 5000 | yes | |
   | slow cloud | 4000 | | |
   | Tuya | 3500 | | |
   | night light | 3000 | | |
   | dead | 1000 | | |
   | cooker, fridge | 9999 (always on) | | |
   | floor heating 1–3 | 2000 | yes | yes |

3. Replaces Bestiarium Climate with one entry (capacity 2) and thermostats
   "Stand floor 1–3" on thermometers 1–3 and floor heating 1–3.
4. Writes the scenario scripts `script.bestiarium_stand_*`:

   | Script | What it sets up |
   |---|---|
   | `reset` | every profile Reliable, cooker at 400 W; every socket off except the "always on" cooker and fridge, which stay on (the balancer would turn them on first anyway) |
   | `night_case` | reset; the commode socket "Stuck state" and switched on (HA still shows off); green zone |
   | `red_zone_dead_socket` | reset; dead socket, night light and slow cloud socket on; then the dead socket stops answering, the cooker at 3100 W pushes the grid into the red zone |
   | `slow_cloud` | reset; commode, bedside and boiler on; the slow cloud socket off with profile "Slow cloud"; green zone |
   | `unstable_tuya` | reset; the Tuya socket "Unstable" and on |

5. Writes the dashboard "Стенд Bestiarium" (`/bestiarium-stand`): grid, mode
   and zones; the scenario buttons; a section per socket (switch, power,
   profile; the heading and the switch tile open the device page with its
   knobs); a logbook of the sockets, zones and mode.

The scenarios assume the stand's Bestiarium Mode thresholds above; with other
limits the cooker power in `red_zone_dead_socket` must change.
