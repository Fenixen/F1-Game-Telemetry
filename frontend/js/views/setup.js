// Setup engineer: recommendations from telemetry, your setup library per track, manual complaints.
import { esc, fmtLap } from "../util.js";

let el = {}, timer = 0;

function mount(host, store) {
  host.innerHTML = `<div class="grid two">
    <div class="card span2"><h3>Doporučení z telemetrie <span class="dim" id="su-reclaps"></span></h3>
      <div id="su-rec"><span class="muted">Načítám…</span></div></div>
    <div class="card span2"><h3>Tvoje setupy na téhle trati <span class="dim">seřazené podle nejlepšího kola</span></h3>
      <div id="su-lib"></div></div>
    <div class="card span2"><h3>Nebo řekni, co auto dělá</h3>
      <div class="issues" id="su-issues"></div></div>
    <div class="card"><h3>Doporučení <span id="su-title"></span></h3><div id="su-advice"><span class="muted">Vyber problém nahoře.</span></div></div>
    <div class="card"><h3>Aktuální nastavení</h3><div id="su-current"></div></div>
  </div>`;
  el = {};
  for (const node of host.querySelectorAll("[id]")) el[node.id.slice(3)] = node;
  el.issues.innerHTML = (store.hello?.issues || []).map((i) =>
    `<button class="btn" data-issue="${esc(i.issue)}">${esc(i.label)}</button>`).join("");
  host.addEventListener("click", (e) => {
    const issue = e.target.closest("[data-issue]")?.dataset.issue;
    if (!issue) return;
    for (const b of el.issues.children) b.classList.toggle("on", b.dataset.issue === issue);
    store.send({ t: "setup", issue });
  });
  const poll = () => store.send({ t: "setup_info" });
  poll();
  timer = setInterval(poll, 3000);
  if (store.setupInfo) setupInfo(store.setupInfo);
  if (store.advice) advice(store.advice);
}

function hide() { clearInterval(timer); }

const changeRows = (changes) => changes.map((c) => `
  <div class="change"><span>${esc(c.label)}</span>
    <span class="v">${c.from ? `${esc(c.from)} → <span class="green">${esc(c.to)}</span>` : esc(c.delta)}</span>
    <small>${esc(c.why)}</small></div>`).join("");

function setupInfo(info) {
  if (!el.rec?.isConnected) return;
  const { rec, library } = info;
  el.reclaps.textContent = rec.laps ? `z ${rec.laps} platných kol` : "";
  if (!rec.items.length) {
    el.rec.innerHTML = `<p class="muted">${rec.laps
      ? "Auto je podle dat vyvážené, nic výrazného nevidím. 👍"
      : "Odjeď 2–3 čistá kola v kuse. Z prokluzu kol, teplot a opotřebení pak poznám, co autu chybí."}</p>`;
  } else {
    el.rec.innerHTML = `<div class="rec-grid">${rec.items.map((item, i) => `
      <div class="rec ${i === 0 ? "top" : ""}">
        <div class="rec-title">${i === 0 ? "Hlavní problém: " : ""}${esc(item.label)}</div>
        ${item.reasons.map((r) => `<div class="note">${esc(r)}</div>`).join("")}
        ${changeRows(item.changes)}
      </div>`).join("")}</div>
      <p class="muted" style="margin-top:10px;font-size:.88rem">Měň jednu věc a odjeď 2–3 kola. Odhady vycházejí z prokluzu
      pneumatik a teplot, ber je jako návrh, ne jako jistotu.</p>`;
  }
  if (rec.notes.length) el.rec.innerHTML += rec.notes.map((n) => `<div class="note">${esc(n)}</div>`).join("");

  el.lib.innerHTML = library.length ? library.map((s, i) => `
    <details class="lib ${s.current ? "current" : ""}" ${i === 0 ? "open" : ""}>
      <summary><b class="num">${fmtLap(s.best_ms)}</b>
        ${i === 0 ? '<span class="pill on">nejrychlejší</span>' : ""}${s.current ? '<span class="pill warn">právě jedeš</span>' : ""}
        <span class="dim">${s.laps} kol · ${esc(s.date)}</span></summary>
      ${s.current ? '<p class="muted">Tohle je tvůj aktuální setup.</p>' : s.diff.length
        ? `<div class="label" style="margin:8px 0 4px">Co změnit oproti aktuálnímu</div>${s.diff.map((d) =>
          `<div class="kv"><span>${esc(d.label)}</span><b>${esc(d.from)} → <span class="green">${esc(d.to)}</span></b></div>`).join("")}`
        : '<p class="muted">Liší se jen palivem.</p>'}
    </details>`).join("")
    : '<p class="muted">Zatím nic. Každé platné kolo se uloží i s nastavením, se kterým jsi ho zajel – příště tu uvidíš, s jakým setupem jsi byl nejrychlejší.</p>';

  el.current.innerHTML = info.current.map((g) => `<div class="label" style="margin:10px 0 4px">${esc(g.group)}</div>
    ${g.items.map((i) => `<div class="kv"><span>${esc(i.label)}</span><b>${esc(i.value)}</b></div>`).join("")}`).join("")
    || '<span class="muted">Hra zatím neposlala nastavení vozu.</span>';
}

function advice(a) {
  if (!el.advice?.isConnected) return;
  el.title.textContent = a.label;
  el.advice.innerHTML = a.changes.length
    ? changeRows(a.changes) + `<p class="muted" style="margin-top:10px;font-size:.88rem">Měň po jedné položce odshora. ${
      a.hasSetup ? "" : "Aktuální setup hra zatím neposlala, vidíš jen směr změny."}</p>`
    : '<span class="muted">Všechny páky pro tenhle problém už jsou na doraz.</span>';
}

export default {
  id: "setup", title: "Setup", mount, hide, setupInfo, advice,
  icon: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
};
