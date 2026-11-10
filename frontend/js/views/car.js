// Car health: bodywork damage, tyres, brakes and power unit wear.
import { clamp, wearColor } from "../util.js";

const BODY = [["fl_wing", "Přední křídlo L"], ["fr_wing", "Přední křídlo P"], ["rear_wing", "Zadní křídlo"],
  ["floor", "Podlaha"], ["diffuser", "Difuzor"], ["sidepod", "Bočnice"]];
const DRIVETRAIN = [["gearbox", "Převodovka"], ["engine", "Motor"]];
const PU = [["ice_wear", "ICE"], ["mguh_wear", "MGU-H"], ["mguk_wear", "MGU-K"], ["es_wear", "Baterie (ES)"],
  ["ce_wear", "Řídicí elektronika"], ["tc_wear", "Turbo"]];
const WHEELS = [["PL", 2], ["PP", 3], ["ZL", 0], ["ZP", 1]];
let el = {};

const row = (label, value) => `<div class="dmg"><span>${label}</span>
  <div class="bar"><i style="width:${clamp(value, 0, 100)}%;background:${wearColor(value)}"></i></div><b>${value} %</b></div>`;

function mount(host) {
  host.innerHTML = `<div class="grid two">
    <div class="card"><h3>Karoserie</h3><div id="c-body"></div><div class="pills" id="c-faults"></div></div>
    <div class="card"><h3>Pohonná jednotka (opotřebení)</h3><div id="c-pu"></div></div>
    <div class="card"><h3>Pneumatiky – poškození / puchýře</h3><div id="c-tyres"></div></div>
    <div class="card"><h3>Brzdy a převodovka</h3><div id="c-brakes"></div></div>
  </div>`;
  el = {};
  for (const node of host.querySelectorAll("[id]")) el[node.id.slice(2)] = node;
}

function slow(s) {
  const p = s.player;
  el.body.innerHTML = BODY.map(([key, label]) => row(label, p.damage[key])).join("");
  const faults = [];
  if (p.damage.drs_fault) faults.push('<span class="pill bad">Porucha DRS</span>');
  if (p.damage.ers_fault) faults.push('<span class="pill bad">Porucha ERS</span>');
  el.faults.innerHTML = faults.join("") || '<span class="pill on">Systémy OK</span>';
  el.pu.innerHTML = PU.map(([key, label]) => row(label, p.engine[key])).join("");
  el.tyres.innerHTML = WHEELS.map(([label, i]) => row(`${label} poškození`, p.tyreDamage[i]) + row(`${label} puchýře`, p.blisters[i])).join("");
  el.brakes.innerHTML = WHEELS.map(([label, i]) => row(`Brzda ${label}`, p.brakeDamage[i])).join("") +
    DRIVETRAIN.map(([key, label]) => row(label, p.damage[key])).join("");
}

export default {
  id: "car", title: "Vůz", mount, slow,
  icon: '<path d="M5 17h14l1-5-3-1-2-4H9L7 11l-3 1z"/><circle cx="8" cy="17" r="2"/><circle cx="16" cy="17" r="2"/>',
};
