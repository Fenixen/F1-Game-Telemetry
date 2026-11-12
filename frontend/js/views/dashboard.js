// Live cockpit view: what the driver's car is doing right now.
import { $, clamp, esc, fmtGap, fmtLap, fmtSector, tempColor, tyreBadge, wearColor } from "../util.js";

const WHEELS = [["FL", 2], ["FR", 3], ["RL", 0], ["RR", 1]];  // screen order -> index in game arrays
let el = {};

function mount(host) {
  host.innerHTML = `
  <div class="grid dash">
    <div class="card c-pos">
      <h3>Pozice</h3>
      <div class="pos-row"><div class="big-num" id="d-pos">P–</div><small id="d-of"></small></div>
      <div class="tyre-line" id="d-tyre"></div>
      <div class="pills" id="d-pens"></div>
    </div>
    <div class="card c-lap">
      <h3>Aktuální kolo <span id="d-invalid" class="red" hidden>NEPLATNÉ</span></h3>
      <div class="laptime"><div class="big-num" id="d-cur">–:––.–</div><div class="delta" id="d-delta"></div></div>
      <div class="sectors" id="d-sectors"></div>
      <div class="lap-pair">
        <div><div class="label">Poslední</div><div class="mid-num" id="d-last">–</div></div>
        <div><div class="label">Nejlepší</div><div class="mid-num" id="d-best">–</div></div>
      </div>
    </div>
    <div class="card c-gaps"><h3>Odstupy <span id="d-gapnote"></span></h3><div id="d-gaps"></div></div>

    <div class="card c-drive">
      <h3>Jízda</h3>
      <div class="revs" id="d-revs">${"<i></i>".repeat(15)}</div>
      <div class="drive">
        <div><div class="big-num" id="d-spd">0</div><div class="label">km/h</div>
          <div class="steer"><i id="d-steer"></i></div></div>
        <div class="gear num" id="d-gear">N</div>
        <div class="pedals"><div class="pedal b"><i id="d-brk"></i></div><div class="pedal t"><i id="d-thr"></i></div></div>
      </div>
      <div class="pills">
        <span class="pill" id="d-drs">DRS</span><span class="pill" id="d-lim">PIT LIMITER</span>
        <span class="pill" id="d-g">G 0.0 / 0.0</span><span class="pill" id="d-rpm">0 rpm</span>
      </div>
    </div>
    <div class="card c-tyres">
      <h3>Pneumatiky <span class="dim">jádro · povrch · brzda</span></h3>
      <div class="tyres">${WHEELS.map(([name]) => `
        <div class="tyre" id="d-w-${name}"><span class="tag">${name}</span>
          <div class="t">–</div><small class="a"></small><small class="b"></small><small class="c"></small><i class="w"></i></div>`).join("")}
      </div>
    </div>
    <div class="card c-energy">
      <h3>Energie a palivo</h3>
      <div class="kv"><span>ERS <span class="dim" id="d-ersmode"></span></span><b id="d-ers">–</b></div>
      <div class="bar" style="margin:4px 0 10px"><i id="d-ersbar" style="background:var(--yellow)"></i></div>
      <div class="kv"><span>Palivo vůči cíli</span><b id="d-fuel">–</b></div>
      <div class="kv"><span>V nádrži</span><b id="d-fuelkg">–</b></div>
      <div class="kv"><span>Směs</span><b id="d-mix">–</b></div>
      <div class="kv"><span>Brzdy vpředu</span><b id="d-bias">–</b></div>
      <div class="kv"><span>Motor</span><b id="d-eng">–</b></div>
    </div>
  </div>`;
  el = {};
  for (const node of host.querySelectorAll("[id]")) el[node.id.slice(2)] = node;
  el.revLeds = [...el.revs.children];
  el.wheels = WHEELS.map(([name, idx]) => {
    const node = $(`#d-w-${name}`, host);
    return { idx, node, t: $(".t", node), a: $(".a", node), b: $(".b", node), c: $(".c", node), w: $(".w", node) };
  });
}

function fast(f) {
  el.spd.textContent = f.spd;
  el.gear.textContent = f.gear === 0 ? "N" : f.gear < 0 ? "R" : f.gear;
  el.thr.style.height = `${f.thr * 100}%`;
  el.brk.style.height = `${f.brk * 100}%`;
  el.steer.style.left = `${50 + clamp(f.steer, -1, 1) * 50}%`;
  const lit = Math.round((f.rev / 100) * 15);
  el.revLeds.forEach((led, i) => { led.className = i < lit ? (i < 5 ? "g" : i < 10 ? "r" : "p") : ""; });
  if (f.aero) {
    // 2026 rules: no DRS, active aero instead + overtake mode
    el.drs.textContent = f.aero.mode ? "AERO: ROVINKA" : "AERO: ZATÁČKA";
    el.drs.className = `pill ${f.aero.mode ? "on" : ""}`;
    el.g.textContent = "OVERTAKE";
    el.g.className = `pill ${f.aero.otActive ? "on" : f.aero.otAvail ? "warn" : ""}`;
  } else {
    el.drs.className = `pill ${f.drs ? "on" : f.drsOk ? "warn" : ""}`;
  }
  el.lim.className = `pill ${f.pitLim ? "warn" : ""}`;
  if (!f.aero) el.g.textContent = `G ${Math.abs(f.gLat).toFixed(1)} / ${f.gLon.toFixed(1)}`;
  el.rpm.textContent = `${f.rpm} rpm`;
  el.cur.textContent = fmtLap(f.lapMs).slice(0, -2);
  el.invalid.hidden = !f.invalid;
  if (f.delta === null) { el.delta.textContent = ""; } else {
    el.delta.textContent = fmtGap(f.delta);
    el.delta.className = `delta ${f.delta <= 0 ? "green" : "red"}`;
  }
  el.ers.textContent = `${f.ers.toFixed(0)} %`;
  el.ersbar.style.width = `${clamp(f.ers, 0, 100)}%`;
  el.ersmode.textContent = f.ersMode;
  el.fuel.textContent = `${f.fuelLaps >= 0 ? "+" : "−"}${Math.abs(f.fuelLaps).toFixed(2)} kola`;
  el.fuel.className = f.fuelLaps < -0.2 ? "red" : f.fuelLaps < 0.1 ? "yellow" : "green";
  el.fuelkg.textContent = `${f.fuelKg.toFixed(1)} kg`;
  el.mix.textContent = f.fuelMix;
  el.bias.textContent = `${f.bias} %`;
  el.eng.textContent = `${f.engT} °C`;
  for (const w of el.wheels) {
    const inner = f.tI[w.idx], wear = f.wear[w.idx];
    w.t.textContent = `${inner}°`;
    w.t.style.color = tempColor(inner);
    w.a.textContent = `povrch ${f.tS[w.idx]}° · brzda ${f.bT[w.idx]}°`;
    w.b.textContent = `${f.tP[w.idx].toFixed(1)} psi`;
    w.c.textContent = `opotřebení ${wear.toFixed(0)} %`;
    w.c.style.color = wearColor(wear);
    w.w.style.width = `${clamp(wear, 0, 100)}%`;
    w.w.style.background = wearColor(wear);
  }
}

function slow(s) {
  const { player: p, session, purple } = s;
  el.pos.textContent = `P${p.pos}`;
  el.of.textContent = `z ${s.cars.length}${session.isRace ? ` · start P${p.grid}` : ""}`;
  el.tyre.innerHTML = `${tyreBadge(p.cmp, p.cmpColor)}<span>${esc(p.cmpName)} ${esc(p.actual)} · ${p.tyreAge} kol · zastávek ${p.pits}</span>`;
  const pills = [];
  if (p.pens) pills.push(`<span class="pill bad">+${p.pens} s penalizace</span>`);
  if (p.unservedDt) pills.push(`<span class="pill bad">Drive-through</span>`);
  if (p.unservedSg) pills.push(`<span class="pill bad">Stop & go</span>`);
  if (p.ccWarnings) pills.push(`<span class="pill warn">Limity trati ${p.ccWarnings}×</span>`);
  el.pens.innerHTML = pills.join("");
  el.last.textContent = fmtLap(p.last);
  el.best.textContent = fmtLap(p.best);
  el.sectors.innerHTML = p.sectors.map((ms, i) => {
    const cls = ms && ms === purple[i] ? "sb" : ms && ms === p.bestSectors[i] ? "pb" : "";
    return `<div class="sector ${cls}"><span class="label">S${i + 1}</span><b>${fmtSector(ms)}</b>
      <small class="dim num">${fmtSector(p.bestSectors[i])}</small></div>`;
  }).join("");
  el.gapnote.textContent = session.isRace ? "" : "rozdíl nejlepších kol";
  const row = (gap, label) => gap
    ? `<div class="gap-row ${session.isRace && gap.ms > 0 && gap.ms < 1000 ? "drs" : ""}"><span class="label">${label}</span>
       <span class="who">${esc(gap.name)}</span><span class="gap">${fmtGap(gap.ms, false)}</span></div>`
    : `<div class="gap-row"><span class="label">${label}</span><span class="who dim">nikdo</span><span></span></div>`;
  el.gaps.innerHTML = row(p.gaps.ahead, "Před") +
    `<div class="gap-row me"><span class="label">Ty</span><span class="who">${esc(p.name)}</span><span class="gap">P${p.pos}</span></div>` +
    row(p.gaps.behind, "Za");
}

export default {
  id: "dash", title: "Kokpit", mount, fast, slow,
  icon: '<path d="M4 15a8 8 0 1 1 16 0"/><path d="M12 15l4-5"/><path d="M4 19h16"/>',
};
