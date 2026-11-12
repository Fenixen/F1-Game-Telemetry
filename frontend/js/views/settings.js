// Radio / voice settings and a short how-to.
import { esc, prefs } from "../util.js";
import * as radio from "../radio.js";

let host = null, storeRef = null;

function render() {
  const s = radio.settings(), voices = radio.voices();
  const seg = (id, options, current) => `<div class="seg" data-seg="${id}">${options.map(([value, label]) =>
    `<button data-v="${value}" class="${String(value) === String(current) ? "on" : ""}">${label}</button>`).join("")}</div>`;
  host.innerHTML = `<div class="grid two">
    <div class="card"><h3>Hlas inženýra</h3>
      <div class="field"><span>Hlasový výstup</span>${seg("enabled", [[1, "Zapnuto"], [0, "Vypnuto"]], s.enabled ? 1 : 0)}</div>
      <div class="field"><span>Hlas</span><select id="st-voice">${voices.length
        ? voices.map((v) => `<option ${v.name === s.voiceName ? "selected" : ""}>${esc(v.name)}</option>`).join("")
        : "<option>Český hlas nenalezen</option>"}</select></div>
      <div class="field"><span>Rychlost řeči</span><input id="st-rate" type="range" min="0.8" max="1.5" step="0.05" value="${s.rate}"></div>
      <div class="field"><span>Hlasitost</span><input id="st-volume" type="range" min="0.2" max="1" step="0.05" value="${s.volume}"></div>
      <div class="field"><span>Pípnutí před hláškou</span>${seg("beep", [[1, "Ano"], [0, "Ne"]], s.beep ? 1 : 0)}</div>
      <div class="field"><span>Zkouška</span><button class="btn" id="st-test">Rádio check</button></div>
      ${s.supported ? "" : '<p class="red">Tenhle prohlížeč neumí převod textu na řeč. Hlášky uvidíš jen v textu.</p>'}
      ${voices.length ? "" : `<p class="help">Na iPadu přidej český hlas v <b>Nastavení → Zpřístupnění → Předčítání obsahu → Hlasy → Čeština</b>
        (nejlépe „Zuzana – vylepšená“).</p>`}
    </div>
    <div class="card span2"><h3>Velikost písma</h3>
      <div class="field"><span>Celá aplikace</span>${seg("scale", [[1, "Normální"], [1.15, "Velké"], [1.3, "Větší"], [1.5, "Obří"]],
        prefs.get("uiScale", 1.15))}</div>
      <p class="help">Na iPadu za jízdy doporučuju „Velké“ nebo „Větší“. Hlavní obrazovka <b>Závod</b> má čísla velká vždy.</p>
    </div>
    <div class="card"><h3>Jak moc má inženýr mluvit</h3>
      <div class="field"><span>Upovídanost</span>${seg("verbosity", [[1, "Jen kritické"], [2, "Normální"], [3, "Všechno"]], storeRef.verbosity)}</div>
      <p class="help">Kritické hlášky (vlajky, safety car, box, penalizace, vážné poškození) zazní vždy a okamžitě.
        Ostatní si inženýr nechá na rovinku, aby tě nerušil v zatáčce.</p>
    </div>
    <div class="card span2"><h3>Dotazy z volantu</h3>
      <p class="help">Ve hře v <b>Settings → Controls → Edit</b> přiřaď tlačítkům akce <b>UDP Action 1–8</b>. Zmáčknutím se zeptáš bez sahání na iPad:</p>
      <ul class="help" style="columns:2;margin-top:8px">
        <li>UDP Action 1 – odstupy</li><li>UDP Action 2 – pneumatiky</li><li>UDP Action 3 – palivo</li>
        <li>UDP Action 4 – strategie</li><li>UDP Action 5 – počasí</li><li>UDP Action 6 – poškození</li>
        <li>UDP Action 7 – tempo</li><li>UDP Action 8 – zopakuj poslední</li></ul>
    </div>
    <div class="card span2"><h3>Tipy pro iPad</h3>
      <ul class="help">
        <li>V Safari dej <b>Sdílet → Přidat na plochu</b> – aplikace poběží na celou obrazovku.</li>
        <li>Aby displej nezhasínal: <b>Nastavení → Displej a jas → Uzamčení → Nikdy</b>.</li>
        <li>iPad i PC musí být ve stejné síti; adresu ukazuje okno aplikace na PC po spuštění.</li>
      </ul>
    </div>
  </div>`;
}

function mount(root, store) {
  host = root; storeRef = store;
  render();
  root.onclick = (e) => {
    const button = e.target.closest(".seg button");
    if (button) {
      const kind = button.parentElement.dataset.seg, value = Number(button.dataset.v);
      if (kind === "verbosity") { store.verbosity = value; store.send({ t: "verbosity", v: value }); }
      else if (kind === "scale") { prefs.set("uiScale", value); document.documentElement.style.fontSize = `${15 * value}px`; }
      else radio.configure({ [kind]: Boolean(value) });
      if (kind === "enabled" && value) radio.unlock();
      render();
    } else if (e.target.id === "st-test") radio.test();
  };
  root.onchange = (e) => {
    if (e.target.id === "st-voice") radio.configure({ voiceName: e.target.value });
    if (e.target.id === "st-rate") radio.configure({ rate: Number(e.target.value) });
    if (e.target.id === "st-volume") radio.configure({ volume: Number(e.target.value) });
  };
  document.addEventListener("voices", onVoices);
}
const onVoices = () => { if (host?.isConnected && host.querySelector("#st-voice")) render(); };
function hide() { document.removeEventListener("voices", onVoices); host.onclick = host.onchange = null; }
function verbosity() { render(); }

export default {
  id: "settings", title: "Nastavení", mount, hide, verbosity, always: true,
  icon: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
};
