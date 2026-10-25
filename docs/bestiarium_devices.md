# bestiarium_devices — Home Assistant Custom Integration

## Overview

`bestiarium_devices` is a Home Assistant custom integration (domain: `bestiarium_devices`, version 0.1.0) that manages switchable devices with **mode-aware load priorities** and **automatic power balancing**. It depends on `bestiarium_mode` for mode selection and power load zone signals.

Core capabilities:

1. **Device registration** — Two device types: *virtual* (combines any switch + power sensor into a new device) and *extended* (attaches priority management to an existing HA device).
2. **Load priority system** — Each device has a default priority (0–9999) and optional per-mode overrides. Special values: `0` = always off, `9999` = always on.
3. **Balancing coordinator** — A singleton `BalancingCoordinator` reacts to power load zone changes and mode switches, executing zone-specific strategies to turn devices on/off based on their priorities.
4. **"Allowed" switch** — One switch per device lets the family exclude it from balancing: off turns the relay off and the balancer never turns it on.
5. **Thermostat-driven devices** — A device option for relays that their own thermostat turns on: the balancer only turns them off.
6. **Headroom** — The balancer turns a device on only if the grid load plus the device's expected power stays at or below the red zone threshold. The expected power is learned from the device's runs (see [Expected Power](#expected-power-consumptionpy-power_memorypy)).
7. **Device control** — The balancer waits for each device's own response time, defers a device that does not respond so the queue goes on, and shows confirmed problems as a "Control state" sensor and "Control events" (see [Device Control](#device-control-controlpy)). Its parameters are the device's options.

Multiple config entries are allowed (one per device). The integration has a declared dependency on `bestiarium_mode`.

**Automation events**: The balancer fires HA bus events at zone changes, device turn-on/off, overload shutdowns, and mode application. See [Automation Events](events.md) for event types, payloads, and automation examples. The events are also described for the HA logbook (`logbook.py`).

**Concepts**: problems, settings and device stability follow the Bestiarium concepts in [`concepts/`](concepts/README.md) — [problem reporting](concepts/problem-reporting.md), [settings vs entities](concepts/settings-vs-entities.md), [stability and adaptation](concepts/stability.md).

---

## File Structure

```
custom_components/bestiarium_devices/
├── __init__.py          # Entry setup/unload, coordinator lifecycle, device registry
├── manifest.json        # Integration metadata (depends on bestiarium_mode)
├── const.py             # Constants, config keys, entity IDs, timing, ControlSettings, event payloads
├── control.py           # DeviceControl, ResponseMemory — control state of each device
├── event.py             # ControlEventEntity — "Control events"
├── data.py              # Type alias for ConfigEntry
├── config_flow.py       # Config flow (virtual + extended), reconfigure (re-pointing), settings (options)
├── repairs.py           # Repairs: a gone relay or power sensor, its fix flow; renames followed
├── helpers.py           # Priority resolution, validation, utility functions
├── balancer.py          # BalancingCoordinator — the zone, subscriptions, periodic jobs
├── strategies.py        # Strategies — green, red, overload, mode, "Allowed"
├── consumption.py       # Runs, stable power, memory eviction, estimate (no HA imports)
├── power_memory.py      # PowerMemory (Store), live run tracking, recorder import
├── entity.py            # BestiariumDeviceEntity — placement shared by all entities
├── switch.py            # AllowedSwitch — per-device "Allowed" switch
├── sensor.py            # ExpectedPowerSensor — "Expected power"
├── logbook.py           # Logbook descriptions of the balancer's bus events
└── translations/
    ├── en.json           # English translations
    └── uk.json           # Ukrainian translations

tests/custom_components/bestiarium_devices/
├── __init__.py
├── test_config_flow.py
├── test_helpers.py
├── test_balancer.py
├── test_consumption.py
├── test_headroom.py
├── test_logbook.py
├── test_power_entities.py
├── test_power_memory.py
├── test_switch.py
└── test_switch_split_device.py
```

---

## Platforms

```python
PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]
```

Each device gets six entities: the "Allowed" switch, three power entities (see [Power Entities](#power-entities)) and two diagnostic control entities, "Control state" and "Control events" (see [Device Control](#device-control-controlpy)). Relays are controlled via service calls to existing switch entities.

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
    "overload_first_queue": bool,
    "thermostat_driven": bool,
}
```

### Config Entry Options

```python
{
    "allowed": bool,   # State of the "Allowed" switch (missing = True)
    "estimated_power_w": int,  # "Estimated power" in the settings form; 0 or missing = not set
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
| `CONF_MODE_OPTIONS_SNAPSHOT` | `"mode_options_snapshot"` (only for migration 1.4, which removes it) |
| `CONF_OVERLOAD_FIRST_QUEUE` | `"overload_first_queue"` |
| `CONF_THERMOSTAT_DRIVEN` | `"thermostat_driven"` |
| `CONF_ALLOWED` (options) | `"allowed"` |
| `CONF_ESTIMATED_POWER` (options) | `"estimated_power_w"` |
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

### One coordinator (`async_setup`)

HA calls `async_setup` once, before any entry of the integration is set up (`CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)`). It creates and starts the one `BalancingCoordinator` (stored at `hass.data[DOMAIN]["balancing_coordinator"]`) and stops it when HA stops. It lives as long as HA, with or without device entries.

Before DEV-007 each entry created the coordinator if `hass.data` had none yet, and stored it only after its start awaited the power memory: the entries HA set up meanwhile each started their own (16–17 balancers on the owner's HA). Creating it in `async_setup` removes the check that could race.

### Setup (`async_setup_entry`)

1. Registers the device with the coordinator (`async_add_device`): its control state follows the relay and power sensor until the entry unloads
2. Starts tracking the device's runs (`async_track_runs`) and, if its memory was never filled and the recorder is loaded, imports its history in a background task (`async_import_history`)
4. Forwards the platforms (for virtual devices the entities' device info creates the `identifiers={(DOMAIN, entry.entry_id)}` device)

`async_remove_entry` drops the device's power memory and response memory. The manifest lists `recorder` in `after_dependencies`, so the recorder is set up before the device entries.

There is **no update listener**: the "Allowed" switch updates the entry options at runtime without a reload; the reconfigure flow reloads explicitly with `async_update_reload_and_abort(..., reason="reconfigure_successful")` — the explicit reason makes HA show this integration's translated message instead of the core one (which has no Ukrainian text yet). Any future flow that changes the entry must reload explicitly too.

### Unload (`async_unload_entry`)

Unloads the platforms; the device's control stops with the entry (`entry.async_on_unload`). The coordinator keeps running.

### Device Removal (`async_remove_config_entry_device`)

Always returns `True` — HA handles entity cleanup automatically.

---

### Migration (`async_migrate_entry`)

| Version | Change |
|---|---|
| 1.1 → 1.2 | An extended device whose `device_id` names no device any more — a pre-2026.8 composite id (HA split shared devices per config entry) or a deleted device — gets the device of its relay (`switch_entity_id`). Detected with `async_get(device_id, include_composite_devices=False)`. Only `device_id` changes; everything else in `data` and `options` stays. Left unchanged: virtual devices, live device ids, entries whose relay is not in the registry, and entries whose relay device is already used by another entry (logged as a warning naming both). |

## Config Flow (`config_flow.py`)

Class: `BestiariumDevicesFlowHandler` (VERSION = 1, MINOR_VERSION = 2)

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
| `pick_device` | Reconfigure of an extended device: its device (another one re-points it) and "Reset the learned consumption" |

### Flow Logic for `extend_device`

1. Validates selected device is not a virtual device (aborts with `"already_configured"`)
2. Finds all enabled switches and power sensors on the device
3. If exactly one switch AND one sensor → skip to `configure_priorities`
4. If multiple of either → route through `select_entities`
5. If no switch → error `"no_switch"`; no power sensor → error `"no_power_sensor"`

Entities of this integration (the "Allowed" switches, which sit on the relay's device) and entities with an `entity_category` (`config` / `diagnostic`: settings of a device, such as the knobs of a test socket) are never relay or power-sensor candidates: one predicate, `_may_be_relay_or_sensor(entity)`, decides. The device lookups (`find_switches_for_device`, `find_power_sensors_for_device`, `find_devices_with_switch`) go through `_candidate_entities(hass, domain)`; `select_entities` offers only the device's own candidates (`include_entities`), and the virtual-device relay and power selectors exclude the rest (`exclude_entities=non_candidate_entity_ids(hass, domain)`).

### Reconfigure Flow

- **Virtual devices**: routes to `create_virtual` form (can change name, switch, sensor; "Reset the learned consumption"); another switch is another connection, so the response time is forgotten
- **Extended devices**: `pick_device` first (default: the current device), then `select_entities` if the device has several switches or sensors, then `configure_priorities`

**Re-pointing (DEV-008).** Picking another device moves the entry to it (SmartThings to Matter: the same appliance, a new device in HA). Only `device_id`, `switch_entity_id` and `power_sensor_entity_id` change in `entry.data`; priorities, "Allowed", the settings, the name and the learned consumption stay, because they belong to the entry, not to the device. The entities keep their `unique_id` and `entity_id` and move to the new device's page on the reload. The **response time is always forgotten** on another device (it belongs to the connection, cloud vs local); "Reset the learned consumption" (`reset_power_memory`, not stored) also empties the power memory. The checks are those of `extend_device` (`RelaySteps._use_device`): the device has a switch and a power sensor. `RelaySteps` (`pick_device`, `select_entities`) is shared with the Repairs fix flow.

### Helper Functions (in `config_flow.py`)

| Function | Purpose |
|---|---|
| `get_bestiarium_modes(hass)` | Returns mode options list from the bestiarium_mode config entry |
| `get_mode_names(hass)` | Returns just the mode name strings |
| `find_power_sensors_for_device(hass, device_id)` | Finds validated power sensors on a device |
| `find_devices_with_switch(hass)` | Returns device IDs that have switch entities |
| `find_switches_for_device(hass, device_id)` | Returns enabled switch entity IDs for a device (not this integration's own) |
| `non_candidate_entity_ids(hass, domain)` | Returns the entity IDs of a domain that may not be a relay or power sensor (this integration's own, and those with an `entity_category`) |
| `is_virtual_device(hass, device_id)` | Checks if device was created by this integration |
| `find_config_entry_for_device(hass, device_id)` | Finds config entry for a virtual device |

---

## Helpers (`helpers.py`)

### Priority Resolution

`get_current_priority(hass, entry)` — The core priority lookup, read from the device's own config entry:

1. Reads the bestiarium_mode select state for current mode (`get_mode_state`)
2. Returns the entry's priority for that mode if configured, otherwise its `"default"` priority — also when the mode select is missing, unknown or unavailable (before DEV-008 stage B every device then got 5000)
3. Final fallback: `LOAD_PRIORITY_DEFAULT` (an entry without priorities)

Without the mode select, once HA has started, the Repairs issue `no_mode_select` says so; it goes when the select appears.

It takes the entry, not a `device_id`: two entries can share a device (a two-relay device extended twice), and each must keep its own priority.

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
- Created in `async_setup`, stored in `hass.data[DOMAIN]["balancing_coordinator"]`, stopped with HA
- The strategies live in `strategies.py` (`Strategies`); the coordinator decides when they run

### Active Zone Detection

```python
class ActiveZone(StrEnum):
    NO_DATA = "no_data"
    PAUSE = "pause"
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    OVERLOAD = "overload"
```

`_determine_active_zone()` checks binary sensors in priority order: OVERLOAD > RED > YELLOW > GREEN. None on: **pause** (green on pause, the sensors available) or **no data** (unavailable). Neither runs a strategy. The zone itself is decided by `bestiarium_mode` ([its zone model](bestiarium_mode.md#zones)).

Each read resolves the sensor's entity_id from the registry by unique_id at that moment (no cached entity_ids).

### Following Renamed Entities

State listeners need entity_ids, so `_subscribe()` tracks the zone sensors and the mode select by their current entity_ids and also tracks their entity registry updates (`async_track_entity_registry_updated_event`, which matches renames by `old_entity_id`). When one is renamed, the coordinator drops and re-creates the listeners with the new entity_ids. It does not re-evaluate the zone on a rename: the entity's state is unchanged, and the re-added entity's new state goes through the normal `_on_zone_changed` path (same zone → nothing to do).

This relies only on the registry update event, so it works both with the HA 2026.10 rename model (entity removed and re-added) and with in-place renames.

### Startup Behavior

1. Listens to entity registry creations: zone sensors that appear later (AC load limits set on a running system) bring it alive without a restart; each new mode entity re-creates the listeners
2. If no zone sensor exists yet (by unique_id): once HA has started, the Repairs issue `no_load_zones` (not fixable here: the text says where to set the limits); deleted when the zones appear. No notification
3. Subscribes to 4 zone binary sensors + mode select entity, and to their registry updates (renames)
4. Determines initial active zone
5. If initial zone is OVERLOAD → executes overload strategy immediately
6. Otherwise → starts periodic job (waits for first interval before executing)
7. Zone sensors removed (the load limits removed) → it stops following them, the zone is no data, and `no_load_zones` again

### State Change Handling

**Zone changes** (`_on_zone_changed`):
- Recalculates active zone via `_determine_active_zone()`
- If zone changed: cancels old periodic job, handles new zone

**After HA has started** the mode is applied again (`async_apply_mode(restart=True)`, the owner's choice: restarts are rare, and the mode's duties do not survive one); **so is a device entry reloaded after the start** (`only=` that entry; the integration reloaded: each of its devices), but not a reload of the modes' settings. A relay not off at the start — on, or still unknown (a cloud relay) — gets the turn-off and the mode's duty. Only the devices that are on get a turn-off, and only the always-on ones that are off a turn-on; `bestiarium_devices_mode_applied` with `reason: "restart"` fires only if one was turned off, and lists only those commands.

**Renamed modes**: the mode form sends `bestiarium_mode_options_renamed`; every device entry moves its priority keys, a removed mode's key goes.

**Mode changes** (`_on_mode_changed`), only when the select goes from one mode to another (a select that goes `unavailable` and comes back with a reload of the mode entry is no change):
- `Strategies.async_apply_mode()` goes over the devices set up:
  - Priority `0` (OFF) → turns off the switch; a device that is **on** gets the mode's **duty**: its turn-off is repeated like any balancer command (`async_mode_duties`, every 5 s while a duty is open: waits for the answer, attempts, deferral, again) until it is off or the mode no longer says off
  - Priority `9999` (ALWAYS_ON) → turns on the switch
  - Other priorities → no immediate action (handled by zone strategies); an open duty closes
- **Off by mode, turned on by a person**: the duty closed when the device turned off, so this is a person's choice; green leaves it (it never turns on a device with priority `0`). In red it goes first (below)
- **Out of attempts**: control state `mode_off_failed` and the control event `mode_off_failed` (see [Device control](#device-control-controlpy))

### Zone Strategies

#### Green Strategy (`_execute_green_strategy`)

- **Trigger**: Periodic, every 60 seconds
- **Action**: Turn ON the **highest-priority OFF device that fits** (one device per cycle)
- **Skips**: Devices already ON, devices with priority `0` (OFF), devices that do not fit (logged at debug: `X W + Y W (source) > Z W`)
- **Sorting**: Candidates sorted by priority descending; the first one that fits is turned on, so a smaller, less important device can go before a large one. Devices go in the order of their entries (`Strategies._devices`), so of two with the same priority the one added first wins, also after a reload
- **ALWAYS_ON** (`9999`) candidates are turned on without the headroom check
- **Device control**: a device that still waits for the answer to its last command, or is deferred, is not a candidate. The command is logged at info only on the first attempt of a series; repeats at debug

#### Headroom: `_fits(entry)`

```
fits = grid load + expected power of the device <= red zone threshold
```

- **Grid load** — the state of the power sensor `bestiarium_mode` watches (`ac_load_limits.ac_power_sensor` in the mode entry data).
- **Red zone threshold** — the `red_threshold` attribute of the mode's red zone sensor, found by unique_id. It already follows the runtime load limit.
- **Expected power** — `PowerMemory.expected_power(entry)`, see [Expected Power](#expected-power-consumptionpy-power_memorypy).
- If the load or the threshold cannot be read, the device does **not** fit (debug log).

Checked only where the balancer turns a device on by itself: the green strategy and "Allowed" turned back on in green. Not checked for mode priority `9999` and not for turning a device on by hand. Turning off is unchanged.

#### Red Strategy (`_execute_red_strategy`)

- **Trigger**: Periodic, every 5 seconds. Also runs once immediately on entering red zone.
- **First**: every ON device whose priority in the current mode is `0` (off by mode, someone turned it on) gets its turn-off **at once**, in the same step, and the mode's duty: it is repeated after the red zone too, and out of attempts it is "Does not turn off by mode"; the queue goes on at the next step
- **Action**: Turn OFF the **lowest-priority ON device** (one device per cycle)
- **Skips**: Devices already OFF, devices with priority `9999` (ALWAYS_ON), deferred devices
- **Sorting**: Candidates sorted by priority ascending
- **Waiting**: while a device of the queue may still answer its turn-off command (its response time, at most `red_max_wait_seconds` for the whole series), the red strategy turns off no other device. Then it defers that device and turns off the next one in the same step. Devices off by mode do not hold the queue

Green, red and "Allowed" fire their `device_turned_on/off` event from one place (`_fire_switched`, payload `DeviceSwitchedData`); the strategies, overload and mode application send relay commands through `_async_command`, which logs a command HA refused (`HomeAssistantError`) and goes on with the other devices. Overload and mode application send their command to deferred devices too, without waiting or counting attempts.

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
| Allowed → on | relay on now if it is off, its current mode priority is not `0`, it is not thermostat-driven and it fits; otherwise it waits for a green cycle | nothing; it waits for its turn in green |

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

## Expected Power (`consumption.py`, `power_memory.py`)

**A run** is a stretch where the device's relay is on **and** its power sensor reads at least `MIN_CONSUMING_POWER_W` (10 W): a boiler that is on but heated (0 W) or a socket in standby is not running. An unavailable power sensor ends a run.

**The power of a run** is the highest level held for at least `STABLE_POWER_SECONDS` (30 s): the maximum, over all windows of at least 30 s, of the minimum power in the window. Power sensors report on change, so the power between two reports is taken as constant. Spikes shorter than 30 s do not count; a run shorter than 30 s is not remembered.

`consumption.py` holds this logic without HA imports: `RunBuilder` (incremental, used live), `runs_from_states` (the same on recorded states), `stable_power`, `records_from_hourly_max`, `add_run`, `estimate`.

**Memory** — `PowerMemory`, one `Store` at `.storage/bestiarium_devices.consumption`, keyed by entry_id:

```json
{ "<entry_id>": { "filled_from_history": true,
                  "runs": [ { "start": "...", "duration_s": 312, "power_w": 2380, "source": "run" } ] } }
```

- `source`: `run` (seen live), `history` (rebuilt from the state history), `statistics` (from hourly long-term statistics). Imported records are a temporary hint.
- Up to `POWER_MEMORY_SIZE` (10) records. A new run evicts the oldest imported record first, then the oldest real run. From `REAL_RUNS_FOR_ESTIMATE` (3) real runs on, imported records are dropped.
- Saved with `async_delay_save` (10 s), not in the entry options: runs are frequent.
- `async_get_power_memory(hass)` returns the loaded memory (an HA `singleton`), shared by the coordinator and the entities. Changes fire the dispatcher signal `SIGNAL_EXPECTED_POWER_CHANGED` for the entry.

**Estimate** (`estimate(records, manual_w)`):

| Real runs (`run`) | Expected power | `estimate_source` |
|---|---|---|
| ≥ 3 | max of the real runs | `memory` |
| 1–2 | max of: estimated power (if set), imported records, real runs | `manual` / `history` / `memory`, whichever gave the max |
| 0 | max of: estimated power (if set), imported records | `manual` / `history` |
| nothing | `LARGE_DEVICE_POWER_W` (2000 W) | `large_device_default` |

**Live tracking** — `async_track_runs` follows the relay and power sensor states; a finished run is added to the memory. A run going on during a restart is counted from the restart.

**Import from the recorder** — on the first setup of an entry whose memory was never filled (`filled_from_history` not set), in a background task:

1. State history of the relay and power sensor for the recorder's `keep_days` → runs with `source: history`, the last 10.
2. With fewer than 3, hourly `max` statistics of the power sensor before the history window: consecutive hours with a max of at least 10 W make one record (`source: statistics`, power = the largest max). The newest ones fill up to 3 records in all.
3. `filled_from_history = true`.

Recorder errors are not caught: the import fails with an error in the log and runs again on the next start. Without the recorder loaded the import waits for a start that has it. The reset button keeps `filled_from_history = true`, so a reset is not undone by a new import.

## Device Control (`control.py`)

One `DeviceControl` per device entry (`BalancingCoordinator.control(entry)`), following the relay and the power sensor (docs: [stability](concepts/stability.md), [problem reporting](concepts/problem-reporting.md)).

### Commands and deferral

- A strategy's command starts or continues a **series** of one direction (turn on / turn off). The attempt waits `max(strategy step, longest response × margin)`; the longest response is the longest of the device's last `RESPONSE_MEMORY_SIZE` (10) answers (`ResponseMemory`, Store `bestiarium_devices.control`, kept across restarts), or `default_response_seconds` before any. In the red zone the deadline is also capped by the series' limit, `red_max_wait_seconds` after its first command.
- At the next step after the deadline (timers within `STEP_JITTER_SECONDS`) the attempt failed. After `attempts` failures, or when the red limit is reached, the device is **deferred** for `defer_minutes`: no strategy picks it; the event `deferred` fires and its control state is "Not responding". When the deferral ends the event `retrying` fires and a new series starts; the state stays "Not responding".
- **Any change of the relay state** ends the series and the deferral, a change to `unavailable` too. "Not responding" ends only when the relay comes back with the expected state or another state than before (a dead device that drops out and returns as it was did not answer); each such change starts the hysteresis again, and after it the event `recovered` fires.
- **Response time** counts from the series' first command to the expected state, also when the answer comes after a repeat; not while the device is deferred.
- **Only set-up devices** have a control (`async_add_device` from `async_setup_entry`, removed on unload); the strategies leave disabled, unloading and removed entries alone, so the Store holds only devices that exist.
- **The other direction**: a strategy asking about the opposite direction changes nothing (the red zone's check of turn-offs keeps a turn-on deferral and a turn-on in flight); only a command of the other direction starts its own series.
- Deferral and series are not stored: after a restart every device gets its attempts again.

### Control state (`ControlStateSensor`, enum, diagnostic)

| State | Raw condition | Confirmed |
|---|---|---|
| `switch_unavailable` | relay `unavailable` / `unknown` / missing | after `tolerance_seconds` |
| `mode_off_failed` | the mode's duty ran out of attempts, until the device is off or the mode no longer says off | at once; ends at once |
| `unresponsive` | deferred, until the relay changes | at once |
| `unstable` | `unstable_dropouts_per_hour` short dropouts (relay or power sensor gone for less than the tolerance) in the last hour | at once |
| `no_power_data` | power sensor `unavailable` / `unknown` / missing | after `tolerance_seconds` |
| `normal` | none of these | — |

The first confirmed state in this order is shown. A problem ends after `hysteresis_seconds` without its raw condition. Availability counts only from HA's start (`async_at_started`); before that the state is "Normal". Each change of the state is logged once: `warning` into a problem, `info` back to normal. Attributes (all in `_unrecorded_attributes`): `direction`, `failed_attempts`, `last_attempt`, `next_attempt`, `response_time`, `dropouts_last_hour`.

### Control events (`ControlEventEntity`, diagnostic)

Event types `deferred`, `retrying`, `recovered`, `mode_off_failed` ("Could not turn off by mode": once a duty, instead of its first `deferred`), with `direction`, `failed_attempts`, `next_attempt`. They are device events for the home's automations; the bus events of [events.md](events.md) are unchanged.

### Settings (`ControlSettings`, options flow)

Algorithm parameters, in the entry's options ([settings vs entities](concepts/settings-vs-entities.md)); a missing option is its default, so entries from before DEV-007 need no migration. "Configure" on the device entry opens the form (`ControlOptionsFlow`); it keeps the other options ("Allowed"), and the balancer reads the settings on every use (no reload). The form also holds the **estimated power** (`estimated_power_w`, 0–100000 W, step 10; 0 = not set) and **"Reset the learned consumption"** (an action, not stored); a new estimated power shows in "Expected power" at once.

| Option | Default | Range | |
|---|---|---|---|
| `attempts` | 3 | 1–10 | |
| `defer_minutes` | 15 | 1–240 | |
| `tolerance_seconds` | 60 | 0–600 | |
| `hysteresis_seconds` | 60 | 0–600 | |
| `unstable_dropouts_per_hour` | 3 | 1–60 | |
| `default_response_seconds` | 15 | 1–120 | advanced |
| `response_margin` | 1.5 | 1–5 | advanced |
| `red_max_wait_seconds` | 30 | 5–120 | advanced |

---

## Power Entities

All entities of a device share `BestiariumDeviceEntity` (`entity.py`): `has_entity_name`, `unique_id = {entry_id}_{key}`, translation `key`, and the device placement described for the "Allowed" switch below (`{key}_standalone` with "{device_name} …" for a relay without a device).

| Entity | Key | Category | What it does |
|---|---|---|---|
| sensor "Expected power" / «Очікуване споживання» | `expected_power` | diagnostic | state = expected power (W, `power`, `measurement`); attributes `estimate_source`, `runs` (start, duration_s, power_w, source; not recorded) |
The estimated power and the reset were a number and a button until DEV-008; they are in the settings form now ([settings vs entities](concepts/settings-vs-entities.md)). Migration 1.3 removes their entities from the registry; the value already was in `entry.options`. Dashboards and automations that used `number.…_estimated_power` or `button.…_reset_power_memory` find no entity.

The sensor updates on `SIGNAL_EXPECTED_POWER_CHANGED`, which the memory and the settings form send. It does not use an entry update listener: HA expects those to reload the entry.

## "Allowed" Switch (`switch.py`)

Class: `AllowedSwitch(BestiariumDeviceEntity, SwitchEntity)` — one per config entry.

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
- Entity names: `Allowed`, `Expected power`, `Control state`, `Control events` (each also as `{device_name} …`), `Default Load Priority`, `{mode_name} Load Priority`
- Repairs issues `missing_entity` (with its fix flow) and `no_load_zones`
- Attribute names and values of "Expected power": `estimate_source` (`memory`, `history`, `manual`, `large_device_default`), `runs` (`manual_power_w` / `manual_power_note` removed in DEV-008 stage B: they repeated the settings form)

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
- `get_current_priority`: mode-specific priority, fallback to default, missing/unknown/unavailable mode select, empty priorities dict, virtual device, two entries on one device keep their own priorities

### `test_migration.py`
- Registries in HA 2026.2 format (a Shelly + MikroTik shared device), split on load: composite `device_id` moves to the relay's split with all other data and options intact; reconfigure then offers the device's relays; live, virtual, relay-less and already-taken cases are left alone

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

### `test_consumption.py`
- Stable power: short spike, steps, level held across changes, run under 30 s; runs from states (0 W, relay off, power drop, unavailable, standby); hourly grouping; eviction; the estimate table

### `test_power_memory.py`
- Store survives a restart, reset, manual value, signal, removal; live runs (40 s run, 0 W, unavailable); recorder import (`recorder_mock`): history runs, statistics up to 3 records, empty recorder, import once per entry, live runs kept

### `test_headroom.py`
- Real `bestiarium_mode` with a red threshold of 4250 W: 2900 + 2400 does not fit, a 300 W device below it turns on instead, 1500 + 2400 fits, unknown load turns nothing on, ALWAYS_ON is not limited

### `test_power_entities.py`
- Placement and categories, sensor attributes, number, reset button

### `test_control_state.py`
- On relays that ignore commands (`FakeRelays.stuck`) and the real `bestiarium_mode`: deferral after 3 failed green steps and the next device turns on; retry after 15 min; recovery after the hysteresis when the relay answers or is switched by hand; the red zone's 30 s limit; a slow device (12 s) gets one command and its response time is remembered; no guessing from the power; tolerance, hysteresis, priority of causes, unstable connection; an hour of a stuck device logs one warning and a line per series; overload and mode reach a deferred device; availability only after HA's start; unrecorded and diagnostic

### `test_single_balancer.py`
- Five entries set up as at HA start (power memory read from disk meanwhile): one command per step; also after a zone change, a reload, adding and removing a device; a mode is applied once

### `test_control_options.py` / `test_control_memory.py`
- Defaults for entries without options; the options flow keeps "Allowed" and estimated power; settings apply without a reload; translations of states, events and settings. Response memory: longest of the last 10, kept in the Store, forgotten on removal

---

## Design Notes & Gotchas

1. **Multi-entry, single coordinator**: Unlike bestiarium_mode (singleton), this integration allows multiple config entries (one per device). The `BalancingCoordinator` is shared — created once in `async_setup`, stopped with HA.

2. **Entities per device**: the "Allowed" switch, the three power entities and the two control entities. Relays are controlled through service calls (`switch.turn_on`, `switch.turn_off`) and state reads.

3. **Device ID semantics**: Virtual devices use `entry.entry_id` as their device ID (since they have no pre-existing HA device). Extended devices use the actual HA `device_id`. This distinction matters for priority lookups.

4. **Priority resolution chain**: Mode-specific → default from entry → `LOAD_PRIORITY_DEFAULT` (5000, only for an entry without priorities). Without a valid mode select each device has its own default.

5. **One device per cycle**: Green and red strategies each affect only ONE device per execution cycle. This gradual approach prevents power oscillation.

6. **ALWAYS_ON immunity**: Red strategy skips `9999` priority devices. Only overload can force them off.

7. **Overload two-step design**: The `overload_first_queue` flag allows designating less-critical devices for immediate shutdown while giving more-critical devices a 5-second grace period. If the first queue resolves the overload, the second queue stays on.

8. **Mode options snapshot** (removed in DEV-008 stage B): `mode_options_snapshot` was written at save time and never read; migration 1.4 removes it from the entries.

9. **Power sensor validation**: Must have `device_class=power` and `unit_of_measurement=W`. The validation runs at config flow time, not continuously.

10. **Missing sensors graceful degradation**: If bestiarium_mode's binary sensors don't exist (AC load limits not configured), the coordinator starts but stays inactive, with the Repairs issue `no_load_zones`. Sensors that appear later (limits configured on a running system) bring it alive at once.

12. **One relay, one device** (`helpers.switch_taken`): the create, reconfigure and Repairs forms refuse a switch another Bestiarium device controls (`switch_taken`); one device with two relays may have two Bestiarium devices. Migration 1.2 uses the same rule. **One relay, one integration** (review 18, `helpers.thermostat_of_switch`): they also refuse the switch of a Bestiarium Climate thermostat (`switch_of_thermostat`).

13. **The memories** (`store.py`): `PowerMemory` and `ResponseMemory` are `EntryStore`s — a value per device entry in a Store, saved a little later, forgotten with the entry.

11. **Repairs of a device** (`repairs.py`): once HA has fully started, a relay or power sensor with no state at all (removed, disabled) is the issue `missing_entity_<entry_id>` ("Fix": the fields of the reconfigure form — device, or switch and sensor of a virtual device); it goes when the entity is back or another is picked. Unavailable is the control state "Switch unavailable", not an issue. A renamed relay or sensor is followed: the entry takes the new entity_id and reloads. Disabled entries are not set up, so not checked.
