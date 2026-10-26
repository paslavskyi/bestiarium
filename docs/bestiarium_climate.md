# bestiarium_climate — Home Assistant Custom Integration

## Overview

`bestiarium_climate` is a Home Assistant custom integration (domain: `bestiarium_climate`, version 0.1.0) that provides **capacity-limited rotation of heating/cooling devices** using virtual thermostats and virtual switches. It depends on `bestiarium_mode`.

Core capabilities:

1. **Virtual switches** — Switch entities whose heating, cooling and off actions are full HA script sequences; heating or cooling comes from the thermostats that control them; the on/off state comes from a power sensor with a hysteresis and a delay.
2. **Virtual thermostats** — Climate entities with hysteresis-based temperature demand, preset modes, and state restoration. Thermostats do NOT directly control switches — the balancer decides and sends each switch what it wants (`ThermostatControl`).
3. **Capacity-limited balancing** — A `BalancingCoordinator` runs every 60 seconds, allocating a limited number of simultaneous device slots to thermostats with the most demand, rotating devices fairly based on working time.
4. **Capacity number entity** — A runtime-adjustable `number` entity (0–100) that controls how many devices can run simultaneously, with state restoration.

The integration uses **HA subentries** — switches and thermostats are added as subentries under a single parent config entry.

**Automation events**: The balancer fires HA bus events at device rotation in a deficit; the thermostat's control state tells switch and temperature problems. See [Automation Events](events.md) for event types, payloads, and automation examples.

---

## File Structure

```
custom_components/bestiarium_climate/
├── __init__.py          # Entry setup/unload, coordinator lifecycle
├── manifest.json        # Integration metadata (depends on bestiarium_mode)
├── const.py             # Constants, config keys, defaults, enums
├── data.py              # Type aliases and TypedDicts for config data
├── config_flow.py       # Main config flow + switch/thermostat subentry flows
├── util.py              # duration_dict_to_timedelta helper
├── climate.py           # VirtualThermostat entity (ClimateEntity + RestoreEntity)
├── switch.py            # VirtualSwitch entity (SwitchEntity)
├── number.py            # CapacityNumber entity (NumberEntity + RestoreEntity)
├── sensor.py            # ControlStateSensor — "Control state" of each thermostat
├── command.py           # SwitchCommand, SwitchCommands — the commands to a switch and their series
├── control.py           # ThermostatControl, ThermostatMemory (Store)
├── balancer.py          # BalancingCoordinator — capacity-limited device rotation
├── rotation.py          # arrange() — who should heat, without the zone (decision 32)
├── strings.json         # Default translation strings
└── translations/
    ├── en.json           # English translations
    └── uk.json           # Ukrainian translations

tests/custom_components/bestiarium_climate/
├── __init__.py
├── conftest.py          # Shared fixtures (entries, subentries)
├── test_config_flow.py
├── test_climate.py
├── test_switch.py
├── test_number.py
└── test_balancer.py
```

---

## Platforms

```python
PLATFORMS = [Platform.CLIMATE, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]
```

---

## Config Entry Data Schema

### Parent Entry

```python
{
    "capacity": int,                     # How many devices can run simultaneously (0–100, default 1)
    "min_working_time": {                # Minimum time a device must run before rotation
        "hours": int,
        "minutes": int,                  # Default: 20 minutes
        "seconds": int,
    },
    "recovery_timeout": {                # Wait time before retrying a non-responding switch
        "hours": int,                    # Default: 1 hour
        "minutes": int,
        "seconds": int,
    },
}
```

### Switch Subentry (`subentry_type: "switch"`)

```python
{
    "name": str,                         # Display name
    "heating_action": list,              # Optional — HA script sequence for heating
    "cooling_action": list,              # Optional — HA script sequence for cooling (one of the two required)
    "off_action": list,                  # HA script sequence for turning off
    "power_entity": str,                 # Entity ID of power sensor (device_class=power)
    "min_power": float,                  # Watts — at or above = "on" (default 0.0)
    "power_hysteresis_percent": int,     # "on" → "off" only below min_power minus this share (default 20, 0–90)
    "power_delay_seconds": int,          # A change of on/off shown after this (default 10, 0–600)
    "power_tolerance_seconds": int,      # No power data shown (unavailable) after this (default 60, 0–600)
}
```

### Thermostat Subentry (`subentry_type: "thermostat"`)

```python
{
    "name": str,                         # Display name
    "temperature_sensor": str,           # Entity ID of temperature sensor
    "target_switch": str,                # Entity ID of the switch this thermostat controls
    "thermostat_mode": "heating" | "cooling",  # ClimateMode
    "cold_tolerance": float,             # °C below target before heating starts (default 0.3)
    "hot_tolerance": float,              # °C above target before cooling starts (default 0.3)
    "min_cycle_duration": dict | None,   # Optional duration dict (hours/minutes/seconds)
    "heater_power": float,               # Optional — W, for the fit check; empty: the device's expected power, else 2000
    "min_temp": float | None,            # Optional minimum setpoint
    "max_temp": float | None,            # Optional maximum setpoint
    "away_temp": float | None,           # Optional preset temperatures
    "comfort_temp": float | None,
    "eco_temp": float | None,
    "home_temp": float | None,
    "sleep_temp": float | None,
    "activity_temp": float | None,
}
```

### Key Constants (`const.py`)

| Constant | Value |
|---|---|
| `DOMAIN` | `"bestiarium_climate"` |
| `SUBENTRY_TYPE_SWITCH` | `"switch"` |
| `SUBENTRY_TYPE_THERMOSTAT` | `"thermostat"` |
| `DEFAULT_CAPACITY` | `1` |
| `DEFAULT_MIN_WORKING_TIME` | `timedelta(minutes=20)` |
| `DEFAULT_RECOVERY_TIMEOUT` | `timedelta(hours=1)` |
| `DEFAULT_TOLERANCE` | `0.3` |
| `CAPACITY_MIN` / `CAPACITY_MAX` | `0` / `100` |
| `BALANCING_INTERVAL_SECONDS` | `60` |
| `ControlSettings` (options) | `attempts` 7, `tolerance_seconds` 60, `hysteresis_seconds` 60 |
| `PRESET_KEYS` | `("away", "comfort", "eco", "home", "sleep", "activity")` |

### ClimateMode Enum

```python
class ClimateMode(StrEnum):
    HEATING = "heating"
    COOLING = "cooling"
```

---

## Integration Lifecycle (`__init__.py`)

### Setup (`async_setup_entry`)

1. Forwards platform setups: CLIMATE, NUMBER, SWITCH
2. Creates a `BalancingCoordinator(hass, entry)` and starts it (60s periodic)
3. Stores coordinator at `hass.data[DOMAIN][entry.entry_id]`
4. Registers reload listener

### Unload (`async_unload_entry`)

1. Stops the coordinator **first**: the commands still under way are cancelled, a stopped coordinator runs no cycle and sets no timer. Unloading a virtual switch ends its running sequence; a cycle after it, while the thermostats go, would turn the others off (review 22, D2)
2. Unloads platforms
3. Then removes the coordinator from `hass.data`: no two coordinators control the same switches across a reload (review 20, C3)

Each config entry gets its own coordinator (unlike `bestiarium_devices` which shares one).

---

## Config Flow (`config_flow.py`)

Class: `BestiariumClimateFlowHandler` (VERSION = 1)

### Main Entry Flow

| Step | Purpose |
|---|---|
| `user` | Form: capacity, min_working_time, recovery_timeout |
| `reconfigure` | Same form with existing values pre-populated |

### Subentry Types

Two subentry flows registered via `async_get_supported_subentry_types`:

#### `SwitchSubentryFlowHandler`

Single step: name, power_entity, min_power, power_tolerance_seconds, power_hysteresis_percent, power_delay_seconds, heating_action, cooling_action, off_action.

Validation:
- Name required
- A heating or a cooling action required (`heating_or_cooling_required`)
- Each action validated as HA validates a script (`invalid_action`)
- Reconfigure: the action a thermostat of this switch needs cannot be removed (`needed_by_thermostat`)
- `off_action` always required
- `power_entity` required
- `min_power` must be a number

Reconfigure reuses the same form; the saved heating and cooling actions are suggested values, so one cleared in the form goes. Before DEV-008 stage B the switch had its own `mode` field; entry migration 1.2 removes it.

#### `ThermostatSubentryFlowHandler`

Two-step flow:
1. **basic** — name, temperature_sensor, target_switch, thermostat_mode, cold/hot tolerance, optional min_cycle_duration, min_temp, max_temp
2. **presets** — optional preset temperatures (away, comfort, eco, home, sleep, activity)

Both steps accumulate into `_options` dict. Reconfigure loads existing subentry data into `_options` before showing forms.

The basic step refuses a switch another thermostat already controls (`switch_taken`: one switch, one thermostat — any switch, not only a virtual one), a switch that is the relay of a Bestiarium Devices device (`switch_is_device`, with the device's name: **one relay lives in one integration**, the owner's rule, review 18) and a virtual switch of this integration without the action of the thermostat's mode (`switch_has_no_action`). An existing thermostat on a device relay (configured before) is the fixable Repairs issue `switch_is_device` (its fix: another switch for the thermostat); the balancer leaves it alone meanwhile.

---

## Virtual Switch (`switch.py`)

Class: `VirtualSwitch(SwitchEntity)`

### Key Behavior

- **Actions**: each is a full HA script sequence (actions, conditions, delays), run by HA's script machinery (`helpers.script.Script`, mode `restart`), validated at setup as HA validates a script. A command stops the sequence of the other command still running. The command waits for its sequence to finish: keep delays in the actions short, the balancer waits too. An action that fails fails the command (`HomeAssistantError`); the balancer counts it as an attempt.
- **Turn on**: the heating or the cooling sequence by the **mode of the thermostat** whose `target_switch` is this switch (`thermostats_of`). Without a thermostat: heating if it has a heating action, else cooling. Several thermostats on one switch (only possible in a configuration made before DEV-008 stage B; the forms now refuse it): the command fails with `switch_shared`, the balancer leaves those thermostats out (no command, no attempt; the set of shared switches is read every cycle), and the fixable Repairs issue `switch_shared` asks another switch for each thermostat but the first, checked as in the thermostat's form.
- **Errors**: whatever fails in a sequence fails the command as a `HomeAssistantError` (`action_failed`); the balancer counts it as an attempt. A saved sequence HA no longer accepts (an integration it used removed) leaves that switch out at setup with the fixable Repairs issue `invalid_action`, whose fix shows the switch's actions; the other switches are set up.
- **Migration 1.2** turned a switch's own "cooling" into "from the thermostat": a cooling switch with a heating action and no thermostat now heats — the fixable Repairs issue `switch_now_heats` says so (no silent change) and shows the switch's actions to keep or remove the heating one; it goes once the switch has a thermostat or no heating action.
- **Turn off**: the off sequence
- **State**: from the power sensor — on at or above `min_power`; on stays on down to `min_power × (1 − power_hysteresis_percent / 100)`. A change is shown only once it has lasted `power_delay_seconds` (a shorter dip or spike is not shown).
- **No power data** (the sensor unavailable, unknown, missing or not a number) for `power_tolerance_seconds` (60 s): the switch is **unavailable**, not off; a shorter dropout is silent; data back → shown at once. Once HA runs, one `info` when the data goes and when it is back (none at the start). The balancer sees "Switch unavailable" after its own tolerance.
- **Why it is in the integration** ([necessity](concepts/necessity.md), refinement 4) — only its differences from HA's template switch count: a delay on the state read from power (no `delay_on` / `delay_off` there; per heater it would take a Threshold helper, an `input_boolean` and an automation); "off" cancels an "on" sequence still running (the template switch's scripts run in `single` mode); its forms check that its actions match its thermostat's mode. The sequences, the hysteresis and plain availability are at parity.

### Device Info

Each switch subentry creates its own device:
- `identifiers`: `{(DOMAIN, subentry.subentry_id)}`
- `model`: `"Virtual Switch"`
- `entry_type`: `DeviceEntryType.SERVICE`

---

## Virtual Thermostat (`climate.py`)

Class: `VirtualThermostat(ClimateEntity, RestoreEntity)` — one per thermostat subentry.

### Key Behavior

- Tracks the temperature sensor; `hvac_action` is HEATING / COOLING when the temperature is beyond the target and the tolerance, IDLE otherwise, OFF when the mode is off.
- A missing reading keeps the last temperature: the thermostat's control (below) decides when the temperature is gone too long. Then the climate entity is **unavailable** (`available = False`) and the thermostat leaves the rotation.
- Presets set the target; `none` leaves a preset and goes back to the target set before it (`saved_temperature`, kept in the Store — before DEV-008 it was read on restore but never written).
- It asks the balancer for an immediate cycle on a change of mode, target or demand.
- Its state holds only climate data: the balancer's internals (`expected_switch_state`, tries, deferral) are not attributes (DEV-008 stage B; they lived in the history).

### State Restoration

`RestoreEntity` restores the HVAC mode, the target and the preset — from its extra data (`extra_restore_state_data`), because a thermostat unavailable at shutdown (no temperature) has a state without attributes; a state saved before DEV-008 stage B is read from its attributes. The rest lives in the Store `bestiarium_climate.thermostats` (`ThermostatMemory`, by subentry id): `saved_temperature` of the thermostat, and `expected_on`, `expected_changed_at`, `tries`, `deferred_until` of its control.

---

## Thermostat control (`control.py`) and "Control state" (`sensor.py`)

`ThermostatControl` — one per thermostat, made by the coordinator; the pattern of `bestiarium_devices` DEV-007 ([stability](concepts/stability.md)):

- It follows the thermostat's switch and temperature sensor; their availability counts only once HA has started.
- **Switch unavailable / no temperature**: gone (unavailable, unknown, missing; for the temperature also not a number) for longer than `tolerance_seconds` → a confirmed problem; it ends after `hysteresis_seconds` without it.
- **Commands** (review 24, E4; `command.py`, review 30, G7): each command is a `SwitchCommand` of the control's `SwitchCommands` — its direction, its task, when it was sent and when it ended. The **series** of a direction is its commands sent without an answer; **only the switch's answer ends it** (the switch in the state of its last command), never a new wanted state: an "on" taken off by yellow or the rotation goes on counting with the next "on", and the "off" between them, answered at once by a switch that is off anyway, ends only the series "off". A command is an attempt of its series when it is sent; it is a **failed attempt** once its window (a cycle, 55 s, from its **end**) is over without an answer (DEC-014, rule 3) — an overtaken one too; `failed_attempts` shows the failed ones of the larger series. A command waits while one of its direction waits for its answer (running, or in its window), an overtaken one too, so a toggling IR code is never sent twice; one the other way overtakes the running one at once and **cancels** it. A command that ends late changes only itself (no other is freed or restamped); one that ignores its cancel still counts for the budget. A switch that was in the state already when the command went closes the series but is no answer: only a change after the command ends "Not responding" (review 33, H4). The cycle runs outside the script that asked for it (a fresh context, review 35, I5): its commands, events and timers carry no script stack, else HA refuses another switch's sequence as a recursion (review 33, H8). A virtual switch whose sequence another command stops ends its command normally for the one who sent it — only its own cancellation is passed on (review 35, I4). A start the power cut short is no failed attempt: the cut closes the series "on" (protection, not a fault). A deferral closes every series and every command (one path of closing, review 30, G1): no command sent before it answers for the retry, a person turning the switch on meanwhile ends no "Not responding". **A start under way** is the start's first command — the only "on" since the balancer wanted it on — waiting for its answer by the same rule as any command (running, or its window after its end; review 30, G2): a failed start before does not make the next one "no start". The deferral's "off" waits too while an "off" runs or is in its window (G4). `failed_attempts` shows a failed attempt when its window ends, and the count that deferred the switch while it is deferred (G5).
- **Not responding**: the switch is **deferred** for the entry's `recovery_timeout` (default 1 hour; 5 minutes on the stand), turned off once (the deferral's "off") and then tried again, when (a) a series has as many failed attempts as `attempts` and the switch is not in its state — **whatever is wanted now**, so a heater out of attempts never takes a running one's place (found on the stand), or (b) a command not answered runs longer than the budget of the attempts (`attempts` × 55 s) — not when the switch already took its state and only the rest of a sequence hangs (review 24, E2). The check runs **before** the capacity is shared, so the deferred switch holds no place in that very cycle (review 24, E1). After the deferral the switch is in the rotation again with new series and gets a start when it has a place. The state stays "Not responding" — **across a restart too** (`unresponsive` in the memory, review 24, E5) — until the switch **answers a command sent out of a deferral**, not merely because it is as wanted (the thermostat needs no heat while the IR is still lost): then, after the hysteresis, back to "Normal", with one `info` line (review 22, D6).
- **In the rotation** only if none of these: a gone switch, a deferred switch and no temperature all free the thermostat's place for the others.

`ControlStateSensor` — `sensor.<thermostat>_control_state`, enum, diagnostic, on the thermostat's device: `switch_unavailable` > `unresponsive` > `no_temperature` > `no_power` > `normal`. Attributes `failed_attempts`, `next_attempt`, `pause_until` (unrecorded). One `warning` when a problem starts, one `info` when it ends. **No power** (`no_power`, "Без живлення", the owner's decisions 28 and 32): the device powering the heater is off (the load protection, a person, a mode), the heater does not heat and its thermostat is not off (off, it is "Normal") — no fault: no attempt, no "Repair", one `info` line when it starts (after the tolerance) and one when it ends (at once, the power back). The device powering an air conditioner need not be "always on".

### Settings (options flow)

One set for all thermostats of the entry (the owner's choice), defaults: `attempts` 7, `tolerance_seconds` 60, `hysteresis_seconds` 60.

---

## Capacity Number (`number.py`)

`CapacityNumber` — how many thermostats may work at once, 0–100. **The one source of the capacity**: a power automation changes it, and a change shares the capacity again at once. The entry's `capacity` is only its initial value (the setup form); the reconfigure form has no capacity field (before DEV-008 its value was silently ignored, because the entity restores its last state). No entity category: it is operated.

---

## Balancing Coordinator (`balancer.py`)

`BalancingCoordinator` — one per climate entry, made before the platforms (they show its controls), started after them.

### Balance Cycle (`async_request_balance`: every 60 s, on a thermostat's change, a capacity change, a zone change and a control's new state)

A request runs the cycle at once; one asked for during a cycle makes one more after it. The cycle never waits for a switch: each command is its own task, so a slow switch (an IR remote) holds back neither the others nor the next decisions, and a switch's sequence may call a climate service. What a switch gets is its control's decision (`ThermostatControl.next_command`, the rules of the commands above); the balancer only sends it and tells the control when its task ended, whatever ended it — an unexpected error included (logged as an `error`, review 22, D1). Each command's end asks for a cycle, so what was wanted meanwhile is decided then; so does a thermostat's switch **turning on** (a virtual switch showing its "on" late, a person turning it on), so a switch on that is not wanted — in red too — is turned off at once, not at the next periodic cycle (review 33, the stand). A switch's "off" runs no cycle at once, and "turned off by another" counts only once the "off" lasts the tolerance: a moment's glitch of a running switch turns nothing off (review 35, I1; DEC-014 rule 1). Only changes of the state count, not of its attributes (I6). A control's new state (a problem confirmed or over, a deferral) runs a cycle at once, so the freed place goes to another thermostat in the same second.

### Load zones (the owner's rules, DEV-008 review 13)

Climate follows the zones of `bestiarium_mode` as the devices' balancer does. It reads the four zone sensors, found by their unique_id `power_load_{zone}` in `bestiarium_mode` (the whole contract between the two, [necessity](concepts/necessity.md) refinement 5): by the rule of the devices' balancer: the highest zone whose sensor is on; none on while green is available (off) is the pause; otherwise no data; none registered (no load limits) is "none" (only the capacity). It follows the sensors created or renamed later (the entity registry).

| Zone | Climate |
|---|---|
| green | the capacity applies; rotation; heaters start **one at a time** (the next a step of 10 s later, once the start under way has its answer (its first command answered, or its window, a cycle from the command's end, over); a switch that does not answer holds no other start longer) and **only if it fits**: load + heater power ≤ the red threshold — the rule of the devices' green strategy (DEV-006) |
| no load limits | the capacity and the rotation only |
| yellow | the ones heating go on; the rotation turns out the one past its working time and starts no other; no new starts (decision 32) |
| pause, no data | nothing starts and nothing rotates; the ones heating go on |
| overload | **all** heaters off at once: they are not in the devices' emergency queue (review 18) |
| red | no starts; every 5 s one heater goes off, the room that needs it least first (the smallest difference between the target and the temperature) |

**The fit check** (the owner's decision, DEV-008 review 15) is a safety net over the capacity: the rest of the load changes, the capacity does not. The load is the power sensor of `bestiarium_mode` (its entry's `ac_power_sensor`), the threshold the red zone sensor's `red_threshold`; an unknown one does not fit. The heater's power is the "Expected power" of the `bestiarium_devices` device that **powers its virtual switch** — the device whose power sensor (`power_sensor_entity_id`) is the virtual switch's `power_entity`, as the socket of an air conditioner (the sensor by unique_id `<entry_id>_expected_power`); else the thermostat's `heater_power`; else 2000 W, a large heater as in devices. Only a device that is the **one** Bestiarium device with that power sensor counts (two on one sensor: neither is surely the power); the devices' default for a large device (2000 W, `estimate_source: large_device_default`) is no knowledge, and the thermostat's `heater_power` counts then (review 20, C4, C8). While that device's relay is off (the load protection or a person cut the power) a heater that **does not heat** (its switch off, no start under way) **leaves the rotation** and gets no command: it holds no place of the capacity, the next thermostat takes it; the balancer follows the relays and decides again the moment one comes back on (review 20, C1; review 24, E3 — also one the memory holds on after a restart). A heater that heats stays: it goes off by its 0 W (turned off by another), so a moment "off" of a cloud relay commands nothing (review 22, D5). The whole contract with Bestiarium Devices is in `devices.py`.

**A power cut is protection, not a fault** (review 18): when the devices' emergency queue cuts an air conditioner's power, its virtual switch reads 0 W and turns off without a command of climate — no failed attempt, no "Not responding" (the rule of B1: a switch that changed after the command). A start that does not fit waits, without a notification; a debug line tells the numbers.

A switch wanted on that turned off after the balancer's command (the devices' load protection, or a person) is no failed attempt: it is wanted off, and a new start has to fit again (review 17, B1). Out of green, a switch wanted on but found off (the devices' load protection turned it off) is wanted off: it is no failed attempt and is not turned on again until green. A thermostat that stops wanting heat (idle, off) is turned off in every zone. The capacity entity (FR-019: an automation lowering it in red) stays a finer setting; the rules above work without it.

1. **Allocate.** The working thermostats are those in the rotation that want heat or cold. Every other one is not wanted on. If the capacity is short (the normal state of a home that needs Bestiarium Climate), the ones that worked least go first: working-time coefficient ascending (time on / minimum working time, the thermostat's own or the entry's), then the temperature difference descending. In a deficit a change fires `bestiarium_climate_device_rotated_in` / `_out`.
   **Two steps** (the owner's decision 32, replacing decision 29's rotation). **1. The arrangement, without the zone** (`rotation.arrange`, a pure function): who should heat, from the capacity, the rank, the pauses and the starts under way — a start under way keeps its place (it has not heated yet; its answer or its budget decides first, review 30, F1); the places left go by rank, so a waiting heater takes the place of one past its working time; a waiting heater in its pause takes no place and turns out nobody; heaters without power, deferred or without temperature never reach it (they are out of the rotation before). **A waiting heater that does not fit** even with the measured power of the running one it would replace freed (load − that power + its own > the red threshold; an unknown load or threshold does not fit) **takes no place**, as one in its pause: the running one goes on heating (the owner's decision SRC-140, review 33, H2); the next step (10 s) decides again, so it starts the step after it fits (review 35, I2). One rule of fitting (`_fits`) serves the arrangement and the start, with the pair "who takes whose place" decided once (I3). **2. The zone's rules on its changes:** green (or no load limits) turns off at once — even before the new heater fits — and starts one at a time, each if it fits with the **measured** power of the ones turned off freed (an idling air conditioner frees what it draws, not its expected power; review 33, H3); yellow only turns off, starts none (the owner's rule: the rotation turns out the one past its time and starts no other); the pause and no data rotate nothing; red turns off one by one, the overload all at once. **After any "off"** (the rotation, the zones, the overload, its power cut, a person, a start cut short before its switch showed "on": the compressor may have begun) a heater **pauses** before it may start again — from the last "off": climate's decision to turn off a heater that is on or whose start was sent, or its switch turning off, the later counting; a cycle runs the moment a pause ends. The pause is the thermostat's minimum cycle duration, else 5 minutes (the compressor's protection; a virtual switch would read a low power from an early start). So the one turned out cannot come straight back, and the next cycle with the same data keeps the arrangement — no cascade. The pause's end is the `pause_until` attribute of the control state, which stays "Normal".
2. **Sync.** Each switch gets what its control decides (`ThermostatControl.next_command`); a command HA refuses is a failed attempt once its window is over, and the cycle goes on with the other switches — one switch never stops the others. The same command is repeated at most once a cycle after the last one's end; a new wanted state is sent at once, but the count of attempts goes on until the switch answers (review 24).

### Capacity Reading

The state of the capacity number, by its unique_id; an unreadable value is `DEFAULT_CAPACITY`.

### Removed in DEV-008 stage B ([necessity](concepts/necessity.md))

- `bestiarium_climate_capacity_exhausted` — fired every minute of a deficit (~1440 a day) while a deficit is the normal state; nobody listened.
- `bestiarium_climate_switch_failed` / `_switch_recovered` — the control state tells the same.

---

## Translations

Three locale files: `strings.json` (default), `en.json`, `uk.json`.

Coverage:
- Main config flow: user and reconfigure steps
- Switch subentry flow: user and reconfigure steps
- Thermostat subentry flow: basic and presets steps
- Validation errors: `invalid_capacity`, `name_required`, `required`, `invalid_min_power`, `heating_or_cooling_required`, `invalid_action`, `needed_by_thermostat`, `switch_taken`, `switch_has_no_action`
- Exceptions: `switch_shared`, `action_failed`, `no_action`
- Repairs of a thermostat, switch or entry that is gone are deleted at start and when the entry is removed; a fix that finds its thermostat or switch gone deletes its issue (review 20, C6)
- Repairs, all fixable: `switch_shared` (another switch for each extra thermostat), `switch_now_heats` and `invalid_action` (the switch's actions)
- `strings.json` removed: the translations are the only source of the texts
- Abort reasons: `reconfigure_successful` — passed explicitly to `async_update_and_abort` in the entry and both subentry reconfigure flows. The explicit `reason="reconfigure_successful"` makes HA show this integration's translated message; without it HA uses the core text, which has no Ukrainian translation yet.
- Entity names: `Capacity` (number entity)

---

## Test Coverage

### `test_config_flow.py`
- Create main entry with custom values and defaults
- Reconfigure main entry (no capacity field); control settings in the options; translations of states and settings
- Switch subentry: create (no mode, hysteresis and delay defaults), a heating or cooling action required, an invalid action refused, the action a thermostat needs kept
- Thermostat subentry: two-step flow (basic + presets); one switch serves one mode; a virtual switch needs the mode's action

### `test_climate.py`
- Thermostat creation from subentry
- Temperature tracking updates current_temp and hvac_action
- IDLE hvac_action when temp meets target
- No balancer internals in the attributes; the target before a preset survives a reload
- Set hvac_mode updates state

### `test_switch.py`
- The whole heating sequence runs; the mode comes from the thermostat; without a thermostat its only action
- A shared switch (an old setting): Repairs, the command fails, nothing runs
- A failing sequence fails the command (`action_failed`); an invalid saved action: the switch left out, a fixable issue, the fix saves the actions
- A cooling switch that now heats: Repairs; the options reload nothing
- Off stops a sequence still in its delay
- Hysteresis and delay; a short glitch is not shown; no power data → unavailable after the delay, back at once
- Migration 1.2 removes the switch's mode

### `test_number.py`
- Capacity number creation, initial value from config
- Set value updates state
- Restore fallback to config entry value

### `test_balancer.py`
- Three heaters, capacity 2: two on; a dead switch is deferred and the third takes its place, one warning
- A refused command does not stop the other switches
- No temperature for more than a minute: off, unavailable, its place to another; back after the hysteresis
- The capacity entity applies at once; nothing repeats every minute; no internals in the attributes; a deferral survives a reload
- A virtual switch without power data is unavailable

---

## Design Notes & Gotchas

1. **Indirect switch control**: Thermostats NEVER directly call switch services. The balancer sets what each control wants (`expected_on`) and sends the switches. This decoupling enables capacity limiting.

2. **Subentry architecture**: Unlike the other bestiarium integrations, this one uses HA's `ConfigSubentryFlow` API. Switches and thermostats are subentries under one parent config entry, not separate config entries.

3. **One coordinator per entry**: Each config entry gets its own `BalancingCoordinator` (unlike `bestiarium_devices` which shares one). The coordinator is tied to the entry's capacity and timing settings.

4. **Power-derived switch state**: `VirtualSwitch.is_on` comes from the power sensor, not from tracking on/off calls. This means the switch reflects actual device behavior (is it consuming power?) rather than optimistic state.

5. **Fair rotation via working_time_coefficient**: When demand exceeds capacity, devices that have worked the least get priority. The coefficient `actual_time / min_required_time` ensures each device gets at least `min_working_time` before being rotated out; a waiting one takes its place in the same cycle, and the one turned out pauses (decision 29).

6. **Non-responding switch handling**: after `attempts` (default 7) failed attempts of a series, or a command hung past their budget, the switch is deferred for `recovery_timeout` (default 1 hour), turned off once and tried again; its thermostat's control state is "Not responding" and its place goes to the others (see [Thermostat control](#thermostat-control-controlpy-and-control-state-sensorpy)).

7. **On-demand rebalancing**: Beyond the 60s periodic cycle, thermostats trigger immediate rebalance when their hvac_action changes to IDLE or OFF. This ensures freed capacity is reallocated promptly.

8. **Action format**: Switch actions use HA's action dict format (`{"action": "domain.service", "target": {...}, "data": {...}}`). The `_execute_action` helper also handles single-item list wrappers.

9. **Duration storage**: Durations (min_working_time, recovery_timeout, min_cycle_duration) are stored as `{"hours": int, "minutes": int, "seconds": int}` dicts, converted to `timedelta` at runtime via `duration_dict_to_timedelta`.

10. **Capacity at runtime**: The capacity `number` entity is adjustable at runtime without reconfiguring. The balancer reads it fresh each cycle via `_get_capacity()`.
