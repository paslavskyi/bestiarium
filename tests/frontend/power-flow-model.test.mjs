// The flow model of the power flow card: the brief's acceptance criteria.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULTS,
  flowModel,
  hours,
  kw,
  readings,
  skeleton,
} from "../../custom_components/bestiarium_mode/frontend/power-flow-model.js";

// A calm home: both relays on, the grid there, nothing flowing
const CALM = {
  mode: "Звичайний",
  inverterHome: true,
  gridInverter: true,
  gridPresent: true,
  homeKw: 0,
  gridKw: 0,
  sunKw: 0,
  batteryKw: 0,
  charge: 91,
  hoursLeft: null,
  hoursToFull: null,
};

const model = (overrides) => flowModel({ ...CALM, ...overrides });

// What HA would hold for the calm home, by the default entities
const CALM_STATES = {
  [DEFAULTS.mode]: { state: "Звичайний", attributes: {} },
  [DEFAULTS.inverter_home_relay]: { state: "on", attributes: {} },
  [DEFAULTS.grid_inverter_relay]: { state: "on", attributes: {} },
  [DEFAULTS.grid_present]: { state: "on", attributes: {} },
  [DEFAULTS.home_power]: { state: "1200", attributes: { unit_of_measurement: "W" } },
  [DEFAULTS.grid_power]: { state: "1500", attributes: { unit_of_measurement: "W" } },
  [DEFAULTS.sun_power]: { state: "1.8", attributes: { unit_of_measurement: "kW" } },
  [DEFAULTS.battery_flow]: { state: "0", attributes: { unit_of_measurement: "kW" } },
  [DEFAULTS.battery_charge]: { state: "91", attributes: { unit_of_measurement: "%" } },
  [DEFAULTS.battery_hours_left]: { state: "7.5", attributes: { unit_of_measurement: "h" } },
  [DEFAULTS.battery_hours_to_full]: { state: "unknown", attributes: {} },
};

test("criterion 1: normal mode feeds the home through the inverter", () => {
  const m = model({ homeKw: 1.2, gridKw: 1.5 });
  assert.equal(m.wires.invHome.status, "flow");
  assert.equal(m.wires.gridHome.status, "off");
  assert.equal(m.wires.gridInv.status, "flow");
  assert.equal(model({ homeKw: 1.2, gridKw: 0 }).wires.gridInv.status, "idle");
  assert.deepEqual(m.nodes.inv.lines, ["живить дім"]);
  assert.equal(m.title, "Звичайний");
});

test("criterion 2: powerful mode feeds the home from the grid", () => {
  const m = model({ mode: "Потужний", inverterHome: false, homeKw: 1.2, gridKw: 1.2 });
  assert.equal(m.wires.gridHome.status, "flow");
  assert.equal(m.wires.invHome.status, "off");
  assert.notEqual(m.wires.gridInv.status, "off");
  assert.deepEqual(m.nodes.inv.lines, ["у резерві"]);
});

test("criterion 3: powerful with solar charging", () => {
  const m = model({
    inverterHome: false,
    gridInverter: false,
    homeKw: 1.2,
    gridKw: 1.2,
    sunKw: 1.8,
    batteryKw: -0.6,
    hoursToFull: 1.5,
  });
  assert.equal(m.wires.gridHome.status, "flow");
  assert.equal(m.wires.gridInv.status, "off");
  assert.equal(m.wires.sun.status, "flow");
  assert.equal(m.wires.invBat.status, "flow");
  assert.equal(m.wires.invBat.reverse, false);
  assert.deepEqual(m.nodes.bat.lines, ["91 % · заряд", "до повного ≈1,5 год"]);
});

test("criterion 4: autonomous without sun discharges the battery into the home", () => {
  const m = model({
    gridInverter: false,
    homeKw: 0.9,
    batteryKw: 0.9,
    hoursLeft: 7.5,
  });
  assert.equal(m.wires.invBat.status, "flow");
  assert.equal(m.wires.invBat.reverse, true);
  assert.equal(m.wires.invHome.status, "flow");
  assert.deepEqual(m.nodes.bat.lines, ["91 % · віддає", "вистачить ≈7,5 год"]);
});

test("criterion 4: without a grid behind the inverter the battery discharges before its sensor sees it", () => {
  const autonomous = model({ gridInverter: false, homeKw: 1.2 });
  assert.equal(autonomous.wires.invBat.status, "flow");
  assert.equal(autonomous.wires.invBat.reverse, true);
  assert.equal(autonomous.nodes.bat.lines[0], "91 % · віддає");
  // The grid behind the inverter may cover the home
  assert.equal(model({ homeKw: 1.2 }).wires.invBat.status, "idle");
  for (const [inverterHome, gridInverter] of [
    [true, true],
    [false, true],
    [false, false],
  ]) {
    const outage = model({ gridPresent: false, inverterHome, gridInverter, homeKw: 1.2 });
    assert.equal(outage.wires.invBat.status, "flow");
    assert.equal(outage.wires.invBat.reverse, true);
  }
  // A charge only from the sensor: the sun's surplus may meet a full battery
  assert.equal(
    model({ gridInverter: false, homeKw: 1.2, sunKw: 1.8 }).wires.invBat.status,
    "idle",
  );
});

test("criterion 5: no grid feeds the home from the inverter whatever the relays", () => {
  const m = model({ mode: "Потужний", gridPresent: false, inverterHome: false, homeKw: 1.2 });
  assert.equal(m.wires.invHome.status, "flow");
  assert.equal(m.wires.gridHome.status, "off");
  assert.equal(m.wires.gridInv.status, "off");
  assert.deepEqual(m.nodes.grid, { lines: ["немає"], dim: true });
  assert.equal(m.title, "Потужний · світла немає");
});

test("criterion 6: a zero battery flow is an idle wire", () => {
  const m = model({ homeKw: 1.2, sunKw: 1.8 });
  assert.equal(m.wires.invBat.status, "idle");
  assert.deepEqual(m.nodes.bat.lines, ["91 %"]);
});

test("criterion 7: unavailable sun shows a dash and an idle wire", () => {
  const states = {
    ...CALM_STATES,
    [DEFAULTS.sun_power]: { state: "unavailable", attributes: {} },
  };
  const m = flowModel(readings(states, DEFAULTS));
  assert.deepEqual(m.nodes.sun.lines, ["—"]);
  assert.equal(m.wires.sun.status, "idle");
  assert.deepEqual(m.nodes.home.lines, ["1,2 кВт"]);
  assert.deepEqual(m.nodes.grid.lines, ["1,5 кВт"]);
});

test("criterion 7: missing entity is unavailable", () => {
  const { [DEFAULTS.home_power]: _, ...states } = CALM_STATES;
  const r = readings(states, DEFAULTS);
  assert.equal(r.homeKw, null);
  assert.deepEqual(flowModel(r).nodes.home.lines, ["—"]);
  assert.equal(readings({}, DEFAULTS).mode, null);
  assert.equal(flowModel(readings({}, DEFAULTS)).title, "—");
});

test("criterion 9: numbers use a decimal comma", () => {
  assert.equal(kw(1.25), "1,3 кВт");
  assert.equal(kw(-0.6), "0,6 кВт");
  assert.equal(kw(2), "2 кВт");
  assert.equal(hours(7.5), "≈7,5 год");
  assert.equal(hours(3), "≈3 год");
});

test("criterion 10: no estimate without a flow or with an unknown estimate", () => {
  assert.deepEqual(
    model({ gridInverter: false, homeKw: 0.9, batteryKw: 0.9 }).nodes.bat.lines,
    ["91 % · віддає"],
  );
  assert.deepEqual(model({ hoursLeft: 7.5, hoursToFull: 1.5 }).nodes.bat.lines, ["91 %"]);
  assert.equal(readings(CALM_STATES, DEFAULTS).hoursToFull, null);
});

test("watts are converted by their unit", () => {
  const r = readings(CALM_STATES, DEFAULTS);
  assert.equal(r.homeKw, 1.2);
  assert.equal(r.sunKw, 1.8);
  assert.equal(r.inverterHome, true);
  assert.equal(r.charge, 91);
});

test("unknown relay leaves its wires open", () => {
  const m = model({ inverterHome: null, homeKw: 1.2 });
  assert.equal(m.wires.gridHome.status, "off");
  assert.equal(m.wires.invHome.status, "off");
  assert.deepEqual(m.nodes.inv.lines, ["—"]);
});

test("unknown grid presence trusts the relays", () => {
  const m = model({ gridPresent: null, homeKw: 1.2, gridKw: 1.5 });
  assert.equal(m.wires.invHome.status, "flow");
  assert.equal(m.wires.gridInv.status, "flow");
  assert.deepEqual(m.nodes.grid, { lines: ["—"], dim: false });
  assert.equal(m.title, "Звичайний");
});

test("below the threshold is no flow", () => {
  const m = model({ sunKw: 0.02, batteryKw: -0.01 });
  assert.deepEqual(m.nodes.sun, { lines: ["немає"], dim: true });
  assert.equal(m.wires.sun.status, "idle");
  assert.equal(m.wires.invBat.status, "idle");
});

test("speed follows the power", () => {
  assert.equal(model({ sunKw: 0.5 }).wires.sun.speed, 1.45);
  assert.equal(model({ sunKw: 5 }).wires.sun.speed, 0.35);
  assert.equal(model({}).wires.sun.speed, null);
});

test("skeleton has every wire and node", () => {
  const svg = skeleton();
  for (const key of ["gridInv", "gridHome", "sun", "invHome", "invBat"]) {
    assert.match(svg, new RegExp(`id="wire-${key}"`));
    assert.match(svg, new RegExp(`id="dots-${key}"`));
  }
  for (const key of ["grid", "sun", "inv", "home", "bat"]) {
    assert.match(svg, new RegExp(`id="node-${key}"`));
  }
});
