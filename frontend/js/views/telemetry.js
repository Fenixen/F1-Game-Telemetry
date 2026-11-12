// Lap telemetry by track distance, linked to a track map: touch a chart and the map shows where that is.
// Corners are detected from the reference lap (Z1, Z2, ... in driving order, not official corner numbers).
import { esc, fmtLap, lineChart, prepCanvas } from "../util.js";

const CHANNELS = [["speed", "Rychlost (km/h)", 200, {}], ["throttle", "Plyn (%)", 120, { yMin: 0, yMax: 100 }],
  ["brake", "Brzda (%)", 120, { yMin: 0, yMax: 100 }], ["gear", "Rychlostní stupeň", 110, { yMin: 0, yMax: 8 }],
  ["steer", "Volant (%)", 110, { yMin: -100, yMax: 100 }]];
const LAPS = [["ref", "#ff9a3b", 1.5, "nejlepší uložené"], ["best", "#b877ff", 1.5, "nejlepší"],
  ["last", "#4da8ff", 1.5, "poslední"], ["cur", "#ffffff", 2, "aktuální"]];
const MAP_H = 300;
let host = null, timer = 0, data = null, outline = null, cursor = null, mappers = {}, ui = {};

function mount(root, store) {
  host = root;
  root.innerHTML = `<div class="grid">
    <div class="grid tl-top">
      <div class="card"><h3>Kde to je <span class="dim">klepni do grafu nebo na zatáčku</span></h3>
        <canvas id="tl-map"></canvas><div class="tl-readout" id="tl-readout"></div></div>
      <div class="card"><h3>Kde ztrácíš <span class="dim" id="tl-vs"></span></h3><div id="tl-coach"></div></div>
    </div>
    <div class="card"><h3>Kola <span class="legend" id="tl-legend"></span></h3>
      <p class="muted" style="font-size:.9rem">Osa X = metry od startovní čáry. Svislé čáry jsou zatáčky Z1, Z2…,
      žlutá čára je místo, na které ukazuješ.</p></div>
    <div class="card"><h3>Delta proti referenčnímu kolu (s) <span class="dim">nad nulou = ztrácíš</span></h3><canvas id="tl-delta"></canvas></div>
    ${CHANNELS.map(([key, title]) => `<div class="card"><h3>${title}</h3><canvas id="tl-${key}" class="tl-chart"></canvas></div>`).join("")}
  </div>`;
  ui = {};
  for (const node of root.querySelectorAll("[id]")) ui[node.id.slice(3)] = node;
  outline = store.outline;
  // Pointer on any chart moves the shared cursor
  root.addEventListener("pointerdown", onPointer);
  root.addEventListener("pointermove", (e) => { if (e.buttons || e.pointerType === "touch") onPointer(e); });
  ui.coach.addEventListener("click", (e) => {
    const m = e.target.closest("[data-m]")?.dataset.m;
    if (m) { cursor = Number(m); draw(); }
  });
  const poll = () => store.send({ t: "traces" });
  poll();
  timer = setInterval(poll, 1000);
  if (store.traces) traces(store.traces);
}

function hide() { clearInterval(timer); }

function onPointer(e) {
  const canvas = e.target.closest("canvas");
  if (!canvas || !mappers[canvas.id]) return;
  const rect = canvas.getBoundingClientRect();
  cursor = Math.max(0, mappers[canvas.id].invX(e.clientX - rect.left));
  draw();
}

function points(trace, channel, bucket) {
  const out = [];
  if (!trace) return out;
  trace[channel].forEach((v, i) => { if (v !== null) out.push([i * bucket, v]); });
  return out;
}

function traces(d) {
  data = d;
  if (!host?.isConnected) return;
  ui.legend.innerHTML = LAPS.map(([key, color, , name]) => d[key]
    ? `<span><i style="background:${color}"></i>${name}${d[key].ms ? ` ${fmtLap(d[key].ms)}` : ""}</span>` : "").join("") +
    (d.stale ? ' <span class="yellow">z minulé session</span>' : "");
  ui.vs.textContent = d.last ? `poslední kolo vs. ${d.refKind === "ref" ? "nejlepší uložené" : "nejlepší"}` : "";
  ui.coach.innerHTML = d.coaching?.length
    ? `<table class="coach">${d.coaching.map((c) => `<tr data-m="${c.apex_m}">
        <td class="c-name">${esc(c.name)}</td>
        <td class="c-loss ${c.loss_ms >= 40 ? "red" : c.loss_ms <= -40 ? "green" : "muted"}">${c.loss_ms >= 0 ? "+" : "−"}${(Math.abs(c.loss_ms) / 1000).toFixed(2)}</td>
        <td>${esc(c.tip)}</td></tr>`).join("")}</table>`
    : '<p class="muted">Zajeď aspoň dvě celá kola. Pak tu uvidíš zatáčku po zatáčce, kde ztrácíš a proč.</p>';
  draw();
}

function outlineUpdate(o) { outline = o; if (host?.isConnected) draw(); }

function valueAt(trace, channel, m, bucket) {
  if (!trace) return null;
  const i = Math.round(m / bucket);
  return trace[channel][i] ?? null;
}

function draw() {
  if (!data || !host?.isConnected) return;
  const sample = data.cur || data.last || data.best || data.ref;
  const xMax = sample ? sample.t.length * data.bucket : undefined;
  const vLines = (data.corners || []).map((c) => ({ x: c.apex_m, label: c.name }));
  const fmtX = (v) => `${v.toFixed(0)} m`;
  for (const [key, , height, opts] of CHANNELS) {
    const series = LAPS.map(([lap, color, width]) => ({ color, width, points: points(data[lap], key, data.bucket) }));
    mappers[`tl-${key}`] = lineChart(ui[key], height, series,
      { ...opts, fmtX, xMin: 0, xMax, vLines, cursor, empty: "Zajeď kolo a objeví se tu průběh" });
  }
  const ref = data.best || data.ref;
  const delta = [];
  if (data.cur && ref) {
    data.cur.t.forEach((t, i) => {
      const r = ref.t[i];
      if (t !== null && r !== null && r !== undefined) delta.push([i * data.bucket, (t - r) / 1000]);
    });
  }
  mappers["tl-delta"] = lineChart(ui.delta, 140, [{ color: "#ffd23b", points: delta }], {
    fmtX, xMin: 0, xMax, vLines, cursor, fmtY: (v) => v.toFixed(2), hLines: [{ y: 0, color: "#8492a0" }],
    empty: "Delta potřebuje jedno dokončené platné kolo" });
  drawMap();
  readout();
}

function readout() {
  if (cursor === null) { ui.readout.innerHTML = '<span class="muted">Klepni do kteréhokoli grafu.</span>'; return; }
  const corner = (data.corners || []).find((c) => cursor >= c.start_m && cursor <= c.end_m);
  const rows = LAPS.filter(([k]) => data[k]).map(([k, color, , name]) => {
    const v = (ch) => valueAt(data[k], ch, cursor, data.bucket);
    return `<div><i style="background:${color}"></i>${name}: <b>${v("speed") ?? "–"} km/h</b>, plyn ${v("throttle") ?? "–"} %,
      brzda ${v("brake") ?? "–"} %, ${v("gear") ?? "–"}. st.</div>`;
  }).join("");
  ui.readout.innerHTML = `<b class="yellow">${cursor.toFixed(0)} m</b>${corner ? ` · zatáčka <b>${esc(corner.name)}</b>` : ""}${rows}`;
}

/** Track position for a lap distance, from the outline recorded while driving. */
function pointAt(pts, m) {
  let lo = 0, hi = pts.length - 1;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (pts[mid][2] < m) lo = mid + 1; else hi = mid; }
  return pts[lo];
}

function drawMap() {
  const { ctx, width } = prepCanvas(ui.map, MAP_H);
  const pts = outline?.pts || [];
  if (pts.length < 10) {
    ctx.fillStyle = "#5a6673"; ctx.font = "14px system-ui"; ctx.textAlign = "center";
    ctx.fillText("Mapa se nakreslí během prvního kola", width / 2, MAP_H / 2);
    return;
  }
  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
  for (const [x, z] of pts) { minX = Math.min(minX, x); maxX = Math.max(maxX, x); minZ = Math.min(minZ, z); maxZ = Math.max(maxZ, z); }
  const pad = 24, scale = Math.min((width - 2 * pad) / (maxX - minX || 1), (MAP_H - 2 * pad) / (maxZ - minZ || 1));
  const ox = (width - (maxX - minX) * scale) / 2, oz = (MAP_H - (maxZ - minZ) * scale) / 2;
  const X = (x) => ox + (x - minX) * scale, Z = (z) => oz + (z - minZ) * scale;
  ctx.lineCap = ctx.lineJoin = "round";
  ctx.strokeStyle = "#2c3640"; ctx.lineWidth = 8;
  ctx.beginPath();
  pts.forEach(([x, z, d], i) => (i && d - pts[i - 1][2] <= 60 ? ctx.lineTo(X(x), Z(z)) : ctx.moveTo(X(x), Z(z))));
  ctx.stroke();
  const [sx, sz] = pts[0];
  ctx.fillStyle = "#fff"; ctx.fillRect(X(sx) - 2, Z(sz) - 8, 4, 16);
  ctx.font = "800 12px system-ui"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  for (const c of data.corners || []) {
    const [x, z] = pointAt(pts, c.apex_m);
    ctx.fillStyle = "#1c232b"; ctx.beginPath(); ctx.arc(X(x), Z(z), 12, 0, 6.3); ctx.fill();
    ctx.fillStyle = "#e9eef3"; ctx.fillText(c.name, X(x), Z(z) + 0.5);
  }
  if (cursor !== null) {
    const [x, z] = pointAt(pts, cursor);
    ctx.fillStyle = "#ffd23b"; ctx.beginPath(); ctx.arc(X(x), Z(z), 9, 0, 6.3); ctx.fill();
    ctx.strokeStyle = "#0a0c0f"; ctx.lineWidth = 3; ctx.stroke();
  }
}

export default {
  id: "telemetry", title: "Telemetrie", mount, hide, traces, outline: outlineUpdate,
  icon: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
};
