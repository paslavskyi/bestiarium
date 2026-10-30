// The power flow card: where the home is fed from now (DEV-014, FEAT-045).
// It builds its scheme once and on each update of HA only switches classes,
// a speed and texts; the dots run by a CSS animation, no JS per frame.

import { DEFAULTS, KEYS, flowModel, layoutFor, readings, skeleton } from "./power-flow-model.js";

// Before the card has a width, the scheme of the mockup
const WIDE_UNTIL_SEEN = Infinity;

const STYLE = `
  ha-card { padding: 16px 20px 8px; container-type: inline-size; }
  ha-card.link { cursor: pointer; }
  .title { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
  .title h2 { margin: 0; font-size: 20px; font-weight: 500; color: var(--primary-text-color); }
  .title span { color: var(--secondary-text-color); font-size: 14px; }
  svg { width: 100%; height: auto; display: block; }
  circle { fill: var(--card-background-color); stroke: var(--primary-color); stroke-width: 3; }
  ha-icon { --mdc-icon-size: 34px; display: flex; justify-content: center; align-items: center;
    width: 40px; height: 40px; color: var(--primary-color); }
  .node.dim { opacity: .4; }
  .node.dim circle { stroke: var(--state-icon-color); }
  .node.dim ha-icon { color: var(--state-icon-color); }
  .node-label { font-size: 15px; font-weight: 500; fill: var(--primary-text-color); }
  .node-value { font-size: 14px; fill: var(--secondary-text-color); }
  .narrow .node-label { font-size: 24px; }
  .narrow .node-value { font-size: 22px; }
  .wire { fill: none; stroke-width: 4; stroke-linecap: round; }
  .wire.off { stroke: var(--slider-track-color); stroke-dasharray: 2 10; }
  .wire.idle { stroke: var(--primary-color); opacity: .45; }
  .wire.flow { stroke: var(--primary-color); }
  .dots { fill: none; stroke: var(--card-background-color); stroke-width: 4; stroke-linecap: round;
    stroke-dasharray: 2 16; animation: run var(--speed, 1.2s) linear infinite; display: none; }
  .dots.sun { stroke: var(--state-light-active-color); }
  .dots.on { display: inline; }
  .dots.rev { animation-direction: reverse; }
  @keyframes run { to { stroke-dashoffset: -18; } }
`;

class BestiariumPowerFlowCard extends HTMLElement {
  setConfig(config) {
    const unknown = Object.keys(config.entities ?? {}).filter((key) => !(key in DEFAULTS));
    if (unknown.length) {
      throw new Error(`Unknown entity key: ${unknown.join(", ")}`);
    }
    this._config = config;
    this._entities = { ...DEFAULTS, ...config.entities };
    this._shown = null;
    if (this._card) {
      this._link();
    }
  }

  static getStubConfig() {
    return {};
  }

  getCardSize() {
    return 5;
  }

  getGridOptions() {
    return { columns: "full" };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._card) {
      this._build();
    }
    this._update();
  }

  _build() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>${STYLE}</style>
      <ha-card><div class="title"><h2></h2><span></span></div><div class="scheme"></div></ha-card>`;
    this._card = root.querySelector("ha-card");
    this._card.addEventListener("click", () => this._navigate());
    this._link();
    this._layout = null;
    // The scheme follows the card's width: rebuilt only when the layout changes
    new ResizeObserver(([entry]) => this._scheme(entry.contentRect.width)).observe(this._card);
    this._scheme(this._card.clientWidth || WIDE_UNTIL_SEEN);
  }

  _scheme(width) {
    const layout = layoutFor(width);
    if (layout === null || layout === this._layout) {
      return;
    }
    this._layout = layout;
    this._card.querySelector(".scheme").innerHTML = skeleton(layout);
    this._shown = null;
    this._update();
  }

  _link() {
    const path = this._config.navigation_path;
    this._card.classList.toggle("link", Boolean(path));
    this._card.querySelector(".title span").textContent = path ? "дотик — екран «Живлення»" : "";
  }

  _navigate() {
    const path = this._config.navigation_path;
    if (path) {
      history.pushState(null, "", path);
      window.dispatchEvent(new CustomEvent("location-changed"));
    }
  }

  _update() {
    if (!this._hass) {
      return;
    }
    const model = flowModel(readings(this._hass.states, this._entities));
    const shown = JSON.stringify(model);
    if (shown === this._shown) {
      return;
    }
    this._shown = shown;
    const root = this.shadowRoot;
    root.querySelector("h2").textContent = model.title;
    for (const key of KEYS.wires) {
      const { status, speed, reverse } = model.wires[key];
      root.getElementById(`wire-${key}`).setAttribute("class", `wire ${status}`);
      const dots = root.getElementById(`dots-${key}`);
      dots.classList.toggle("on", status === "flow");
      dots.classList.toggle("rev", reverse);
      if (speed !== null) {
        dots.style.setProperty("--speed", `${speed}s`);
      }
    }
    for (const key of KEYS.nodes) {
      const { lines, dim } = model.nodes[key];
      root.getElementById(`node-${key}`).classList.toggle("dim", dim);
      root.getElementById(`node-${key}-line-0`).textContent = lines[0] ?? "";
      root.getElementById(`node-${key}-line-1`).textContent = lines[1] ?? "";
    }
  }
}

customElements.define("bestiarium-power-flow-card", BestiariumPowerFlowCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "bestiarium-power-flow-card",
  name: "Bestiarium — схема живлення",
  description: "Звідки зараз живиться дім",
});
