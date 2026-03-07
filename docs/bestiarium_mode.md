# bestiarium_mode — Home Assistant Custom Integration

## Overview

`bestiarium_mode` is a Home Assistant custom integration (domain: `bestiarium_mode`, version 0.1.0) that provides two capabilities:

1. **Mode selector** — A `select` entity that lets the user define and choose between named operational modes (e.g. "Eco", "Boost", "Night"). Each mode carries metadata: name (required), description (optional), and icon (optional, default `mdi:home`).
2. **Power load zone binary sensors** — Four `binary_sensor` entities that implement a traffic-light model over an AC power sensor, indicating the current load zone: Green, Yellow, Red, Overload.

The integration is singleton — only one config entry is allowed (unique_id: `"bestiarium_mode"`).

---

## File Structure

```
custom_components/bestiarium_mode/
├── __init__.py          # Entry setup/unload/reload
├── manifest.json        # Integration metadata
├── const.py             # Constants, config keys, defaults
├── data.py              # Type alias for ConfigEntry
├── config_flow.py       # Multi-step config flow (user + reconfigure)
├── select.py            # BestiariumModeSelect entity
├── binary_sensor.py     # PowerLoadBinarySensor entities (4 zones)
└── translations/
    ├── en.json           # English translations
    └── uk.json           # Ukrainian translations

tests/custom_components/bestiarium_mode/
├── __init__.py
├── test_config_flow.py
├── test_binary_sensor.py
└── test_select.py
```

---

## Platforms

Registered in `__init__.py`:

```python
PLATFORMS = [Platform.BINARY_SENSOR, Platform.SELECT]
```

---

## Config Entry Data Schema

The config entry stores two top-level keys in `entry.data`:

```python
{
    "options": [                         # List of mode definitions
        {
            "name": str,                 # Required — mode display name
            "description": str,          # Optional — mode description
            "icon": str,                 # Optional — MDI icon (default "mdi:home")
        },
        # ... up to 10 options (edit_0 through edit_9)
    ],
    "ac_load_limits": {                  # Optional — power load monitoring config
        "ac_power_sensor": str,          # Entity ID of a sensor with device_class=power
        "ac_load_limit_w": int,          # Maximum load in watts (1–100000)
        "ac_yellow_zone_percent": int,   # Yellow threshold as % of limit (default 75, min 50)
        "ac_red_zone_percent": int,      # Red threshold as % of limit (default 90, must > yellow)
        "ac_overload_recovery_seconds": int  # Seconds before Green re-engages (default 30, range 1–300)
    }
}
```

### Config Key Constants (`const.py`)

| Constant | Value |
|---|---|
| `DOMAIN` | `"bestiarium_mode"` |
| `CONF_AC_LOAD_LIMITS` | `"ac_load_limits"` |
| `CONF_AC_POWER_SENSOR` | `"ac_power_sensor"` |
| `CONF_AC_LOAD_LIMIT_W` | `"ac_load_limit_w"` |
| `CONF_AC_YELLOW_ZONE_PERCENT` | `"ac_yellow_zone_percent"` |
| `CONF_AC_RED_ZONE_PERCENT` | `"ac_red_zone_percent"` |
| `CONF_AC_OVERLOAD_RECOVERY_SECONDS` | `"ac_overload_recovery_seconds"` |
| `DEFAULT_OVERLOAD_RECOVERY_SECONDS` | `30` |

---

## Config Flow (`config_flow.py`)

Class: `BestiariumModeFlowHandler` (VERSION = 1)

### Flow Architecture

The config flow uses a **menu-driven pattern** where both the initial setup (`async_step_user`) and reconfiguration (`async_step_reconfigure`) share a common options-list menu via `_async_step_options_list()`.

### Steps

| Step | Purpose |
|---|---|
| `user` | Initial flow entry — sets unique_id, aborts if already configured, shows menu |
| `reconfigure` | Re-entry for existing config — loads existing options/ac_load_limits, shows menu |
| `add` | Form to create a new mode option (name, description, icon) |
| `edit_0` ... `edit_9` | Form to edit/delete an existing option by index (hard-coded step methods) |
| `configure_ac_load_limits` | Form to set power sensor, watt limit, zone thresholds, recovery time |
| `complete` | Validates at least 1 option exists, creates or updates config entry |

### Menu Options

The menu dynamically builds from current `_options`:
- One entry per existing option: `edit_{i}` → "🏷️ {name}"
- `configure_ac_load_limits` → "⚡ Configure AC load limits"
- `add` → "➕ Add option"
- `complete` → "💾 Save"

### Validation Rules

- **name_required** — Option name cannot be empty
- **yellow_zone_min** — Yellow zone must be >= 50%
- **red_zone_must_exceed_yellow** — Red zone % must be strictly greater than yellow zone %

### Edit Steps Limitation

There are 10 hard-coded `async_step_edit_N` methods (N = 0..9), each delegating to `_async_step_edit(N, user_input)`. This limits the maximum number of editable options to 10. HA requires named step methods for menu navigation.

### Reconfigure Flow

Uses `SOURCE_RECONFIGURE`. Loads existing `entry.data` into `_options` and `_ac_load_limits` on first entry. On save, calls `async_update_reload_and_abort()` to update and reload the entry.

---

## Select Entity (`select.py`)

Class: `BestiariumModeSelect(SelectEntity, RestoreEntity)`

### Key Properties

| Property | Value |
|---|---|
| `_attr_unique_id` | `"select"` |
| `_attr_translation_key` | `"mode"` |
| Entity ID (typical) | `select.bestiarium_mode` |
| Extends | `SelectEntity`, `RestoreEntity` |

### Behavior

- **Options**: Derived from `entry.data["options"]` — returns list of name strings
- **Current option**: Stored in `_current_option`, defaults to first option on startup
- **State restore**: On `async_added_to_hass`, restores previous selection if it still exists in the current options list; falls back to first option otherwise
- **Icon**: Dynamically returns the icon of the currently selected mode option
- **Extra attributes**:
  - `description` — description of the currently selected mode (if present)
  - `icon` — icon of the currently selected mode
  - `options_metadata` — dict mapping each option name to its `{description, icon}` metadata

### Select Action

`async_select_option(option)` — validates option exists in the list, updates `_current_option`, writes state.

---

## Binary Sensor Entities (`binary_sensor.py`)

Class: `PowerLoadBinarySensor(BinarySensorEntity)`

### Zone Model

Four binary sensors implement a **traffic-light pattern** for AC power load monitoring:

| Zone | `is_on` condition | Icon |
|---|---|---|
| `green` | `power < yellow_threshold` (with recovery delay) | `mdi:check-circle` |
| `yellow` | `yellow_threshold <= power < red_threshold` | `mdi:alert-circle` |
| `red` | `red_threshold <= power < limit_w` | `mdi:close-circle` |
| `overload` | `power >= limit_w` | `mdi:flash-alert` |

Where thresholds are calculated:
```
yellow_threshold = limit_w * yellow_zone_percent / 100
red_threshold    = limit_w * red_zone_percent / 100
```

### Entity Attributes

| Attribute | Scope | Description |
|---|---|---|
| `zone` | All zones | Zone name string ("green", "yellow", "red", "overload") |
| `yellow_threshold` | All zones | Calculated yellow threshold in watts |
| `red_threshold` | All zones | Calculated red threshold in watts |
| `limit_w` | All zones | Configured watt limit |
| `recovery_pending` | Green only | Boolean — whether green zone is waiting for recovery timeout |

### Unique IDs and Translation Keys

Pattern: `power_load_{zone.value}` — e.g. `power_load_green`, `power_load_yellow`, etc.

### Entity Creation

Binary sensors are **only created** if `ac_load_limits` is present in the config entry data AND both `ac_power_sensor` and `ac_load_limit_w` are set. Otherwise, the platform silently skips entity creation.

### State Tracking

- Subscribes to state changes of the configured power sensor via `async_track_state_change_event`
- Reads initial state on `async_added_to_hass`
- Unsubscribes on `async_will_remove_from_hass`

### Availability

`available` returns `True` only when `_is_on is not None`. When the power sensor is unavailable, unknown, or has an unparseable value, `_is_on` is set to `None`, making the binary sensor unavailable.

### Green Zone Recovery Logic

The Green zone has special **delayed ON** behavior to prevent rapid toggling:

1. When power drops below yellow_threshold, Green does NOT immediately turn ON
2. Instead: `_is_on = False`, `_recovery_pending = True`, a timer is started (`async_call_later`)
3. After `recovery_seconds` elapse, `_async_recovery_complete` fires: `_is_on = True`, `_recovery_pending = False`
4. If power rises above the threshold during recovery, the timer is cancelled (`_cancel_pending_recovery`)
5. If power drops again after cancellation, a new recovery timer starts from scratch
6. Unavailable/unknown sensor states also cancel recovery

The OFF transition for Green is **immediate** (no delay).

---

## Cross-Integration Dependencies

### Consumed by `bestiarium_devices`

The `bestiarium_devices` integration reads from `bestiarium_mode`:

- **Mode selection**: Reads `select.bestiarium_mode` entity to get the current operational mode. The devices integration config flow uses `get_bestiarium_modes()` to fetch available mode options and aborts with `"no_bestiarium_mode"` if the mode integration isn't configured.
- **Power load zones**: Reads the 4 binary sensor entities to determine load zone status for device balancing decisions.

### Consumed by `bestiarium_climate`

The `bestiarium_climate` integration references `DOMAIN_BESTIARIUM_MODE = "bestiarium_mode"` in its constants, indicating it also depends on mode state for climate control decisions.

### Entity IDs Used by Consumers

```python
BESTIARIUM_MODE_SELECT_ENTITY_ID = "select.bestiarium_mode"
# Binary sensors: binary_sensor.power_load_green, binary_sensor.power_load_yellow, etc.
```

---

## Translations

Two locales supported: `en` (English) and `uk` (Ukrainian).

Translation keys cover:
- Config flow steps, menus, field labels, field descriptions
- Validation errors: `name_required`, `yellow_zone_min`, `red_zone_must_exceed_yellow`, `no_options`
- Abort reasons: `already_configured`, `reconfigure_successful`
- Entity names: `Power Load - Green/Yellow/Red`, `Overload`, `Mode`

---

## Test Coverage

### `test_config_flow.py`
- Full config flow: init → add option → configure AC limits → complete → verify entry data
- AC load limits validation (yellow < 50%, red <= yellow)
- Reconfigure flow: loads existing data, adds option, updates limits, saves

### `test_binary_sensor.py`
- Entity creation with/without `ac_load_limits` config
- Zone boundary tests at exact threshold values (green, yellow, red, overload)
- Overload above limit
- Unavailable/unknown/invalid sensor values → all sensors unavailable
- Zone transitions on state change
- Extra state attributes
- Green zone recovery: delayed ON, immediate OFF, cancellation on power rise, restart after cancellation, default recovery seconds, unavailable cancels recovery
- `recovery_pending` attribute only on Green zone

### `test_select.py`
- Entity creation, initial state (first option), options list
- Icon and `options_metadata` extra attributes

---

## Design Notes & Gotchas

1. **Singleton constraint**: The integration enforces a single config entry via `async_set_unique_id("bestiarium_mode")` + `_abort_if_unique_id_configured()`.

2. **Hard-coded edit steps**: Due to HA config flow limitations (step methods must be named), there are 10 hard-coded `async_step_edit_N` methods. Maximum 10 mode options.

3. **Recovery timeout is Green-only**: Only the Green zone has delayed ON behavior. Yellow, Red, and Overload respond immediately to power changes. This prevents rapid cycling when power hovers near the yellow threshold.

4. **No API/device integration**: This is a purely local, config-driven integration. No external API calls, no device communication.

5. **RestoreEntity on Select**: The select entity restores its state across HA restarts. If the restored option was removed during reconfiguration, it falls back to the first available option.

6. **Config flow menu pattern**: The flow uses `async_show_menu` for the main options list, which is a less common HA pattern. Menu options are dynamically generated from current state.

7. **ac_load_limits is optional**: If not configured, the binary sensor platform skips entity creation entirely. The select entity is always created.

8. **Type alias**: `BestiariumModeConfigEntry = ConfigEntry[dict]` — a simple type alias for clarity, no runtime behavior.

