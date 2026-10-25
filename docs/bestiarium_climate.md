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
├── control.py           # ThermostatControl, ThermostatMemory (Store)
├── balancer.py          # BalancingCoordinator — capacity-limited device rotation
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

1. Stops the `BalancingCoordinator`
2. Unloads platforms
3. Removes coordinator from `hass.data`

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

The basic step refuses a switch another thermostat already controls (`switch_taken`: one switch, one thermostat — any switch, not only a virtual one) and a virtual switch of this integration without the action of the thermostat's mode (`switch_has_no_action`).

---

## Virtual Switch (`switch.py`)

Class: `VirtualSwitch(SwitchEntity)`

### Key Behavior

- **Actions**: each is a full HA script sequence (actions, conditions, delays), run by HA's script machinery (`helpers.script.Script`, mode `restart`), validated at setup as HA validates a script. A command stops the sequence of the other command still running. The command waits for its sequence to finish: keep delays in the actions short, the balancer waits too. An action that fails fails the command (`HomeAssistantError`); the balancer counts it as an attempt.
- **Turn on**: the heating or the cooling sequence by the **mode of the thermostat** whose `target_switch` is this switch (`thermostats_of`). Without a thermostat: heating if it has a heating action, else cooling. Several thermostats on one switch (only possible in a configuration made before DEV-008 stage B; the forms now refuse it): the command fails with `switch_shared`, the balancer leaves those thermostats out, and the Repairs issue `switch_shared` tells to give each its own switch.
- **Errors**: whatever fails in a sequence fails the command as a `HomeAssistantError` (`action_failed`); the balancer counts it as an attempt. A saved sequence HA no longer accepts (an integration it used removed) leaves that switch out at setup with the fixable Repairs issue `invalid_action`, whose fix shows the switch's actions; the other switches are set up.
- **Migration 1.2** turned a switch's own "cooling" into "from the thermostat": a cooling switch with a heating action and no thermostat now heats — the Repairs issue `switch_now_heats` says so (no silent change); it goes once the switch has a thermostat or no heating action.
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
- **Not responding**: each balancing cycle in which the switch is not as wanted counts an attempt (`failed_attempts` shows each at once); after `attempts` the switch is **deferred** for the entry's `recovery_timeout`, turned off once, and then tried again. It ends when the switch takes the wanted state (after the hysteresis).
- **In the rotation** only if none of these: a gone switch, a deferred switch and no temperature all free the thermostat's place for the others.

`ControlStateSensor` — `sensor.<thermostat>_control_state`, enum, diagnostic, on the thermostat's device: `switch_unavailable` > `unresponsive` > `no_temperature` > `normal`. Attributes `failed_attempts`, `next_attempt` (unrecorded). One `warning` when a problem starts, one `info` when it ends.

### Settings (options flow)

One set for all thermostats of the entry (the owner's choice), defaults: `attempts` 7, `tolerance_seconds` 60, `hysteresis_seconds` 60.

---

## Capacity Number (`number.py`)

`CapacityNumber` — how many thermostats may work at once, 0–100. **The one source of the capacity**: a power automation changes it, and a change shares the capacity again at once. The entry's `capacity` is only its initial value (the setup form); the reconfigure form has no capacity field (before DEV-008 its value was silently ignored, because the entity restores its last state). No entity category: it is operated.

---

## Balancing Coordinator (`balancer.py`)

`BalancingCoordinator` — one per climate entry, made before the platforms (they show its controls), started after them.

### Balance Cycle (`async_request_balance`: every 60 s, on a thermostat's change, a capacity change, a zone change and a control's new state)

A request runs the cycle at once; one asked for during a cycle makes one more after it. The cycle never waits for a switch: each command is its own task, one at a time per switch, so a slow switch (an IR remote) holds back neither the others nor the next decisions, and a switch's sequence may call a climate service. A control's new state (a problem confirmed or over, a deferral) runs a cycle at once, so the freed place goes to another thermostat in the same second.

### Load zones (the owner's rules, DEV-008 review 13)

Climate follows the zones of `bestiarium_mode` as the devices' balancer does. It reads the four zone sensors, found by their unique_id `power_load_{zone}` in `bestiarium_mode` (the whole contract between the two, [necessity](concepts/necessity.md) refinement 5): the one on is the zone; all four off and available is the pause; one unavailable is no data; none registered (no load limits) is "none", treated as green.

| Zone | Climate |
|---|---|
| green | the capacity applies; rotation; heaters start **one at a time** (the next a step of 10 s later, once the load shows the last) and **only if it fits**: load + heater power ≤ the red threshold — the rule of the devices' green strategy (DEV-006) |
| no load limits | the capacity and the rotation only |
| yellow | the ones heating go on; a rotation turns the one going out off but starts no other; no new starts |
| pause, no data, overload | nothing starts and nothing rotates; the ones heating go on (the overload's emergency queue of the devices turns off what it must) |
| red | no starts; every 5 s one heater goes off, the room that needs it least first (the smallest difference between the target and the temperature) |

**The fit check** (the owner's decision, DEV-008 review 15) is a safety net over the capacity: the rest of the load changes, the capacity does not. The load is the power sensor of `bestiarium_mode` (its entry's `ac_power_sensor`), the threshold the red zone sensor's `red_threshold`; an unknown one does not fit. The heater's power is the "Expected power" of the `bestiarium_devices` device whose relay is the thermostat's switch (its entry's `switch_entity_id`, the sensor by unique_id `<entry_id>_expected_power`); else the thermostat's `heater_power`; else 2000 W, a large heater as in devices. A start that does not fit waits, without a notification; a debug line tells the numbers.

Out of green, a switch wanted on but found off (the devices' load protection turned it off) is wanted off: it is no failed attempt and is not turned on again until green. A thermostat that stops wanting heat (idle, off) is turned off in every zone. The capacity entity (FR-019: an automation lowering it in red) stays a finer setting; the rules above work without it.

1. **Allocate.** The working thermostats are those in the rotation that want heat or cold. Every other one is not wanted on. If the capacity is short (the normal state of a home that needs Bestiarium Climate), the ones that worked least go first: working-time coefficient ascending (time on / minimum working time, the thermostat's own or the entry's), then the temperature difference descending. In a deficit a change fires `bestiarium_climate_device_rotated_in` / `_out`.
2. **Sync.** Each switch gets the state wanted (`ThermostatControl.to_command`); a command HA refuses counts as an attempt and the cycle goes on with the other switches — one switch never stops the others. The same command is repeated at most once a cycle; a new wanted state (the thermostat off and back to heat) is sent at once and starts the count of attempts again.

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
- Repairs: `switch_shared`, `switch_now_heats`, `invalid_action` (fixable: the switch's actions)
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

5. **Fair rotation via working_time_coefficient**: When demand exceeds capacity, devices that have worked the least get priority. The coefficient `actual_time / min_required_time` ensures each device gets at least `min_working_time` before being rotated out.

6. **Non-responding switch handling**: After `attempts` (default 7) cycles in which the switch is not as wanted, it is deferred for `recovery_timeout` (default 1 hour), turned off once and tried again; its thermostat's control state is "Not responding" and its place goes to the others (see [Thermostat control](#thermostat-control-controlpy-and-control-state-sensorpy)).

7. **On-demand rebalancing**: Beyond the 60s periodic cycle, thermostats trigger immediate rebalance when their hvac_action changes to IDLE or OFF. This ensures freed capacity is reallocated promptly.

8. **Action format**: Switch actions use HA's action dict format (`{"action": "domain.service", "target": {...}, "data": {...}}`). The `_execute_action` helper also handles single-item list wrappers.

9. **Duration storage**: Durations (min_working_time, recovery_timeout, min_cycle_duration) are stored as `{"hours": int, "minutes": int, "seconds": int}` dicts, converted to `timedelta` at runtime via `duration_dict_to_timedelta`.

10. **Capacity at runtime**: The capacity `number` entity is adjustable at runtime without reconfiguring. The balancer reads it fresh each cycle via `_get_capacity()`.
