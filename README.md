# Bestiarium

A set of [Home Assistant](https://www.home-assistant.io/) custom integrations for **smart power balancing** of heating and cooling devices. Bestiarium monitors grid load, rotates devices within a capacity budget, and handles non-responding switches — all without cloud dependencies.

## Integrations

The project ships four custom components that work together:

### bestiarium_mode

Defines operating **modes** (user-created profiles) and monitors **AC power load zones** (green / yellow / red / overload) derived from a power sensor and configurable thresholds.

- `select` entity — active mode selector
- `binary_sensor` entities — one per load zone (green, yellow, red, overload)
- `number` entity — current load limit (W), changeable at runtime, e.g. by an automation when the power source changes

### bestiarium_devices

Registers physical and virtual devices with **per-mode load priorities** and runs a `BalancingCoordinator` that applies green / red / overload strategies to shed or restore load. Depends on `bestiarium_mode`.

### bestiarium_climate

Provides **capacity-limited rotation** of heating/cooling devices through virtual thermostats and virtual switches.

- Virtual thermostats compute temperature demand (hysteresis) and expose an `expected_switch_state` flag.
- A `BalancingCoordinator` allocates a limited number of simultaneous device slots, rotating fairly by working time.
- Handles switch retry, non-responding switch recovery, and fires `bestiarium_climate_switch_failed` events.
- Uses HA subentries — switches and thermostats are added under a single config entry.

Depends on `bestiarium_mode`.

### bestiarium_mock

Standalone test harness that simulates **13 smart-socket devices**, **4 thermometers**, and a **grid power monitor** with a time-delayed overload alarm. No physical hardware required.

## Installation

Copy the desired integration folders from `custom_components/` into your Home Assistant `custom_components/` directory and restart.

```
custom_components/
├── bestiarium_mode/
├── bestiarium_devices/
├── bestiarium_climate/
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

### Knowledge base

Detailed per-integration documentation (file structure, config schemas, entity specs, coordinator logic, design notes) lives in `docs/`:

- [`docs/bestiarium_mode.md`](docs/bestiarium_mode.md)
- [`docs/bestiarium_devices.md`](docs/bestiarium_devices.md)
- [`docs/bestiarium_climate.md`](docs/bestiarium_climate.md)
- [`docs/bestiarium_mock.md`](docs/bestiarium_mock.md)

## License

[MIT](LICENSE)
