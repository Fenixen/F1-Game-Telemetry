// Lap telemetry traces by track distance: current lap vs last lap vs personal best.
import { fmtLap, lineChart } from "../util.js";

const CHANNELS = [["speed", "Rychlost (km/h)", 190, {}], ["throttle", "Plyn (%)", 110, { yMin: 0, yMax: 100 }],
  ["brake", "Brzda (%)", 110, { yMin: 0, yMax: 100 }], ["gear", "Rychlostní stupeň", 110, { yMin: 0, yMax: 8 }],
  ["steer", "Volant (%)", 110, { yMin: -100, yMax: 100 }]];
const LAPS = [["best", "#b877ff", 1.5], ["last", "#4da8ff", 1.5], ["cur", "#ffffff", 2]];
let host = null, timer = 0, canvases = {}, legend = null, deltaCanvas = null;

function mount(root, store) {
  host = root;
  root.innerHTML = `<div class="grid">
    <div class="card"><h3>Telemetrie kola <span class="legend" id="tl-legend"></span></h3>
      <p class="muted" style="font-size:.86rem">Osa X je vzdálenost na trati v metrech – stejné místo trati je vždy pod sebou.</p></div>
    <div class="card"><h3>Delta vůči nejlepšímu kolu (s)</h3><canvas id="tl-delta"></canvas></div>
    ${CHANNELS.map(([key, title]) => `<div class="card"><h3>${title}</h3><canvas id="tl-${key}"></canvas></div>`).join("")}
  </div>`;
  legend = root.querySelector("#tl-legend");
  deltaCanvas = root.querySelector("#tl-delta");
  canvases = Object.fromEntries(CHANNELS.map(([key]) => [key, root.querySelector(`#tl-${key}`)]));
  const poll = () => store.send({ t: "traces" });
  poll();
  timer = setInterval(poll, 1000);
  if (store.traces) traces(store.traces);
}
function hide() { clearInterval(timer); }

function points(trace, channel, bucket) {
  const out = [];
  if (!trace) return out;
  trace[channel].forEach((v, i) => { if (v !== null) out.push([i * bucket, v]); });
  return out;
}

function traces(data) {
  if (!host?.isConnected) return;
  const names = { best: "nejlepší", last: "poslední", cur: "aktuální" };
  legend.innerHTML = LAPS.map(([key, color]) => data[key]
    ? `<span><i style="background:${color}"></i>${names[key]}${data[key].ms ? ` ${fmtLap(data[key].ms)}` : ""}</span>` : "").join("");
  const fmtX = (v) => `${v.toFixed(0)} m`;
  // Same X range on every chart so the same corner lines up vertically
  const sample = data.cur || data.last || data.best;
  const xMax = sample ? sample.t.length * data.bucket : undefined;
  for (const [key, , height, opts] of CHANNELS) {
    const series = LAPS.map(([lap, color, width]) => ({ color, width, points: points(data[lap], key, data.bucket) }));
    lineChart(canvases[key], height, series, { ...opts, fmtX, xMin: 0, xMax, empty: "Zajeď kolo a objeví se tu průběh" });
  }
  const delta = [];
  if (data.cur && data.best) {
    data.cur.t.forEach((t, i) => {
      const ref = data.best.t[i];
      if (t !== null && ref !== null && ref !== undefined) delta.push([i * data.bucket, (t - ref) / 1000]);
    });
  }
  lineChart(deltaCanvas, 130, [{ color: "#ffd23b", points: delta }], {
    fmtX, xMin: 0, xMax, fmtY: (v) => v.toFixed(2), hLines: [{ y: 0, color: "#8492a0" }],
    empty: "Delta potřebuje jedno dokončené platné kolo" });
}

export default {
  id: "telemetry", title: "Telemetrie", mount, hide, traces,
  icon: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
};
