// What the power flow card shows, from the states of HA: pure functions,
// tested with node --test (tests/frontend/).

// The entities of the owner's home; a card's `entities` overrides them
export const DEFAULTS = {
  mode: "sensor.zhyvlennia_rezhym",
  inverter_home_relay: "switch.rele_invertor_dim",
  grid_inverter_relay: "switch.serverna_rele_invertor",
  grid_present: "binary_sensor.zhyvlennia_merezha_ye",
  home_power: "sensor.grid_main_load_em0_power",
  grid_power: "sensor.grid_main_load_em1_power",
  sun_power: "sensor.livoltek_pv_power",
  // kW, "+" is a discharge; it shows a flow only while the charge changes
  battery_flow: "sensor.zhyvlennia_batareia_potuzhnist_skhema",
  battery_charge: "sensor.zhyvlennia_zariad",
  battery_hours_left: "sensor.zhyvlennia_batareia_hodyn",
  battery_hours_to_full: "sensor.batareia_do_povnogo_zariadu",
};

// Below this power (kW) nothing flows
const THRESHOLD = 0.05;
const DASH = "—";

export const KEYS = {
  nodes: ["grid", "sun", "inv", "home", "bat"],
  wires: ["gridInv", "gridHome", "sun", "invHome", "invBat"],
};

const known = (stateObj) =>
  stateObj && !["unavailable", "unknown"].includes(stateObj.state) ? stateObj : null;

const onOff = (stateObj) => (known(stateObj) ? stateObj.state === "on" : null);

const number = (stateObj) => {
  const value = known(stateObj) ? Number(stateObj.state) : NaN;
  return Number.isFinite(value) ? value : null;
};

const kilowatts = (stateObj) => {
  const value = number(stateObj);
  return value !== null && stateObj.attributes.unit_of_measurement === "W" ? value / 1000 : value;
};

export function readings(states, entities) {
  const of = (key) => states[entities[key]];
  return {
    mode: known(of("mode"))?.state ?? null,
    inverterHome: onOff(of("inverter_home_relay")),
    gridInverter: onOff(of("grid_inverter_relay")),
    gridPresent: onOff(of("grid_present")),
    homeKw: kilowatts(of("home_power")),
    gridKw: kilowatts(of("grid_power")),
    sunKw: kilowatts(of("sun_power")),
    batteryKw: kilowatts(of("battery_flow")),
    charge: number(of("battery_charge")),
    hoursLeft: number(of("battery_hours_left")),
    hoursToFull: number(of("battery_hours_to_full")),
  };
}

const decimal = (value, step) =>
  String(Number((Math.round(Math.abs(value) / step) * step).toFixed(1))).replace(".", ",");

export const kw = (value) => `${decimal(value, 0.1)} кВт`;
export const hours = (value) => `≈${decimal(value, 0.5)} год`;

const flows = (kilowatts) => kilowatts !== null && Math.abs(kilowatts) > THRESHOLD;

// The faster the more power, as in the mockup; in 0.05 s steps, so small
// changes of the power do not restart the animation
const speed = (kilowatts) => Math.round(Math.max(0.35, 1.6 - Math.abs(kilowatts) * 0.35) * 20) / 20;

const wire = (status, kilowatts = null, reverse = false) => ({
  status,
  speed: status === "flow" ? speed(kilowatts ?? 0) : null,
  reverse,
});

export function flowModel(r) {
  const noGrid = r.gridPresent === false;
  // Without the grid the inverter feeds the home, whatever the relays show
  const homeFromInv = r.inverterHome === true || noGrid;
  const homeFromGrid = r.inverterHome === false && !noGrid;
  const gridToInv = r.gridInverter === true && !noGrid;
  // The grid's power beyond what it feeds the home straight
  let gridToInvKw = r.gridKw;
  if (homeFromGrid) {
    gridToInvKw = r.gridKw === null || r.homeKw === null ? null : Math.max(0, r.gridKw - r.homeKw);
  }

  // The sensor sees a discharge only once the charge falls; with no grid
  // behind the inverter feeding the home, the battery covers what the sun lacks.
  // Without the grid the cloud's sun often goes too: unknown counts as none
  let batKw = r.batteryKw;
  const lacking = r.homeKw === null ? null : r.homeKw - (r.sunKw ?? 0);
  // A charge only from the sensor
  if (homeFromInv && (noGrid || r.gridInverter === false) && lacking > THRESHOLD) {
    batKw = Math.max(r.batteryKw ?? 0, lacking);
  }
  const discharging = flows(batKw) && batKw > 0;
  const charging = flows(batKw) && batKw < 0;
  const sunFlows = r.sunKw > THRESHOLD;

  const wires = {
    gridInv: wire(gridToInv ? (flows(gridToInvKw) ? "flow" : "idle") : "off", gridToInvKw),
    gridHome: wire(homeFromGrid ? "flow" : "off", r.homeKw),
    sun: wire(sunFlows ? "flow" : "idle", r.sunKw),
    invHome: wire(homeFromInv ? "flow" : "off", r.homeKw),
    invBat: wire(flows(batKw) ? "flow" : "idle", batKw, discharging),
  };

  let gridLines = [DASH];
  if (noGrid) gridLines = ["немає"];
  else if (r.gridPresent) gridLines = [flows(r.gridKw) ? kw(r.gridKw) : "є"];

  let sunLines = [DASH];
  if (r.sunKw !== null) sunLines = [sunFlows ? kw(r.sunKw) : "немає"];

  let invLines = [DASH];
  if (homeFromInv) invLines = ["живить дім"];
  else if (homeFromGrid) invLines = ["у резерві"];

  const charge = r.charge === null ? DASH : `${Math.round(r.charge)} %`;
  const batLines = [charge + (discharging ? " · віддає" : charging ? " · заряд" : "")];
  if (discharging && r.hoursLeft !== null) batLines.push(`вистачить ${hours(r.hoursLeft)}`);
  if (charging && r.hoursToFull !== null) batLines.push(`до повного ${hours(r.hoursToFull)}`);

  return {
    title: (r.mode ?? DASH) + (noGrid ? " · світла немає" : ""),
    nodes: {
      grid: { lines: gridLines, dim: noGrid },
      sun: { lines: sunLines, dim: sunLines[0] === "немає" },
      inv: { lines: invLines, dim: false },
      home: { lines: [r.homeKw === null ? DASH : kw(r.homeKw)], dim: false },
      bat: { lines: batLines, dim: false },
    },
    wires,
  };
}

// The wide scheme's values (14 of 900 units) are 11 px from a card this wide
const WIDE_FROM_PX = 760;

// The layout for a card's width; null while it is hidden (no width)
export const layoutFor = (width) => (width ? (width < WIDE_FROM_PX ? "narrow" : "wide") : null);

// The scheme of the mockup, and a narrower one for a phone whose captions sit
// above and below the circles: node centres, the viewBox, where each caption goes
const LAYOUTS = {
  wide: {
    viewBox: [900, 370],
    centres: { grid: [230, 70], sun: [670, 70], inv: [450, 190], home: [230, 310], bat: [670, 310] },
    // Beside the circle, away from the wires: the left column to the left
    captions: { grid: "left", sun: "right", inv: "right", home: "left", bat: "right" },
    lineHeight: 19,
  },
  narrow: {
    viewBox: [600, 550],
    centres: { grid: [130, 100], sun: [470, 100], inv: [300, 250], home: [130, 400], bat: [470, 400] },
    captions: { grid: "above", sun: "above", inv: "right", home: "below", bat: "below" },
    lineHeight: 26,
  },
};
const RADIUS = 34;
const ICONS = {
  grid: "mdi:transmission-tower",
  sun: "mdi:solar-panel",
  inv: "mdi:sine-wave",
  home: "mdi:home",
  bat: "mdi:battery-high",
};
const NAMES = { grid: "Мережа", sun: "Сонце", inv: "Інвертор", home: "Дім", bat: "Батарея" };
// Value lines under a name: the battery needs two
const LINES = 2;

function paths({ centres }) {
  const [g, s, i, h, b] = KEYS.nodes.map((key) => centres[key]);
  const r = RADIUS;
  return {
    gridInv: `M ${g[0] + r} ${g[1]} L ${i[0] - 20} ${g[1]} Q ${i[0]} ${g[1]} ${i[0]} ${i[1] - r}`,
    gridHome: `M ${g[0]} ${g[1] + r} L ${h[0]} ${h[1] - r}`,
    sun: `M ${s[0] - r} ${s[1]} L ${i[0] + 20} ${s[1]} Q ${i[0]} ${s[1]} ${i[0]} ${i[1] - r}`,
    invHome: `M ${i[0]} ${i[1] + r} Q ${i[0]} ${h[1]} ${i[0] - 20} ${h[1]} L ${h[0] + r} ${h[1]}`,
    invBat: `M ${i[0]} ${i[1] + r} Q ${i[0]} ${b[1]} ${i[0] + 20} ${b[1]} L ${b[0] - r} ${b[1]}`,
  };
}

// Where a node's caption starts (x, the label's baseline) and how it is anchored
function caption(layout, key) {
  const [x, y] = layout.centres[key];
  const step = layout.lineHeight;
  switch (layout.captions[key]) {
    case "left":
      return { x: x - 48, y: y - 2, anchor: "end" };
    case "right":
      return { x: x + 48, y: y - 2, anchor: "start" };
    // A name and one value above the circle: the top nodes have one value line
    case "above":
      return { x, y: y - RADIUS - 14 - step, anchor: "middle" };
    default:
      return { x, y: y + RADIUS + step + 4, anchor: "middle" };
  }
}

function node(layout, key) {
  const [x, y] = layout.centres[key];
  const { x: tx, y: ty, anchor } = caption(layout, key);
  const text = (cls, id, n, content = "") =>
    `<text class="${cls}" id="${id}" x="${tx}" y="${ty + n * layout.lineHeight}" text-anchor="${anchor}">${content}</text>`;
  const lines = Array.from({ length: LINES }, (_, n) => text("node-value", `node-${key}-line-${n}`, n + 1)).join("");
  return `<g class="node" id="node-${key}">
    <circle cx="${x}" cy="${y}" r="${RADIUS}"/>
    <foreignObject x="${x - 20}" y="${y - 20}" width="40" height="40"><ha-icon icon="${ICONS[key]}"></ha-icon></foreignObject>
    ${text("node-label", `node-${key}-label`, 0, NAMES[key])}${lines}
  </g>`;
}

export function skeleton(name) {
  const layout = LAYOUTS[name];
  const d = paths(layout);
  const wires = KEYS.wires
    .map(
      (key) => `<path class="wire" id="wire-${key}" d="${d[key]}"/>
    <path class="dots${key === "sun" ? " sun" : ""}" id="dots-${key}" d="${d[key]}"/>`,
    )
    .join("");
  const [width, height] = layout.viewBox;
  return `<svg class="${name}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">${wires}${KEYS.nodes
    .map((key) => node(layout, key))
    .join("")}</svg>`;
}
