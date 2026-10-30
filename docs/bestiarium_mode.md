# bestiarium_mode — Home Assistant Custom Integration

## Overview

`bestiarium_mode` is a Home Assistant custom integration (domain: `bestiarium_mode`, version 0.1.0) that provides two capabilities:

1. **Mode selector** — A `select` entity that lets the user define and choose between named operational modes (e.g. "Eco", "Boost", "Night"). A mode is its name (its description and icon were removed in DEV-008 stage B: nothing read them).
2. **Power load zone binary sensors** — Four `binary_sensor` entities that implement a traffic-light model over an AC power sensor, indicating the current load zone: Green, Yellow, Red, Overload.
3. **Power load limit** — A `number` entity holding the current AC load limit (W). It can be changed at runtime (UI or automation); the zones recalculate immediately.

The integration is singleton — only one config entry is allowed (unique_id: `"bestiarium_mode"`).

---

## File Structure

```
custom_components/bestiarium_mode/
├── __init__.py          # Entry setup/unload/reload; serves the power flow card
├── manifest.json        # Integration metadata
├── const.py             # Constants, config keys, defaults
├── data.py              # ConfigEntry type alias, power_load_monitoring_configured()
├── config_flow.py       # Multi-step config flow (user + reconfigure)
├── select.py            # BestiariumModeSelect entity
├── zones.py             # ZoneTracker — the one current zone of the entry
├── binary_sensor.py     # PowerLoadBinarySensor entities (4 zones, views of the zone)
├── repairs.py           # Repairs: the zones' power sensor is gone, its fix flow
├── number.py            # PowerLoadLimitNumber entity (runtime load limit)
├── card.py              # Serves the power flow card (static path, extra JS URL)
├── frontend/
│   ├── power-flow-card.js   # The card's element
│   └── power-flow-model.js  # What it shows: pure functions
└── translations/
    ├── en.json           # English translations
    └── uk.json           # Ukrainian translations

tests/custom_components/bestiarium_mode/
├── __init__.py
├── test_config_flow.py
├── test_binary_sensor.py
├── test_zones.py
├── test_number.py
├── test_select.py
└── test_card.py

tests/frontend/power-flow-model.test.mjs   # node --test: the card's model
```

---

## Platforms

Registered in `__init__.py`:

```python
PLATFORMS = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SELECT]
```

There is **no config entry update listener**: the load limit entity updates the entry at runtime and must not reload it. The reconfigure flow reloads explicitly with `async_update_reload_and_abort()`. Any future flow that changes the entry must reload explicitly too.

---

## Config Entry Data Schema

The config entry stores two top-level keys in `entry.data`:

```python
{
    "options": [                         # List of mode definitions
        {
            "name": str,                 # Required — mode display name (the only key since 1.2)
        },
        # ... up to 10 options (edit_0 through edit_9)
    ],
    "ac_load_limits": {                  # Optional — power load monitoring config
        "ac_power_sensor": str,          # Entity ID of a sensor with device_class=power
        "ac_load_limit_w": int,          # Maximum load in watts (1–100000)
        "ac_yellow_zone_percent": int,   # Yellow threshold as % of limit (default 75, min 50)
        "ac_red_zone_percent": int,      # Red threshold as % of limit (default 90, must > yellow)
        "ac_overload_recovery_seconds": int,  # The pause before green, s (default 30, range 1–300)
        "ac_zone_hysteresis_percent": float,  # The margin of going down, % of the limit (default 3, 0–20)
        "ac_power_tolerance_seconds": int,   # No power data longer than this: no data (default 60, 0–600)
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
| `add` | Form to create a new mode option (name) |
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

Uses `SOURCE_RECONFIGURE`. Loads existing `entry.data` into `_options` and `_ac_load_limits` on first entry, and remembers each option's name (`_origins`). On save it sends the dispatcher signal `bestiarium_mode_options_renamed` with the renamed modes (`{old: new}`) and the removed ones: `bestiarium_devices` moves the devices' priority keys, so a renamed mode keeps them. Then it calls `async_update_reload_and_abort(..., reason="reconfigure_successful")` to update and reload the entry. The explicit `reason="reconfigure_successful"` makes HA show this integration's translated message; without it HA uses the core text, which has no Ukrainian translation yet. The load limit field defaults to the current limit, including changes made through the limit entity.

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
- **Icon**: always `mdi:home` (before DEV-008 stage B it changed with the mode)
- **No extra attributes** (DEV-008 stage B, [necessity](concepts/necessity.md)): `description`, `icon` and `options_metadata` repeated the form
- **Migration 1.2** strips each stored mode to its name
- **A renamed current mode stays current**: the select remembers the new name from the rename signal as its extra restore data, without writing a state (the devices would take a written one for a change of mode and apply it); the reload restores the new name instead of falling to the first mode
- **Names are unique** (`name_exists` in the add and edit forms): two modes of one name would merge the devices' priorities

### Select Action

`async_select_option(option)` — validates option exists in the list, updates `_current_option`, writes state.

---

## Binary Sensor Entities (`binary_sensor.py`)

Class: `PowerLoadBinarySensor(BinarySensorEntity)`

### Zones

One `ZoneTracker` per entry (`zones.py`, in `entry.runtime_data`) decides **one** current zone; the four binary sensors only show it (`is_on` = their zone is the current one). Before DEV-008 each sensor computed its own state and only green had a delay: below the yellow threshold yellow went off at once while green waited 30 s, so no sensor was on and the balancer stopped — the flapping "yellow → none → yellow" on the stand.

| Zone | When | Sensors | Balancer |
|---|---|---|---|
| `green` | below the yellow threshold, the pause is over | green on | green strategy |
| `pause` (green on pause) | below "yellow threshold − margin", for `ac_overload_recovery_seconds` (30 s) | all four off and available | nothing |
| `yellow` / `red` / `overload` | by the thresholds | that one on | as before |
| `no_data` | the power sensor unavailable, unknown or not a number for longer than `ac_power_tolerance_seconds` (60 s) | all unavailable; one `warning` (and an `info` when data is back) | nothing |

Rules (the owner's model, DEV-008):

- **Up at once from any zone**, the pause and green too: `power >= threshold` → that zone.
- **Down with a margin** (`ac_zone_hysteresis_percent`, 3 % of the limit, 150 W at 5000 W): a zone goes down only when `power < its threshold − margin`; red → yellow and overload → lower at once, to the zone of `power + margin`.
- **The pause only before green**: yellow → pause → green after the pause continuously below "yellow threshold − margin"; a load within the margin restarts it, above the yellow threshold it is yellow at once (the next pause starts from zero).
- **Lost data**: the zone stays as it was for the tolerance, then no data; data back → the zone by the load at once (green through the pause).
- **A limit change** recalculates by the same rules.

| Zone (sensor) | Threshold | Icon |
|---|---|---|
| `green` | below the yellow threshold | `mdi:check-circle` |
| `yellow` | the yellow threshold | `mdi:alert-circle` |
| `red` | `red_threshold` | `mdi:close-circle` |
| `overload` | `limit_w` | `mdi:flash-alert` |

Where thresholds are calculated:
```
yellow_threshold = limit_w * yellow_zone_percent / 100
red_threshold    = limit_w * red_zone_percent / 100
```

### Entity Attributes

Only what something reads (DEV-008 stage B, [necessity](concepts/necessity.md)); the limit is the limit entity's, the thresholds are configuration.

| Attribute | Sensor | Description |
|---|---|---|
| `red_threshold` | red | its threshold in watts; the balancer of `bestiarium_devices` reads it |

Removed in DEV-008 stage B: `zone` and `limit_w` on every sensor, the other zones' thresholds, green's `recovery_pending` (the pause is "all four off and available"), yellow's `yellow_threshold`.

### One zone value: a template sensor

Bestiarium has no "load zone" entity: it would repeat the four sensors. A
template sensor gives one value with all six states:

```yaml
template:
  - sensor:
      - name: "Load zone"
        unique_id: load_zone
        state: >
          {% if is_state('binary_sensor.power_load_overload', 'on') %}overload
          {% elif is_state('binary_sensor.power_load_red', 'on') %}red
          {% elif is_state('binary_sensor.power_load_yellow', 'on') %}yellow
          {% elif is_state('binary_sensor.power_load_green', 'on') %}green
          {% elif is_state('binary_sensor.power_load_green', 'off') %}pause
          {% else %}no_data{% endif %}
```

- **pause** — all four off and available: the load is below the yellow threshold, the pause before green runs; the balancer turns nothing on or off.
- **no data** — all four unavailable: the power sensor gave no data for longer than the tolerance; the balancer turns nothing on or off; a `warning` in the log.

### Without load limits

The entry removes its zone sensors from the registry (orphans would stay
unavailable); `bestiarium_devices` stops following them and shows the
Repairs issue "Balancing waits for the load zones".

### Unique IDs and Translation Keys

Pattern: `power_load_{zone.value}` — e.g. `power_load_green`, `power_load_yellow`, etc.

### Entity Creation

Binary sensors are **only created** if `ac_load_limits` is present in the config entry data AND both `ac_power_sensor` and `ac_load_limit_w` are set. Otherwise, the platform silently skips entity creation.

### State Tracking

- The tracker follows the power sensor (`async_track_state_change_event`) and `bestiarium_mode_load_limit_changed` (rebuilds the thresholds, recalculates the zone)
- It tells the sensors by the dispatcher signal `bestiarium_mode_zone_changed_<entry_id>`; they write their states in that one dispatch, so consumers such as `bestiarium_devices` never see two zones at once

### Repairs

Once HA has fully started, a power sensor of the zones with no state at all (removed, disabled) is the Repairs issue `missing_power_sensor`; "Fix" picks a sensor again and reloads the entry. Unavailable is "no data", not an issue.

---

## Number Entity (`number.py`)

Class: `PowerLoadLimitNumber(NumberEntity)`

| Property | Value |
|---|---|
| `unique_id` | `power_load_limit` |
| `entity_id` | `number.power_load_limit` (from the English name "Power Load Limit") |
| `translation_key` | `power_load_limit` (en "Power Load Limit", uk "Ліміт навантаження") |
| `entity_category` | none: automations operate it (DEV-008 stage B; it was `config`) |
| `device_class` | `power`, unit `W` |
| Range | `LOAD_LIMIT_MIN_W`–`LOAD_LIMIT_MAX_W` (1–100000), step 1, box mode — same range as the config flow |

Created only when power load monitoring is configured (same rule as the binary sensors, `power_load_monitoring_configured()`).

**Single source of truth**: the value is `entry.data["ac_load_limits"]["ac_load_limit_w"]`. Setting a new value:

1. If it equals the current limit, does nothing.
2. Writes it to the config entry (`async_update_entry`, no reload) — so it survives HA restarts and the reconfigure form shows it.
3. Writes the entity state.
4. Fires `bestiarium_mode_load_limit_changed` (`limit_w`, `previous_limit_w`); the zone sensors recalculate in its dispatch.

There is no `RestoreEntity`: restoring asynchronously would let the zone sensors compute their first state with a stale limit at startup (e.g. a spurious overload and emergency shutdown in `bestiarium_devices`).

---

## Power flow card (`card.py`, `frontend/`)

A Lovelace card, `custom:bestiarium-power-flow-card` (DEV-014, FEAT-045):
where the home is fed from now — the grid straight to the home, through the
inverter, the sun, the battery. Five nodes (Мережа, Сонце, Інвертор, Дім,
Батарея), five wires, the mode's name above the scheme ("… · світла немає"
without the grid); a tap opens `navigation_path` when it is set.

**Why in Mode.** The power modes are Mode's subject, and Mode is always set up
in the home. `async_setup` (the integration's, not an entry's) serves
`frontend/` as the static path `/bestiarium_mode/frontend/<digest of its
files>` and adds its `power-flow-card.js` to every page of HA
(`frontend.add_extra_js_url`), only when HA's frontend is loaded
(`after_dependencies: ["frontend", "http"]`). An update of Bestiarium is a new
URL for the card and for the model it imports, so both are cached for long;
an update of HA does not touch the files. Nothing to do but
add the card to a dashboard (it is in the card picker). No HACS, no card-mod,
no CDN: icons are HA's own `ha-icon`.

**Files.** `power-flow-model.js` — pure functions (`readings`, `flowModel`,
`kw`, `hours`, `skeleton`), tested with `node --test` (`make test-card`, part
of `make test` and of CI). `power-flow-card.js` — the element: it builds the
scheme once and on each state update only switches classes, a CSS speed and
texts; the dots run by a CSS animation (`stroke-dashoffset`), no JS per
frame. Below 760 px of card width (where the wide captions would be under
11 px) it uses a narrower scheme (captions above
and below the circles), so a phone reads them. Colours are the theme's
variables (`--primary-color`, `--card-background-color`,
`--primary-text-color`, `--secondary-text-color`, `--state-icon-color`,
`--state-light-active-color`, `--slider-track-color`).

**Configuration.** Every entity has a default — the owner's home — and
`entities` overrides any of them (an unknown key is an error):

| Key | Default | Note |
|---|---|---|
| `mode` | `sensor.zhyvlennia_rezhym` | its state is the mode's name |
| `inverter_home_relay` | `switch.rele_invertor_dim` | `on`: the home from the inverter |
| `grid_inverter_relay` | `switch.serverna_rele_invertor` | `on`: the grid into the inverter |
| `grid_present` | `binary_sensor.zhyvlennia_merezha_ye` | `off`: no grid, the home on the inverter whatever the relays |
| `home_power` | `sensor.grid_main_load_em0_power` | what the home draws |
| `grid_power` | `sensor.grid_main_load_em1_power` | all that comes from the grid |
| `sun_power` | `sensor.livoltek_pv_power` | |
| `battery_flow` | `sensor.zhyvlennia_batareia_potuzhnist_skhema` | kW, "+" a discharge; not the inverter's own battery power |
| `battery_charge` | `sensor.zhyvlennia_zariad` | % |
| `battery_hours_left` | `sensor.zhyvlennia_batareia_hodyn` | shown while discharging |
| `battery_hours_to_full` | `sensor.batareia_do_povnogo_zariadu` | shown while charging, if known |

Powers are read in kW, or in W by their `unit_of_measurement`.

**Flows** (below 0.05 kW nothing flows):

- The home is fed from the inverter when its relay is `on` or there is no
  grid; from the grid when the relay is `off` and the grid is there; an
  unknown relay shows both home wires open and "—" under the inverter.
- "Мережа → Дім" carries the home's power; "Мережа → Інвертор" (connected
  when its relay is `on` and the grid is there) all of the grid's power while
  the home is on the inverter, else `max(grid − home, 0)`.
- "Сонце → Інвертор" flows above the threshold.
- The battery follows its flow sensor, which sees a flow only once the
  charge changes. One exception: the inverter feeding the home with no grid
  behind it (no grid, or the grid relay open) is a sure discharge,
  `max(sensor, home − sun)` when the home lacks sun (DEV-014-03, 1); an
  unknown sun counts as none, since the cloud often goes with the grid
  (DEV-014-06, 1). A charge comes from the sensor
  only: the sun's surplus may meet a full battery.
- A flowing wire's dots run faster with more power:
  `max(0.35, 1.6 − kW × 0.35)` s, in 0.05 s steps.
- An unavailable or missing entity is "—" on its node and no flow on its
  wire; the rest of the card works.

**In the home** (the "Живлення" screen, at its top, instead of the three
state tiles):

```yaml
type: custom:bestiarium-power-flow-card
```

and elsewhere, to open that screen on a tap:

```yaml
type: custom:bestiarium-power-flow-card
navigation_path: /<dashboard>/<the power screen>
```

---

## Cross-Integration Dependencies

### Consumed by `bestiarium_devices`

The `bestiarium_devices` integration reads from `bestiarium_mode`:

- **Mode selection**: Reads the mode select entity (found by unique_id `select`) to get the current operational mode. The devices integration config flow uses `get_bestiarium_modes()` to fetch available mode options and aborts with `"no_bestiarium_mode"` if the mode integration isn't configured.
- **Power load zones**: Reads the 4 binary sensor entities to determine load zone status for device balancing decisions.

### Consumed by `bestiarium_climate`

The `bestiarium_climate` integration references `DOMAIN_BESTIARIUM_MODE = "bestiarium_mode"` in its constants, indicating it also depends on mode state for climate control decisions.

### How Consumers Find These Entities

`bestiarium_devices` looks the entities up in the entity registry by platform `bestiarium_mode` + `unique_id` (`select`, `power_load_{green,yellow,red,overload}`), not by entity_id, and follows renames at runtime. **Do not change these unique_ids** — they are the contract with consumers and keep the registry (and user customizations) intact.

---

## Translations

Two locales supported: `en` (English) and `uk` (Ukrainian).

Translation keys cover:
- Config flow steps, menus, field labels, field descriptions
- Validation errors: `name_required`, `yellow_zone_min`, `red_zone_must_exceed_yellow`, `no_options`
- Abort reasons: `already_configured`, `reconfigure_successful`
- Entity names: `Power Load - Green/Yellow/Red`, `Overload`, `Power Load Limit`, `Mode`

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
- Green zone recovery: delayed ON, immediate OFF, cancellation on power rise, restart after cancellation, default recovery seconds, unavailable cancels recovery
- Only the red sensor has an attribute (`red_threshold`)

### `test_number.py`
- Limit entity created from the existing entry value (owner's config), `entity_id` `number.power_load_limit`, config category, range
- Zones at the configured limit; recalculated on limit change without a power change; attributes follow the limit
- No reload on limit change; same value is not written and fires no event; event payload
- Limit survives a reload; any value in range accepted; out-of-range rejected

### `test_select.py`
- Entity creation, initial state (first option), options list
- A static icon; no metadata attributes

### `test_migration.py`
- 1.2: description and icon go, the rest stays

---

## Design Notes & Gotchas

1. **Singleton constraint**: The integration enforces a single config entry via `async_set_unique_id("bestiarium_mode")` + `_abort_if_unique_id_configured()`.

2. **Hard-coded edit steps**: Due to HA config flow limitations (step methods must be named), there are 10 hard-coded `async_step_edit_N` methods. Maximum 10 mode options.

3. **Recovery timeout is Green-only**: Only the Green zone has delayed ON behavior. Yellow, Red, and Overload respond immediately to power changes. This prevents rapid cycling when power hovers near the yellow threshold.

4. **No API/device integration**: This is a purely local, config-driven integration. No external API calls, no device communication.

5. **RestoreEntity on Select**: The select entity restores its state across HA restarts. If the restored option was removed during reconfiguration, it falls back to the first available option.

6. **Config flow menu pattern**: The flow uses `async_show_menu` for the main options list, which is a less common HA pattern. Menu options are dynamically generated from current state.

7. **ac_load_limits is optional**: If not configured, the binary sensor and number platforms skip entity creation entirely. The select entity is always created.

8. **Type alias**: `BestiariumModeConfigEntry = ConfigEntry[dict]` — a simple type alias for clarity, no runtime behavior.

