// Race view: the few numbers that matter, readable with a half-second glance at 300 km/h.
// Everything is big and colour-coded so you do not have to read, only look.
import { $, clamp, esc, fmtClock, fmtGap, fmtLap, tempColor, wearColor } from "../util.js";

const WHEELS = [["PL", 2], ["PP", 3], ["ZL", 0], ["ZP", 1]];  // screen order -> index in game arrays
let el = {}, gapMemory = { lap: 0, ahead: null, behind: null }, lastRadio = null;

function mount(host, store) {
  host.innerHTML = `
  <div class="focus">
    <div class="f-flag" id="f-flag" hidden></div>
    <div class="f-card f-pos">
      <div class="f-label" id="f-poslabel">Pozice</div>
      <div class="f-huge" id="f-pos">P–</div>
      <div class="f-sub" id="f-lap">—</div>
    </div>
    <div class="f-card f-delta">
      <div class="f-label" id="f-deltalabel">Delta na nejlepší kolo</div>
      <div class="f-huge" id="f-delta">—</div>
      <div class="f-sub"><span id="f-cur">–:––.–</span> <span class="f-dim" id="f-invalid"></span></div>
    </div>
    <div class="f-card f-gaps">
      <div class="f-gap" id="f-ahead"></div>
      <div class="f-gap" id="f-behind"></div>
    </div>
    <div class="f-card f-tyres" id="f-tyres">
      ${WHEELS.map(([n]) => `<div class="f-tyre" data-w="${n}"><span>${n}</span><b>–</b><i>–</i></div>`).join("")}
    </div>
    <div class="f-card f-energy">
      <div class="f-row"><span class="f-label">Palivo</span><b id="f-fuel">–</b></div>
      <div class="f-row"><span class="f-label">Baterie</span><b id="f-ers">–</b></div>
      <div class="f-bar"><i id="f-ersbar"></i></div>
      <div class="f-pills" id="f-pills"></div>
    </div>
    <div class="f-card f-plan">
      <div class="f-label" id="f-planlabel">Plán</div>
      <div class="f-plantext" id="f-plan">—</div>
      <div class="f-sub" id="f-times"></div>
    </div>
    <div class="f-card f-radio" id="f-radio"><span class="f-dim">Rádio zatím mlčí.</span></div>
  </div>`;
  el = {};
  for (const node of host.querySelectorAll("[id]")) el[node.id.slice(2)] = node;
  el.wheels = WHEELS.map(([n, idx]) => {
    const node = host.querySelector(`[data-w="${n}"]`);
    return { idx, node, temp: $("b", node), wear: $("i", node) };
  });
  if (lastRadio) radio(lastRadio);
  document.addEventListener("radio", onRadio);
}

function hide() { document.removeEventListener("radio", onRadio); }
const onRadio = (e) => radio(e.detail);

function radio(msg) {
  lastRadio = msg;
  if (!el.radio?.isConnected) return;
  el.radio.className = `f-card f-radio p${msg.prio}`;
  el.radio.innerHTML = `<span class="f-dim">${esc(msg.time)}</span> ${esc(msg.text)}`;
}

function fast(f) {
  const d = f.delta;
  el.delta.textContent = d === null ? "—" : fmtGap(d).replace(/(\.\d\d)\d$/, "$1");
  el.delta.className = `f-huge ${d === null ? "" : d <= 0 ? "green" : "red"}`;
  el.cur.textContent = fmtLap(f.lapMs).slice(0, -2);
  el.invalid.textContent = f.invalid ? "· kolo neplatné" : "";
  for (const w of el.wheels) {
    const t = f.tI[w.idx], wear = f.wear[w.idx];
    w.temp.textContent = `${t}°`;
    w.node.style.background = `${tempColor(t)}26`;
    w.node.style.borderColor = tempColor(t);
    w.wear.textContent = `${wear.toFixed(0)} %`;
    w.wear.style.color = wearColor(wear);
  }
  el.fuel.textContent = `${f.fuelLaps >= 0 ? "+" : "−"}${Math.abs(f.fuelLaps).toFixed(1)} kola`;
  el.fuel.className = f.fuelLaps < -0.2 ? "red" : f.fuelLaps < 0.1 ? "yellow" : "green";
  el.ers.textContent = `${f.ers.toFixed(0)} %`;
  el.ersbar.style.width = `${clamp(f.ers, 0, 100)}%`;
  const pills = [];
  if (f.aero) {
    pills.push(`<span class="pill ${f.aero.mode ? "on" : ""}">AERO ${f.aero.mode ? "ROVINKA" : "ZATÁČKA"}</span>`);
    pills.push(`<span class="pill ${f.aero.otActive ? "on" : f.aero.otAvail ? "warn" : ""}">OVERTAKE</span>`);
  } else {
    pills.push(`<span class="pill ${f.drs ? "on" : f.drsOk ? "warn" : ""}">DRS</span>`);
  }
  if (f.pitLim) pills.push('<span class="pill warn">LIMITER</span>');
  el.pills.innerHTML = pills.join("");
}

function gapBlock(gap, label, race, trendMs) {
  if (!gap) return `<div class="f-label">${label}</div><div class="f-gapname f-dim">nikdo</div>`;
  let trend = "";
  if (trendMs !== null && Math.abs(trendMs) >= 100) {
    // ahead: gap shrinking is good; behind: gap growing is good
    const good = label === "Před tebou" ? trendMs < 0 : trendMs > 0;
    trend = `<span class="${good ? "green" : "red"}">${trendMs < 0 ? "▼" : "▲"}${(Math.abs(trendMs) / 1000).toFixed(1)}</span>`;
  }
  const close = race && gap.ms < 1000;
  return `<div class="f-label">${label} ${trend}</div>
    <div class="f-gapnum ${close ? "green" : ""}">${fmtGap(gap.ms, false).replace(/(\.\d)\d\d$/, "$1")}</div>
    <div class="f-gapname">${esc(gap.name)}</div>`;
}

function slow(s) {
  const { player: p, session, strategy: st } = s;
  el.pos.textContent = `P${p.pos}`;
  el.lap.textContent = session.isRace
    ? `Kolo ${Math.min(p.lap, session.totalLaps)} / ${session.totalLaps}`
    : `Zbývá ${fmtClock(session.timeLeft)}`;

  // Gap trend: change since the last lap line, for the same car
  if (p.lap !== gapMemory.lap) gapMemory = { lap: p.lap, ahead: p.gaps.ahead, behind: p.gaps.behind };
  const trend = (side) => {
    const now = p.gaps[side], then = gapMemory[side];
    return now && then && now.idx === then.idx ? now.ms - then.ms : null;
  };
  el.ahead.innerHTML = gapBlock(p.gaps.ahead, "Před tebou", session.isRace, trend("ahead"));
  el.behind.innerHTML = gapBlock(p.gaps.behind, "Za tebou", session.isRace, trend("behind"));

  const flag = session.sc === "SC" ? ["yellow", "SAFETY CAR"] : session.sc === "VSC" ? ["yellow", "VIRTUÁLNÍ SAFETY CAR"]
    : p.flag === 3 ? ["yellow", "ŽLUTÁ VLAJKA"] : p.flag === 2 ? ["blue", "MODRÁ VLAJKA"] : null;
  el.flag.hidden = !flag;
  if (flag) { el.flag.className = `f-flag ${flag[0]}`; el.flag.textContent = flag[1]; }

  if (session.isRace) {
    el.planlabel.textContent = "Strategie";
    if (st.pitNeeded && st.pitLap) {
      const tyre = st.nextCompound ? ` → <span style="color:${esc(st.nextCompound.color)}">${esc(st.nextCompound.short)}</span>` : "";
      el.plan.innerHTML = `BOX kolo ${st.pitLap}${tyre}`;
    } else {
      el.plan.textContent = "Do cíle";
    }
  } else {
    el.planlabel.textContent = "Nejlepší kolo";
    el.plan.textContent = fmtLap(p.best);
  }
  el.times.textContent = `Poslední ${fmtLap(p.last)}${session.isRace ? ` · nejlepší ${fmtLap(p.best)}` : ""}`;
}

export default {
  id: "focus", title: "Závod", mount, hide, fast, slow, wide: true,
  icon: '<path d="M5 21V4"/><path d="M5 4h12l-2 4 2 4H5"/>',
};
