// Strategy wall: pit plan, tyre life, fuel, pace comparison.
import { esc, fmtLap, lineChart, tyreBadge } from "../util.js";

let el = {};

function mount(host) {
  host.innerHTML = `<div class="grid two">
    <div class="card span2"><h3>Plán</h3><div class="plan" id="s-plan">—</div>
      <div style="margin-top:12px"><div class="label" style="margin-bottom:6px">Stinty</div><div class="stints" id="s-stints"></div></div></div>
    <div class="card"><h3>Pneumatiky a palivo</h3><div id="s-kv"></div></div>
    <div class="card"><h3>Sady pneumatik</h3><div class="sets" id="s-sets"></div></div>
    <div class="card span2"><h3>Opotřebení pneumatik po kolech
      <span class="legend"><span><i style="background:#ff4b4b"></i>PL</span><span><i style="background:#ff9a3b"></i>PP</span>
      <span><i style="background:#4da8ff"></i>ZL</span><span><i style="background:#2fe074"></i>ZP</span>
      <span><i style="background:#8492a0"></i>projekce</span></span></h3><canvas id="s-wear"></canvas></div>
    <div class="card span2"><h3>Tempo <span class="legend" id="s-pace-legend"></span></h3><canvas id="s-pace"></canvas></div>
    <div class="card span2"><h3>Pneumatiky soupeřů</h3><div class="sets" id="s-rivals"></div></div>
  </div>`;
  el = {};
  for (const node of host.querySelectorAll("[id]")) el[node.id.slice(2)] = node;
}

function planText(s) {
  const st = s.strategy, race = s.session.isRace;
  if (!race) return "Strategii zastávek řešíme v závodě. Tady sleduj opotřebení a tempo na delší jízdu.";
  if (!st.pitNeeded) {
    const finish = st.wearAtFinish !== null ? ` Pneumatiky v cíli odhadem na <b>${st.wearAtFinish.toFixed(0)} %</b>.` : "";
    return `Bez další zastávky – jedeme do cíle.${finish}`;
  }
  const tyre = st.nextCompound ? ` → ${tyreBadge(st.nextCompound.short, st.nextCompound.color)} ${esc(st.nextCompound.name)}` : "";
  const why = st.twoCompoundsDone ? "pneumatiky nevydrží do cíle" : "povinná druhá směs";
  const rejoin = st.rejoinPos ? ` Zastávka teď = výjezd kolem <b>P${st.rejoinPos}</b> (ztráta ~${st.pitLoss.toFixed(0)} s).` : "";
  return `Zastávka v <b>${st.pitLap}. kole</b>${tyre} <span class="muted">(${why})</span>.${rejoin}`;
}

function slow(s) {
  const st = s.strategy, p = s.player;
  el.plan.innerHTML = planText(s);
  const total = Math.max(s.session.totalLaps, p.lap, 1);
  el.stints.innerHTML = st.stints.map((x) =>
    `<div style="background:${esc(x.color)};flex:${Math.max(1, x.to - x.from + 1)}">${esc(x.short)} ${x.from}–${x.to}</div>`).join("") +
    (s.session.isRace && p.lap < total ? `<div style="flex:${total - p.lap};background:transparent"></div>` : "");
  const kv = (k, v, cls = "") => `<div class="kv"><span>${k}</span><b class="${cls}">${v}</b></div>`;
  const dash = (v, unit = "", digits = 1) => (v === null || v === undefined ? "—" : `${v.toFixed(digits)}${unit}`);
  el.kv.innerHTML =
    kv("Opotřebení teď (max)", dash(st.wearNow, " %", 0)) +
    kv("Opotřebení za kolo", dash(st.wearRate, " %", 2)) +
    kv("Kol do hrany (70 %)", dash(st.lapsToCliff, "", 1)) +
    kv("Zbývá kol", dash(st.lapsLeft, "", 1)) +
    kv("Palivo vůči cíli", `${st.fuelLaps >= 0 ? "+" : "−"}${Math.abs(st.fuelLaps).toFixed(2)} kola`, st.fuelLaps < -0.2 ? "red" : "green") +
    kv("Spotřeba za kolo", dash(st.fuelPerLap, " kg", 2)) +
    kv("Okno zastávky (hra)", st.gameIdealLap ? `kolo ${st.gameIdealLap}–${st.gameLatestLap}` : "—");
  el.sets.innerHTML = p.tyreSets.map((t) => `
    <div class="set ${t.fitted ? "fitted" : t.avail ? "" : "used"}">${tyreBadge(t.short, t.color)}
      <span>${esc(t.actual)} · ${t.wear} %<br><span class="dim">${t.fitted ? "nasazeno" : t.avail ? `~${t.life} kol` : "použitá"}</span></span></div>`).join("")
    || '<span class="muted">Hra zatím neposlala seznam sad.</span>';
  el.rivals.innerHTML = s.cars.map((c) => `
    <div class="set ${c.me ? "fitted" : ""}"><span class="dim num">P${c.pos}</span>${tyreBadge(c.cmp, c.cmpColor)}
      <span>${esc(c.name)}<br><span class="dim">${c.age} kol · ${c.pits}× box</span></span></div>`).join("");

  // Tyre wear per lap with a projection to the cliff
  const colors = ["#4da8ff", "#2fe074", "#ff4b4b", "#ff9a3b"];  // RL RR FL FR
  const stintLaps = [];
  for (let i = s.lapLog.length - 1; i >= 0; i--) {
    const rec = s.lapLog[i];
    if (stintLaps.length && rec.tyre_age >= stintLaps[0].tyre_age) break;
    stintLaps.unshift(rec);
  }
  const series = colors.map((color, w) => ({ color, points: stintLaps.map((r) => [r.lap, r.wear[w]]), dots: true }));
  if (stintLaps.length && st.wearRate) {
    const last = stintLaps[stintLaps.length - 1];
    const end = Math.min(total, last.lap + Math.ceil((80 - last.wear_max) / st.wearRate));
    series.push({ color: "#8492a0", dash: [5, 4], width: 1.5,
      points: [[last.lap, last.wear_max], [end, last.wear_max + st.wearRate * (end - last.lap)]] });
  }
  lineChart(el.wear, 200, series, { xInt: true, yMin: 0, yMax: 100, xMax: s.session.isRace ? total : undefined,
    hLines: [{ y: 70, color: "#ff4b4b" }], fmtY: (v) => `${v.toFixed(0)} %`, empty: "Graf se naplní po prvním kole" });

  // Lap time comparison with the cars around us
  const me = s.cars.find((c) => c.me);
  const picks = [[me, "#ffffff", "ty"], [s.cars.find((c) => c.pos === p.pos - 1), "#ff9a3b", null],
    [s.cars.find((c) => c.pos === p.pos + 1), "#4da8ff", null], [p.pos > 2 ? s.cars[0] : null, "#b877ff", null]];
  const pace = [], legend = [];
  for (const [car, color, label] of picks) {
    if (!car) continue;
    const clean = car.laps.map((ms, i) => [i + 1, ms / 1000]).filter(([, t]) => t > 0);
    // Hide pit/SC laps so the scale stays readable
    const best = Math.min(...clean.map(([, t]) => t));
    pace.push({ color, points: clean.filter(([, t]) => t < best * 1.12), dots: true, width: car.me ? 2.5 : 1.5 });
    legend.push(`<span><i style="background:${color}"></i>${esc(label || car.name)}</span>`);
  }
  el["pace-legend"].innerHTML = legend.join("");
  lineChart(el.pace, 200, pace, { xInt: true, fmtY: (v) => fmtLap(Math.round(v * 1000)).slice(0, -2), empty: "Graf se naplní po prvním kole" });
}

export default {
  id: "strategy", title: "Strategie", mount, slow,
  icon: '<path d="M4 19V5"/><path d="M4 19h16"/><path d="M7 15l4-5 3 3 5-7"/>',
};
