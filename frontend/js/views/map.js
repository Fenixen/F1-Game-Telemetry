// Track map (recorded from live car positions) + weather radar.
import { esc, fmtGap, prefs, prepCanvas } from "../util.js";

const WEATHER = ["☀️ Jasno", "🌤️ Polojasno", "☁️ Zataženo", "🌦️ Slabý déšť", "🌧️ Silný déšť", "⛈️ Bouřka"];
const HEIGHT = 560;
let canvas, side, raf = 0, mirror = prefs.get("mirror", false);
let dots = new Map();          // car idx -> {x, z, tx, tz} for smooth motion between 4 Hz updates
let latest = null, outlineData = null;

function mount(host, store) {
  host.innerHTML = `<div class="grid map-wrap">
    <div class="card"><h3>Mapa trati <button class="btn ghost sm" id="m-mirror">Zrcadlit</button></h3>
      <canvas id="m-canvas"></canvas></div>
    <div id="m-side" class="grid" style="align-content:start"></div></div>`;
  canvas = host.querySelector("#m-canvas");
  side = host.querySelector("#m-side");
  host.querySelector("#m-mirror").addEventListener("click", () => { mirror = !mirror; prefs.set("mirror", mirror); });
  outlineData = store.outline;
  dots = new Map();
  const loop = () => { draw(); raf = requestAnimationFrame(loop); };
  raf = requestAnimationFrame(loop);
}
function hide() { cancelAnimationFrame(raf); }
function outline(data) { outlineData = data; }

function slow(s) {
  latest = s;
  for (const c of s.cars) {
    const dot = dots.get(c.i);
    if (dot) { dot.tx = c.x; dot.tz = c.z; } else dots.set(c.i, { x: c.x, z: c.z, tx: c.x, tz: c.z });
  }
  const sess = s.session;
  const forecast = sess.forecast.slice(0, 7).map((f) => `
    <div class="fc"><b class="num">${f.t === 0 ? "teď" : `+${f.t} min`}</b><span>${WEATHER[f.w] || "?"}</span>
    <span class="num ${f.rain >= 50 ? "blue" : "muted"}">${f.rain} %</span></div>`).join("");
  const around = s.cars.filter((c) => Math.abs(c.pos - s.player.pos) <= 2).map((c) => `
    <div class="kv"><span><span class="dim num">P${c.pos}</span> ${esc(c.name)}</span>
    <b>${c.me ? "ty" : c.gap === null ? "—" : fmtGap(c.gap - (s.cars.find((x) => x.me)?.gap ?? 0))}</b></div>`).join("");
  side.innerHTML = `
    <div class="card"><h3>Počasí</h3>
      <div class="kv"><span>Teď</span><b>${WEATHER[sess.weather] || "?"}</b></div>
      <div class="kv"><span>Trať / vzduch</span><b>${sess.trackTemp} °C / ${sess.airTemp} °C</b></div>
      <div class="forecast" style="margin-top:10px">${forecast || '<span class="muted">Bez předpovědi</span>'}</div></div>
    <div class="card"><h3>Kolem tebe</h3>${around}</div>`;
}

function draw() {
  const { ctx, width } = prepCanvas(canvas, HEIGHT);
  const pts = outlineData?.pts || [];
  const cars = latest?.cars || [];
  const cloud = pts.length > 10 ? pts : cars.map((c) => [c.x, c.z]);
  if (!cloud.length) {
    ctx.fillStyle = "#5a6673"; ctx.font = "14px system-ui"; ctx.textAlign = "center";
    ctx.fillText("Mapa se vykreslí během prvního kola", width / 2, HEIGHT / 2);
    return;
  }
  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
  for (const [x, z] of cloud) { minX = Math.min(minX, x); maxX = Math.max(maxX, x); minZ = Math.min(minZ, z); maxZ = Math.max(maxZ, z); }
  const pad = 34, scale = Math.min((width - 2 * pad) / (maxX - minX || 1), (HEIGHT - 2 * pad) / (maxZ - minZ || 1));
  const ox = (width - (maxX - minX) * scale) / 2, oz = (HEIGHT - (maxZ - minZ) * scale) / 2;
  const X = (x) => (mirror ? width - (ox + (x - minX) * scale) : ox + (x - minX) * scale);
  const Z = (z) => oz + (z - minZ) * scale;

  if (pts.length > 10) {
    const len = latest?.session.trackLen || 1, s2 = (latest?.session.s2 || 0) * len, s3 = (latest?.session.s3 || 0) * len;
    const yellow = (latest?.session.zones || []).map((zone, i, all) =>
      zone.f === 3 ? [zone.s * len, (all[i + 1]?.s ?? 1) * len] : null).filter(Boolean);
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.strokeStyle = "#232b34"; ctx.lineWidth = 12;
    ctx.beginPath();
    pts.forEach(([x, z], i) => (i ? ctx.lineTo(X(x), Z(z)) : ctx.moveTo(X(x), Z(z))));
    if (outlineData.complete) ctx.closePath();
    ctx.stroke();
    // Sector colours on top, drawn segment by segment so gaps in a partial outline stay empty
    ctx.lineWidth = 4;
    for (let i = 1; i < pts.length; i++) {
      const [x, z, d] = pts[i], [px, pz, pd] = pts[i - 1];
      if (d - pd > 60) continue;
      const flagged = yellow.some(([a, b]) => d >= a && d < b);
      ctx.strokeStyle = flagged ? "#ffd23b" : d < s2 || !s2 ? "#ff4b4b" : d < s3 ? "#4da8ff" : "#ffd23b88";
      if (!flagged && d >= s3 && s3) ctx.strokeStyle = "#c9a227";
      ctx.beginPath(); ctx.moveTo(X(px), Z(pz)); ctx.lineTo(X(x), Z(z)); ctx.stroke();
    }
    const [sx, sz] = pts[0];
    ctx.fillStyle = "#fff"; ctx.fillRect(X(sx) - 2, Z(sz) - 9, 4, 18);
  }

  ctx.font = "700 10px system-ui"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  const ordered = [...cars].sort((a, b) => (a.me ? 1 : 0) - (b.me ? 1 : 0));
  for (const c of ordered) {
    const dot = dots.get(c.i);
    if (!dot || c.st === "DNF" || c.st === "OUT") continue;
    dot.x += (dot.tx - dot.x) * 0.18; dot.z += (dot.tz - dot.z) * 0.18;
    const x = X(dot.x), z = Z(dot.z), r = c.me ? 11 : 8;
    ctx.beginPath(); ctx.arc(x, z, r, 0, Math.PI * 2);
    ctx.fillStyle = c.color; ctx.fill();
    ctx.lineWidth = c.me ? 3 : 1.5; ctx.strokeStyle = c.me ? "#fff" : "#0a0c0f"; ctx.stroke();
    ctx.fillStyle = "#0a0c0f"; ctx.fillText(c.pos, x, z + 0.5);
  }
}

export default {
  id: "map", title: "Mapa", mount, hide, slow, outline,
  icon: '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2z"/><path d="M9 4v14M15 6v14"/>',
};
