"""In-memory model of the current session, rebuilt from the UDP packet stream.

`RaceState.apply()` folds one decoded packet into the model and returns the
domain events it caused (lap completed, pit entry, ...). The engineer reacts to
those events; the web layer only reads snapshots.
"""
from __future__ import annotations

import time
from typing import Any

from .udp import constants as C

TRACE_BUCKET_M = 8      # distance resolution of lap telemetry traces
OUTLINE_BUCKET_M = 10   # distance resolution of the recorded track outline
TRACE_CHANNELS = ("speed", "throttle", "brake", "gear", "steer", "t")

Event = tuple[str, dict]


def join_ms(minutes: int, ms: int) -> int:
    """The spec splits some times into a minutes part and a milliseconds part."""
    return minutes * 60000 + ms


class Car:
    def __init__(self, idx: int):
        self.idx = idx
        self.lap: dict = {}
        self.tel: dict = {}
        self.status: dict = {}
        self.damage: dict = {}
        self.part: dict = {}
        self.x = self.z = 0.0
        self.best_ms = 0
        self.best_sectors = [0, 0, 0]
        self.last_sectors = [0, 0, 0]
        self.stints: list[dict] = []
        self.lap_times: list[int] = []  # completed laps, index 0 = lap 1 (0 = unknown)

    @property
    def name(self) -> str:
        raw = self.part.get("name") or f"Vůz {self.idx + 1}"
        return raw.title() if raw.isupper() else raw

    @property
    def team(self) -> tuple[str, str]:
        return C.TEAMS.get(self.part.get("team_id", -1), C.DEFAULT_TEAM)

    @property
    def position(self) -> int:
        return self.lap.get("position", 0)

    @property
    def gap_leader_ms(self) -> int:
        return join_ms(self.lap.get("delta_leader_min", 0), self.lap.get("delta_leader_ms", 0))

    @property
    def gap_front_ms(self) -> int:
        return join_ms(self.lap.get("delta_front_min", 0), self.lap.get("delta_front_ms", 0))

    @property
    def on_track(self) -> bool:
        return self.lap.get("result_status", 0) == 2 and self.lap.get("pit_status", 0) == 0

    @property
    def compound(self) -> tuple[str, str, str]:
        return C.VISUAL_COMPOUNDS.get(self.status.get("visual_compound", 0), ("?", "neznámé", "#888888"))


class RaceState:
    def __init__(self):
        self.last_rx = 0.0
        self.reset(0)

    def reset(self, session_uid: int) -> None:
        self.session_uid = session_uid
        self.session_time = 0.0
        self.player_idx = 0
        self.num_active = 0
        self.session: dict = {}
        self.cars = [Car(i) for i in range(C.NUM_CARS)]
        self.setup: dict = {}
        self.tyre_sets: list[dict] = []
        self.fitted_set = 255
        self.lap_log: list[dict] = []       # one record per completed player lap
        self.fastest_lap: tuple[int, int] | None = None  # (car idx, ms)
        self.final: dict | None = None
        self.g_lat = self.g_lon = 0.0
        self.suggested_gear = 0
        self.buttons = 0
        self.traces: dict[str, dict | None] = {"cur": None, "last": None, "best": None}
        self.outline: list[tuple[float, float] | None] = []
        self.outline_filled = 0
        self.outline_version = 0
        self._outline_sent_fill = 0
        self._lap_was_invalid = False

    # ------------------------------------------------------------------ accessors

    @property
    def player(self) -> Car:
        return self.cars[self.player_idx]

    @property
    def connected(self) -> bool:
        return time.monotonic() - self.last_rx < 3.0

    @property
    def session_type(self) -> int:
        return self.session.get("session_type", 0)

    @property
    def is_race(self) -> bool:
        return self.session_type in C.RACE_SESSIONS

    @property
    def is_quali(self) -> bool:
        return self.session_type in C.QUALI_SESSIONS

    @property
    def track_length(self) -> int:
        return self.session.get("track_length", 0)

    def active_cars(self) -> list[Car]:
        """Cars that take part in the session, sorted by track position."""
        cars = [c for c in self.cars if c.position > 0 and c.lap.get("result_status", 0) >= 2 and c.part]
        return sorted(cars, key=lambda c: c.position)

    def car_at(self, position: int) -> Car | None:
        for c in self.cars:
            if c.position == position and c.lap.get("result_status", 0) >= 2:
                return c
        return None

    def laps_remaining(self) -> float:
        """Race laps still to drive, including the rest of the current lap."""
        total, lap = self.session.get("total_laps", 0), self.player.lap
        if not total or not lap or not self.track_length:
            return 0.0
        frac = max(0.0, min(1.0, lap["lap_distance"] / self.track_length))
        return max(0.0, total - lap["lap_num"] + 1 - frac)

    # ------------------------------------------------------------------ packet folding

    def apply(self, header: dict, body: dict) -> list[Event]:
        self.last_rx = time.monotonic()
        events: list[Event] = []
        if header["session_uid"] != self.session_uid:
            self.reset(header["session_uid"])
            events.append(("session_started", {}))
        self.session_time = header["session_time"]
        self.player_idx = min(header["player_idx"], C.NUM_CARS - 1)

        pid = header["packet_id"]
        if pid == C.MOTION:
            self._on_motion(body)
        elif pid == C.SESSION:
            self._on_session(body)
        elif pid == C.LAP_DATA:
            self._on_lap_data(body, events)
        elif pid == C.EVENT:
            self._on_event(body, events)
        elif pid == C.PARTICIPANTS:
            self.num_active = body["num_active"]
            for car, part in zip(self.cars, body["cars"]):
                car.part = part if car.idx < self.num_active else {}
        elif pid == C.CAR_SETUPS:
            self.setup = body["cars"][self.player_idx]
        elif pid == C.CAR_TELEMETRY:
            for car, tel in zip(self.cars, body["cars"]):
                car.tel = tel
            self.suggested_gear = body["suggested_gear"]
            self._sample_trace()
        elif pid == C.CAR_STATUS:
            for car, status in zip(self.cars, body["cars"]):
                car.status = status
        elif pid == C.CAR_DAMAGE:
            for car, damage in zip(self.cars, body["cars"]):
                car.damage = damage
        elif pid == C.SESSION_HISTORY:
            self._on_history(body)
        elif pid == C.TYRE_SETS:
            if body["car_idx"] == self.player_idx:
                self.tyre_sets, self.fitted_set = body["sets"], body["fitted_idx"]
        elif pid == C.FINAL_CLASSIFICATION:
            self.final = body
            events.append(("final", body))
        return events

    def _on_session(self, body: dict) -> None:
        if body["track_length"] != self.track_length:
            n = body["track_length"] // OUTLINE_BUCKET_M + 1
            self.outline, self.outline_filled = [None] * n, 0
        self.session = body

    def _on_motion(self, body: dict) -> None:
        n = len(self.outline)
        for car, m in zip(self.cars, body["cars"]):
            car.x, car.z = m["pos"][0], m["pos"][2]
            # Record the track outline from cars driving on track (pit lane would distort it)
            if n and car.on_track:
                dist = car.lap.get("lap_distance", -1.0)
                bucket = int(dist // OUTLINE_BUCKET_M)
                if dist >= 0 and bucket < n and self.outline[bucket] is None:
                    self.outline[bucket] = (car.x, car.z)
                    self.outline_filled += 1
        me = body["cars"][self.player_idx]
        self.g_lat, self.g_lon = me["g_lat"], me["g_lon"]
        if self.outline_filled - self._outline_sent_fill >= max(1, n // 30) or (
                self.outline_filled == n and self._outline_sent_fill != n):
            self._outline_sent_fill = self.outline_filled
            self.outline_version += 1

    def _on_lap_data(self, body: dict, events: list[Event]) -> None:
        for car, lap in zip(self.cars, body["cars"]):
            prev, car.lap = car.lap, lap
            if not prev:
                continue
            is_player = car.idx == self.player_idx
            # A lap ends when the lap counter advances, or on the final lap when the car is classified
            took_flag = (prev["result_status"] == 2 and lap["result_status"] == 3
                         and lap["lap_num"] == prev["lap_num"] and lap["last_lap_ms"] != prev["last_lap_ms"])
            if (lap["lap_num"] > prev["lap_num"] or took_flag) and lap["last_lap_ms"] > 0:
                valid = not (self._lap_was_invalid if is_player else prev["lap_invalid"])
                while len(car.lap_times) < prev["lap_num"]:
                    car.lap_times.append(0)
                car.lap_times[prev["lap_num"] - 1] = lap["last_lap_ms"]
                if valid and (car.best_ms == 0 or lap["last_lap_ms"] < car.best_ms):
                    car.best_ms = lap["last_lap_ms"]
                if is_player:
                    self._finish_player_lap(prev["lap_num"], lap["last_lap_ms"], valid)
                events.append(("lap_complete", {
                    "idx": car.idx, "lap": prev["lap_num"], "ms": lap["last_lap_ms"], "valid": valid}))
            if prev["pit_status"] == 0 and lap["pit_status"] != 0:
                events.append(("pit_enter", {"idx": car.idx}))
            elif prev["pit_status"] != 0 and lap["pit_status"] == 0:
                events.append(("pit_exit", {"idx": car.idx, "stop_ms": prev["pit_stop_timer_ms"]}))
        self._lap_was_invalid = bool(self.player.lap.get("lap_invalid"))

    def _on_event(self, body: dict, events: list[Event]) -> None:
        code = body["code"]
        if code == "BUTN":
            # Rising edge on a "UDP Action n" button -> driver asks the engineer something
            pressed = body["buttons"] & ~self.buttons
            self.buttons = body["buttons"]
            for action, bit in C.UDP_ACTION_BITS.items():
                if pressed & bit:
                    events.append(("button", {"action": action}))
            return
        if code == "FTLP":
            self.fastest_lap = (body["vehicle_idx"], round(body["lap_time"] * 1000))
        elif code == "FLBK":
            self.traces["cur"] = None  # a flashback rewinds time; the partial trace is no longer valid
        events.append(("game_event", body))

    def _on_history(self, body: dict) -> None:
        idx = body["car_idx"]
        if idx >= C.NUM_CARS:
            return
        car, laps = self.cars[idx], body["laps"]
        car.stints = body["stints"]

        def lap_field(lap_no: int, field: str, minutes: str | None = None) -> int:
            if not 1 <= lap_no <= len(laps):
                return 0
            lap = laps[lap_no - 1]
            return join_ms(lap[minutes], lap[field]) if minutes else lap[field]

        best = lap_field(body["best_lap_num"], "lap_ms")
        if best:
            car.best_ms = best
        car.best_sectors = [
            lap_field(body["best_s1_lap"], "s1_ms", "s1_min"),
            lap_field(body["best_s2_lap"], "s2_ms", "s2_min"),
            lap_field(body["best_s3_lap"], "s3_ms", "s3_min"),
        ]
        done = [lap for lap in laps if lap["lap_ms"] > 0]
        if done:
            last = done[-1]
            car.last_sectors = [
                join_ms(last["s1_min"], last["s1_ms"]), join_ms(last["s2_min"], last["s2_ms"]),
                join_ms(last["s3_min"], last["s3_ms"]),
            ]
        car.lap_times = [lap["lap_ms"] for lap in laps if lap["lap_ms"] > 0]

    # ------------------------------------------------------------------ player lap bookkeeping

    def _finish_player_lap(self, lap_no: int, ms: int, valid: bool) -> None:
        me = self.player
        wear = [round(w, 1) for w in me.damage.get("tyre_wear", [0, 0, 0, 0])]
        self.lap_log.append({
            "lap": lap_no, "ms": ms, "valid": valid, "wear": wear, "wear_max": max(wear),
            "fuel_kg": round(me.status.get("fuel_in_tank", 0.0), 2),
            "compound": me.status.get("visual_compound", 0), "tyre_age": me.status.get("tyre_age", 0),
            "pos": me.lap.get("position", 0),
            "ers": round(100 * me.status.get("ers_store", 0.0) / C.ERS_MAX_J),
        })
        cur = self.traces["cur"]
        if cur and cur["filled"] >= 0.8 * len(cur["t"]):
            cur["ms"] = ms
            self.traces["last"] = cur
            best = self.traces["best"]
            if valid and (best is None or ms < best["ms"]):
                self.traces["best"] = cur
        self.traces["cur"] = None

    def _sample_trace(self) -> None:
        me = self.player
        if not self.track_length or not me.lap or me.lap["pit_status"] != 0:
            return
        dist = me.lap["lap_distance"]
        n = self.track_length // TRACE_BUCKET_M + 2
        bucket = int(dist // TRACE_BUCKET_M)
        if dist < 0 or bucket >= n:
            return
        cur = self.traces["cur"]
        if cur is None or len(cur["t"]) != n:
            cur = self.traces["cur"] = {ch: [None] * n for ch in TRACE_CHANNELS} | {"filled": 0, "ms": 0}
        if cur["t"][bucket] is None:
            cur["filled"] += 1
        tel = me.tel
        cur["speed"][bucket] = tel["speed"]
        cur["throttle"][bucket] = round(tel["throttle"] * 100)
        cur["brake"][bucket] = round(tel["brake"] * 100)
        cur["gear"][bucket] = tel["gear"]
        cur["steer"][bucket] = round(tel["steer"] * 100)
        cur["t"][bucket] = me.lap["cur_lap_ms"]

    def live_delta_ms(self) -> int | None:
        """Time gained/lost against the personal best lap at the same point on track."""
        cur, best, lap = self.traces["cur"], self.traces["best"], self.player.lap
        if not cur or not best or not lap or len(best["t"]) != len(cur["t"]):
            return None
        bucket = int(lap["lap_distance"] // TRACE_BUCKET_M)
        if not 0 <= bucket < len(cur["t"]) or cur["t"][bucket] is None or best["t"][bucket] is None:
            return None
        return cur["t"][bucket] - best["t"][bucket]

    # ------------------------------------------------------------------ derived numbers

    def current_stint(self) -> list[dict]:
        """Trailing lap records driven on the tyre set that is fitted right now."""
        stint: list[dict] = []
        for rec in reversed(self.lap_log):
            if stint and (rec["compound"] != stint[-1]["compound"] or rec["tyre_age"] >= stint[-1]["tyre_age"]):
                break
            stint.append(rec)
        stint.reverse()
        me = self.player.status
        if stint and (stint[-1]["compound"] != me.get("visual_compound") or stint[-1]["tyre_age"] > me.get("tyre_age", 0)):
            return []  # tyres were changed after the last completed lap
        return stint

    def wear_rate(self) -> float | None:
        """Tyre wear in % per lap on the most worn wheel, for the current stint."""
        stint = self.current_stint()
        if len(stint) >= 2:
            return max(0.0, (stint[-1]["wear_max"] - stint[0]["wear_max"]) / (len(stint) - 1))
        age = self.player.status.get("tyre_age", 0)
        wear = max(self.player.damage.get("tyre_wear", [0.0]))
        return wear / age if age >= 1 and wear > 0 else None

    def fuel_per_lap(self) -> float | None:
        recent = self.lap_log[-4:]
        burns = [a["fuel_kg"] - b["fuel_kg"] for a, b in zip(recent, recent[1:])]
        burns = [b for b in burns if b > 0]
        return sum(burns) / len(burns) if burns else None

    def gaps(self) -> dict[str, Any]:
        """Player's neighbours on track with time gaps in ms (race) or best-lap deltas (other)."""
        me = self.player
        ahead, behind = self.car_at(me.position - 1), self.car_at(me.position + 1)
        out: dict[str, Any] = {"ahead": None, "behind": None}
        if self.is_race:
            if ahead:
                out["ahead"] = {"idx": ahead.idx, "name": ahead.name, "ms": me.gap_front_ms}
            if behind:
                out["behind"] = {"idx": behind.idx, "name": behind.name, "ms": behind.gap_front_ms}
        else:
            if ahead and ahead.best_ms and me.best_ms:
                out["ahead"] = {"idx": ahead.idx, "name": ahead.name, "ms": me.best_ms - ahead.best_ms}
            if behind and behind.best_ms and me.best_ms:
                out["behind"] = {"idx": behind.idx, "name": behind.name, "ms": behind.best_ms - me.best_ms}
        return out
