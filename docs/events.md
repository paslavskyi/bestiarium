# Bestiarium Automation Events

This document describes all Home Assistant bus events fired by `bestiarium_mode`, `bestiarium_devices` and `bestiarium_climate`. Use these events to build automations that react to load limit changes, zone changes, device shedding/restoring, capacity rotation, overload shutdowns, and recovery.

**Event naming convention**: `{domain}_{what_happened}` (snake_case).

---

## bestiarium_mode Events

Fired from `custom_components/bestiarium_mode/number.py`.

### `bestiarium_mode_load_limit_changed`

**When**: The current AC load limit changes (via the `number.power_load_limit` entity, from the UI or an automation). Not fired when the same value is set again. The zone binary sensors recalculate while this event is dispatched.

| Field              | Type | Description                 |
|--------------------|------|-----------------------------|
| `limit_w`          | int  | New load limit in watts     |
| `previous_limit_w` | int  | Previous load limit in watts |

**Example automations** — Set the limit when the power source changes, and log the change:

```yaml
- alias: Load limit follows the power source
  trigger:
    - platform: state
      entity_id: switch.grid_bypass_relay
  action:
    - service: number.set_value
      target:
        entity_id: number.power_load_limit
      data:
        value: "{{ 8800 if trigger.to_state.state == 'on' else 5000 }}"

- alias: Log load limit changes
  trigger:
    - platform: event
      event_type: bestiarium_mode_load_limit_changed
  action:
    - service: logbook.log
      data:
        name: Bestiarium
        message: "Load limit {{ trigger.event.data.previous_limit_w }} W → {{ trigger.event.data.limit_w }} W"
```

The values and the relay entity above are examples of a user's automation; Bestiarium itself only accepts the current limit.

---

## bestiarium_devices Events

All events are fired from `custom_components/bestiarium_devices/balancer.py`. Their payloads are a contract: each has a `TypedDict` in `const.py` (`EVENT_DATA` maps an event to it), the logbook descriptions are typed with them, and the balancer tests check every fired payload against them.

### `bestiarium_devices_zone_changed`

**When**: Active power load zone transitions (e.g. green → red).

| Field           | Type   | Description                    |
|-----------------|--------|--------------------------------|
| `previous_zone` | string | Previous active zone value     |
| `zone`          | string | New active zone value          |

**Example automation** — Notify when entering red zone:

```yaml
trigger:
  - platform: event
    event_type: bestiarium_devices_zone_changed
    event_data:
      zone: red
action:
  - service: notify.persistent_notification
    data:
      message: "Power load entered red zone"
```

---

### `bestiarium_devices_device_turned_on`

**When**: Green strategy turns on a device (restores power after load improved), or the device's "Allowed" switch is turned back on in the green zone. In both cases only if the device fits below the red zone threshold; the payload is unchanged.

| Field              | Type   | Description                    |
|--------------------|--------|--------------------------------|
| `strategy`         | string | `"green"`, or `"allowed"` when the "Allowed" switch is turned back on in the green zone |
| `device_id`        | string | Device registry ID             |
| `device_name`      | string | Human-readable device name     |
| `switch_entity_id` | string | Entity ID of the switch        |
| `priority`         | int    | Resolved load priority         |

**Example automation** — Log restored devices:

```yaml
trigger:
  - platform: event
    event_type: bestiarium_devices_device_turned_on
action:
  - service: logbook.log
    data:
      message: "Device {{ trigger.event.data.device_name }} was restored"
```

---

### `bestiarium_devices_device_turned_off`

**When**: Red strategy turns off a device (sheds load when zone is red), or the device's "Allowed" switch is turned off.

| Field              | Type   | Description                    |
|--------------------|--------|--------------------------------|
| `strategy`         | string | `"red"`, or `"allowed"` when the "Allowed" switch is turned off |
| `device_id`        | string | Device registry ID             |
| `device_name`      | string | Human-readable device name     |
| `switch_entity_id` | string | Entity ID of the switch        |
| `priority`         | int    | Resolved load priority         |

**Example automation** — Notify when a device is shed:

```yaml
trigger:
  - platform: event
    event_type: bestiarium_devices_device_turned_off
action:
  - service: notify.mobile_app
    data:
      title: "Device shed"
      message: "{{ trigger.event.data.device_name }} was turned off due to red zone"
```

---

### `bestiarium_devices_overload_first_queue`

**When**: Overload strategy completes first-queue shutdown (high-priority devices turned off).

| Field                | Type    | Description                              |
|----------------------|---------|------------------------------------------|
| `devices_turned_off` | int     | Number of devices turned off             |
| `device_ids`         | list    | Device registry IDs                      |
| `device_names`       | list    | Human-readable device names              |
| `overload_cleared`   | boolean | `true` if overload cleared after first queue |

**Example automation** — Emergency notification on overload:

```yaml
trigger:
  - platform: event
    event_type: bestiarium_devices_overload_first_queue
action:
  - service: light.turn_on
    target:
      entity_id: light.warning
    data:
      flash: short
  - service: notify.persistent_notification
    data:
      title: "Power overload"
      message: "First queue shutdown: {{ trigger.event.data.device_names | join(', ') }}"
```

---

### `bestiarium_devices_overload_second_queue`

**When**: Overload strategy completes second-queue shutdown (all remaining devices turned off).

| Field               | Type | Description                    |
|---------------------|------|--------------------------------|
| `first_queue_count` | int  | Number of devices in first queue |
| `second_queue_count`| int  | Number of devices in second queue |
| `device_ids`        | list | Device registry IDs            |
| `device_names`      | list | Human-readable device names   |

**Example automation** — Critical alert:

```yaml
trigger:
  - platform: event
    event_type: bestiarium_devices_overload_second_queue
action:
  - service: notify.mobile_app
    data:
      title: "Critical: All devices shut down"
      message: "Overload second queue executed"
```

---

### `bestiarium_devices_overload_cleared`

**When**: Overload sensor goes OFF after first-queue shutdown; second queue is skipped.

| Field               | Type   | Description                              |
|---------------------|--------|------------------------------------------|
| `cleared_after`     | string | `"first_queue"` or `"wait_period"`        |
| `first_queue_count` | int    | Number of devices turned off in first queue |

**Example automation** — Inform user situation resolved:

```yaml
trigger:
  - platform: event
    event_type: bestiarium_devices_overload_cleared
action:
  - service: notify.persistent_notification
    data:
      message: "Overload cleared after first queue ({{ trigger.event.data.first_queue_count }} devices)"
```

---

### `bestiarium_devices_mode_applied`

**When**: Mode priorities have been applied after a mode change.

| Field               | Type | Description                    |
|---------------------|------|--------------------------------|
| `mode`              | str  | New mode name (e.g. `"Night"`) |
| `devices_turned_off`| list | Device IDs turned off         |
| `devices_turned_on` | list | Device IDs turned on          |

**Example automation** — Confirm mode switch:

```yaml
trigger:
  - platform: event
    event_type: bestiarium_devices_mode_applied
action:
  - service: logbook.log
    data:
      message: "Mode {{ trigger.event.data.mode }} applied"
```

---

## bestiarium_climate Events

All events are fired from `custom_components/bestiarium_climate/balancer.py`.

### `bestiarium_climate_switch_failed`

**When**: A switch exceeds `MAX_SWITCH_RETRIES` (switch not responding to turn on/off).

| Field              | Type   | Description                    |
|--------------------|--------|--------------------------------|
| `entity_id`        | string | Thermostat entity ID           |
| `thermostat`       | string | Thermostat label               |
| `switch_entity_id` | string | Target switch entity ID        |

**Example automation** — Alert on switch failure:

```yaml
trigger:
  - platform: event
    event_type: bestiarium_climate_switch_failed
action:
  - service: notify.persistent_notification
    data:
      title: "Climate switch failed"
      message: "{{ trigger.event.data.thermostat }} — switch {{ trigger.event.data.switch_entity_id }} not responding"
```

---

### `bestiarium_climate_device_rotated_in`

**When**: Capacity slot allocated to a thermostat that was previously OFF (deficit path).

| Field                    | Type   | Description                    |
|--------------------------|--------|--------------------------------|
| `thermostat`             | string | Thermostat entity ID or label  |
| `switch_entity_id`       | string | Target switch entity ID        |
| `working_time_coefficient` | float | Sort coefficient (lower = less time) |
| `temperature_diff`       | float  | Temperature difference (demand) |

**Example automation** — Track rotation:

```yaml
trigger:
  - platform: event
    event_type: bestiarium_climate_device_rotated_in
action:
  - service: logbook.log
    data:
      message: "{{ trigger.event.data.thermostat }} rotated in"
```

---

### `bestiarium_climate_device_rotated_out`

**When**: Capacity slot removed from a thermostat that was previously ON (deficit path).

| Field                    | Type   | Description                    |
|--------------------------|--------|--------------------------------|
| `thermostat`             | string | Thermostat entity ID or label  |
| `switch_entity_id`       | string | Target switch entity ID        |
| `working_time_coefficient` | float | Sort coefficient               |
| `temperature_diff`       | float  | Temperature difference         |

**Example automation** — Know which thermostat was paused:

```yaml
trigger:
  - platform: event
    event_type: bestiarium_climate_device_rotated_out
action:
  - service: logbook.log
    data:
      message: "{{ trigger.event.data.thermostat }} rotated out"
```

---

### `bestiarium_climate_switch_recovered`

**When**: `_handle_recovery` clears `switch_not_responding` because the switch is now OFF.

| Field              | Type   | Description                    |
|--------------------|--------|--------------------------------|
| `thermostat`       | string | Thermostat entity ID or label  |
| `switch_entity_id` | string | Target switch entity ID        |

**Example automation** — Confirm device back online:

```yaml
trigger:
  - platform: event
    event_type: bestiarium_climate_switch_recovered
action:
  - service: notify.persistent_notification
    data:
      message: "{{ trigger.event.data.thermostat }} switch recovered"
```

---

### `bestiarium_climate_capacity_exhausted`

**When**: Demand exceeds capacity (deficit branch in `_calculate_expected_state`).

| Field               | Type | Description                          |
|---------------------|------|--------------------------------------|
| `capacity`          | int  | Current capacity (simultaneous slots) |
| `demand`            | int  | Number of thermostats requesting heat |
| `thermostats_on`    | list | Entity IDs with allocated slots      |
| `thermostats_waiting` | list | Entity IDs waiting for a slot      |

**Example automation** — Dashboard indicator:

```yaml
trigger:
  - platform: event
    event_type: bestiarium_climate_capacity_exhausted
action:
  - service: input_boolean.turn_on
    target:
      entity_id: input_boolean.capacity_exhausted
```

---

## See Also

- [bestiarium_mode](bestiarium_mode.md) — Modes, load zones and the load limit

- [bestiarium_devices](bestiarium_devices.md) — Power-load balancing integration
- [bestiarium_climate](bestiarium_climate.md) — Capacity-limited rotation integration
