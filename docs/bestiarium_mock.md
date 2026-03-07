# bestiarium_mock — Home Assistant Custom Integration

## Overview

`bestiarium_mock` is a Home Assistant custom integration (domain: `bestiarium_mock`, version 0.1.0) that provides **simulated smart-home devices** for testing the Bestiarium ecosystem without physical hardware. It has no dependencies on other integrations.

Core capabilities:

1. **13 mock smart-socket devices** — Each has a controllable switch, a power sensor, a state binary sensor, and a "mock enabled" toggle. 3 of the 13 have variable power via a number entity.
2. **4 mock thermometers** — Each has a temperature sensor and a number entity for manual temperature input.
3. **Grid simulation** — A grid power sensor (sum of all device power + random background noise) and a grid overload alarm binary sensor with a time-delayed trigger.
4. **Singleton integration** — Only one config entry is allowed (enforced by unique ID).

The integration is purely a test harness. All entity state is managed by a central `MockDevicesCoordinator` that recalculates power and state when any switch or number changes.

---

## File Structure

```
custom_components/bestiarium_mock/
├── __init__.py          # Entry setup/unload, coordinator lifecycle
├── manifest.json        # Integration metadata (no dependencies)
├── const.py             # Constants, device specs, thermometer specs, grid params
├── data.py              # Type alias for config entry
├── config_flow.py       # Singleton config flow (confirmation only)
├── coordinator.py       # MockDevicesCoordinator — central state management
├── switch.py            # MockDeviceSwitch + MockEnabledSwitch
├── sensor.py            # MockDevicePowerSensor + MockGridPowerSensor + MockThermometerTemperatureSensor
├── binary_sensor.py     # MockDeviceStateSensor + MockGridAlarmSensor
├── number.py            # MockPowerNumber (variable-power devices) + MockTemperatureNumber (thermometers)
└── translations/
    ├── en.json           # English translations
    └── uk.json           # Ukrainian translations

tests/custom_components/bestiarium_mock/
├── __init__.py
├── test_config_flow.py
├── test_entities.py
└── test_coordinator.py
```

---

## Platforms

```python
PLATFORMS = [Platform.SWITCH, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER]
```

---

## Config Entry

**Singleton**: Unique ID is set to `DOMAIN` ("bestiarium_mock"). Only one instance allowed.

**Data**: Empty dict `{}`. No user-configurable parameters.

**Type alias**:
```python
type BestiariumMockConfigEntry = ConfigEntry[MockDevicesCoordinator]
```

---

## Device Specifications (`const.py`)

### MockDeviceSpec

```python
@dataclass(frozen=True)
class MockDeviceSpec:
    device_id: str
    name: str
    power_w: int                    # Default/fixed power in watts
    power_min_w: int | None = None  # If set, device has variable power
    power_max_w: int | None = None
```

### Mock Devices (13 total)

| device_id | name | power_w | Variable Power Range |
|---|---|---|---|
| `boiler` | Mock Boiler | 1800 | — |
| `ac` | Mock AC | 1200 | — |
| `cooker` | Mock Cooker | 400 | 400–4200 W |
| `router` | Mock Router | 20 | — |
| `kettle` | Mock Kettle | 1000 | — |
| `heating` | Mock Heating | 1000 | — |
| `washing_machine` | Mock Washing Machine | 60 | 60–1200 W |
| `dishwasher` | Mock Dishwasher | 60 | 60–1800 W |
| `fridge` | Mock Fridge | 80 | — |
| `socket_1` | Mock Socket 1 | 600 | — |
| `socket_2` | Mock Socket 2 | 600 | — |
| `socket_3` | Mock Socket 3 | 600 | — |
| `socket_4` | Mock Socket 4 | 600 | — |

### MockThermometerSpec

```python
@dataclass(frozen=True)
class MockThermometerSpec:
    device_id: str
    name: str
    default_temp_c: float = 20.0
    temp_min_c: float = -20.0
    temp_max_c: float = 50.0
```

### Mock Thermometers (4 total)

| device_id | name | Default | Range |
|---|---|---|---|
| `thermo_1` | Mock Thermometer 1 | 20.0°C | -20.0 to 50.0°C |
| `thermo_2` | Mock Thermometer 2 | 20.0°C | -20.0 to 50.0°C |
| `thermo_3` | Mock Thermometer 3 | 20.0°C | -20.0 to 50.0°C |
| `thermo_4` | Mock Thermometer 4 | 20.0°C | -20.0 to 50.0°C |

### Grid Constants

| Constant | Value |
|---|---|
| `GRID_MAX_POWER_W` | 5000 |
| `GRID_RANDOM_MIN_W` | 40 |
| `GRID_RANDOM_MAX_W` | 100 |
| `GRID_ALARM_THRESHOLD_SECONDS` | 10 |
| `GRID_UPDATE_INTERVAL_SECONDS` | 5 |
| `MOCK_GRID.device_id` | `"grid"` |

---

## Entities Per Mock Device

Each of the 13 mock devices produces the following entities, grouped under one HA device:

| Entity Type | Class | Unique ID Pattern | Translation Key | Notes |
|---|---|---|---|---|
| `switch` | `MockDeviceSwitch` | `mock_{device_id}` | `device_switch` | Main on/off control. RestoreEntity. |
| `switch` | `MockEnabledSwitch` | `mock_{device_id}_mock_enabled` | `mock_enabled` | Enables/disables mock behavior. Default ON. RestoreEntity. Icon: `mdi:test-tube`. |
| `sensor` | `MockDevicePowerSensor` | `mock_{device_id}_power` | `power` | Power in watts. device_class=POWER. |
| `binary_sensor` | `MockDeviceStateSensor` | `mock_{device_id}_state` | `state` | ON when power > 0. device_class=POWER. |
| `number` | `MockPowerNumber` | `mock_{device_id}_mock_power` | `mock_power` | **Only for variable-power devices** (cooker, washing_machine, dishwasher). RestoreEntity. Icon: `mdi:lightning-bolt`. |

**Device info**: `identifiers={(DOMAIN, f"mock_{device_id}")}`, manufacturer="Bestiarium", model="Mock Smart Socket".

## Entities Per Mock Thermometer

Each of the 4 mock thermometers produces:

| Entity Type | Class | Unique ID Pattern | Translation Key | Notes |
|---|---|---|---|---|
| `sensor` | `MockThermometerTemperatureSensor` | `mock_{device_id}_temperature` | `temperature` | Temperature in °C. device_class=TEMPERATURE. |
| `number` | `MockTemperatureNumber` | `mock_{device_id}_mock_temperature` | `mock_temperature` | Manual temperature input. RestoreEntity. Step: 0.1. Icon: `mdi:thermometer`. |

**Device info**: `identifiers={(DOMAIN, f"mock_{device_id}")}`, manufacturer="Bestiarium", model="Mock Thermometer".

## Grid Entities

| Entity Type | Class | Unique ID | Translation Key | Notes |
|---|---|---|---|---|
| `sensor` | `MockGridPowerSensor` | `mock_grid_power` | `grid_power` | Total power in watts. device_class=POWER. |
| `binary_sensor` | `MockGridAlarmSensor` | `mock_grid_alarm` | `grid_alarm` | Overload alarm. device_class=PROBLEM. Icon: `mdi:flash-alert`. |

**Device info**: `identifiers={(DOMAIN, "mock_grid")}`, manufacturer="Bestiarium", model="Mock Grid Monitor".

---

## Entity Count Summary

| Category | switch | sensor | binary_sensor | number | Total |
|---|---|---|---|---|---|
| 13 devices | 26 (13 main + 13 mock_enabled) | 13 power | 13 state | 3 variable power | 55 |
| 4 thermometers | — | 4 temperature | — | 4 temperature | 8 |
| Grid | — | 1 grid power | 1 grid alarm | — | 2 |
| **Total** | **26** | **18** | **14** | **7** | **65** |

---

## Integration Lifecycle (`__init__.py`)

### Setup (`async_setup_entry`)

1. Creates `MockDevicesCoordinator(hass, entry)`
2. Stores coordinator at `hass.data[DOMAIN][entry.entry_id]`
3. Forwards platform setups (SWITCH, SENSOR, BINARY_SENSOR, NUMBER)
4. Calls `coordinator.async_start()` — runs initial updates, starts 5s grid timer
5. Registers reload listener

### Unload (`async_unload_entry`)

1. Calls `coordinator.async_stop()` — cancels grid timer
2. Removes coordinator from `hass.data`
3. Unloads platforms

---

## Config Flow (`config_flow.py`)

Class: `BestiariumMockFlowHandler` (VERSION = 1)

Simple confirmation flow — no input fields:

1. `async_set_unique_id(DOMAIN)` + `_abort_if_unique_id_configured()` — enforces singleton
2. First visit shows a form with `description_placeholders={"device_count": "10"}`
3. On confirmation (empty user_input), creates entry with title "Mock Devices" and empty data

---

## MockDevicesCoordinator (`coordinator.py`)

Central state manager. All entities register themselves with the coordinator during platform setup.

### Registration Methods

Entities register via typed methods:
- `register_device_switch(device_id, switch)`
- `register_mock_enabled_switch(device_id, switch)`
- `register_power_sensor(device_id, sensor)`
- `register_power_number(device_id, number)`
- `register_state_sensor(device_id, sensor)`
- `register_grid_power_sensor(sensor)`
- `register_grid_alarm_sensor(sensor)`
- `register_temperature_sensor(device_id, sensor)`
- `register_temperature_number(device_id, number)`

### Notification Callbacks

Called by entities when user changes state:

| Method | Trigger | Updates |
|---|---|---|
| `notify_switch_changed(device_id)` | Device switch toggled | Device + grid |
| `notify_mock_enabled_changed(device_id)` | Mock enabled switch toggled | Device + grid |
| `notify_power_number_changed(device_id)` | Power number value set | Device + grid |
| `notify_temperature_number_changed(device_id)` | Temperature number value set | Thermometer only |

### Power Calculation (`_calculate_device_power`)

For a given device:
1. If switch is OFF or mock_enabled is OFF → 0 watts
2. If a `MockPowerNumber` is registered and has a value → use that value
3. Otherwise → use `MockDeviceSpec.power_w` (fixed default)

### Device Update (`_update_device`)

Sets power sensor and state sensor based on calculated power (state = ON when power > 0).

### Thermometer Update (`_update_thermometer`)

Reads value from `MockTemperatureNumber` and pushes it to the corresponding `MockThermometerTemperatureSensor`.

### Grid Update (`_update_grid`)

1. Sums `_calculate_device_power` for all 13 devices
2. Adds `_grid_random_power` (background noise)
3. Writes total to grid power sensor
4. Calls `_update_grid_alarm`

### Grid Alarm (`_update_grid_alarm`)

Time-delayed overload detection:

- **Power > 5000 W and no timer**: Start `_overload_start_time = now`
- **Power > 5000 W and timer running**: If elapsed >= 10s → alarm ON
- **Power <= 5000 W**: Reset timer, alarm OFF

### Periodic Grid Update

Every 5 seconds (`GRID_UPDATE_INTERVAL_SECONDS`), randomizes `_grid_random_power` between 40–100 W and recalculates grid.

---

## Entity Details

### MockDeviceSwitch (`switch.py`)

- `RestoreEntity` — restores on/off state from last state
- On turn_on/turn_off: sets `_attr_is_on`, writes state, calls `coordinator.notify_switch_changed`
- Default state: OFF

### MockEnabledSwitch (`switch.py`)

- `RestoreEntity` — restores on/off state
- Controls whether the mock device produces power when its main switch is on
- Default state: ON (mock behavior enabled)
- Icon: `mdi:test-tube`

### MockDevicePowerSensor (`sensor.py`)

- `device_class=POWER`, `state_class=MEASUREMENT`, unit=W
- Passive — set by coordinator via `set_power(power)`

### MockGridPowerSensor (`sensor.py`)

- `device_class=POWER`, `state_class=MEASUREMENT`, unit=W
- Passive — set by coordinator via `set_power(power)`

### MockThermometerTemperatureSensor (`sensor.py`)

- `device_class=TEMPERATURE`, `state_class=MEASUREMENT`, unit=°C
- Passive — set by coordinator via `set_temperature(temperature)`
- Default value: `thermo_spec.default_temp_c` (20.0)

### MockDeviceStateSensor (`binary_sensor.py`)

- `device_class=POWER`
- Passive — set by coordinator via `set_state(is_on=bool)`

### MockGridAlarmSensor (`binary_sensor.py`)

- `device_class=PROBLEM`, icon=`mdi:flash-alert`
- Passive — set by coordinator via `set_alarm(is_alarm=bool)`

### MockPowerNumber (`number.py`)

- `RestoreEntity` — restores last value
- Unit=W, mode=BOX, step=1.0, icon=`mdi:lightning-bolt`
- Range from `MockDeviceSpec.power_min_w` to `power_max_w`
- Only created for devices where `has_variable_power` is True (cooker, washing_machine, dishwasher)
- On value change: writes state, calls `coordinator.notify_power_number_changed`

### MockTemperatureNumber (`number.py`)

- `RestoreEntity` — restores last value
- Unit=°C, mode=BOX, step=0.1, icon=`mdi:thermometer`
- Range from `MockThermometerSpec.temp_min_c` to `temp_max_c` (-20 to 50)
- On value change: writes state, calls `coordinator.notify_temperature_number_changed`

---

## Translations

Two locale files: `en.json`, `uk.json`.

Coverage:
- Config flow: user step (with long description listing all devices), abort reason `already_configured`
- Entity names: Switch, Mock Enabled, Power, Grid Power, Temperature, State, Grid Overload Alarm, Mock Power, Set Temperature

---

## Test Coverage

### `test_config_flow.py`
- Shows confirmation form with device count placeholder
- Creates entry on confirmation (title "Mock Devices", empty data)
- Aborts if already configured (singleton enforcement)
- Only one instance allowed (end-to-end)

### `test_entities.py`
- All expected entities created (switches, sensors, binary sensors, numbers per device; grid entities)
- Switch initial state OFF; mock_enabled initial state ON
- Power sensor initial value 0; state sensor initial state OFF
- Number entity has correct min/max range (cooker: 400–4200)
- Grid alarm initial state OFF
- Switch turn_on updates power sensor to spec value (boiler: 1800W)
- Switch turn_off sets power to 0
- Mock disabled sets power to 0 (even when switch is on)
- State sensor reflects power (ON when power > 0)
- Unload entry works correctly (ConfigEntryState transitions)

### `test_coordinator.py`
- Power = 0 when switch off; power = 0 when mock disabled
- Power from spec when switch on + mock enabled (boiler → 1800W)
- Power number entity overrides spec (cooker: number=2500 overrides spec=400)
- Grid power = sum of devices; grid includes random component
- Grid alarm OFF when under limit; starts timer when over limit
- Alarm triggers after threshold duration (10s); resets when power drops
- Alarm not triggered if duration not reached
- Notification handlers update device + grid correctly

---

## Design Notes & Gotchas

1. **Coordinator-centric architecture**: Entities are passive — they never compute their own state. All state calculation flows through the coordinator. Entities register themselves, and the coordinator pushes updates to them.

2. **Two-switch per device**: Each device has a main switch (simulates the smart socket control) and a "mock enabled" switch (simulates whether the device is plugged in / responding). Both must be ON for the device to draw power.

3. **Power number override**: For variable-power devices, the `MockPowerNumber` value takes precedence over the device spec's default `power_w`. This allows testing different power draw scenarios.

4. **Grid random noise**: The 5-second periodic timer adds 40–100 W of random background power to simulate baseline household consumption. Uses `random.uniform` (not cryptographic).

5. **Time-delayed alarm**: The grid alarm does NOT trigger instantly when power > 5000 W. It requires sustained overload for 10 seconds (`GRID_ALARM_THRESHOLD_SECONDS`). This mirrors real breaker behavior.

6. **No strings.json**: Unlike the other bestiarium integrations, there is no `strings.json` — translations are loaded directly from `translations/en.json`.

7. **State restoration**: Switches (`MockDeviceSwitch`, `MockEnabledSwitch`) and number entities (`MockPowerNumber`, `MockTemperatureNumber`) use `RestoreEntity` to persist state across restarts. Sensors and binary sensors are coordinator-driven and recalculated on startup.

8. **Entity grouping**: All entities for one physical mock device share the same device info via `identifiers={(DOMAIN, f"mock_{device_id}")}`, so they appear together in the HA device page.

9. **Platform setup order matters**: `__init__.py` calls `async_forward_entry_setups` BEFORE `coordinator.async_start()`. This ensures all entities are registered with the coordinator before the initial update runs.

10. **No dependency on other bestiarium integrations**: Unlike `bestiarium_climate` and `bestiarium_devices` which depend on `bestiarium_mode`, this integration is standalone and can be used independently for testing.
