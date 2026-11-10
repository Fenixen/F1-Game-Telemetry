// Timing tower: the whole field at a glance.
import { esc, fmtGap, fmtLap, fmtSector, tyreBadge } from "../util.js";

let body = null, head = null;

function mount(host) {
  host.innerHTML = `<div class="card"><div class="scroll-x"><table class="tower">
    <thead><tr id="t-head"></tr></thead><tbody id="t-body"></tbody></table></div></div>`;
  body = host.querySelector("#t-body");
  head = host.querySelector("#t-head");
}

function slow(s) {
  const race = s.session.isRace;
  head.innerHTML = `<th>P</th><th class="l">Jezdec</th><th>${race ? "Ztráta" : "Na P1"}</th><th>Interval</th>
    <th>Poslední</th><th>Nejlepší</th><th>S1</th><th>S2</th><th>S3</th><th class="l">Pneu</th><th>Box</th><th class="l">Stav</th>`;
  const fastest = Math.min(...s.cars.map((c) => c.best || Infinity));
  body.innerHTML = s.cars.map((c) => {
    const gap = c.pos === 1 ? (race ? "Lídr" : "—") : c.gap === null ? "—" : fmtGap(c.gap);
    const interval = c.pos === 1 || c.int === null ? "—" : fmtGap(c.int);
    const bestCls = c.best && c.best === fastest ? "purple" : "";
    const lastCls = c.last && c.last === c.best ? "green" : "";
    const sectors = c.sectors.map((ms, i) => {
      const cls = ms && ms === s.purple[i] ? "purple" : ms && ms === c.bestSectors[i] ? "green" : "";
      return `<td class="${cls}">${fmtSector(ms)}</td>`;
    }).join("");
    const moved = race && c.grid ? c.grid - c.pos : 0;
    const arrow = moved > 0 ? `<span class="green"> ▲${moved}</span>` : moved < 0 ? `<span class="red"> ▼${-moved}</span>` : "";
    return `<tr class="${c.me ? "me" : ""}">
      <td>${c.pos}</td>
      <td class="l"><span class="teambar" style="background:${esc(c.color)}"></span>${esc(c.name)}${arrow}</td>
      <td>${gap}</td><td>${interval}</td>
      <td class="${lastCls}">${fmtLap(c.last)}</td><td class="${bestCls}">${fmtLap(c.best)}</td>${sectors}
      <td class="l">${tyreBadge(c.cmp, c.cmpColor)} <span class="dim">${c.age}</span></td>
      <td>${c.pits}</td>
      <td class="l st">${esc(c.st)}${c.pen ? ` <span class="red">+${c.pen}s</span>` : ""}</td></tr>`;
  }).join("");
}

export default {
  id: "timing", title: "Časomíra", mount, slow,
  icon: '<path d="M5 6h14M5 12h14M5 18h9"/>',
};
