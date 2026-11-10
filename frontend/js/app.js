// App shell: WebSocket connection, tab routing and the top status bar.
import { $, esc, fmtClock } from "./util.js";
import * as radio from "./radio.js";
import dashboard from "./views/dashboard.js";
import timing from "./views/timing.js";
import map from "./views/map.js";
import strategy from "./views/strategy.js";
import telemetry from "./views/telemetry.js";
import setup from "./views/setup.js";
import car from "./views/car.js";
import settings from "./views/settings.js";

const VIEWS = [dashboard, timing, map, strategy, telemetry, setup, car, settings];
const WEATHER_ICON = ["☀️", "🌤️", "☁️", "🌦️", "🌧️", "⛈️"];

export const store = { slow: null, fast: null, outline: null, traces: null, advice: null, hello: null, verbosity: 2 };
let socket = null, retry = 500, active = null;

function send(message) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/ws`);
  socket.onopen = () => { retry = 500; paintConnection(); };
  socket.onclose = () => {
    store.slow = null; paintConnection(); paintShell();
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 5000);  // back off so a stopped server is not hammered
  };
  socket.onmessage = (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    handle(message.t, message.d);
  };
}

function handle(type, data) {
  switch (type) {
    case "fast": store.fast = data; active.fast?.(data, store); break;
    case "slow": store.slow = data; paintConnection(); paintShell(); if (data.ready) active.slow?.(data, store); break;
    case "radio": radio.onMessage(data); break;
    case "outline": store.outline = data; active.outline?.(data, store); break;
    case "traces": store.traces = data; active.traces?.(data, store); break;
    case "advice": store.advice = data; active.advice?.(data, store); break;
    case "verbosity": store.verbosity = data; active.verbosity?.(data, store); break;
    case "hello": store.hello = data; store.verbosity = data.verbosity; radio.setHistory(data.radio); break;
  }
}

function paintConnection() {
  const el = $("#conn"), online = socket?.readyState === WebSocket.OPEN, game = store.slow?.connected;
  el.className = `conn ${online ? (game ? "on" : "wait") : "off"}`;
  el.lastElementChild.textContent = online ? (game ? "live" : "čekám na hru") : "offline";
  $("#nodata-state").textContent = online
    ? "Server běží, ale ze hry zatím nepřišla žádná data."
    : "Bez spojení se serverem – běží aplikace na PC?";
}

function paintShell() {
  const s = store.slow, ready = Boolean(s?.ready);
  $("#nodata").hidden = ready || active.always;
  $("#views").hidden = !ready && !active.always;
  if (!ready) {
    $("#top-session").textContent = "—"; $("#top-progress").textContent = "—";
    $("#top-weather").textContent = "—"; $("#top-flag").hidden = true;
    return;
  }
  const { session, player } = s;
  $("#top-session").textContent = `${session.typeName} · ${session.track}`;
  $("#top-progress").textContent = session.isRace
    ? `Kolo ${Math.min(player.lap, session.totalLaps)}/${session.totalLaps}`
    : `Zbývá ${fmtClock(session.timeLeft)}`;
  $("#top-weather").textContent =
    `${WEATHER_ICON[session.weather] || ""} ${session.trackTemp}° trať · ${session.airTemp}° vzduch`;
  const flag = $("#top-flag");
  const state = session.sc ? ["yellow", session.sc]
    : player.flag === 3 ? ["yellow", "ŽLUTÁ"] : player.flag === 2 ? ["blue", "MODRÁ"]
    : session.paused ? ["green", "PAUZA"] : null;
  flag.hidden = !state;
  if (state) { flag.className = `flag-pill ${state[0]}`; flag.textContent = state[1]; }
}

function show(view) {
  active?.hide?.();
  active = view;
  for (const tab of document.querySelectorAll(".tab")) tab.classList.toggle("active", tab.dataset.id === view.id);
  const host = $("#views");
  host.innerHTML = "";
  view.mount(host, store);
  paintShell();
  if (store.slow?.ready) view.slow?.(store.slow, store);
  if (store.fast) view.fast?.(store.fast, store);
  if (store.outline) view.outline?.(store.outline, store);
  $("#main").scrollTop = 0;
}

function boot() {
  store.send = send;  // views talk to the server through the store, not by importing the shell
  $("#tabs").innerHTML = VIEWS.map((v) =>
    `<button class="tab" data-id="${v.id}"><svg viewBox="0 0 24 24">${v.icon}</svg>${esc(v.title)}</button>`).join("");
  $("#tabs").addEventListener("click", (e) => {
    const id = e.target.closest(".tab")?.dataset.id;
    if (id) show(VIEWS.find((v) => v.id === id));
  });
  radio.setup((q) => send({ t: "query", q }));
  $("#radio-toggle").addEventListener("click", () => $("#radio").classList.toggle("open"));
  $("#splash-go").addEventListener("click", () => { radio.unlock(); $("#splash").remove(); });
  $("#splash-mute").addEventListener("click", () => { radio.configure({ enabled: false }); $("#splash").remove(); });
  show(dashboard);
  connect();
}

boot();
