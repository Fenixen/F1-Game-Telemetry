"""The race engineer's brain: turns state changes into radio messages (in Czech)."""
from __future__ import annotations

import time
from typing import Callable

from .. import strategy
from ..state import Car, RaceState
from ..udp import constants as C
from .engine import CHATTY, CRITICAL, NORMAL, Radio, fmt_gap, fmt_lap, fmt_num, plural

TYRE_HOT_C, TYRE_COLD_C, BRAKE_HOT_C = 105, 65, 1050
SUSTAIN_TICKS = 25                    # condition must hold this many ticks (~5 s) before we report it
WEAR_STEPS = (40, 60, 75)
DRS_RANGE_MS = 1000

# In-game "UDP Action n" buttons -> driver questions
BUTTON_QUERIES = {1: "gaps", 2: "tyres", 3: "fuel", 4: "strategy", 5: "weather", 6: "damage", 7: "pace", 8: "repeat"}

_DAMAGE_NAMES = {
    "fl_wing": "přední křídlo vlevo", "fr_wing": "přední křídlo vpravo", "rear_wing": "zadní křídlo",
    "floor": "podlaha", "diffuser": "difuzor", "sidepod": "bočnice", "gearbox": "převodovka", "engine": "motor",
}


class Engineer(Radio):
    def __init__(self, state: RaceState, emit: Callable[[dict], None],
                 clock: Callable[[], float] = time.monotonic):
        super().__init__(state, emit, clock)
        self.mem: dict = {}

    # ------------------------------------------------------------------ event entry points

    def on_events(self, events: list[tuple[str, dict]]) -> None:
        for name, data in events:
            handler = getattr(self, f"_ev_{name}", None)
            if handler:
                handler(data)

    def tick(self) -> None:
        """Periodic checks (~5 Hz) for conditions that are states rather than events."""
        s = self.state
        if s.connected and s.session and s.player.lap and s.player.status and s.player.part:
            self._greet()
            self._check_weather()
            self._check_safety_car()
            self._check_flags()
            self._check_warnings()
            if s.player.tel and s.player.damage:
                self._check_tyres()
                self._check_damage()
            if s.is_race:
                self._check_gaps()
                self._check_ers()
                self._check_pit_call()
            else:
                self._check_session_clock()
                self._check_traffic()
        self.flush()

    # ------------------------------------------------------------------ helpers

    def _changed(self, key: str, value):
        """Returns the previous value if `value` differs from the remembered one, else None marker."""
        prev = self.mem.get(key, _UNSET)
        self.mem[key] = value
        return prev if prev != value else _SAME

    def _once(self, key) -> bool:
        if key in self.mem:
            return False
        self.mem[key] = True
        return True

    def _sustained(self, key: str, condition: bool) -> bool:
        count = self.mem.get(key, 0) + 1 if condition else 0
        self.mem[key] = count
        return count == SUSTAIN_TICKS

    def _racing(self) -> bool:
        """Green-flag running on track (no safety car, not in the pits)."""
        return self.state.player.on_track and self.state.session.get("safety_car", 0) == 0

    # ------------------------------------------------------------------ session lifecycle

    def _ev_session_started(self, _: dict) -> None:
        self.mem.clear()
        self.clear()

    def _greet(self) -> None:
        if not self._once("greeted"):
            return
        s, me = self.state, self.state.player
        session = s.session
        track = C.TRACKS.get(session["track_id"], "trať")
        weather = C.WEATHER.get(session["weather"], "").lower()
        if s.is_race:
            text = (f"Rádio check. {track}, závod na {plural(session['total_laps'], 'kolo', 'kola', 'kol')}. "
                    f"Startuješ z {me.lap['grid_position']}. místa na pneumatikách {me.compound[1]}. "
                    f"Počasí {weather}, trať {session['track_temp']} °C. Jdeme na to.")
        else:
            text = (f"Rádio check. {C.SESSION_TYPES.get(s.session_type, 'Session')}, {track}. "
                    f"Počasí {weather}, trať {session['track_temp']} °C.")
        self.say("greet", text, NORMAL, "info", now=True)

    def _ev_final(self, body: dict) -> None:
        if not self._once("final"):
            return
        result = body["cars"][self.state.player_idx]
        pos = result["position"]
        if not self.state.is_race:
            self.say("final", f"Konec. Končíš na P{pos}, nejlepší kolo {fmt_lap(result['best_lap_ms'])}.",
                     CRITICAL, "info")
            return
        if result["result_status"] != 3:
            self.say("final", "Dnes to bohužel nedopadlo. Hlavu vzhůru, příště to vrátíme.", CRITICAL, "info")
            return
        if pos == 1:
            comment = "Vítězství! Fantastický závod, skvělá práce!"
        elif pos <= 3:
            comment = "Pódium! Skvělá práce."
        elif pos <= 10:
            comment = "Body jsou doma, dobrá práce."
        else:
            comment = "Dnes to na body nebylo, ale odjel jsi to čistě."
        points = f" Bereme {plural(result['points'], 'bod', 'body', 'bodů')}." if result["points"] else ""
        self.say("final", f"Šachovnicová vlajka, P{pos}! {comment}{points}", CRITICAL, "info")

    # ------------------------------------------------------------------ laps

    def _ev_lap_complete(self, data: dict) -> None:
        if data["idx"] != self.state.player_idx:
            return
        if self.state.is_race:
            self._race_lap_report(data)
        else:
            self._timed_lap_report(data)

    def _race_lap_report(self, data: dict) -> None:
        s, me, lap = self.state, self.state.player, data["lap"]
        total = s.session.get("total_laps", 0)
        left = total - lap
        parts = [f"Kolo {lap}, {fmt_lap(data['ms'])}."]
        log = s.lap_log
        if data["valid"] and me.best_ms == data["ms"] and lap > 1:
            parts.append("Osobní nejlepší.")
        elif len(log) >= 2 and lap > 2:
            diff = data["ms"] - log[-2]["ms"]
            if abs(diff) >= 150:
                parts.append(f"O {fmt_gap(diff)} {'pomalejší' if diff > 0 else 'rychlejší'} než minule.")

        gaps = s.gaps()
        close = False
        for side, label, better, worse in (("ahead", "Před tebou", "stahuješ ho", "odjíždí ti"),
                                           ("behind", "Za tebou", "odjíždíš mu", "dotahuje tě")):
            gap = gaps[side]
            if not gap:
                if side == "ahead":
                    parts.append("Vedeš závod.")
                continue
            close = close or gap["ms"] < 2000
            text = f"{label} {gap['name']} {fmt_gap(gap['ms'])}"
            prev = self.mem.get(f"gap_{side}")
            if prev and prev[0] == gap["idx"] and abs(gap["ms"] - prev[1]) >= 200:
                closing = gap["ms"] < prev[1]
                text += f", {better if closing == (side == 'ahead') else worse}"
            self.mem[f"gap_{side}"] = (gap["idx"], gap["ms"])
            parts.append(text + ".")
        if lap == 1:
            gained = me.lap["grid_position"] - me.position
            if gained > 0:
                parts.insert(0, f"Dobrý start, získal jsi {plural(gained, 'pozici', 'pozice', 'pozic')}. P{me.position}.")
            elif gained < 0:
                parts.insert(0, f"Po startu jsi P{me.position}, ztráta {plural(-gained, 'pozice', 'pozic', 'pozic')}.")
        if left > 0:
            self.say("lap", " ".join(parts), NORMAL if close or lap % 3 == 0 or lap == 1 else CHATTY, "lap", 5)

        if left == 1:
            self.say("laps_left", "Poslední kolo! Dovez to do cíle.", NORMAL, "info", 5)
        elif left in (3, 5, 10) and total > left + 2:
            self.say("laps_left", f"{plural(left, 'kolo', 'kola', 'kol').capitalize()} do konce.", NORMAL, "info", 5)
        elif total >= 8 and lap == total // 2:
            self.say("laps_left", "Polovina závodu za námi.", CHATTY, "info", 5)

        if left > 1:
            self._fuel_report(lap)
            if lap % 5 == 0:
                wear = max(me.damage.get("tyre_wear", [0]))
                self.say("status", f"Stav: pneumatiky {wear:.0f} % opotřebení, "
                                   f"palivo {self._fuel_text()}.", NORMAL, "car", 60)

    def _timed_lap_report(self, data: dict) -> None:
        s, me = self.state, self.state.player
        if not data["valid"]:
            self.say("lap", f"{fmt_lap(data['ms'])}, ale kolo se nepočítá.", NORMAL, "lap", 5)
            return
        if me.lap.get("driver_status") == 3 and data["ms"] != me.best_ms:
            return  # out-lap, nothing to report
        text = f"{fmt_lap(data['ms'])}. Jsi P{me.position}."
        if me.best_ms == data["ms"]:
            text = "Osobní nejlepší, " + text
        leader = s.car_at(1)
        if leader and leader is not me and leader.best_ms and me.best_ms:
            text += f" Na první místo ztrácíš {fmt_gap(me.best_ms - leader.best_ms)}."
        cutoff = {5: len(s.active_cars()) - 5, 6: 10}.get(s.session_type)
        if cutoff and cutoff > 0:
            if me.position <= cutoff:
                text += f" Zatím postupuješ, hranice je P{cutoff}."
            else:
                edge = s.car_at(cutoff)
                if edge and edge.best_ms and me.best_ms:
                    text += f" Jsi mimo postup, chybí {fmt_gap(me.best_ms - edge.best_ms)}."
        self.say("lap", text, NORMAL, "lap", 5)

    # ------------------------------------------------------------------ fuel / ERS

    def _fuel_text(self) -> str:
        delta = self.state.player.status.get("fuel_remaining_laps", 0.0)
        return f"{'plus' if delta >= 0 else 'mínus'} {fmt_num(abs(delta))} kola"

    def _fuel_report(self, lap: int) -> None:
        delta = self.state.player.status.get("fuel_remaining_laps", 0.0)
        if lap < 2:
            return
        if delta < -0.25:
            self.say("fuel_low", f"Palivo nevychází, jsme {fmt_num(-delta)} kola pod cílem. "
                                 "Lift and coast, případně chudší směs.", NORMAL, "car", 150)
        elif delta > 1.5 and lap >= 3:
            self.say("fuel_high", f"Paliva máme {fmt_num(delta)} kola navíc, můžeš jet bohatší směs.",
                     CHATTY, "car", 400)

    def _check_ers(self) -> None:
        pct = 100 * self.state.player.status.get("ers_store", 0.0) / C.ERS_MAX_J
        if self._racing() and pct < 8:
            self.say("ers_low", "Baterie je skoro prázdná, dobíjej.", NORMAL, "car", 120)

    # ------------------------------------------------------------------ tyres & brakes

    def _check_tyres(self) -> None:
        me = self.state.player
        tel, wear = me.tel, max(me.damage["tyre_wear"])
        inner = tel["tyre_inner_temp"]
        front, rear = (inner[2] + inner[3]) / 2, (inner[0] + inner[1]) / 2
        racing = self._racing()
        if self._sustained("hot_front", racing and front > TYRE_HOT_C):
            self.say("tyre_temp", f"Přední pneumatiky se přehřívají, {front:.0f} °C. Šetři je v zatáčkách.",
                     NORMAL, "car", 90)
        if self._sustained("hot_rear", racing and rear > TYRE_HOT_C):
            self.say("tyre_temp", f"Zadní pneumatiky se přehřívají, {rear:.0f} °C. Opatrně na plyn.",
                     NORMAL, "car", 90)
        dry = me.status.get("visual_compound") not in C.WET_COMPOUNDS
        cold = racing and dry and tel["speed"] > 120 and max(front, rear) < TYRE_COLD_C
        if self._sustained("cold", cold) and me.lap["lap_num"] > 1:
            self.say("tyre_temp", "Pneumatiky jsou studené, dostaň do nich teplotu.", NORMAL, "car", 120)
        if self._sustained("hot_brakes", racing and max(tel["brake_temp"]) > BRAKE_HOT_C):
            self.say("brakes", "Brzdy jsou moc horké, brzdi dřív a jemněji.", NORMAL, "car", 120)

        # Wear thresholds: report each one once per tyre set
        last_step = self.mem.get("wear_step", 0)
        if wear < last_step - 20:
            last_step = 0  # fresh tyres fitted
        step = max((x for x in WEAR_STEPS if wear >= x), default=0)
        if step > last_step:
            if step == 40:
                self.say("wear", "Pneumatiky na 40 % opotřebení, zatím v pořádku.", CHATTY, "car", 10)
            elif step == 60:
                self.say("wear", "Pneumatiky na 60 %, začnou ztrácet. Blíží se čas na zastávku.", NORMAL, "car", 10)
            else:
                self.say("wear", "Pneumatiky jsou za hranou, přes 75 %. Hrozí defekt, potřebujeme zastavit.",
                         CRITICAL, "car", 10)
        self.mem["wear_step"] = max(step, last_step)

    def _check_damage(self) -> None:
        damage = self.state.player.damage
        for part, label in _DAMAGE_NAMES.items():
            value, prev = damage[part], self.mem.get(f"dmg_{part}", 0)
            if value < prev:
                self.mem[f"dmg_{part}"] = value  # repaired in the pits
            if value < prev + 5:
                continue
            self.mem[f"dmg_{part}"] = value
            if part in ("gearbox", "engine"):
                if value >= 50:
                    self.say(f"dmg_{part}", f"Pozor, {label} má {value} % opotřebení. Šetři ho, řaď dřív.",
                             NORMAL, "damage", 120)
                continue
            if value >= 50:
                advice = "Je to vážné, box na opravu." if "wing" in part else "Výrazně ztrácíš přítlak."
            elif value >= 25:
                advice = "Ztrácíš přítlak, zvaž opravu při zastávce."
            else:
                advice = "Dá se s tím jet dál."
            self.say(f"dmg_{part}", f"Poškození: {label} {value} %. {advice}",
                     CRITICAL if value >= 50 else NORMAL, "damage", 8)
        for fault, label in (("drs_fault", "DRS"), ("ers_fault", "ERS")):
            if self._changed(fault, damage[fault]) is not _SAME and damage[fault]:
                self.say(fault, f"Máme poruchu {label}. Musíš to dojet bez něj.", CRITICAL, "damage", 60)
        if self._changed("blown", damage["engine_blown"]) is not _SAME and damage["engine_blown"]:
            self.say("blown", "Motor je pryč. Zastav vůz na bezpečném místě.", CRITICAL, "damage", 60)

    # ------------------------------------------------------------------ gaps & traffic

    def _check_gaps(self) -> None:
        if not self._racing() or self.state.player.lap["lap_num"] < 2:
            return
        gaps = self.state.gaps()
        ahead, behind = gaps["ahead"], gaps["behind"]
        if ahead and 0 < ahead["ms"] < DRS_RANGE_MS:
            self.say("drs_ahead", f"{ahead['name']} je v dosahu DRS, {fmt_gap(ahead['ms'])}. Jdi na něj.",
                     NORMAL, "gap", 60)
        if behind and 0 < behind["ms"] < DRS_RANGE_MS:
            self.say("drs_behind", f"{behind['name']} za tebou má DRS, {fmt_gap(behind['ms'])}. Braň se.",
                     NORMAL, "gap", 60)

    def _check_traffic(self) -> None:
        s, me = self.state, self.state.player
        if not me.on_track or me.lap.get("driver_status") not in (2, 3) or not s.track_length:
            return
        for car in s.cars:
            if car is me or not car.on_track or car.lap.get("driver_status") != 1:
                continue
            behind_m = (me.lap["lap_distance"] - car.lap["lap_distance"]) % s.track_length
            if 0 < behind_m < 250:
                self.say(f"traffic{car.idx}", f"{car.name} za tebou na rychlém kole, pusť ho.", NORMAL, "gap", 25)
                return

    def _check_session_clock(self) -> None:
        left = self.state.session.get("time_left", 0)
        prev = self.mem.get("time_left", left)
        self.mem["time_left"] = left
        for mark, text in ((300, "Zbývá 5 minut."), (120, "Poslední 2 minuty, je čas na poslední pokus.")):
            if prev > mark >= left:
                self.say(f"clock{mark}", text, NORMAL, "info", 30)

    # ------------------------------------------------------------------ pit stops

    def _check_pit_call(self) -> None:
        s, me = self.state, self.state.player
        if not self._racing() or not s.track_length:
            return
        plan = strategy.compute(s)
        if not plan.get("pitNeeded") or plan.get("pitLap") is None:
            return
        lap, frac = me.lap["lap_num"], me.lap["lap_distance"] / s.track_length
        tyre = f" Nasadíme {plan['nextCompound']['name']}." if plan.get("nextCompound") else ""
        if lap == plan["pitLap"] - 1 and frac > 0.5 and self._once(("box_next", lap)):
            self.say("box", f"Box příští kolo, příští kolo box.{tyre}", NORMAL, "strategy", 20)
        elif lap == plan["pitLap"] and 0.55 < frac < 0.97 and self._once(("box_now", lap)):
            self.say("box", f"Box, box. Tohle kolo box.{tyre}", CRITICAL, "strategy", 20)

    def _ev_pit_enter(self, data: dict) -> None:
        s, me = self.state, self.state.player
        car = s.cars[data["idx"]]
        if car is me:
            if s.is_race:
                self.say("pit_in", f"Pit limiter. Limit je {s.session.get('pit_speed_limit', 80)} km/h.",
                         CRITICAL, "strategy", 20)
        elif s.is_race and me.on_track and abs(car.position - me.position) == 1:
            where = "před tebou" if car.position < me.position else "za tebou"
            hint = " Volná trať, zatlač." if car.position < me.position else " Pozor na undercut."
            self.say(f"rival_pit{car.idx}", f"{car.name} {where} jede do boxů.{hint}", NORMAL, "strategy", 60)

    def _ev_pit_exit(self, data: dict) -> None:
        s, me = self.state, self.state.player
        if data["idx"] != me.idx or not s.is_race:
            return
        stop = f"Zastávka {fmt_gap(data['stop_ms'])}. " if data["stop_ms"] else ""
        self.say("pit_out", f"{stop}Vyjíždíš na P{me.position}, pneumatiky {me.compound[1]} jsou studené, "
                            "opatrně první zatáčky.", CRITICAL, "strategy", 20)

    # ------------------------------------------------------------------ race control

    def _check_safety_car(self) -> None:
        status = self.state.session.get("safety_car", 0)
        prev = self._changed("sc", status)
        if prev is _SAME or prev is _UNSET:
            return
        if status == 1:
            text = "Safety car na trati, safety car. Zpomal a hlídej deltu."
            plan = strategy.compute(self.state)
            if self.state.is_race and (plan.get("pitNeeded") or plan.get("wearNow", 0) > 35):
                text += " Zastávka je teď levná, zvaž box."
            self.say("sc", text, CRITICAL, "flag", 5)
        elif status == 2:
            self.say("sc", "Virtuální safety car. Hlídej deltu, nepředjíždět.", CRITICAL, "flag", 5)
        elif status == 0 and prev in (1, 2):
            self.say("sc", "Zelená, zelená! Závodíme.", CRITICAL, "flag", 5)

    def _check_flags(self) -> None:
        flag = self.state.player.status.get("fia_flag", 0)
        prev = self._changed("flag", flag)
        if prev is _SAME or prev is _UNSET:
            return
        if flag == 3:
            self.say("flag", "Žluté vlajky před tebou. Zpomal, nepředjíždět.", CRITICAL, "flag", 8)
        elif flag == 2:
            self.say("flag", "Modrá vlajka, pusť rychlejší vůz.", CRITICAL, "flag", 15)
        elif prev == 3 and self.state.session.get("safety_car", 0) == 0:
            self.say("flag_clear", "Trať je volná, zelená.", NORMAL, "flag", 8)

    def _check_warnings(self) -> None:
        count = self.state.player.lap.get("cc_warnings", 0)
        prev = self._changed("cc_warnings", count)
        if prev is _SAME or prev is _UNSET or count <= prev:
            return
        extra = " Další už znamená penalizaci." if count >= 3 else ""
        self.say("cc", f"Varování za limity trati, máš {plural(count, 'varování', 'varování', 'varování')}.{extra}",
                 NORMAL, "penalty", 5)

    def _ev_game_event(self, ev: dict) -> None:
        handler = getattr(self, f"_game_{ev['code']}", None)
        if handler:
            handler(ev)

    def _car(self, idx: int) -> Car | None:
        return self.state.cars[idx] if idx < C.NUM_CARS else None

    def _game_FTLP(self, ev: dict) -> None:
        car, ms = self._car(ev["vehicle_idx"]), round(ev["lap_time"] * 1000)
        if not car or not self.state.is_race:
            return
        if car is self.state.player:
            self.say("ftlp", f"Nejrychlejší kolo závodu, {fmt_lap(ms)}! Pěkně.", NORMAL, "lap", 5)
        else:
            self.say("ftlp", f"{car.name} zajel nejrychlejší kolo, {fmt_lap(ms)}.", CHATTY, "info", 20)

    def _game_RTMT(self, ev: dict) -> None:
        car, me = self._car(ev["vehicle_idx"]), self.state.player
        if not car or car is me or not self.state.is_race:
            return
        ahead = 0 < car.position < me.position
        self.say(f"rtmt{car.idx}", f"{car.name} odstoupil." + (" Posouváš se o místo nahoru." if ahead else ""),
                 NORMAL if ahead else CHATTY, "info", 60)

    def _game_DRSE(self, _: dict) -> None:
        self.say("drs", "DRS je povoleno.", CHATTY, "info", 20)

    def _game_DRSD(self, _: dict) -> None:
        self.say("drs", "DRS je zakázáno.", NORMAL, "info", 20)

    def _game_RDFL(self, _: dict) -> None:
        self.say("red", "Červená vlajka! Závod je přerušen, zpomal a vrať se do boxů.", CRITICAL, "flag", 30)

    def _game_SCAR(self, ev: dict) -> None:
        if ev["event_type"] == 1 and ev["safety_car_type"] == 1:
            self.say("sc_in", "Safety car zajíždí v tomto kole. Připrav se na restart, zahřej pneumatiky.",
                     CRITICAL, "flag", 30)

    def _game_COLL(self, ev: dict) -> None:
        me = self.state.player_idx
        if me not in (ev["vehicle1_idx"], ev["vehicle2_idx"]):
            return
        other = self._car(ev["vehicle2_idx"] if ev["vehicle1_idx"] == me else ev["vehicle1_idx"])
        self.say("coll", f"Kontakt s vozem {other.name if other else ''}. Kontroluji data z vozu.",
                 NORMAL, "damage", 15)

    def _game_OVTK(self, ev: dict) -> None:
        me = self.state.player_idx
        if not self.state.is_race or self.state.player.lap.get("lap_num", 0) < 2:
            return  # lap 1 is chaos, the lap report sums it up
        if ev["overtaking_idx"] == me and (car := self._car(ev["overtaken_idx"])):
            self.say("ovtk", f"Pěkný manévr, {car.name} je za tebou.", NORMAL, "gap", 6)
        elif ev["overtaken_idx"] == me and (car := self._car(ev["overtaking_idx"])):
            self.say("ovtk", f"{car.name} je před tebou. Drž se ho a vrať mu to.", NORMAL, "gap", 6)

    def _game_DTSV(self, ev: dict) -> None:
        if ev["vehicle_idx"] == self.state.player_idx:
            self.say("served", "Penalizace je odpykaná, jedeme dál.", NORMAL, "penalty", 10)

    _game_SGSV = _game_DTSV

    def _game_PENA(self, ev: dict) -> None:
        me, kind = self.state.player, ev["penalty_type"]
        car = self._car(ev["vehicle_idx"])
        if car is None:
            return
        if car is not me:
            near = car.position and abs(car.position - me.position) <= 2
            if kind == 4 and near and self.state.is_race:
                self.say(f"pen{car.idx}", f"{car.name} dostal penalizaci {ev['time']} sekund.", NORMAL, "penalty", 30)
            return
        if kind in C.LAP_INVALIDATED_PENALTIES:
            if not self.state.is_race:
                self.say("invalid", "Kolo zneplatněno, limity trati.", NORMAL, "penalty", 5)
        elif kind == 4:
            self.say("pen", f"Dostal jsi časovou penalizaci {ev['time']} sekund.", CRITICAL, "penalty", 5)
        elif kind == 0:
            self.say("pen", "Trest průjezdu boxy. Musíme ho odjet do tří kol.", CRITICAL, "penalty", 5)
        elif kind == 1:
            self.say("pen", "Trest stop and go. Musíme ho odjet do tří kol.", CRITICAL, "penalty", 5)
        elif kind == 6:
            self.say("pen", "Jsme diskvalifikováni.", CRITICAL, "penalty", 5)

    # ------------------------------------------------------------------ driver questions

    def _ev_button(self, data: dict) -> None:
        query = BUTTON_QUERIES.get(data["action"])
        if query:
            self.answer(query)

    def answer(self, query: str) -> None:
        """Driver asked something (iPad button or a bound wheel button). Always answered immediately."""
        s = self.state
        if query == "repeat":
            if self.history:
                self.emit(self.history[-1])
            return
        if not (s.connected and s.session and s.player.lap and s.player.status):
            text = "Slyším tě, ale nemám data z vozu. Zkontroluj UDP telemetrii ve hře."
        else:
            handler = getattr(self, f"_q_{query}", None)
            text = handler() if handler else "Tomu nerozumím, zopakuj to."
        self.say(f"q_{query}", text, CRITICAL, "reply", 1.0)

    def _q_radio_check(self) -> str:
        return "Rádio check, slyším tě hlasitě a zřetelně."

    def _q_gaps(self) -> str:
        s, me = self.state, self.state.player
        gaps = s.gaps()
        parts = [f"Jsi P{me.position}."]
        if gaps["ahead"]:
            parts.append(f"Před tebou {gaps['ahead']['name']}, {fmt_gap(gaps['ahead']['ms'])}.")
        elif me.position == 1:
            parts.append("Před tebou nikdo, vedeš.")
        if gaps["behind"]:
            parts.append(f"Za tebou {gaps['behind']['name']}, {fmt_gap(gaps['behind']['ms'])}.")
        if not s.is_race:
            parts.append("Jsou to rozdíly nejlepších kol.")
        return " ".join(parts)

    def _q_tyres(self) -> str:
        s, me = self.state, self.state.player
        wear = me.damage.get("tyre_wear", [0, 0, 0, 0])
        inner = me.tel.get("tyre_inner_temp", [0, 0, 0, 0])
        text = (f"Pneumatiky {me.compound[1]}, stáří {plural(me.status['tyre_age'], 'kolo', 'kola', 'kol')}. "
                f"Opotřebení vpředu {wear[2]:.0f} a {wear[3]:.0f} %, vzadu {wear[0]:.0f} a {wear[1]:.0f} %. "
                f"Teploty vpředu {(inner[2] + inner[3]) // 2}, vzadu {(inner[0] + inner[1]) // 2} °C.")
        rate = s.wear_rate()
        if rate and s.is_race:
            laps = max(0, int((strategy.WEAR_CLIFF_PCT - max(wear)) / rate))
            text += f" Tímhle tempem vydrží ještě asi {plural(laps, 'kolo', 'kola', 'kol')}."
        return text

    def _q_fuel(self) -> str:
        status = self.state.player.status
        text = f"V nádrži {fmt_num(status['fuel_in_tank'])} kila, to je {self._fuel_text()} oproti cíli."
        if status["fuel_remaining_laps"] < -0.25:
            text += " Musíme šetřit, lift and coast."
        return text

    def _q_strategy(self) -> str:
        s = self.state
        if not s.is_race:
            return "Strategii zastávek řešíme až v závodě. Teď se soustřeď na čisté kolo."
        plan = strategy.compute(s)
        if plan.get("pitNeeded"):
            text = f"Plán je zastávka v {plan['pitLap']}. kole."
            if plan.get("nextCompound"):
                text += f" Nasadíme {plan['nextCompound']['name']}."
            if plan.get("rejoinPos"):
                text += f" Kdybys zastavil teď, vyjedeš kolem P{plan['rejoinPos']}."
            return text
        text = "Další zastávku neplánujeme, jedeme do cíle."
        if plan.get("wearAtFinish") is not None:
            text += f" Pneumatiky budou v cíli na {plan['wearAtFinish']:.0f} %."
        return text

    def _q_weather(self) -> str:
        session = self.state.session
        now = session["weather"]
        text = (f"Teď je {C.WEATHER.get(now, '?').lower()}, trať {session['track_temp']} °C, "
                f"vzduch {session['air_temp']} °C.")
        for f in session.get("forecast", []):
            if f["session_type"] == session["session_type"] and f["time_offset"] > 0 and (f["weather"] >= 3) != (now >= 3):
                return text + (f" Za {plural(f['time_offset'], 'minutu', 'minuty', 'minut')} čekáme "
                               f"{C.WEATHER.get(f['weather'], '?').lower()}, pravděpodobnost deště {f['rain']} %.")
        return text + " Předpověď je beze změny."

    def _q_damage(self) -> str:
        damage = self.state.player.damage
        # gearbox / engine values are wear, so they only count once they get serious
        hurt = [f"{label} {damage[part]} %" for part, label in _DAMAGE_NAMES.items()
                if damage.get(part, 0) >= (30 if part in ("gearbox", "engine") else 5)]
        faults = [label for key, label in (("drs_fault", "porucha DRS"), ("ers_fault", "porucha ERS")) if damage.get(key)]
        if not hurt and not faults:
            return "Vůz je v pořádku, žádné poškození."
        return "Poškození: " + ", ".join(hurt + faults) + "."

    def _q_pace(self) -> str:
        s, me = self.state, self.state.player
        parts = [f"Tvoje poslední kolo {fmt_lap(me.lap['last_lap_ms'])}, nejlepší {fmt_lap(me.best_ms)}."]
        for car, label in ((s.car_at(me.position - 1), "před tebou"), (s.car_at(me.position + 1), "za tebou")):
            if car and car.lap.get("last_lap_ms"):
                parts.append(f"{car.name} {label} jel {fmt_lap(car.lap['last_lap_ms'])}.")
        leader = s.car_at(1)
        if leader and leader is not me and me.position > 2 and leader.lap.get("last_lap_ms"):
            parts.append(f"Lídr {leader.name} {fmt_lap(leader.lap['last_lap_ms'])}.")
        return " ".join(parts)

    # ------------------------------------------------------------------ weather

    def _check_weather(self) -> None:
        session = self.state.session
        now = session["weather"]
        prev = self._changed("weather", now)
        if prev is not _SAME and prev is not _UNSET:
            if now >= 3 > prev:
                self.say("rain", "Začíná pršet. Sleduj přilnavost, připrav se na přechodné pneumatiky.",
                         CRITICAL, "weather", 60)
            elif prev >= 3 > now:
                self.say("rain", "Přestává pršet, trať bude osychat.", NORMAL, "weather", 60)
            elif now == 4 and prev == 3:
                self.say("rain", "Déšť sílí. Na přechodných to brzy nepůjde, mokré budou lepší.",
                         NORMAL, "weather", 60)
        for f in session.get("forecast", []):
            if f["session_type"] != session["session_type"] or not 0 < f["time_offset"] <= 15:
                continue
            if now < 3 <= f["weather"]:
                self.say("forecast", f"Podle radaru déšť za {plural(f['time_offset'], 'minutu', 'minuty', 'minut')}, "
                                     f"pravděpodobnost {f['rain']} %.", NORMAL, "weather", 300)
                return
            if f["weather"] < 3 <= now:
                self.say("forecast", f"Za {plural(f['time_offset'], 'minutu', 'minuty', 'minut')} má přestat pršet.",
                         NORMAL, "weather", 300)
                return


_UNSET = object()
_SAME = object()
