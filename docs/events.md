# Bestiarium Automation Events

This document describes all Home Assistant bus events fired by `bestiarium_mode`, `bestiarium_devices` and `bestiarium_climate`. Use these events to build automations that react to load limit changes, zone changes, device shedding/restoring, capacity rotation and overload shutdowns.

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

### `bestiarium_devices_zone_changed` — removed in DEV-008 stage B

The zone sensors of `bestiarium_mode` tell the same: trigger on their state
(for example `binary_sensor.power_load_red` turning `on`). See
[necessity](concepts/necessity.md). For one zone value see the template
example in [bestiarium_mode.md](bestiarium_mode.md#one-zone-value-a-template-sensor).

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

**When**: Mode priorities have been applied: after a mode change, and again
after HA has started (the owner's choice: restarts are rare).

| Field               | Type | Description                    |
|---------------------|------|--------------------------------|
| `mode`              | str  | Mode name (e.g. `"Night"`)     |
| `devices_turned_off`| list | Device IDs turned off         |
| `devices_turned_on` | list | Device IDs turned on          |
| `reason`            | str  | `"selected"` — a person or an automation chose the mode; `"restart"` — applied again after HA started (DEV-008 stage B) |

After a restart the event fires **only if a device was turned off**, and
`devices_turned_off` lists only the devices that were on: the family can turn
on what it needs.

**Example automation** — Tell the family what a restart turned off:

```yaml
trigger:
  - platform: event
    event_type: bestiarium_devices_mode_applied
    event_data:
      reason: restart
action:
  - service: notify.family_tablet
    data:
      message: >
        After a restart the mode {{ trigger.event.data.mode }} turned off
        {{ trigger.event.data.devices_turned_off | length }} devices
```

---

### Device control events (event entities)

Next to these bus events, each device has a "Control events" event entity (`event.<device>_control_events`, diagnostic) with the event types `deferred`, `retrying` and `recovered` and the attributes `direction`, `failed_attempts`, `next_attempt` — see [Device Control](bestiarium_devices.md#device-control-controlpy). Automations trigger on its state:

```yaml
trigger:
  - platform: state
    entity_id: event.rozetka_za_komodom_control_events
    attribute: event_type
    to: deferred
```

---

## bestiarium_climate Events

All events are fired from `custom_components/bestiarium_climate/balancer.py`, only in a deficit of capacity, when a thermostat goes in or out of the rotation.

Removed in DEV-008 stage B ([necessity](concepts/necessity.md)): `bestiarium_climate_switch_failed` and `_switch_recovered` (the thermostat's "Control state" sensor tells it), `bestiarium_climate_capacity_exhausted` (fired every minute of a deficit, which is the normal state).

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

In green a rotation fires `rotated_out` at once and `rotated_in` when the new heater starts (the moment it fits); in yellow only `rotated_out` (no one starts there, decision 32); the red zone's turn-offs one by one are not a rotation and fire nothing (an `info` line each), nor do the pause, no data and the overload ([climate zones](bestiarium_climate.md#load-zones-the-owners-rules-dev-008-review-13)).

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

---

## See Also

- [bestiarium_mode](bestiarium_mode.md) — Modes, load zones and the load limit

- [bestiarium_devices](bestiarium_devices.md) — Power-load balancing integration
- [bestiarium_climate](bestiarium_climate.md) — Capacity-limited rotation integration
