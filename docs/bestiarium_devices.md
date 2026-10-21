# bestiarium_devices — Home Assistant Custom Integration

## Overview

`bestiarium_devices` is a Home Assistant custom integration (domain: `bestiarium_devices`, version 0.1.0) that manages switchable devices with **mode-aware load priorities** and **automatic power balancing**. It depends on `bestiarium_mode` for mode selection and power load zone signals.

Core capabilities:

1. **Device registration** — Two device types: *virtual* (combines any switch + power sensor into a new device) and *extended* (attaches priority management to an existing HA device).
2. **Load priority system** — Each device has a default priority (0–9999) and optional per-mode overrides. Special values: `0` = always off, `9999` = always on.
3. **Balancing coordinator** — A singleton `BalancingCoordinator` reacts to power load zone changes and mode switches, executing zone-specific strategies to turn devices on/off based on their priorities.
4. **"Allowed" switch** — One switch per device lets the family exclude it from balancing: off turns the relay off and the balancer never turns it on.
5. **Thermostat-driven devices** — A device option for relays that their own thermostat turns on: the balancer only turns them off.

Multiple config entries are allowed (one per device). The integration has a declared dependency on `bestiarium_mode`.

**Automation events**: The balancer fires HA bus events at zone changes, device turn-on/off, overload shutdowns, and mode application. See [Automation Events](events.md) for event types, payloads, and automation examples.

---

## File Structure

```
custom_components/bestiarium_devices/
├── __init__.py          # Entry setup/unload, coordinator lifecycle, device registry
├── manifest.json        # Integration metadata (depends on bestiarium_mode)
├── const.py             # Constants, config keys, entity IDs, timing
├── data.py              # Type alias for ConfigEntry
├── config_flow.py       # Multi-step config flow (virtual + extended device creation)
├── helpers.py           # Priority resolution, validation, utility functions
├── balancer.py          # BalancingCoordinator — zone strategies and periodic jobs
├── switch.py            # AllowedSwitch — per-device "Allowed" switch
└── translations/
    ├── en.json           # English translations
    └── uk.json           # Ukrainian translations

tests/custom_components/bestiarium_devices/
├── __init__.py
├── test_config_flow.py
├── test_helpers.py
├── test_balancer.py
├── test_switch.py
└── test_switch_split_device.py
```

---

## Platforms

```python
PLATFORMS: list[Platform] = [Platform.SWITCH]
```

The only entity is the per-device "Allowed" switch. Relays are controlled via service calls to existing switch entities.

---

## Config Entry Data Schema

Each device gets its own config entry. The data shape depends on device type:

### Virtual Device

```python
{
    "device_type": "virtual",
    "device_name": str,                   # User-provided name
    "switch_entity_id": str,              # Entity ID of the switch to control
    "power_sensor_entity_id": str,        # Entity ID of the power sensor (W)
    "load_priorities": {
        "default": int,                   # Default priority (0–9999)
        "Eco": int,                       # Optional per-mode override
        "Boost": int,                     # Optional per-mode override
        # ... one key per mode name
    },
    "mode_options_snapshot": list[str],   # Snapshot of priority keys at save time
    "overload_first_queue": bool,         # Turn off in first wave during overload
    "thermostat_driven": bool,            # Only turned off by the balancer (missing = False)
}
```

### Extended Device

```python
{
    "device_type": "extended",
    "device_id": str,                     # HA device registry ID
    "device_name": str,                   # From device registry (name_by_user or name)
    "switch_entity_id": str,              # Selected switch entity
    "power_sensor_entity_id": str,        # Selected power sensor entity
    "load_priorities": { ... },           # Same structure as virtual
    "mode_options_snapshot": list[str],
    "overload_first_queue": bool,
    "thermostat_driven": bool,
}
```

### Config Entry Options

```python
{
    "allowed": bool,   # State of the "Allowed" switch (missing = True)
}
```

Options hold runtime state set by the family, not configuration. No migration was needed for `thermostat_driven` or `allowed`: both are read with defaults (`False` / `True`), like `overload_first_queue` before them.

### Config Key Constants (`const.py`)

| Constant | Value |
|---|---|
| `DOMAIN` | `"bestiarium_devices"` |
| `DOMAIN_BESTIARIUM_MODE` | `"bestiarium_mode"` |
| `CONF_DEVICE_TYPE` | `"device_type"` |
| `CONF_DEVICE_NAME` | `"device_name"` |
| `CONF_DEVICE_ID` | `"device_id"` |
| `CONF_SWITCH_ENTITY_ID` | `"switch_entity_id"` |
| `CONF_POWER_SENSOR_ENTITY_ID` | `"power_sensor_entity_id"` |
| `CONF_LOAD_PRIORITIES` | `"load_priorities"` |
| `CONF_MODE_OPTIONS_SNAPSHOT` | `"mode_options_snapshot"` |
| `CONF_OVERLOAD_FIRST_QUEUE` | `"overload_first_queue"` |
| `CONF_THERMOSTAT_DRIVEN` | `"thermostat_driven"` |
| `CONF_ALLOWED` (options) | `"allowed"` |
| `CONF_DEFAULT_PRIORITY` | `"default"` |
| `DEVICE_TYPE_VIRTUAL` | `"virtual"` |
| `DEVICE_TYPE_EXTENDED` | `"extended"` |

### Priority Constants

| Constant | Value | Meaning |
|---|---|---|
| `LOAD_PRIORITY_MIN` | `0` | Device should be OFF in this mode |
| `LOAD_PRIORITY_MAX` | `9999` | Device should always be ON in this mode |
| `LOAD_PRIORITY_DEFAULT` | `5000` | Fallback when no priority is configured |
| `LOAD_PRIORITY_OFF` | `0` | Alias for min — device OFF |
| `LOAD_PRIORITY_ALWAYS_ON` | `9999` | Alias for max — always ON |

### Cross-Integration Unique IDs

`bestiarium_mode` entities are found in the entity registry by platform `bestiarium_mode` + `unique_id`, never by `entity_id`: entity_ids come from entity names (a fresh install names the overload sensor `binary_sensor.overload`) and users can rename them.

| Constant | Value |
|---|---|
| `MODE_SELECT_UNIQUE_ID` | `"select"` |
| `ZONE_SENSOR_UNIQUE_ID` | `"power_load_{zone}"` (`green`, `yellow`, `red`, `overload`) |

These unique_ids are a contract with `bestiarium_mode`; the end-to-end tests run against the real `bestiarium_mode` entities.

### Timing Constants

| Constant | Value | Usage |
|---|---|---|
| `GREEN_INTERVAL_SECONDS` | `60` | Green strategy periodic interval |
| `RED_INTERVAL_SECONDS` | `5` | Red strategy periodic interval |
| `OVERLOAD_WAIT_SECONDS` | `5` | Pause between first and second overload queue |

---

## Integration Lifecycle (`__init__.py`)

### Setup (`async_setup_entry`)

1. Initializes `hass.data[DOMAIN]` dict if needed
2. Creates and starts a shared `BalancingCoordinator` (stored at `hass.data[DOMAIN]["balancing_coordinator"]`) — one coordinator for all device entries
3. Forwards the `switch` platform (the "Allowed" switch; for virtual devices its device info creates the `identifiers={(DOMAIN, entry.entry_id)}` device)

There is **no update listener**: the "Allowed" switch updates the entry options at runtime without a reload; the reconfigure flow reloads explicitly with `async_update_reload_and_abort()`. Any future flow that changes the entry must reload explicitly too.

### Unload (`async_unload_entry`)

1. Unloads platforms
2. If this is the **last** entry being unloaded, stops the `BalancingCoordinator` and cleans up `hass.data[DOMAIN]`

### Device Removal (`async_remove_config_entry_device`)

Always returns `True` — HA handles entity cleanup automatically.

---

## Config Flow (`config_flow.py`)

Class: `BestiariumDevicesFlowHandler` (VERSION = 1)

### Prerequisites

The flow aborts with `"no_bestiarium_mode"` if no `bestiarium_mode` config entry exists.

### Steps

| Step | Purpose |
|---|---|
| `user` | Entry point — menu: "Create virtual device" or "Extend existing device" |
| `create_virtual` | Form: device name, switch entity, power sensor entity |
| `extend_device` | Form: device selector. Validates device has switch + power sensor |
| `select_entities` | Shown only when device has multiple switches or sensors. Selects which to use |
| `configure_priorities` | Form: default priority, optional per-mode priorities, overload_first_queue and thermostat_driven flags |
| `reconfigure` | Loads existing data, routes to appropriate form based on device type |

### Flow Logic for `extend_device`

1. Validates selected device is not a virtual device (aborts with `"already_configured"`)
2. Finds all enabled switches and power sensors on the device
3. If exactly one switch AND one sensor → skip to `configure_priorities`
4. If multiple of either → route through `select_entities`
5. If no switch → error `"no_switch"`; no power sensor → error `"no_power_sensor"`

### Reconfigure Flow

- **Virtual devices**: routes to `create_virtual` form (can change name, switch, sensor)
- **Extended devices with single entities**: routes directly to `configure_priorities`
- **Extended devices with multiple entities**: routes through `select_entities`

### Helper Functions (in `config_flow.py`)

| Function | Purpose |
|---|---|
| `get_bestiarium_modes(hass)` | Returns mode options list from the bestiarium_mode config entry |
| `get_mode_names(hass)` | Returns just the mode name strings |
| `find_power_sensors_for_device(hass, device_id)` | Finds validated power sensors on a device |
| `find_devices_with_switch(hass)` | Returns device IDs that have switch entities |
| `find_switches_for_device(hass, device_id)` | Returns enabled switch entity IDs for a device |
| `is_virtual_device(hass, device_id)` | Checks if device was created by this integration |
| `find_config_entry_for_device(hass, device_id)` | Finds config entry for a virtual device |

---

## Helpers (`helpers.py`)

### Priority Resolution

`get_current_priority(hass, device_id)` — The core priority lookup:

1. Reads the bestiarium_mode select state for current mode (`get_mode_state`)
2. Falls back to `LOAD_PRIORITY_DEFAULT` (5000) if mode entity is missing/unknown/unavailable
3. Finds the config entry matching `device_id` (virtual devices use `entry.entry_id` as device_id)
4. Returns mode-specific priority if configured, otherwise the `"default"` priority from the entry
5. Final fallback: `LOAD_PRIORITY_DEFAULT`

### bestiarium_mode Entity Lookup

- `mode_entity_id(hass, domain, unique_id)` — current entity_id of a `bestiarium_mode` entity from the entity registry (O(1) index lookup), or `None`
- `get_mode_state(hass)` — state of the mode select entity, or `None`

### Power Sensor Validation

`validate_power_sensor(hass, entity_id)` — Checks that entity exists, has `device_class=power`, and `unit_of_measurement=W`. Returns error string or `None`.

### Form Key Helpers

- `build_priority_form_key(mode_name)` → `f"priority_{mode_name}"`
- `extract_priorities_from_user_input(user_input, mode_names)` — Extracts and converts priority values from the config flow form. Default is always stored; per-mode values only stored when user provides them.

### Device ID Resolution

- `get_device_id_from_entry(entry)` — Virtual: `entry.entry_id`; Extended: `entry.data["device_id"]`
- `get_device_name_from_entry(entry)` — Returns `device_name` or falls back to device_id

---

## Balancing Coordinator (`balancer.py`)

Class: `BalancingCoordinator`

### Lifecycle

- **One coordinator** per HA instance, shared across all device config entries
- Created on first device entry setup, stored in `hass.data[DOMAIN]["balancing_coordinator"]`
- Stopped when last device entry is unloaded

### Active Zone Detection

```python
class ActiveZone(StrEnum):
    NONE = "none"
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    OVERLOAD = "overload"
```

`_determine_active_zone()` checks binary sensors in priority order: OVERLOAD > RED > YELLOW > GREEN > NONE.

Each read resolves the sensor's entity_id from the registry by unique_id at that moment (no cached entity_ids).

### Following Renamed Entities

State listeners need entity_ids, so `_subscribe()` tracks the zone sensors and the mode select by their current entity_ids and also tracks their entity registry updates (`async_track_entity_registry_updated_event`, which matches renames by `old_entity_id`). When one is renamed, the coordinator drops and re-creates the listeners with the new entity_ids. It does not re-evaluate the zone on a rename: the entity's state is unchanged, and the re-added entity's new state goes through the normal `_on_zone_changed` path (same zone → no `zone_changed` event).

This relies only on the registry update event, so it works both with the HA 2026.10 rename model (entity removed and re-added) and with in-place renames.

### Startup Behavior

1. Checks if power load binary sensors exist (at least one of the four has a state, looked up by unique_id)
2. If missing: logs warning, sends persistent notification, stays running but inactive
3. Subscribes to 4 zone binary sensors + mode select entity, and to their registry updates (renames)
4. Determines initial active zone
5. If initial zone is OVERLOAD → executes overload strategy immediately
6. Otherwise → starts periodic job (waits for first interval before executing)

### State Change Handling

**Zone changes** (`_on_zone_changed`):
- Recalculates active zone via `_determine_active_zone()`
- If zone changed: cancels old periodic job, handles new zone

**Mode changes** (`_on_mode_changed`):
- Calls `_apply_mode_priorities()` which iterates all device entries:
  - Priority `0` (OFF) → turns off the switch
  - Priority `9999` (ALWAYS_ON) → turns on the switch
  - Other priorities → no immediate action (handled by zone strategies)

### Zone Strategies

#### Green Strategy (`_execute_green_strategy`)

- **Trigger**: Periodic, every 60 seconds
- **Action**: Turn ON the **highest-priority OFF device** (one device per cycle)
- **Skips**: Devices already ON, devices with priority `0` (OFF)
- **Sorting**: Candidates sorted by priority descending

#### Red Strategy (`_execute_red_strategy`)

- **Trigger**: Periodic, every 5 seconds. Also runs once immediately on entering red zone.
- **Action**: Turn OFF the **lowest-priority ON device** (one device per cycle)
- **Skips**: Devices already OFF, devices with priority `9999` (ALWAYS_ON)
- **Sorting**: Candidates sorted by priority ascending

#### Turning Devices On: `balancer_may_turn_on(entry)`

The balancer turns a relay on in exactly three places, all gated by `helpers.balancer_may_turn_on(entry)` — "Allowed" is on **and** the device is not `thermostat_driven`:

| Place | Rule |
|---|---|
| Green strategy | not a candidate otherwise (so it never blocks lower-priority devices) |
| Mode priority `9999` (ALWAYS_ON) in `_apply_mode_priorities` | skipped otherwise ("Allowed" off beats "always on") |
| "Allowed" turned back on (`async_allowed_changed`) | see below |

Turning off is unchanged: red strategy, overload (both queues) and mode priority `0` turn off any ON device, including disallowed ones switched on by hand and thermostat-driven ones.

#### "Allowed" Switch Changes (`async_allowed_changed`)

| Change | Green zone | Other zones / inactive balancer |
|---|---|---|
| Allowed → off | relay off now (if not off) | relay off now (if not off) |
| Allowed → on | relay on now if it is off, its current mode priority is not `0` and it is not thermostat-driven | nothing; it waits for its turn in green |

Both fire `bestiarium_devices_device_turned_off` / `_turned_on` with `strategy: "allowed"`. A relay without a state is left alone.

#### Yellow Strategy

- **No action** — yellow is a waiting/warning state. No periodic job runs.

#### Overload Strategy (`_execute_overload_strategy`)

- **Trigger**: Immediately when overload zone activates (no periodic job)
- **Action**: **Two-step emergency shutdown**:
  1. Turn off all devices with `overload_first_queue=True` (first queue)
  2. Check if the overload has cleared
  3. If not cleared: wait `OVERLOAD_WAIT_SECONDS` (5s), check again
  4. If still not cleared: turn off all remaining devices (second queue)
- **Fail-safe**: inside this sequence the overload counts as cleared **only if the overload sensor reports `off`** (`_overload_cleared`). A missing, `unknown` or `unavailable` sensor state means the overload may still be going on, so the second queue is shut down too — turning off more is safer than overloading the supply. Active zone detection outside the sequence is unchanged.
- **Backward compat**: If no devices have `overload_first_queue=True`, turns off ALL devices immediately (no two-step, no wait)
- **Ignores** priority — all devices are turned off including ALWAYS_ON

### Zone Change Behavior

| Transition | Behavior |
|---|---|
| Any → GREEN | Start 60s periodic job (waits for first interval) |
| Any → YELLOW | Stop periodic job, wait |
| Any → RED | Start 5s periodic job + execute red strategy once immediately |
| Any → OVERLOAD | Stop periodic job, execute overload immediately |
| Zone unchanged | Timer resets (restarts from zero) on every zone binary sensor change |

---

## "Allowed" Switch (`switch.py`)

Class: `AllowedSwitch(SwitchEntity)` — one per config entry.

| Property | Value |
|---|---|
| `unique_id` | `{entry_id}_allowed` |
| Name | `has_entity_name`, translation `allowed` — "Allowed" / "Дозволено" (shown as "<device> Allowed") |
| State | `entry.options["allowed"]` (default `True`); written with `async_update_entry`, no reload |
| Category | none — meant for dashboards |

**Device placement:**

- **Virtual device** — its own Bestiarium device (`identifiers={(DOMAIN, entry_id)}`, model "Virtual Device").
- **Extended device** — the device of the relay (`switch_entity_id`), linked via `entity.device_entry = async_entity_id_to_device(...)`, as core helper integrations do. Since HA 2026.8 a device belongs to a single config entry, so the switch is linked to, not owned by, that device: no extra device is created, the switch shows on the real device page and follows its area. The relay's device is used rather than `data.device_id` because that id can be a pre-2026.8 composite id (the 2026.8 migration moved the relay to the right split).
- **Relay without a device / not in the registry** — no device; translation `allowed_standalone`, "{device_name} Allowed".

The entity_id is generated by HA from the area, device and entity name (e.g. `switch.bathroom_heating_floor_allowed`). If the relay is moved to another device, the switch follows after a reload.

## Translations

Two locales: `en` (English) and `uk` (Ukrainian).

Translation keys cover:
- Config flow steps, menus, field labels, field descriptions
- Validation errors: `no_bestiarium_mode`, `no_switch`, `no_power_sensor`, `invalid_switch`, `invalid_power_sensor`, `name_required`, `required`
- Abort reasons: `not_implemented`, `already_configured`, `no_bestiarium_mode`, `no_devices_with_switch`, `reconfigure_successful`
- Config flow field `thermostat_driven` (label + description)
- Entity names: `Allowed`, `{device_name} Allowed`, `Default Load Priority`, `{mode_name} Load Priority`

---

## Test Coverage

### `test_config_flow.py`
- Abort without bestiarium_mode
- Create virtual device: full flow with all priorities, only default priority, partial priorities
- Virtual device validation: empty name, invalid power sensor
- Extend existing device: full flow with auto-detected switch/sensor
- Extend device errors: no power sensor, no switch (disabled switch), already-configured virtual device redirect
- Multiple switches/sensors: combined selection step for multiple switches, multiple sensors, both multiple
- Reconfigure: virtual device (change name/switch/sensor), extended device (change priorities), preserves overload_first_queue
- Reconfigure extended device with multiple entities routes through select_entities

### `test_helpers.py`
- `build_priority_form_key`: basic and with spaces
- `extract_priorities_from_user_input`: all provided, none provided (only default), partial, missing default fallback
- `get_current_priority`: mode-specific priority, fallback to default, no entry for device, missing/unknown/unavailable mode select, empty priorities dict, virtual device uses entry_id

### `test_balancer.py`
- **Init/Cleanup**: start/stop, initial zone detection, double start warning, stop when not running, missing sensors still running
- **Mode changes**: OFF priority turns off switch, ALWAYS_ON priority turns on switch
- **Green strategy**: highest priority device turns on, already-on skipped, OFF priority skipped, ALWAYS_ON turns on
- **Red strategy**: lowest priority turns off, already-off skipped, ALWAYS_ON skipped
- **Overload strategy**: all devices off including ALWAYS_ON, contrast with red (ALWAYS_ON)
- **Two-step overload**: first then second after wait, cleared after first queue skips second, cleared during wait skips second, all in first queue, no first queue devices turns off all immediately, mixed queues with persistent overload
- **Overload fail-safe**: overload sensor missing / `unknown` / `unavailable` after the first queue or during the wait → second queue still runs
- **bestiarium_mode entities by unique_id** (real `bestiarium_mode`): fresh install (`binary_sensor.overload`) runs the emergency shutdown, renamed red sensor still runs the red strategy, renaming the active zone sensor keeps the zone, renamed mode select drives priorities, no AC load limits → inactive + notification
- **Zone transitions**: green 60s periodic job, red 5s periodic job, zone change stops old/starts new job, overload executes immediately

---

## Design Notes & Gotchas

1. **Multi-entry, single coordinator**: Unlike bestiarium_mode (singleton), this integration allows multiple config entries (one per device). The `BalancingCoordinator` is shared — created on first entry, destroyed on last unload.

2. **One entity per device**: The only entity is the "Allowed" switch. Relays are controlled through service calls (`switch.turn_on`, `switch.turn_off`) and state reads.

3. **Device ID semantics**: Virtual devices use `entry.entry_id` as their device ID (since they have no pre-existing HA device). Extended devices use the actual HA `device_id`. This distinction matters for priority lookups.

4. **Priority resolution chain**: Mode-specific → default from entry → `LOAD_PRIORITY_DEFAULT` (5000). The mode select entity must be in a valid state for mode-specific priorities to apply.

5. **One device per cycle**: Green and red strategies each affect only ONE device per execution cycle. This gradual approach prevents power oscillation.

6. **ALWAYS_ON immunity**: Red strategy skips `9999` priority devices. Only overload can force them off.

7. **Overload two-step design**: The `overload_first_queue` flag allows designating less-critical devices for immediate shutdown while giving more-critical devices a 5-second grace period. If the first queue resolves the overload, the second queue stays on.

8. **Mode options snapshot**: `mode_options_snapshot` stores the list of priority keys at config save time. This tracks which modes existed when priorities were configured.

9. **Power sensor validation**: Must have `device_class=power` and `unit_of_measurement=W`. The validation runs at config flow time, not continuously.

10. **Missing sensors graceful degradation**: If bestiarium_mode's binary sensors don't exist (AC load limits not configured), the coordinator starts but stays inactive. A persistent notification informs the user. Sensors that appear later (limits configured on a running system) are picked up after the devices integration reloads or HA restarts.
