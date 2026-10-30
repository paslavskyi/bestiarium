# Bestiarium

A set of [Home Assistant](https://www.home-assistant.io/) custom integrations for **smart power balancing** of heating and cooling devices. Bestiarium monitors grid load, rotates devices within a capacity budget, and handles non-responding switches — all without cloud dependencies.

## Integrations

The project ships five custom components that work together:

### bestiarium_mode

Defines operating **modes** (user-created profiles) and monitors **AC power load zones** (green / yellow / red / overload) derived from a power sensor and configurable thresholds.

- `select` entity — active mode selector
- `binary_sensor` entities — one per load zone (green, yellow, red, overload)
- `number` entity — current load limit (W), changeable at runtime, e.g. by an automation when the power source changes

### bestiarium_devices

Registers physical and virtual devices with **per-mode load priorities** and runs a `BalancingCoordinator` that applies green / red / overload strategies to shed or restore load. Depends on `bestiarium_mode`.

- `switch` entity per device — "Allowed": off keeps the device off and out of balancing
- Device option "switched on by its own thermostat" — the balancer only turns such devices off
- Headroom: the balancer turns a device on only if the grid load plus the device's expected power stays below the red zone; the expected power is learned from the device's runs
- Device control: the balancer waits for each device's own response time, defers a device that does not respond so the queue goes on, and shows confirmed problems as a diagnostic "Control state" sensor and "Control events" entity; the thresholds are the device's options
- `sensor` "Expected power" per device; the estimated power and the reset of the power memory are in the device's settings form

### bestiarium_climate

Provides **capacity-limited rotation** of heating/cooling devices through virtual thermostats and virtual switches.

- Virtual thermostats compute temperature demand (hysteresis); the balancer controls their switches.
- A `BalancingCoordinator` allocates a limited number of simultaneous device slots, rotating fairly by working time, and follows the load zones of `bestiarium_mode` (yellow starts nothing, red turns heaters off one by one).
- Each thermostat has a "Control state" sensor: switch unavailable, not responding (retried, then deferred), no temperature.
- Virtual switches run HA script sequences and read their state from a power sensor.
- Uses HA subentries — switches and thermostats are added under a single config entry.

Depends on `bestiarium_mode`.

### bestiarium_ir

IR devices as ordinary devices of HA, without scripts of pre-generated codes.

- A Zigbee TS1201 blaster (ZHA) becomes an `infrared` emitter of HA.
- A Hisense air conditioner (remote DG11R2-01, protocol Kelon168) becomes a `climate` device with a "Display" switch, on any `infrared` emitter; its state is assumed and restored.
- Uses HA subentries — blasters and air conditioners are added under a single config entry.

### bestiarium_mock

Test stand that simulates **19 smart sockets** (three of them the power of air conditioners run by Bestiarium Climate through virtual switches) (one per test scenario, each with a fault profile: reliable, slow cloud, unstable, stuck state, unresponsive or custom), **5 thermometers**, and a **grid power monitor**, with reading fault profiles for the thermometers and the grid. Its IR air conditioner (a Hisense behind a socket) hears its own emitter and recognises the frames with the Kelon168 module of `bestiarium_ir`. No physical hardware required. `make stand` sets up the stand and its dashboard in the development HA.

## Installation

Copy the desired integration folders from `custom_components/` into your Home Assistant `custom_components/` directory and restart.

```
custom_components/
├── bestiarium_mode/
├── bestiarium_devices/
├── bestiarium_climate/
├── bestiarium_ir/
└── bestiarium_mock/
```

Each integration is added through the Home Assistant UI via **Settings → Devices & Services → Add Integration**.

## Development

The project uses a devcontainer for development. Open in VS Code or Cursor with the Dev Containers extension.

Requires Python 3.14 (Home Assistant 2026.10).

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/
```

### Concepts

Every change follows the Bestiarium concepts in [`docs/concepts/`](docs/concepts/README.md): [problem reporting](docs/concepts/problem-reporting.md), [settings vs entities](docs/concepts/settings-vs-entities.md) and [stability and adaptation](docs/concepts/stability.md). The skill [`.claude/skills/bestiarium-concepts`](.claude/skills/bestiarium-concepts/SKILL.md) routes a change to them and holds the pre-commit checklist.

### Knowledge base

Detailed per-integration documentation (file structure, config schemas, entity specs, coordinator logic, design notes) lives in `docs/`:

- [`docs/bestiarium_mode.md`](docs/bestiarium_mode.md)
- [`docs/bestiarium_devices.md`](docs/bestiarium_devices.md)
- [`docs/bestiarium_climate.md`](docs/bestiarium_climate.md)
- [`docs/bestiarium_ir.md`](docs/bestiarium_ir.md)
- [`docs/bestiarium_mock.md`](docs/bestiarium_mock.md)

## Brand assets

Each integration ships its icon and logo in `custom_components/<domain>/brand/` (Home Assistant 2026.3+ shows them; restart Home Assistant after updating). Source SVGs are in [`docs/brand/`](docs/brand/README.md).

## License

[MIT](LICENSE)
