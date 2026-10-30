# bestiarium_mock — the Bestiarium test stand

## Overview

`bestiarium_mock` (domain `bestiarium_mock`) simulates smart-home devices for
testing the Bestiarium integrations without hardware. It depends on
`infrared` (its IR emitter) and `bestiarium_ir` (the Kelon168 module its air
conditioner recognises frames with).

1. **19 mock smart sockets**, one per test scenario. Each socket has a
   **fault model** that makes it misbehave like a real cloud socket (ignored
   commands, slow replies, a stale state in HA, short dropouts), chosen with
   a **fault profile**.
2. **5 mock thermometers** with a temperature set by hand.
3. **A grid**: the sum of what the loads draw plus 40–100 W of background,
   (the overload alarm was removed in DEV-008 stage B: nobody read it, the
   overload zone tells it).
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
├── ir_ac.py             # MockIrAc — the stand's Hisense: hears its emitter, draws by its state, warms its room
├── button.py            # "Remote: off" of each IR air conditioner (DEV-013)
├── infrared.py          # MockIrEmitter — the emitter of the IR air conditioner
├── coordinator.py       # MockDevicesCoordinator — sockets, thermometers, grid
├── entity.py            # MockSocketEntity, MockIrAcEntity — entities follow their device
├── switch.py            # MockDeviceSwitch — the socket's switch
├── sensor.py            # Socket power, grid power, thermometer temperature
├── binary_sensor.py     # Socket "State" (draws power, diagnostic)
├── readings.py          # MockReading — the fault model of a thermometer or the grid
├── number.py            # Fault parameters, cooker power, thermometer temperature
├── select.py            # Fault profile
├── config_flow.py       # Confirmation form counting the device set (VERSION 2)
└── translations/        # en, uk

tests/custom_components/bestiarium_mock/
├── conftest.py          # `stand` fixture (seeded generators), `advance` (clock)
├── test_config_flow.py
├── test_entities.py
├── test_coordinator.py  # grid
├── test_faults.py       # the fault model
├── test_profiles.py     # profiles, custom parameters, restore
├── test_ir_ac.py        # the IR air conditioner and its emitter
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
| `cooker` | Mock Cooker | 400–5500 W | Reliable | pushes the load to yellow, red, overload |
| `fridge` | Mock Fridge | 100 W | Reliable | "always on" priority in modes |
| `floor_heating_1..3` | Mock Floor Heating 1–3 | 1200 W | Reliable | Bestiarium Devices only, "turned on by a thermostat" (review 18: one relay, one integration) |
| `matter_socket` | Matter socket (free) | 2000 W | Reliable | no Bestiarium device: the boiler is re-pointed to it (DEV-008) |
| `heater` | Mock Heater | 1500 W | Reliable | no Bestiarium device: the switch of the universal thermostat "Stand heater" (DEV-011), read by its power |
| `ac_1..4` | Mock AC 1–4 Power | 5–1200 W | Reliable | the power of an air conditioner: an "always on" device in the first queue; the stand's IR scripts set 1200 W (running) or 5 W (standby); the AC's virtual switch of Bestiarium Climate reads this socket's power. its knob "IR lost" (`switch.mock_ac_N_ir_lost`, config) is an IR that does not reach the AC, the socket still working; its knob "IR hangs" (`switch.mock_ac_N_ir_hangs`, config) is an IR "on" that never ends while the knob is on (review 22, D3); the fault profile is the socket's (the power relay's) own (review 20, C7). `ac_4` is the cloud air conditioner of the universal thermostat "Stand cloud AC" (DEV-011): its template switch runs `ac_4`'s IR scripts as the home's Tuya scenes |
| `hisense_power` | Mock Hisense Power | 5–1200 W | Reliable | the power of the stand's Hisense (DEV-012): what it draws is set by the air conditioner `hisense` by its state; an "always on" device in the first queue, as `ac_1..4` |
| `hisense_2_power` | Mock Hisense 2 Power | 5–1200 W | Reliable | the power of the second Hisense (DEV-013), the air conditioner thermostat's; as `hisense_power` |
| `thermo_1..6` | Mock Thermometer 1–6 | — | — | thermostat inputs; `thermo_6` is the room of `hisense_2`, which warms or cools it |

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
| `delay_min_s`, `delay_max_s` | 0–120 s | the command takes effect after a random delay from the range; a repeat of the command on its way changes nothing (the device is already carrying it out), the opposite command replaces it |
| `stuck_percent` | 0–100 % | the load switches but HA keeps the old state, until a later command goes through without sticking (like SmartThings until its next poll) |
| `dropouts_per_hour` | 0–60 | on average this many dropouts an hour (exponential gaps) |
| `dropout_min_s`, `dropout_max_s` | 1–600 s | a dropout makes the switch **and** the power sensor `unavailable` for a random time; the load keeps its state, and the "State" binary sensor keeps showing it |

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
| `binary_sensor` "State" | `mock_<id>_state` | diagnostic | whether the load draws power, whatever the switch shows; stays available during a dropout (the tester's view of the truth) |
| `select` "Fault profile" | `mock_<id>_fault_profile` | config | |
| 7 × `number` fault parameters | `mock_<id>_<parameter>` | config | editable in Custom only |
| `number` "Mock Power" | `mock_cooker_mock_power` | config | cooker only, 400–5500 W |

Per thermometer: `sensor` "Temperature" (`mock_<id>_temperature`), `number`
"Set Temperature" (`mock_<id>_mock_temperature`, `config`) and `select` "Fault
profile" (`mock_<id>_reading_profile`, `config`).

Grid: `sensor` "Grid Power" (`mock_grid_power`) and `select` "Fault profile"
(`mock_grid_reading_profile`, `config`).

### The IR air conditioners (DEV-012, DEV-013)

The device `mock_hisense` ("Mock Hisense", model "Mock IR Air Conditioner") is
a Hisense behind the socket `hisense_power`; `mock_hisense_2` ("Mock Hisense
2") the same behind `hisense_2_power`, with a room. Each hears its emitter and
recognises each frame with `bestiarium_ir/kelon168.py`, as the real unit:

- a frame sets the whole state (mode, target, fan, on/off);
- while the display is off, a frame other than "off" only lights it;
- the display button (a light frame) switches the display off;
- "off" works with the display off;
- the socket cut: the unit is off and hears nothing; the power back: still off.

It draws by its state: heat, cool, auto 1200 W, dry 300 W, fan only 40 W,
off 5 W.

**Its room** (DEV-013; `hisense_2` and `thermo_6`): every 10 s, heating or
cooling, it moves the room 0.1 °C (0.6 °C a minute) until its own sensor —
the room plus the knob "Sensor offset" — reaches its setpoint; then it
**idles** at 40 W and the room stays. The room's temperature is set as by
a tester (its number), so a scenario may set it too. `hisense` has no room.

| Entity | Unique ID | Category | Notes |
|---|---|---|---|
| `infrared` "IR emitter" | `mock_hisense_emitter` | — | the stand's emitter; its state is the time of the last signal (each frame in the logbook) |
| `sensor` "Air conditioner state" | `mock_hisense_ac_state` | diagnostic | off, heat, cool, auto, dry, fan only; attributes `temperature`, `fan`, `display`; restored |
| `sensor` "Last frame" | `mock_hisense_last_frame` | diagnostic | carried out, only lit the display, off ignored, not recognised; attributes `action` (the button), `power`, `mode`, `temperature`, `fan` |
| `switch` "IR lost" | `mock_hisense_ir_lost` | config | no frame reaches the unit |
| `switch` "Ignores off" | `mock_hisense_ignores_off` | config | the unit runs on after "off" |
| `number` "Sensor offset" | `mock_hisense_sensor_offset` | config | −5…5 °C, default 0: how much warmer than the room its own sensor reads (it idles sooner); restored |
| `button` "Remote: off" | `mock_hisense_remote_off` | config | the unit turned off with its remote: no frame, HA's assumed state stays as it was (scenario 8) |

The entities of `mock_hisense_2` are the same, `mock_hisense_2_…`.


The reading profiles of the thermometers and the grid (`readings.py`,
DR-c621b3b8): **Reliable**; **Unstable** — short dropouts (6 an hour, 10–40 s,
unavailable meanwhile); **No data** — unavailable until the profile changes;
**Frozen** — the value no longer changes. Each reading has its own seeded
generator.

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

The entities other integrations placed on a removed device go with it: the
"Allowed" switch and power entities of a Bestiarium Devices entry on Mock AC,
for example. Such an entry stays, without entities, until `make stand`
removes it (it is named after a retired test socket) and adds the new set.

---

## The stand (`make stand`)

`scripts/stand_setup.py` (with its modules in `scripts/stand/`: `ha` the client
of the HA, `layout` what the stand is made of, `entries`, `scenarios`,
`dashboard`) sets up the stand in the development HA through its
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
   An existing Bestiarium Mode keeps its modes and limits and gets the stand's
   modes it lacks ("Night" for the scenarios); its power sensor is
   set (reconfigure flow) to the test grid's current entity_id. Every test
   device gets back its own name (no `name_by_user`, no area) and every test
   entity the entity_id `<domain>.<unique_id>` (for example
   `sensor.mock_boiler_power`, `select.mock_boiler_fault_profile`), whatever HA
   generated from old names or areas. An automation that no longer loads is
   removed only if it refers to a test entity that is gone; any other broken
   automation is reported and kept.
2. Removes the Bestiarium Devices entries of the stand — those with an entity
   on a test device, and those without entities named after a retired test
   socket — and adds one per socket. Entries of other devices, standalone and
   disabled ones are kept.

   | Socket | Priority | Overload first queue | Thermostat-driven |
   |---|---|---|---|
   | commode, bedside | 5200 (commode added first) | | |
   | boiler | 5000 | yes | |
   | slow cloud | 4000 | | |
   | Tuya | 3500, **off** in "Night" | | |
   | night light | 3000 | | |
   | dead | 1000 | | |
   | cooker, fridge | 9999 (always on) | | |
   | floor heating 1–3 | 2000 | yes | yes |

   The Matter socket gets no entry: it is free for re-pointing the boiler to
   it (the boiler's "Reconfigure" → another device).

3. Replaces the stand's Bestiarium Climate entry (the one with the stand
   thermostats; others are kept) with one entry (capacity 2, control
   settings: 2 attempts, so a dead switch is deferred in 2 minutes; the
   stepwise "off" 3 attempts a minute apart, so its whole chain takes 4
   minutes; the recovery timeout 5 minutes) and thermostats "Stand AC 1–3" on
   thermometers 1–3 and the virtual switches of AC 1–3 (each with its power
   socket: the "Allowed" of its AC socket's device, DEV-010), each with a minimum
   cycle duration of 1 minute (the pause after an "off" and the working time
   before a rotation, decision 29), so that a scenario shows its result
   within 4 minutes of its button. Since DEV-011 the same entry has two
   universal thermostats, heating, each read by its socket's power (20 W)
   with the same minimum cycle: "Stand heater" (thermometer 4, the switch
   of the free heater socket, 1500 W) and "Stand cloud AC" (thermometer 5,
   the template switch "Stand cloud AC switch" made by the template
   helper's own form, whose actions are the IR scripts of `ac_4`, as the
   home's switches over Tuya scenes; made again on each run).
   Then the stand's Hisense (DEV-012): Bestiarium IR is added if missing, and
   its subentry "Stand Hisense" made again on the emitter
   `infrared.mock_hisense_emitter`; the other subentries of Bestiarium IR are
   kept.

4. Writes the scenario scripts `script.bestiarium_stand_*`:

   | Script | What it sets up |
   |---|---|
   | `reset` | every profile Reliable (sockets, thermometers, grid), thermostats off, mode "Grid on", cooker at 400 W; every socket off except the "always on" cooker and fridge, which stay on (the balancer would turn them on first anyway); the stand's loads allowed again (explicitly: a restart of HA in between changes nothing), the air conditioners' power sockets ("Allowed") on again, the three rooms at 20 °C; the Hisense off, then its display on (assumed only, no frame) and its knobs off |
   | `night_case` | reset; the commode socket "Stuck state" and switched on (HA still shows off); green zone |
   | `red_zone_dead_socket` | reset; dead socket, night light and slow cloud socket on; then the dead socket stops answering, the cooker at 3100 W pushes the grid into the red zone |
   | `slow_cloud` | reset; commode, bedside and boiler on; the slow cloud socket off with profile "Slow cloud"; green zone |
   | `zone_flicker` | reset; the cooker holds the grid at the yellow threshold ±100 W, 12 times 5 s each way (2 min); the zone stays yellow |
   | `mode_off` | reset; the Tuya socket on; mode "Night" turns it off |
   | `mode_off_unresponsive` | reset; the Tuya socket on, then "Unresponsive"; "Night": retries, then "Does not turn off by mode" |
   | `mode_off_turned_on` | reset; the Tuya socket on; "Night" turns it off; 10 s later a person turns it on; green leaves it on |
   | `mode_off_red` | reset; "Night"; the Tuya socket, night light and boiler on; the cooker pushes the grid 200 W into red: the Tuya socket goes first, then the queue |
   | `thermostats_dead_switch` | reset; the IR of AC 1 lost ("IR lost"); the stand's loads not allowed (the load stays green); AC 1's room at 15 °C, the others at 21 °C, all heat to 25 °C (capacity 2), so AC 1 starts first: after 2 attempts (about 2 minutes) it is "Not responding" and AC 3 takes its place |
   | `thermostats_hung_command` | reset; the IR "on" of AC 3 never ends ("IR hangs"; AC 3, as AC 1 may be deferred by the dead switch scenario); the stand's loads not allowed; AC 3's room at 15 °C, the others at 21 °C, all heat to 25 °C, so AC 3 starts first (its start under way is not rotated out): past 2 attempts' time (110 s) it is "Not responding", its "off" overtakes the hung "on" |
   | `climate_rotation` | reset; the stand's loads not allowed; the three thermostats heat (capacity 2): after a minute (the stand thermostats' minimum cycle duration) the one past its time goes out and the waiting one starts — the "off" at once, the start once it fits (decision 32); the one turned out pauses a minute, so there is no cascade |
   | `climate_switch_glitch` | no reset, in the zone as it is: the first running air conditioner draws its standby for 15 s, so its switch shows "off" for a moment (its power delay is 10 s): climate sends no command and keeps the arrangement (review 37, J1–J2); for an "off" that lasts, its IR "off" button, as a person with the remote |
   | `climate_stuck_on` | reset; AC 1 the first to start (its room the coldest); once it runs, its IR is lost ("IR lost") and its room at 30 °C: the thermostat wants it off — "off" at once and twice more a minute apart, "Does not turn off" after a minute, the event "out of control" at 3 minutes, its socket ("Allowed" of `ac_1`) off and the event "power cut" at 4 (DEV-010) |
   | `climate_bypass_on_pause` | reset; the IR of AC 1 lost, capacity 1, only AC 1 wants heat (the other rooms at 30 °C, so no rotation takes it out): after 2 attempts "Not responding" (the script waits for it, 5 minutes at most, else stops); then a person turns it on with the remote (its power at 1200 W): it is turned off stepwise all the same, the deferral not lengthened |
   | `climate_red_entry` | reset (the thermostats off); the cooker pushes the grid 300 W into red for a minute (always on: the devices' red strategy never turns it off; up to 5500 W since DEV-010, 4200 W did not reach red from the reset), then back to 400 W: each air conditioner's IR "off" runs once (its script in the logbook), not every 5 s; no control state changes |
   | `thermostats_no_temperature` | reset; the stand's loads not allowed; AC 2's room at 15 °C, the others at 21 °C, all heat to 25 °C, so AC 2 starts first; 90 s later thermometer 2 "No data": after a minute AC 2 is off, its thermostat unavailable, "No temperature data" |
   | `grid_no_data` | reset; the grid "No data": after a minute the zones are unavailable (no data) |
   | `mode_after_restart` | reset; "Night"; then the Tuya socket on, as a person would: restart HA, and the mode turns it off again with `mode_applied` `reason: restart` |
   | `unstable_tuya` | reset; the Tuya socket "Unstable" and on |
   | `universal_auto` | scenario 1 (DEV-011): reset; the heater's room at 18 °C, "Stand heater" in auto to 21 °C: it starts; 2 minutes later the room at 22 °C: it stops |
   | `universal_manual` | scenario 1: reset; capacity 0; "Stand heater" in manual ("heat"): it starts all the same; a minute later the cooker pushes the grid into yellow: it goes on; a minute later the room at 22 °C: it stops by itself; then off |
   | `manual_red` | scenario 2: reset; "Stand heater" manual and running; the cooker pushes the grid 300 W into red: the thermostat is off at once, its heater the first to go off, the event `manual_turned_off` (`zone_entered`); the cooker back: the thermostat stays off |
   | `manual_on_in_red` | scenario 2: reset; red; "Stand heater" set to "heat": off at once, the heater never on, the event (`turned_on`) |
   | `climate_coexistence` | scenario 9: reset; the stand's loads not allowed; AC 1 and AC 2 (virtual thermostats) and "Stand heater" (auto) want heat, capacity 2: two run; after the minimum cycle the rotation lets the waiting one in |
   | `hisense_manual_modes` | scenario 5 (DEV-012): reset; the Hisense heats to 24 °C, 10 s later cools to 22 °C, then dry, fan only, the fan speed high, off: each a frame of its own (one in the emitter's logbook) and its power (1200, 1200, 300, 40, 40, 5 W) |
   | `hisense_display` | scenario 6 (DEV-012): reset; heat 24 °C; the display off (one display frame); the target 26 °C: two frames 3 s apart, the first only lights the display ("Last frame": only lit the display, then carried out); off: one frame |
   | `cloud_ac_universal` | scenario 10: reset; "Stand cloud AC" in auto, its room at 18 °C: the template switch runs the IR script, 1200 W, it runs by its power; the room at 25 °C: off (5 W); cold again and running, its IR lost and the room warm: "off" three times a minute apart, "Does not turn off", the event "out of control" — no socket is cut (it has none) |

5. Writes the dashboard "Стенд Bestiarium" (`/bestiarium-stand`): grid, mode
   and zones; a section of the Hisense (its climate, display, state, last
   frame, emitter and power); the scenario buttons; a section per socket
   (switch, power, profile, control state; the heading and the switch tile
   open the device page with its knobs; the free Matter socket has no control
   state); a logbook of the sockets, zones, mode and the Hisense's frames.

6. Checks that the load zones of Bestiarium Mode are available, and fails
   instead of printing "Done." if they are not.

The scenarios assume the stand's Bestiarium Mode thresholds above; with other
limits the cooker power in `red_zone_dead_socket` must change. `zone_flicker`
and `mode_off_red` set the cooker from the grid's current load (a template), so
the grid noise does not move them.
