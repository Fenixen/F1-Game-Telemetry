// Team radio: message feed + text-to-speech through the browser's speech engine.
import { $, esc, prefs } from "./util.js";

const CATEGORY = {
  info: "Info", lap: "Kolo", gap: "Souboj", car: "Vůz", damage: "Poškození", strategy: "Strategie",
  flag: "Vlajky", weather: "Počasí", penalty: "Komisaři", reply: "Odpověď",
};
const QUESTIONS = [
  ["gaps", "Odstupy"], ["tyres", "Pneumatiky"], ["fuel", "Palivo"], ["strategy", "Strategie"],
  ["weather", "Počasí"], ["damage", "Poškození"], ["pace", "Tempo"], ["repeat", "Zopakuj"],
];

const synth = window.speechSynthesis;
const state = {
  enabled: prefs.get("voice", true), rate: prefs.get("rate", 1.1), volume: prefs.get("volume", 1),
  voiceName: prefs.get("voiceName", ""), beep: prefs.get("beep", true),
  queue: [], speaking: false, audio: null, seen: new Set(),
};

export function voices() {
  return synth ? synth.getVoices().filter((v) => v.lang.toLowerCase().startsWith("cs")) : [];
}
function pickVoice() {
  const list = voices();
  return list.find((v) => v.name === state.voiceName) || list[0] || null;
}

/** Must run inside a tap handler: iOS only allows audio after a user gesture. */
export function unlock() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx && !state.audio) state.audio = new Ctx();
    state.audio?.resume();
    if (synth) synth.speak(new SpeechSynthesisUtterance(" "));
  } catch { /* no audio available: the feed still works */ }
}

function beep() {
  const ctx = state.audio;
  if (!state.beep || !ctx || ctx.state !== "running") return;
  const osc = ctx.createOscillator(), gain = ctx.createGain(), t = ctx.currentTime;
  osc.type = "sine"; osc.frequency.value = 1320;
  gain.gain.setValueAtTime(0.0001, t);
  gain.gain.exponentialRampToValueAtTime(0.12 * state.volume, t + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
  osc.connect(gain).connect(ctx.destination);
  osc.start(t); osc.stop(t + 0.18);
}

function next() {
  if (state.speaking || !state.queue.length) return;
  const msg = state.queue.shift();
  const utter = new SpeechSynthesisUtterance(msg.say || msg.text);
  const voice = pickVoice();
  if (voice) utter.voice = voice;
  utter.lang = voice ? voice.lang : "cs-CZ";
  utter.rate = state.rate; utter.volume = state.volume;
  const node = document.getElementById(`msg-${msg.id}`);
  const done = () => {
    clearTimeout(guard);
    if (!state.speaking) return;
    state.speaking = false;
    node?.classList.remove("speaking");
    next();
  };
  // Safari sometimes never fires onend; the guard keeps the queue moving.
  const guard = setTimeout(done, 3000 + (msg.text.length * 110) / state.rate);
  utter.onend = done; utter.onerror = done;
  state.speaking = true;
  node?.classList.add("speaking");
  beep();
  setTimeout(() => synth.speak(utter), state.beep ? 170 : 0);
}

function speak(msg) {
  if (!state.enabled || !synth) return;
  if (msg.prio === 1) {
    // Critical call: drop chatter that has not been spoken yet and go first.
    state.queue = state.queue.filter((m) => m.prio === 1);
    state.queue.push(msg);
  } else {
    state.queue.push(msg);
    if (state.queue.length > 4) state.queue.splice(0, state.queue.length - 4);
  }
  next();
}

function render(msg, fresh) {
  const feed = $("#radio-feed");
  const node = document.createElement("div");
  node.id = `msg-${msg.id}`;
  node.className = `msg p${msg.prio} c-${msg.cat}${fresh ? " new" : ""}`;
  node.innerHTML = `<div class="meta"><span>${esc(CATEGORY[msg.cat] || msg.cat)}${msg.lap ? ` · kolo ${msg.lap}` : ""}</span>
    <span>${esc(msg.time)}</span></div>${esc(msg.text)}`;
  feed.prepend(node);
  while (feed.children.length > 80) feed.lastChild.remove();
}

export function onMessage(msg, fresh = true) {
  const repeated = state.seen.has(msg.id);
  if (!repeated) { state.seen.add(msg.id); render(msg, fresh); }
  document.dispatchEvent(new CustomEvent("radio", { detail: msg }));  // e.g. the race view shows the latest call
  if (fresh) speak(msg);
}

/** Called on every (re)connect: the server is the source of truth, and its ids restart with it. */
export function setHistory(list) {
  state.seen.clear();
  $("#radio-feed").innerHTML = "";
  for (const msg of list) onMessage(msg, false);
}

export function setup(ask) {
  const box = $("#radio-ask");
  box.innerHTML = QUESTIONS.map(([q, label]) => `<button class="btn" data-q="${q}">${label}</button>`).join("");
  box.addEventListener("click", (e) => {
    const q = e.target.closest("[data-q]")?.dataset.q;
    if (q) { unlock(); ask(q); }
  });
  const mute = $("#radio-mute");
  const paint = () => { mute.textContent = state.enabled ? "🔊" : "🔇"; };
  mute.addEventListener("click", () => { configure({ enabled: !state.enabled }); paint(); });
  paint();
  if (synth) synth.onvoiceschanged = () => document.dispatchEvent(new Event("voices"));
}

export function configure(patch) {
  Object.assign(state, patch);
  if ("enabled" in patch) {
    prefs.set("voice", state.enabled);
    if (!state.enabled && synth) { state.queue = []; synth.cancel(); state.speaking = false; }
    const mute = $("#radio-mute");
    if (mute) mute.textContent = state.enabled ? "🔊" : "🔇";
  }
  if ("rate" in patch) prefs.set("rate", state.rate);
  if ("volume" in patch) prefs.set("volume", state.volume);
  if ("voiceName" in patch) prefs.set("voiceName", state.voiceName);
  if ("beep" in patch) prefs.set("beep", state.beep);
}
export const settings = () => ({ ...state, supported: Boolean(synth) });
export function test() {
  unlock();
  speak({ id: 0, prio: 1, text: "Rádio check. Slyšíš mě dobře?" });
}
