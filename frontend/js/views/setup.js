// Setup engineer: current car setup + advice for handling complaints.
import { esc } from "../util.js";

let el = {}, lastIssue = null;

function mount(host, store) {
  host.innerHTML = `<div class="grid two">
    <div class="card span2"><h3>Co auto dělá špatně?</h3>
      <p class="muted" style="margin-bottom:10px;font-size:.88rem">Vyber problém a inženýr navrhne konkrétní změny oproti aktuálnímu nastavení.
      Hra neumí přijmout setup po síti, hodnoty si přepiš v garáži.</p>
      <div class="issues" id="su-issues"></div></div>
    <div class="card"><h3>Doporučení <span id="su-title"></span></h3><div id="su-advice"><span class="muted">Zatím nic – vyber problém nahoře.</span></div></div>
    <div class="card"><h3>Co říkají data</h3><div id="su-notes"></div></div>
    <div class="card span2"><h3>Aktuální nastavení</h3><div class="grid three" id="su-current"></div></div>
  </div>`;
  el = {};
  for (const node of host.querySelectorAll("[id]")) el[node.id.slice(3)] = node;
  el.issues.innerHTML = (store.hello?.issues || []).map((i) =>
    `<button class="btn" data-issue="${esc(i.issue)}">${esc(i.label)}</button>`).join("");
  const ask = (issue) => {
    lastIssue = issue;
    for (const b of el.issues.children) b.classList.toggle("on", b.dataset.issue === issue);
    store.send({ t: "setup", issue });
  };
  host.addEventListener("click", (e) => {
    const issue = e.target.closest("[data-issue]")?.dataset.issue;
    if (issue) ask(issue);
  });
  if (store.advice) advice(store.advice);
}

function advice(a) {
  if (!el.advice?.isConnected) return;
  el.title.textContent = a.label;
  if (!a.changes.length) {
    el.advice.innerHTML = '<span class="muted">Všechny páky pro tenhle problém už jsou na doraz.</span>';
    return;
  }
  el.advice.innerHTML = a.changes.map((c, i) => `
    <div class="change"><span>${i + 1}. ${esc(c.label)}</span>
      <span class="v">${c.from ? `${esc(c.from)} → <span class="green">${esc(c.to)}</span>` : esc(c.delta)}</span>
      <small>${esc(c.why)}</small></div>`).join("") +
    `<p class="muted" style="margin-top:10px;font-size:.84rem">Měň po jedné položce odshora a po každé změně odjeď 2–3 kola. ${
      a.hasSetup ? "" : "Aktuální setup hra zatím neposlala, proto vidíš jen směr změny."}</p>`;
}

function slow(s) {
  el.notes.innerHTML = s.setupNotes.map((n) => `<div class="note">${esc(n.text)}${
    n.issue ? ` <button class="btn sm ghost" data-issue="${esc(n.issue)}">Řešit</button>` : ""}</div>`).join("")
    || '<span class="muted">Telemetrie zatím neukazuje nic nápadného. Odjeď pár kol v kuse.</span>';
  el.current.innerHTML = s.setupView.map((g) => `<div><div class="label" style="margin-bottom:6px">${esc(g.group)}</div>
    ${g.items.map((i) => `<div class="kv"><span>${esc(i.label)}</span><b>${esc(i.value)}</b></div>`).join("")}</div>`).join("")
    || '<span class="muted">Hra zatím neposlala nastavení vozu.</span>';
}

export default {
  id: "setup", title: "Setup", mount, slow, advice,
  icon: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
};
