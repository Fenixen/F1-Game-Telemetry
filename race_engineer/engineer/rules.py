"""The race engineer's brain: turns state changes into radio messages (in Czech).

Style guide for the texts: short, spoken Czech the way a real engineer talks
on the radio. Most topics have several phrasings so it does not sound like a
recording. Critical calls (flags, safety car, box) stay short and repeated
("Box, box"), because that is what cuts through at 300 km/h.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from .. import analysis, strategy
from ..state import TRACE_BUCKET_M, Car, RaceState
from ..udp import constants as C
from .engine import CHATTY, CRITICAL, NORMAL, Radio, fmt_gap, fmt_lap, fmt_num, plural

log = logging.getLogger("race_engineer")

TYRE_HOT_C, TYRE_COLD_C, BRAKE_HOT_C = 105, 65, 1050
SUSTAIN_TICKS = 25                    # condition must hold this many ticks (~5 s) before we report it
WEAR_STEPS = (40, 60, 75)
DRS_RANGE_MS = 1000
RACE_REPORT_DELAY_S = 1.0             # let positions and gaps settle after the line
TIMED_REPORT_DELAY_S = 2.0            # the game re-sorts qualifying positions a moment after the lap

# In-game "UDP Action n" buttons -> driver questions
BUTTON_QUERIES = {1: "gaps", 2: "tyres", 3: "fuel", 4: "strategy", 5: "weather", 6: "damage", 7: "pace", 8: "repeat"}

_DAMAGE_NAMES = {
    "fl_wing": "levá strana předního křídla", "fr_wing": "pravá strana předního křídla",
    "rear_wing": "zadní křídlo", "floor": "podlaha", "diffuser": "difuzor", "sidepod": "bočnice",
    "gearbox": "převodovka", "engine": "motor",
}
# "na měkkých" – compound names in the locative case, for "you are on ..." sentences
_ON_TYRES = {"měkké": "měkkých", "super měkké": "super měkkých", "střední": "středních", "tvrdé": "tvrdých",
             "přechodné": "přechodných", "mokré": "mokrých"}
_UNSET, _SAME = object(), object()


def on_tyres(car: Car) -> str:
    return "na " + _ON_TYRES.get(car.compound[1], car.compound[1])


class Engineer(Radio):
    def __init__(self, state: RaceState, emit: Callable[[dict], None],
                 clock: Callable[[], float] = time.monotonic):
        super().__init__(state, emit, clock)
        self.mem: dict = {}

    # ------------------------------------------------------------------ entry points

    def on_events(self, events: list[tuple[str, dict]]) -> None:
        for name, data in events:
            handler = getattr(self, f"_ev_{name}", None)
            if handler:
                try:
                    handler(data)
                except Exception:
                    log.exception("Chyba při zpracování události %s", name)

    def tick(self) -> None:
        """Periodic checks (~5 Hz) for conditions that are states rather than events."""
        s = self.state
        self.run_due()
        if s.connected and s.session and s.player.lap and s.player.status and s.player.part:
            checks = [self._greet, self._check_weather, self._check_race_control, self._check_flags,
                      self._check_warnings]
            if s.player.tel and s.player.damage:
                checks += [self._check_tyres, self._check_damage]
            if s.is_race:
                checks += [self._check_gaps, self._check_ers, self._check_pit_call]
            else:
                checks += [self._check_session_clock, self._check_traffic, self._check_balance]
            for check in checks:
                try:
                    check()
                except Exception:  # a bug in one check must not mute all the others
                    log.exception("Chyba v kontrole %s", check.__name__)
        self.flush()

    # ------------------------------------------------------------------ helpers

    def _changed(self, key: str, value):
        """Previous value if `value` differs from the remembered one (_UNSET the first time), else _SAME."""
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

    def _car(self, idx: int) -> Car | None:
        return self.state.cars[idx] if 0 <= idx < C.MAX_CARS else None

    def _session_name(self) -> str:
        st = self.state.session_type
        if st in C.RACE_SESSIONS:
            return "závodu"
        if st in C.QUALI_SESSIONS:
            return "kvalifikace" if st < 10 else "sprint shootoutu"
        return "tréninku"

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
            laps = plural(session["total_laps"], "kolo", "kola", "kol")
            text = [
                f"Rádio check. {track}, {laps}, startuješ z P{me.lap['grid_position']} {on_tyres(me)}. "
                f"Je {weather}, trať má {session['track_temp']} stupňů. Hodně štěstí, jedeme.",
                f"Slyšíš mě? {track}, závod na {laps}. Jdeš z P{me.lap['grid_position']}, "
                f"pneumatiky {me.compound[1]}. Čistý start a pak si to rozjedeme.",
            ]
        else:
            name = C.SESSION_TYPES.get(s.session_type, "Session")
            text = [
                f"Rádio check. {name}, {track}. Je {weather}, trať {session['track_temp']} stupňů. Jsem tu, kdybys něco potřeboval.",
                f"Slyšíme se? {name} v {track}. Trať {session['track_temp']} stupňů, {weather}. Jdeme na to.",
            ]
        self.say("greet", text, NORMAL, "info", now=True)

    # ------------------------------------------------------------------ laps

    def _ev_lap_complete(self, data: dict) -> None:
        if data["idx"] != self.state.player_idx:
            return
        if self.state.is_race:
            self.later(RACE_REPORT_DELAY_S, lambda: self._race_lap_report(data))
        else:
            before = self.state.player.position
            self.later(TIMED_REPORT_DELAY_S, lambda: self._timed_lap_report(data, before))

    def _race_lap_report(self, data: dict) -> None:
        s, me, lap = self.state, self.state.player, data["lap"]
        total = s.session.get("total_laps", 0)
        left = total - lap
        if left <= 0 or me.lap.get("result_status", 2) != 2:
            return  # the finish is handled by the chequered flag
        log_ = s.lap_log
        time_txt = fmt_lap(data["ms"])
        parts = []
        if lap == 1:
            gained = me.lap["grid_position"] - me.position
            if gained > 0:
                parts.append(self.pick("start", [
                    f"Výborný start! Plus {plural(gained, 'místo', 'místa', 'míst')}, jsi P{me.position}.",
                    f"Super první kolo, získal jsi {plural(gained, 'pozici', 'pozice', 'pozic')}. P{me.position}.",
                ]))
            elif gained < 0:
                parts.append(self.pick("start", [
                    f"Jsi P{me.position}, start nás stál {plural(-gained, 'místo', 'místa', 'míst')}. Nevadí, závod je dlouhý.",
                    f"P{me.position}. Pár míst jsme ztratili, ale máme tempo to vrátit.",
                ]))
            else:
                parts.append(f"Držíš P{me.position}, čistý start.")
        elif data["valid"] and me.best_ms == data["ms"]:
            parts.append(self.pick("lap_pb", [f"{time_txt}, tvoje nejlepší kolo. Pěkně!",
                                              f"Osobák, {time_txt}. Tohle je tempo.",
                                              f"{time_txt}, nejrychlejší kolo, co jsi dneska zajel."]))
        else:
            parts.append(time_txt + ".")
            if len(log_) >= 2:
                diff = data["ms"] - log_[-2]["ms"]
                if diff >= 400:
                    parts.append(f"O {fmt_gap(diff)} pomalejší než minule.")
                elif diff <= -400:
                    parts.append(f"O {fmt_gap(diff)} rychlejší, dobrý.")

        gaps = s.gaps()
        close = False
        ahead, behind = gaps["ahead"], gaps["behind"]
        if ahead:
            close = close or ahead["ms"] < 2000
            parts.append(self._gap_phrase("ahead", ahead))
        elif me.position == 1:
            parts.append(self.pick("leading", ["Vedeš, drž tempo.", "Jsi první, všechno pod kontrolou.",
                                               "Pořád vedeš, jen tak dál."]))
        if behind:
            close = close or behind["ms"] < 2000
            parts.append(self._gap_phrase("behind", behind))
        if lap != 1:
            prio = NORMAL if close or lap % 3 == 0 else CHATTY
        else:
            prio = NORMAL
        self.say("lap", " ".join(parts), prio, "lap", 5)

        if left == 1:
            self.say("laps_left", ["Poslední kolo! Dovez to domů.", "Tohle je poslední kolo, soustřeď se.",
                                   "Poslední kolo, ještě jedno čisté!"], CRITICAL, "info", 5)
        elif left in (3, 5, 10) and total > left + 2:
            n = plural(left, "kolo", "kola", "kol")
            self.say("laps_left", [f"Ještě {n}.", f"{n.capitalize()} do konce, drž to.", f"Zbývá {n}."],
                     NORMAL, "info", 5)
        elif total >= 8 and lap == total // 2:
            self.say("laps_left", "Máme za sebou polovinu závodu.", CHATTY, "info", 5)

        if left > 1:
            self._fuel_report(lap)
            if lap % 5 == 0:
                wear = max(me.damage.get("tyre_wear", [0]))
                self.say("status", [f"Krátce ke stavu: gumy {wear:.0f} procent, palivo {self._fuel_text()}.",
                                    f"Gumy máš na {wear:.0f} procentech, palivo {self._fuel_text()}. Všechno v pohodě."],
                         NORMAL, "car", 60)

    def _gap_phrase(self, side: str, gap: dict) -> str:
        """'Hamilton 1,2 před tebou, stahuješ ho' with a trend vs. the previous lap."""
        name, value = gap["name"], fmt_gap(gap["ms"], False)
        prev = self.mem.get(f"gap_{side}")
        self.mem[f"gap_{side}"] = (gap["idx"], gap["ms"])
        trend = ""
        if prev and prev[0] == gap["idx"] and abs(gap["ms"] - prev[1]) >= 200:
            closing = gap["ms"] < prev[1]
            if side == "ahead":
                trend = self.pick("trend_a", ["stahuješ ho", "bereš mu to", "jde ti to"]) if closing else \
                    self.pick("trend_a2", ["trochu ti ujíždí", "odjíždí ti"])
            else:
                trend = self.pick("trend_b", ["tlačí na tebe", "dotahuje tě"]) if closing else \
                    self.pick("trend_b2", ["máš ho pod kontrolou", "odjíždíš mu"])
        if side == "ahead":
            text = self.pick("gap_a", [f"{name} před tebou {value}", f"Před tebou {name}, {value}",
                                       f"{name} je {value} před tebou"])
        else:
            text = self.pick("gap_b", [f"{name} za tebou {value}", f"Za tebou {name}, {value}",
                                       f"{name} je {value} za tebou"])
        return f"{text}, {trend}." if trend else f"{text}."

    def _timed_lap_report(self, data: dict, pos_before: int) -> None:
        s, me = self.state, self.state.player
        if not data["valid"]:
            self.say("lap", [f"{fmt_lap(data['ms'])}, ale kolo se nepočítá. Limity trati.",
                             "Tohle kolo nám škrtli, limity trati. Zkusíme to znovu."], NORMAL, "lap", 5)
            return
        if me.lap.get("driver_status") == 3 and data["ms"] != me.best_ms:
            return  # out-lap, nothing to say
        pos, time_txt = me.position, fmt_lap(data["ms"])
        is_pb = me.best_ms == data["ms"]
        parts = []
        if is_pb and pos_before and pos < pos_before:
            parts.append(self.pick("q_up", [f"Výborně! {time_txt}, posouváš se z P{pos_before} na P{pos}.",
                                            f"{time_txt}, super kolo! Z P{pos_before} na P{pos}."]))
        elif is_pb:
            parts.append(self.pick("q_pb", [f"{time_txt}, tvoje nejlepší. Jsi P{pos}.",
                                            f"Zlepšení, {time_txt}. Držíš P{pos}."]))
        else:
            parts.append(self.pick("q_lap", [f"{time_txt}, to nebylo zlepšení. Pořád P{pos}.",
                                             f"{time_txt}. Nejlepší máš pořád {fmt_lap(me.best_ms)}, jsi P{pos}."]))
        leader = s.car_at(1)
        if pos == 1:
            parts.append(self.pick("q_p1", ["Jsi nejrychlejší!", "Nikdo nejel rychleji, paráda.",
                                            "Máš nejlepší čas ze všech."]))
        elif leader and leader.best_ms and me.best_ms:
            parts.append(f"Na první místo chybí {fmt_gap(me.best_ms - leader.best_ms, False)}.")
        cutoff = {5: len(s.active_cars()) - 5, 6: 10, 10: len(s.active_cars()) - 5, 11: 10}.get(s.session_type)
        if cutoff and cutoff > 0 and me.best_ms:
            edge = s.car_at(cutoff + 1) if pos <= cutoff else s.car_at(cutoff)
            if edge and edge.best_ms:
                margin = abs(edge.best_ms - me.best_ms)
                if pos <= cutoff:
                    parts.append(f"Postupuješ, rezerva {fmt_gap(margin, False)}." if margin > 150 else
                                 f"Postupuješ, ale jen o {fmt_gap(margin, False)}. Je to těsné.")
                else:
                    parts.append(f"Zatím jsi pod čarou, na P{cutoff} chybí {fmt_gap(margin, False)}.")
        coaching = self._corner_coaching(data) if not is_pb else None
        if coaching:
            parts.append(coaching)
        self.say("lap", " ".join(parts), NORMAL, "lap", 5)

    def _corner_coaching(self, data: dict) -> str | None:
        """'Most time is lost in Z3: braking 12 m earlier' – after laps that were not a PB."""
        s = self.state
        ref, last = s.reference_trace(), s.traces.get("last")
        if not ref or not last or last.get("ms") != data["ms"] or ref is last:
            return None
        corners = analysis.detect_corners(ref, TRACE_BUCKET_M)
        worst = max(analysis.compare_corners(ref, last, corners, TRACE_BUCKET_M),
                    key=lambda c: c["loss_ms"], default=None)
        if not worst or worst["loss_ms"] < 120:
            return None
        tip = worst["tip"].rstrip(".")
        return f"Nejvíc ztrácíš v {worst['name']}, {fmt_gap(worst['loss_ms'], False)}: {tip[0].lower() + tip[1:]}."

    # ------------------------------------------------------------------ finish / session end

    def _game_CHQF(self, _: dict) -> None:
        """The player took the chequered flag. Positions settle a moment later."""
        if self.state.is_race:
            self.later(1.0, self._race_finish)

    def _race_finish(self) -> None:
        if not self._once("finish"):
            return
        me = self.state.player
        pos, grid = me.position, me.lap.get("grid_position", 0)
        moved = grid - pos if grid else 0
        if pos == 1:
            text = ["Ano! Vyhráli jsme! Neuvěřitelná jízda, gratuluju!",
                    "JSI VÍTĚZ! Fantastický závod, celý tým šílí!",
                    "Vítězství! To byla jízda, díky moc, kámo!"]
        elif pos <= 3:
            text = [f"Pódium! P{pos}, skvělá práce, opravdu dobrá jízda!",
                    f"P{pos} a jsme na pódiu! Gratuluju, tohle se povedlo."]
        elif pos <= 10:
            text = [f"Cíl, P{pos}. Body jsou doma, dobrá práce.",
                    f"P{pos}, bereme body. Solidní závod, díky."]
        else:
            text = [f"Cíl, P{pos}. Dnes to na body nebylo, ale odjel jsi to celé. Díky.",
                    f"P{pos}. Těžký den, ale něco jsme se naučili. Díky za závod."]
        text = self.pick("finish", text)
        if moved >= 3:
            text += f" Ze startu z P{grid} jsi získal {plural(moved, 'místo', 'místa', 'míst')}!"
        self.say("finish", text, CRITICAL, "info", 60)

    def _game_RCWN(self, ev: dict) -> None:
        winner = self._car(ev["vehicle_idx"])
        me = self.state.player
        if winner and winner is not me and me.lap.get("result_status") == 2:
            self.say("winner", f"{winner.name} vyhrál závod. Dojeď to v klidu, každé místo se počítá.",
                     NORMAL, "info", 60)

    def _game_SEND(self, _: dict) -> None:
        s, me = self.state, self.state.player
        if s.is_race or not me.best_ms or not self._once("session_end"):
            return
        self.say("session_end", [
            f"Konec {self._session_name()}. Končíš na P{me.position}, nejlepší kolo {fmt_lap(me.best_ms)}. Dobrá práce.",
            f"A je konec. P{me.position}, nejlepší čas {fmt_lap(me.best_ms)}. Díky, jdeme se na to podívat do dat.",
        ], CRITICAL, "info", 60)

    def _ev_final(self, body: dict) -> None:
        """Results screen. Only speaks if the chequered-flag call did not happen (e.g. after a retirement)."""
        if not self.state.is_race or "finish" in self.mem:
            return
        result = body["cars"][self.state.player_idx]
        if result["result_status"] == 3:
            self._race_finish()
        elif self._once("finish"):
            self.say("finish", ["Dnes to bohužel nevyšlo. Hlavu vzhůru, příště to vrátíme.",
                                "Smůla, dneska to nebylo ono. Zvedneme se a jedeme dál."], CRITICAL, "info", 60)

    # ------------------------------------------------------------------ fuel / ERS

    def _fuel_text(self) -> str:
        delta = self.state.player.status.get("fuel_remaining_laps", 0.0)
        return f"{'plus' if delta >= 0 else 'mínus'} {fmt_num(abs(delta))} kola"

    def _fuel_report(self, lap: int) -> None:
        delta = self.state.player.status.get("fuel_remaining_laps", 0.0)
        if lap < 2:
            return
        if delta < -0.25:
            self.say("fuel_low", [f"Palivo nám nevychází, jsme {fmt_num(-delta)} kola pod cílem. Lift and coast prosím.",
                                  f"Musíme šetřit palivo, chybí {fmt_num(-delta)} kola. Uber před brzděním."],
                     NORMAL, "car", 150)
        elif delta > 1.5 and lap >= 3:
            self.say("fuel_high", f"Paliva máme {fmt_num(delta)} kola navíc, klidně jeď bohatší směs.",
                     CHATTY, "car", 400)

    def _check_ers(self) -> None:
        pct = 100 * self.state.player.status.get("ers_store", 0.0) / C.ERS_MAX_J
        if self._racing() and pct < 8:
            self.say("ers_low", ["Baterie je skoro prázdná, dobíjej.", "Jsi na dně baterie, chvíli šetři."],
                     NORMAL, "car", 120)

    # ------------------------------------------------------------------ tyres & brakes

    def _check_tyres(self) -> None:
        me = self.state.player
        tel, wear = me.tel, max(me.damage["tyre_wear"])
        inner = tel["tyre_inner_temp"]
        front, rear = (inner[2] + inner[3]) / 2, (inner[0] + inner[1]) / 2
        racing = self._racing()
        if self._sustained("hot_front", racing and front > TYRE_HOT_C):
            self.say("tyre_temp", ["Přední gumy se přehřívají, uber v zatáčkách.",
                                   f"Předek má {front:.0f} stupňů, to je moc. Šetři přední pneumatiky."],
                     NORMAL, "car", 90)
        if self._sustained("hot_rear", racing and rear > TYRE_HOT_C):
            self.say("tyre_temp", ["Zadní gumy se přehřívají, jemněji na plyn.",
                                   f"Zadek má {rear:.0f} stupňů. Opatrně na výjezdech."], NORMAL, "car", 90)
        dry = me.status.get("visual_compound") not in C.WET_COMPOUNDS
        cold = racing and dry and tel["speed"] > 120 and max(front, rear) < TYRE_COLD_C
        if self._sustained("cold", cold) and me.lap["lap_num"] > 1:
            self.say("tyre_temp", ["Gumy jsou studené, potřebujeme do nich dostat teplotu.",
                                   "Pneumatiky nejsou v okně, zahřej je."], NORMAL, "car", 120)
        if self._sustained("hot_brakes", racing and max(tel["brake_temp"]) > BRAKE_HOT_C):
            self.say("brakes", ["Brzdy jsou horké, brzdi o kousek dřív a jemněji.",
                                "Teploty brzd jsou vysoko, dej jim chvilku vydechnout."], NORMAL, "car", 120)

        # Wear thresholds: report each one once per tyre set
        last_step = self.mem.get("wear_step", 0)
        if wear < last_step - 20:
            last_step = 0  # fresh tyres fitted
        step = max((x for x in WEAR_STEPS if wear >= x), default=0)
        if step > last_step:
            if step == 40:
                self.say("wear", "Gumy jsou na čtyřiceti procentech, zatím v pohodě.", CHATTY, "car", 10)
            elif step == 60:
                self.say("wear", ["Gumy jsou na šedesáti procentech, začnou ztrácet. Blíží se zastávka.",
                                  "Šedesát procent opotřebení. Brzy začnou odcházet."], NORMAL, "car", 10)
            else:
                self.say("wear", "Gumy jsou za hranou, přes 75 procent. Hrozí defekt, musíme do boxů.",
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
                    self.say(f"dmg_{part}", f"Pozor, opotřebení – {label} {value} procent. Šetři auto, řaď dřív.",
                             NORMAL, "damage", 120)
                continue
            if value >= 50:
                advice = "Je to vážné, potřebujeme do boxů." if "wing" in part else "Ztrácíme hodně přítlaku."
            elif value >= 25:
                advice = "Stojí nás to přítlak, při zastávce to vyměníme."
            else:
                advice = "Dá se s tím jet dál."
            self.say(f"dmg_{part}", f"Hlásím poškození – {label}, zhruba {value} procent. {advice}",
                     CRITICAL if value >= 50 else NORMAL, "damage", 8)
        for fault, label in (("drs_fault", "DRS"), ("ers_fault", "ERS")):
            if self._changed(fault, damage[fault]) is not _SAME and damage[fault]:
                self.say(fault, f"Máme poruchu {label}. Budeš se bez něj muset obejít.", CRITICAL, "damage", 60)
        if self._changed("blown", damage["engine_blown"]) is not _SAME and damage["engine_blown"]:
            self.say("blown", "Motor je pryč. Zastav na bezpečném místě. Je mi to líto.", CRITICAL, "damage", 60)

    # ------------------------------------------------------------------ gaps & traffic

    def _check_gaps(self) -> None:
        if not self._racing() or self.state.player.lap["lap_num"] < 2:
            return
        gaps = self.state.gaps()
        ahead, behind = gaps["ahead"], gaps["behind"]
        boost = "overtake" if self.state.aero.get("regs_2026") else "DRS"
        if ahead and 0 < ahead["ms"] < DRS_RANGE_MS:
            self.say("drs_ahead", [f"{ahead['name']} je do vteřiny před tebou, máš {boost}. Jdi do toho!",
                                   f"{ahead['name']} je na dosah, {fmt_gap(ahead['ms'], False)}. Zaútoč."],
                     NORMAL, "gap", 60)
        if behind and 0 < behind["ms"] < DRS_RANGE_MS:
            self.say("drs_behind", [f"{behind['name']} je do vteřiny za tebou. Braň se na rovince.",
                                    f"Pozor, {behind['name']} má na tebe {boost}. Drž vnitřek."],
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
                self.say(f"traffic{car.idx}", [f"{car.name} za tebou na rychlém kole, uhni mu.",
                                               f"Rychlé auto za tebou, {car.name}. Pusť ho."], NORMAL, "gap", 25)
                return

    def _check_session_clock(self) -> None:
        left = self.state.session.get("time_left", 0)
        prev = self.mem.get("time_left", left)
        self.mem["time_left"] = left
        if prev > 300 >= left:
            self.say("clock300", "Zbývá pět minut.", NORMAL, "info", 30)
        elif prev > 120 >= left:
            self.say("clock120", ["Poslední dvě minuty, je čas na poslední pokus.",
                                  "Dvě minuty do konce. Jestli chceš ještě jedno kolo, musíš teď."],
                     NORMAL, "info", 30)
        elif prev > 0 == left and self.state.player.lap.get("driver_status") == 1:
            self.say("clock0", "Čas vypršel, ale tohle kolo ještě dokonči. Je to poslední šance!", CRITICAL, "info", 30)

    def _check_balance(self) -> None:
        """Practice: once the slip data is clear, point the driver to the setup screen."""
        diagnosis = self.state.balance.diagnose()
        if len(self.state.balance.laps) < 3 or not diagnosis:
            return
        top = diagnosis[0]
        if self._once(("balance", top["issue"])):
            from .setup_advisor import ISSUES
            label = ISSUES.get(top["issue"], ("",))[0].lower()
            self.say("balance", f"Z dat mi vychází {label}. Návrh na úpravu setupu máš na iPadu v záložce Setup.",
                     NORMAL, "car", 600)

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
            self.say("box", [f"Box příští kolo, příští kolo box.{tyre}",
                             f"Připrav se, příští kolo jdeme do boxů.{tyre}"], NORMAL, "strategy", 20)
        elif lap == plan["pitLap"] and 0.55 < frac < 0.97 and self._once(("box_now", lap)):
            self.say("box", f"Box, box. Tohle kolo box.{tyre}", CRITICAL, "strategy", 20)

    def _ev_pit_enter(self, data: dict) -> None:
        s, me = self.state, self.state.player
        car = s.cars[data["idx"]]
        if car is me:
            if s.is_race:
                self.say("pit_in", f"Limiter! Rychlost v boxech {s.session.get('pit_speed_limit', 80)}.",
                         CRITICAL, "strategy", 20)
        elif s.is_race and me.on_track and abs(car.position - me.position) == 1:
            if car.position < me.position:
                text = [f"{car.name} před tebou jde do boxů. Volná trať, teď zatlač!",
                        f"{car.name} zajíždí do boxů. Máš čistý vzduch, dej mi pár rychlých kol."]
            else:
                text = [f"{car.name} za tebou jede do boxů, pozor na undercut.",
                        f"{car.name} boxuje. Zkusí undercut, potřebuju od tebe rychlá kola."]
            self.say(f"rival_pit{car.idx}", text, NORMAL, "strategy", 60)

    def _ev_pit_exit(self, data: dict) -> None:
        s, me = self.state, self.state.player
        if data["idx"] != me.idx or not s.is_race:
            return
        stop = data["stop_ms"]
        if stop and stop < 2800:
            verdict = self.pick("stop_good", [f"Super zastávka, {fmt_gap(stop)}!", f"Rychlá zastávka, {fmt_gap(stop)}."])
        elif stop:
            verdict = f"Zastávka trvala {fmt_gap(stop)}."
        else:
            verdict = ""
        self.say("pit_out", f"{verdict} Jsi venku na P{me.position}. Gumy jsou studené, první zatáčky opatrně.".strip(),
                 CRITICAL, "strategy", 20)

    # ------------------------------------------------------------------ race control

    def _sc_deployed(self, kind: int) -> None:
        if kind == 1:
            text = "Safety car, safety car. Zpomal a drž deltu."
            plan = strategy.compute(self.state)
            if self.state.is_race and (plan.get("pitNeeded") or plan.get("wearNow", 0) > 35):
                text += " Zastávka je teď levná, zvažujeme box."
            self.say("sc_deploy", text, CRITICAL, "flag", 20)
        elif kind == 2:
            self.say("vsc_deploy", ["Virtuální safety car, VSC. Drž se nad deltou.",
                                   "VSC, VSC. Zpomal a hlídej deltu na displeji."], CRITICAL, "flag", 20)

    def _green(self) -> None:
        self.say("green", ["Zelená, zelená! Jedeme!", "Zelená vlajka, závodíme!", "Restart, zelená! Dej tomu všechno!"],
                 CRITICAL, "flag", 15)

    def _check_race_control(self) -> None:
        """Safety car status from the session packet; backs up the SCAR events if one is missed."""
        session = self.state.session
        status = session.get("safety_car", 0)
        prev = self._changed("sc", status)
        if prev is not _SAME and prev is not _UNSET:
            if status in (1, 2):
                self._sc_deployed(status)
            elif status == 0 and prev in (1, 2):
                self._green()
        red = session.get("num_red_flag_periods", 0)
        prev_red = self._changed("red_periods", red)
        if prev_red is not _SAME and prev_red is not _UNSET and red > prev_red:
            self._red_flag()

    def _game_SCAR(self, ev: dict) -> None:
        kind, phase = ev["safety_car_type"], ev["event_type"]
        if kind == 3:
            if phase == 0:
                self.say("formation", "Formovací kolo. Zahřej gumy a brzdy, ale opatrně.", NORMAL, "flag", 60)
            return
        if phase == 0:
            self._sc_deployed(kind)
        elif phase == 1:
            if kind == 1:
                self.say("sc_in", ["Safety car na konci kola zajíždí. Připrav se na restart, zahřej gumy.",
                                   "Safety car jde dovnitř, tohle kolo. Restart se blíží, buď ve střehu."],
                         CRITICAL, "flag", 30)
            else:
                self.say("sc_in", "VSC končí, připrav se. Zelená bude každou chvíli.", CRITICAL, "flag", 30)
        elif phase in (2, 3):
            self._green()

    def _game_RDFL(self, _: dict) -> None:
        self._red_flag()

    def _red_flag(self) -> None:
        self.say("red", "Červená vlajka, červená vlajka! Závod je přerušen. Zpomal a pomalu do boxů.",
                 CRITICAL, "flag", 60)

    def _check_flags(self) -> None:
        flag = self.state.player.status.get("fia_flag", 0)
        prev = self._changed("flag", flag)
        if prev is _SAME or prev is _UNSET:
            return
        if flag == 3:
            self.say("flag", ["Žlutá vlajka! Zpomal, nepředjíždět.", "Žlutá před tebou, opatrně, nepředjíždíme."],
                     CRITICAL, "flag", 8)
        elif flag == 2:
            self.say("flag_blue", ["Modrá vlajka, pusť ho.", "Modrá, za tebou je rychlejší auto. Uhni."],
                     CRITICAL, "flag", 15)
        elif prev == 3 and self.state.session.get("safety_car", 0) == 0:
            self.say("flag_clear", "Žlutá je pryč, trať je čistá.", NORMAL, "flag", 8)

    def _check_warnings(self) -> None:
        count = self.state.player.lap.get("cc_warnings", 0)
        prev = self._changed("cc_warnings", count)
        if prev is _SAME or prev is _UNSET or count <= prev:
            return
        if count >= 3:
            text = f"Další varování za limity trati, už jich máš {count}. Ještě jedno a je z toho penalizace."
        else:
            text = [f"Varování za limity trati, máš {plural(count, 'varování', 'varování', 'varování')}. Pozor na to.",
                    "Dostal jsi varování za limity trati. Drž se uvnitř bílých čar."]
        self.say("cc", text, NORMAL, "penalty", 5)

    def _ev_game_event(self, ev: dict) -> None:
        handler = getattr(self, f"_game_{ev['code']}", None)
        if handler:
            handler(ev)

    def _game_FTLP(self, ev: dict) -> None:
        car, ms = self._car(ev["vehicle_idx"]), round(ev["lap_time"] * 1000)
        if not car or not self.state.is_race:
            return
        if car is self.state.player:
            self.say("ftlp", [f"Nejrychlejší kolo závodu, {fmt_lap(ms)}! Paráda.",
                              f"Máš nejrychlejší kolo, {fmt_lap(ms)}."], NORMAL, "lap", 5)
        else:
            self.say("ftlp", f"{car.name} zajel nejrychlejší kolo, {fmt_lap(ms)}.", CHATTY, "info", 20)

    def _game_RTMT(self, ev: dict) -> None:
        car, me = self._car(ev["vehicle_idx"]), self.state.player
        if not car or car is me or not self.state.is_race:
            return
        ahead = 0 < car.position < me.position
        self.say(f"rtmt{car.idx}", f"{car.name} odstoupil." + (" To je pro nás místo nahoru." if ahead else ""),
                 NORMAL if ahead else CHATTY, "info", 60)

    def _game_DRSE(self, _: dict) -> None:
        if not self.state.aero.get("regs_2026"):
            self.say("drs", "DRS je povolené.", CHATTY, "info", 20)

    def _game_DRSD(self, _: dict) -> None:
        if not self.state.aero.get("regs_2026"):
            self.say("drs", "DRS je zakázané.", NORMAL, "info", 20)

    def _game_COLL(self, ev: dict) -> None:
        me = self.state.player_idx
        if me not in (ev["vehicle1_idx"], ev["vehicle2_idx"]):
            return
        other = self._car(ev["vehicle2_idx"] if ev["vehicle1_idx"] == me else ev["vehicle1_idx"])
        who = f" Byl to {other.name}." if other else ""
        self.say("coll", [f"Kontakt!{who} Jsi v pořádku? Kontroluju data.",
                          f"Dotek.{who} Dívám se na poškození."], NORMAL, "damage", 15)

    def _game_OVTK(self, ev: dict) -> None:
        me = self.state.player_idx
        if not self.state.is_race or self.state.player.lap.get("lap_num", 0) < 2:
            return  # lap 1 is chaos, the lap report sums it up
        if ev["overtaking_idx"] == me and (car := self._car(ev["overtaken_idx"])):
            self.say("ovtk", [f"Krásně! {car.name} je za tebou.", f"Výborně, {car.name} předjetý!",
                              f"Pěkný manévr, {car.name} je pryč."], NORMAL, "gap", 6)
        elif ev["overtaken_idx"] == me and (car := self._car(ev["overtaking_idx"])):
            self.say("ovtk", [f"{car.name} tě předjel. Drž se za ním, šance přijde.",
                              f"{car.name} je před tebou. Zůstaň v klidu a vrať mu to."], NORMAL, "gap", 6)

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
                self.say(f"pen{car.idx}", f"{car.name} dostal {ev['time']} vteřin penalizaci. To se nám může hodit.",
                         NORMAL, "penalty", 30)
            return
        if kind in C.LAP_INVALIDATED_PENALTIES:
            if not self.state.is_race:
                self.say("invalid", ["Kolo zrušeno, limity trati.", "Tohle kolo nám škrtli, limity."],
                         NORMAL, "penalty", 5)
        elif kind == 4:
            self.say("pen", f"Dostali jsme {ev['time']} vteřin penalizaci. Nic se neděje, jedeme dál.",
                     CRITICAL, "penalty", 5)
        elif kind == 0:
            self.say("pen", "Máme průjezd boxy. Musíme ho odjet do tří kol, řeknu ti kdy.", CRITICAL, "penalty", 5)
        elif kind == 1:
            self.say("pen", "Máme stop and go. Musíme ho odjet do tří kol.", CRITICAL, "penalty", 5)
        elif kind == 6:
            self.say("pen", "Jsme diskvalifikováni. Je mi líto.", CRITICAL, "penalty", 5)

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
        if not (s.session and s.player.lap and s.player.status):
            text = "Slyším tě, ale nemám data z auta. Zkontroluj UDP telemetrii ve hře."
        else:
            handler = getattr(self, f"_q_{query}", None)
            text = handler() if handler else "Promiň, nerozuměl jsem. Zopakuj to."
        self.say(f"q_{query}", text, CRITICAL, "reply", 1.0)

    def _q_radio_check(self) -> str:
        return "Slyším tě nahlas a jasně."

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
            parts.append("To jsou rozdíly nejlepších kol.")
        return " ".join(parts)

    def _q_tyres(self) -> str:
        s, me = self.state, self.state.player
        wear = me.damage.get("tyre_wear", [0, 0, 0, 0])
        inner = me.tel.get("tyre_inner_temp", [0, 0, 0, 0])
        text = (f"Jedeš {on_tyres(me)}, {plural(me.status['tyre_age'], 'kolo', 'kola', 'kol')} staré. "
                f"Opotřebení vpředu {wear[2]:.0f} a {wear[3]:.0f}, vzadu {wear[0]:.0f} a {wear[1]:.0f} procent. "
                f"Teploty vpředu {(inner[2] + inner[3]) // 2}, vzadu {(inner[0] + inner[1]) // 2} stupňů.")
        rate = s.wear_rate()
        if rate and s.is_race:
            laps = max(0, int((strategy.WEAR_CLIFF_PCT - max(wear)) / rate))
            text += f" Tímhle tempem vydrží ještě tak {plural(laps, 'kolo', 'kola', 'kol')}."
        return text

    def _q_fuel(self) -> str:
        status = self.state.player.status
        text = f"V nádrži {fmt_num(status['fuel_in_tank'])} kila, to je {self._fuel_text()} proti plánu."
        if status["fuel_remaining_laps"] < -0.25:
            text += " Musíme šetřit, lift and coast."
        else:
            text += " Vychází to."
        return text

    def _q_strategy(self) -> str:
        s = self.state
        if not s.is_race:
            return "Zastávky řešíme až v závodě. Teď se soustřeď na čisté kolo."
        plan = strategy.compute(s)
        if plan.get("pitNeeded"):
            text = f"Plán je zastávka v {plan['pitLap']}. kole."
            if plan.get("nextCompound"):
                text += f" Nasadíme {plan['nextCompound']['name']}."
            if plan.get("rejoinPos"):
                text += f" Kdybys šel do boxů teď, vyjedeš kolem P{plan['rejoinPos']}."
            return text
        text = "Už nezastavujeme, jedeme do cíle."
        if plan.get("wearAtFinish") is not None:
            text += f" Gumy budou v cíli zhruba na {plan['wearAtFinish']:.0f} procentech."
        return text

    def _q_weather(self) -> str:
        session = self.state.session
        now = session["weather"]
        text = (f"Teď je {C.WEATHER.get(now, '?').lower()}, trať {session['track_temp']}, "
                f"vzduch {session['air_temp']} stupňů.")
        for f in session.get("forecast", []):
            if f["session_type"] == session["session_type"] and f["time_offset"] > 0 and (f["weather"] >= 3) != (now >= 3):
                return text + (f" Za {plural(f['time_offset'], 'minutu', 'minuty', 'minut')} čekáme "
                               f"{C.WEATHER.get(f['weather'], '?').lower()}, šance na déšť {f['rain']} procent.")
        return text + " Žádná změna se nečeká."

    def _q_damage(self) -> str:
        damage = self.state.player.damage
        # gearbox / engine values are wear, so they only count once they get serious
        hurt = [f"{label} {damage[part]} procent" for part, label in _DAMAGE_NAMES.items()
                if damage.get(part, 0) >= (30 if part in ("gearbox", "engine") else 5)]
        faults = [label for key, label in (("drs_fault", "porucha DRS"), ("ers_fault", "porucha ERS")) if damage.get(key)]
        if not hurt and not faults:
            return "Auto je v pořádku, žádné poškození."
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
                self.say("rain", ["Začíná pršet. Opatrně na přilnavost, připrav se na přechodné gumy.",
                                  "Hlásí déšť, už to začíná. Pozor na brzdění."], CRITICAL, "weather", 60)
            elif prev >= 3 > now:
                self.say("rain", "Přestává pršet, trať bude osychat.", NORMAL, "weather", 60)
            elif now == 4 and prev == 3:
                self.say("rain", "Déšť sílí. Na přechodných to brzy nepůjde, mokré budou lepší.",
                         NORMAL, "weather", 60)
        for f in session.get("forecast", []):
            if f["session_type"] != session["session_type"] or not 0 < f["time_offset"] <= 15:
                continue
            mins = plural(f["time_offset"], "minutu", "minuty", "minut")
            if now < 3 <= f["weather"]:
                self.say("forecast", [f"Radar ukazuje déšť za {mins}, pravděpodobnost {f['rain']} procent.",
                                      f"Za {mins} by mohlo pršet, šance {f['rain']} procent. Budu to sledovat."],
                         NORMAL, "weather", 300)
                return
            if f["weather"] < 3 <= now:
                self.say("forecast", f"Za {mins} má přestat pršet.", NORMAL, "weather", 300)
                return
