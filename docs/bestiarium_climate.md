# bestiarium_climate — Home Assistant Custom Integration

## Overview

`bestiarium_climate` is a Home Assistant custom integration (domain: `bestiarium_climate`, version 0.1.0) that provides **capacity-limited rotation of heating/cooling devices** using virtual thermostats and virtual switches. It depends on `bestiarium_mode`.

Core capabilities:

1. **Virtual switches** — Action-based switch entities that execute HA service calls (heating action, cooling action, off action) and derive their on/off state from a power sensor.
2. **Virtual thermostats** — Climate entities with hysteresis-based temperature demand, preset modes, and state restoration. Thermostats do NOT directly control switches — they set an `expected_switch_state` flag that the balancer reads.
3. **Capacity-limited balancing** — A `BalancingCoordinator` runs every 60 seconds, allocating a limited number of simultaneous device slots to thermostats with the most demand, rotating devices fairly based on working time.
4. **Capacity number entity** — A runtime-adjustable `number` entity (0–100) that controls how many devices can run simultaneously, with state restoration.

The integration uses **HA subentries** — switches and thermostats are added as subentries under a single parent config entry.

**Automation events**: The balancer fires HA bus events at capacity exhaustion, device rotation, switch failure, and recovery. See [Automation Events](events.md) for event types, payloads, and automation examples.

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
PLATFORMS = [Platform.CLIMATE, Platform.NUMBER, Platform.SWITCH]
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
    "mode": "heating" | "cooling",       # ClimateMode enum
    "heating_action": dict | None,       # HA action dict (service call) for heating
    "cooling_action": dict | None,       # HA action dict (service call) for cooling
    "off_action": dict,                  # HA action dict (service call) for turning off
    "power_entity": str,                 # Entity ID of power sensor (device_class=power)
    "min_power": float,                  # Watts threshold — above = "on" (default 0.0)
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
| `MAX_SWITCH_RETRIES` | `7` |
| `EVENT_SWITCH_FAILED` | `"bestiarium_climate_switch_failed"` |
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

Single step: name, mode (heating/cooling), power_entity, min_power, heating_action, cooling_action, off_action.

Validation:
- Name required
- Heating mode requires `heating_action`; cooling mode requires `cooling_action`
- `off_action` always required
- `power_entity` required
- `min_power` must be a number

Reconfigure reuses the same form, merging existing subentry data to preserve both actions.

#### `ThermostatSubentryFlowHandler`

Two-step flow:
1. **basic** — name, temperature_sensor, target_switch, thermostat_mode, cold/hot tolerance, optional min_cycle_duration, min_temp, max_temp
2. **presets** — optional preset temperatures (away, comfort, eco, home, sleep, activity)

Both steps accumulate into `_options` dict. Reconfigure loads existing subentry data into `_options` before showing forms.

---

## Virtual Switch (`switch.py`)

Class: `VirtualSwitch(SwitchEntity)`

### Key Behavior

- **Turn on**: Executes `heating_action` (if mode=heating) or `cooling_action` (if mode=cooling) via `_execute_action`
- **Turn off**: Executes `off_action` via `_execute_action`
- **State derivation**: `is_on` is derived from the power sensor — if power reading >= `min_power`, switch is ON; otherwise OFF
- **Power tracking**: Subscribes to power sensor state changes; updates `is_on` reactively

### Action Execution (`_execute_action`)

Parses an HA action dict: extracts `action` (e.g. `"switch.turn_on"`), splits into domain + service, merges `target` into `data`, calls `hass.services.async_call` with `blocking=False`.

Handles both dict and single-item list formats for the action payload.

### Device Info

Each switch subentry creates its own device:
- `identifiers`: `{(DOMAIN, subentry.subentry_id)}`
- `model`: `"Virtual Switch"`
- `entry_type`: `DeviceEntryType.SERVICE`

---

## Virtual Thermostat (`climate.py`)

Class: `VirtualThermostat(ClimateEntity, RestoreEntity)`

### Key Behavior

- **Does NOT directly control switches** — sets `expected_switch_state` flag, which the balancer reads
- **Temperature demand**: Uses hysteresis with cold/hot tolerance
  - Heating: demand when `current_temp < target_temp - cold_tolerance`
  - Cooling: demand when `current_temp > target_temp + hot_tolerance`
- **HVAC modes**: `[HEAT, OFF]` for heating mode; `[COOL, OFF]` for cooling mode
- **HVAC action**: Computed from temperature demand — HEATING/COOLING when demand exists, IDLE when no demand, OFF when mode is OFF
- **Presets**: Supports 6 preset keys (away, comfort, eco, home, sleep, activity). Selecting a preset saves the current target temp and applies the preset temp. Clearing restores saved temp.
- **Rebalance trigger**: When hvac_action changes to IDLE or OFF, triggers an immediate balancer cycle via `_trigger_rebalance()`

### State Restoration

Restores from `RestoreEntity`:
- hvac_mode, target_temperature, preset_mode, saved_temperature
- expected_switch_state, expected_switch_state_changed_at
- switch_state_change_tries, switch_not_responding, recovery_time

### Extra State Attributes

| Attribute | Type | Description |
|---|---|---|
| `expected_switch_state` | `bool` | Whether the balancer wants the target switch ON |
| `expected_switch_state_changed_at` | `str` (ISO) | When expected state last changed |
| `switch_state_change_tries` | `int` | Consecutive failed sync attempts |
| `switch_not_responding` | `bool` | True when max retries exceeded |
| `recovery_time` | `str` (ISO) or null | When recovery period ends |

### Setter Methods (Called by Balancer)

The thermostat exposes setter methods for the balancer to mutate state:
- `set_expected_switch_state(value: bool)`
- `set_switch_state_change_tries(value: int)`
- `set_switch_not_responding(value: bool)`
- `set_recovery_time(value: datetime | None)`

### Device Info

Each thermostat subentry creates its own device:
- `identifiers`: `{(DOMAIN, subentry.subentry_id)}`
- `model`: `"Virtual Thermostat"`
- `entry_type`: `DeviceEntryType.SERVICE`

---

## Capacity Number (`number.py`)

Class: `CapacityNumber(NumberEntity, RestoreEntity)`

- **Range**: 0–100, step 1
- **Translation key**: `"capacity"`
- **Unique ID**: `f"{entry.entry_id}_capacity"`
- **State restore**: Restores last value on startup; falls back to config entry's `capacity` value
- **Runtime adjustable**: User can change capacity via the HA UI; balancer reads it each cycle

---

## Balancing Coordinator (`balancer.py`)

Class: `BalancingCoordinator`

### Lifecycle

- One coordinator **per config entry** (not shared)
- Runs every 60 seconds (`BALANCING_INTERVAL_SECONDS`)
- Also triggered on-demand by thermostats when hvac_action changes to IDLE/OFF

### Balance Cycle (`_async_balance`)

Each cycle runs four phases in order:

#### Phase 1: Recovery (`_handle_recovery`)

For each thermostat with `recovery_time` in the past:
- If target switch is still ON: send turn_off, increment tries. If tries >= `MAX_SWITCH_RETRIES` (7), fire `EVENT_SWITCH_FAILED`.
- If target switch is OFF: clear `switch_not_responding`, `recovery_time`, and tries (recovery complete).

#### Phase 2: Turn Off Idle (`_turn_off_idle`)

For each thermostat with `hvac_action` IDLE or OFF and `expected_switch_state` True:
- Set `expected_switch_state = False`, reset tries to 0.

This frees capacity slots for thermostats that actually have demand.

#### Phase 3: Calculate Expected State (`_calculate_expected_state`)

Collects all thermostats with `hvac_action` HEATING or COOLING and `switch_not_responding` False.

Three strategies based on capacity vs. demand:

| Condition | Strategy |
|---|---|
| `capacity <= 0` | Turn off all working thermostats |
| `capacity >= count` | Turn on all working thermostats |
| `capacity < count` (deficit) | Sort by working_time_coefficient ASC then temperature_diff DESC; allocate top `capacity` slots |

**Working time coefficient**: `actual_working_seconds / min_required_seconds`. Lower values = less time worked = higher priority for getting a slot. This drives fair rotation.

**Temperature diff**: `abs(target - current)`. Higher diff = more demand = tiebreaker when coefficients are equal.

#### Phase 4: Sync Switches (`_sync_switches`)

For each thermostat (skipping `switch_not_responding`):
- If actual switch state matches expected: reset tries to 0
- If mismatch: send turn_on/turn_off, increment tries
- If tries >= `MAX_SWITCH_RETRIES` (7): fire `EVENT_SWITCH_FAILED`, force turn off, mark `switch_not_responding = True`, set `recovery_time = now + recovery_timeout`, clear expected state

### Capacity Reading (`_get_capacity`)

1. Tries `number.bestiarium_climate_capacity` entity
2. Falls back to entity registry lookup by unique_id `f"{entry_id}_capacity"`
3. Final fallback: `DEFAULT_CAPACITY` (1)

### Thermostat Discovery (`_get_thermostats`)

Reads from `hass.data[CLIMATE_DATA_COMPONENT]`, filters for `VirtualThermostat` instances belonging to this config entry.

### Event: `bestiarium_climate_switch_failed`

Fired when a switch exceeds `MAX_SWITCH_RETRIES` (during sync or recovery). Payload:

```python
{
    "entity_id": str,            # thermostat entity_id
    "thermostat": str,           # same
    "switch_entity_id": str,     # target_switch entity_id
}
```

---

## Translations

Three locale files: `strings.json` (default), `en.json`, `uk.json`.

Coverage:
- Main config flow: user and reconfigure steps
- Switch subentry flow: user and reconfigure steps
- Thermostat subentry flow: basic and presets steps
- Validation errors: `invalid_capacity`, `name_required`, `required`, `invalid_min_power`
- Abort reasons: `reconfigure_successful`
- Entity names: `Capacity` (number entity)

---

## Test Coverage

### `test_config_flow.py`
- Create main entry with custom values and defaults
- Reconfigure main entry
- Switch subentry: create (heating mode), validation (heating requires heating_action, cooling requires cooling_action)
- Thermostat subentry: two-step flow (basic + presets)

### `test_climate.py`
- Thermostat creation from subentry
- Temperature tracking updates current_temp and hvac_action
- IDLE hvac_action when temp meets target
- Extra state attributes present
- Set hvac_mode updates state

### `test_switch.py`
- Switch creation from subentry
- Turn on executes heating_action
- Turn off executes off_action
- Power below min_power = off; above = on

### `test_number.py`
- Capacity number creation, initial value from config
- Set value updates state
- Restore fallback to config entry value

### `test_balancer.py`
- Coordinator start/stop
- `_turn_off_idle`: clears expected when hvac_action is IDLE
- `_calculate_expected_state`: capacity=0 turns off all
- `_sync_switches`: resets tries when match, calls turn_on when mismatch
- `_handle_recovery`: clears not_responding when switch is off

---

## Design Notes & Gotchas

1. **Indirect switch control**: Thermostats NEVER directly call switch services. They set `expected_switch_state`, and the balancer's `_sync_switches` phase does the actual switching. This decoupling enables capacity limiting.

2. **Subentry architecture**: Unlike the other bestiarium integrations, this one uses HA's `ConfigSubentryFlow` API. Switches and thermostats are subentries under one parent config entry, not separate config entries.

3. **One coordinator per entry**: Each config entry gets its own `BalancingCoordinator` (unlike `bestiarium_devices` which shares one). The coordinator is tied to the entry's capacity and timing settings.

4. **Power-derived switch state**: `VirtualSwitch.is_on` comes from the power sensor, not from tracking on/off calls. This means the switch reflects actual device behavior (is it consuming power?) rather than optimistic state.

5. **Fair rotation via working_time_coefficient**: When demand exceeds capacity, devices that have worked the least get priority. The coefficient `actual_time / min_required_time` ensures each device gets at least `min_working_time` before being rotated out.

6. **Non-responding switch handling**: After 5 failed sync attempts, the switch is marked `not_responding`, forced off, and enters a recovery period (`recovery_timeout`, default 1 hour). During recovery, the balancer periodically retries turning the switch off. The `bestiarium_climate_switch_failed` event can be used for automations/notifications.

7. **On-demand rebalancing**: Beyond the 60s periodic cycle, thermostats trigger immediate rebalance when their hvac_action changes to IDLE or OFF. This ensures freed capacity is reallocated promptly.

8. **Action format**: Switch actions use HA's action dict format (`{"action": "domain.service", "target": {...}, "data": {...}}`). The `_execute_action` helper also handles single-item list wrappers.

9. **Duration storage**: Durations (min_working_time, recovery_timeout, min_cycle_duration) are stored as `{"hours": int, "minutes": int, "seconds": int}` dicts, converted to `timedelta` at runtime via `duration_dict_to_timedelta`.

10. **Capacity at runtime**: The capacity `number` entity is adjustable at runtime without reconfiguring. The balancer reads it fresh each cycle via `_get_capacity()`.
