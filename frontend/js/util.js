// Small shared helpers: formatting, safe HTML, canvas setup, local settings.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
/** Escape text for innerHTML. Driver names come from the network, so never trust them. */
export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ESC[ch]);

export function fmtLap(ms) {
  if (!ms || ms <= 0) return "–:––.–––";
  const m = Math.floor(ms / 60000), s = Math.floor((ms % 60000) / 1000), t = ms % 1000;
  return `${m}:${String(s).padStart(2, "0")}.${String(t).padStart(3, "0")}`;
}
export function fmtSector(ms) {
  if (!ms || ms <= 0) return "–";
  return ms >= 60000 ? fmtLap(ms) : (ms / 1000).toFixed(3);
}
export function fmtGap(ms, sign = true) {
  if (ms === null || ms === undefined) return "–";
  const s = Math.abs(ms) / 1000;
  const body = s >= 60 ? `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}` : s.toFixed(3);
  return (sign ? (ms < 0 ? "−" : "+") : "") + body;
}
export function fmtClock(seconds) {
  const m = Math.floor(seconds / 60), s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
export const $ = (selector, root = document) => root.querySelector(selector);

export function tyreBadge(short, color) {
  return `<span class="tyre-badge" style="color:${esc(color)}"><span>${esc(short)}</span></span>`;
}

/** Colour for a tyre carcass temperature (°C). */
export function tempColor(t) {
  if (t < 70) return "#4da8ff";
  if (t < 85) return "#5fd3c8";
  if (t <= 103) return "#2fe074";
  if (t <= 110) return "#ff9a3b";
  return "#ff4b4b";
}
export function wearColor(w) {
  return w < 40 ? "#2fe074" : w < 60 ? "#ffd23b" : w < 75 ? "#ff9a3b" : "#ff4b4b";
}

/** Size a canvas for the device pixel ratio and return a context in CSS pixels. */
export function prepCanvas(canvas, height) {
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || canvas.parentElement.clientWidth;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.height = `${height}px`;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { ctx, width, height };
}

/** Line chart with shared axes. series: [{color, points: [[x, y], ...], dash?, width?}] */
export function lineChart(canvas, height, series, opts = {}) {
  const { ctx, width } = prepCanvas(canvas, height);
  const pad = { l: 44, r: 10, t: 8, b: 22 };
  const all = series.flatMap((s) => s.points);
  if (!all.length) {
    ctx.fillStyle = "#5a6673"; ctx.font = "13px system-ui"; ctx.textAlign = "center";
    ctx.fillText(opts.empty || "Zatím žádná data", width / 2, height / 2);
    return;
  }
  const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
  const x0 = opts.xMin ?? Math.min(...xs), x1 = Math.max(opts.xMax ?? -Infinity, Math.max(...xs), x0 + 1);
  let y0 = opts.yMin ?? Math.min(...ys), y1 = opts.yMax ?? Math.max(...ys);
  if (y1 - y0 < 1e-9) { y0 -= 1; y1 += 1; }
  const margin = opts.yMin === undefined ? (y1 - y0) * 0.08 : 0;
  y0 -= margin; y1 += opts.yMax === undefined ? (y1 - y0) * 0.08 : 0;
  const X = (x) => pad.l + ((x - x0) / (x1 - x0)) * (width - pad.l - pad.r);
  const Y = (y) => pad.t + (1 - (y - y0) / (y1 - y0)) * (height - pad.t - pad.b);
  ctx.font = "11px system-ui"; ctx.fillStyle = "#5a6673"; ctx.strokeStyle = "#1c232b"; ctx.lineWidth = 1;
  const fmtY = opts.fmtY || ((v) => v.toFixed(0));
  for (let i = 0; i <= 4; i++) {
    const v = y0 + ((y1 - y0) * i) / 4, y = Y(v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(width - pad.r, y); ctx.stroke();
    ctx.textAlign = "right"; ctx.fillText(fmtY(v), pad.l - 6, y + 4);
  }
  const fmtX = opts.fmtX || ((v) => v.toFixed(0));
  ctx.textAlign = "center";
  if (opts.xInt) {
    // Whole-number axis (laps): label each integer once, thinning out when there are many
    const step = Math.max(1, Math.ceil((x1 - x0) / 12));
    for (let v = Math.ceil(x0); v <= x1; v += step) ctx.fillText(fmtX(v), X(v), height - 6);
  } else {
    for (let i = 0; i <= 5; i++) {
      const v = x0 + ((x1 - x0) * i) / 5;
      ctx.fillText(fmtX(v), X(v), height - 6);
    }
  }
  for (const line of opts.hLines || []) {
    if (line.y < y0 || line.y > y1) continue;
    ctx.strokeStyle = line.color; ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(pad.l, Y(line.y)); ctx.lineTo(width - pad.r, Y(line.y)); ctx.stroke();
    ctx.setLineDash([]);
  }
  for (const s of series) {
    if (!s.points.length) continue;
    ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 2; ctx.setLineDash(s.dash || []);
    ctx.beginPath();
    s.points.forEach(([x, y], i) => (i ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y))));
    ctx.stroke(); ctx.setLineDash([]);
    if (s.dots) {
      ctx.fillStyle = s.color;
      for (const [x, y] of s.points) { ctx.beginPath(); ctx.arc(X(x), Y(y), 2.5, 0, 6.3); ctx.fill(); }
    }
  }
}

/** Per-device preferences. localStorage can be blocked (private mode), so every access is guarded. */
export const prefs = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(`re.${key}`);
      return raw === null ? fallback : JSON.parse(raw);
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`re.${key}`, JSON.stringify(value)); } catch { /* not persisted, fine */ }
  },
};
