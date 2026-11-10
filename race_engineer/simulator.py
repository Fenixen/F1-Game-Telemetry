"""Fake F1 25 game: produces real binary UDP packets for a scripted race.

Lets you try the whole app (and run the tests) without the game running:
    python -m race_engineer.simulator            # send to 127.0.0.1:20777
    python -m race_engineer --demo               # app + simulator in one process
"""
from __future__ import annotations

import argparse
import math
import random
import socket
import time

from .udp import constants as C
from .udp import packets as P

DRIVERS = [
    ("VERSTAPPEN", 2, 1), ("NORRIS", 8, 4), ("LECLERC", 1, 16), ("PIASTRI", 8, 81), ("RUSSELL", 0, 63),
    ("HAMILTON", 1, 44), ("ANTONELLI", 0, 12), ("Player", 3, 23), ("SAINZ", 3, 55), ("ALONSO", 4, 14),
    ("GASLY", 5, 10), ("TSUNODA", 2, 22), ("HADJAR", 6, 6), ("STROLL", 4, 18), ("OCON", 7, 31),
    ("BEARMAN", 7, 87), ("HULKENBERG", 9, 27), ("LAWSON", 6, 30), ("BORTOLETO", 9, 5), ("COLAPINTO", 5, 43),
]
PLAYER_IDX = 7
COMPOUNDS = {"S": (16, 20, 2.6), "M": (17, 19, 1.7), "H": (18, 18, 1.1)}  # visual, actual, wear %/lap
FUEL_PER_LAP = 1.9
PIT_SPEED = 22.2  # m/s = 80 km/h


class _Track:
    """Closed synthetic circuit with a physically plausible speed profile."""

    def __init__(self, n: int = 1500):
        pts = []
        for i in range(n):
            a = 2 * math.pi * i / n
            r = 1 + 0.28 * math.sin(2 * a) + 0.16 * math.cos(3 * a) + 0.07 * math.sin(5 * a)
            pts.append((820 * r * math.cos(a), 520 * r * math.sin(a)))
        self.pts, self.n = pts, n
        seg = [math.dist(pts[i], pts[(i + 1) % n]) for i in range(n)]
        self.length = sum(seg)
        self.ds = self.length / n
        self.curv = []
        for i in range(n):
            (x0, z0), (x1, z1), (x2, z2) = pts[i - 1], pts[i], pts[(i + 1) % n]
            turn = math.atan2(z2 - z1, x2 - x1) - math.atan2(z1 - z0, x1 - x0)
            turn = (turn + math.pi) % (2 * math.pi) - math.pi
            self.curv.append(turn / self.ds)
        v = [min(88.0, max(22.0, math.sqrt(19.0 / max(abs(k), 1e-5)))) for k in self.curv]
        for _ in range(2):  # braking limit (backwards) and traction limit (forwards)
            for i in range(n - 1, -1, -1):
                v[i] = min(v[i], math.sqrt(v[(i + 1) % n] ** 2 + 2 * 36.0 * self.ds))
            for i in range(n):
                v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2 * 8.0 * self.ds))
        self.speed = v

    def index(self, s: float) -> int:
        return int(s / self.length * self.n) % self.n


class _Car:
    def __init__(self, idx: int, grid: int, pace: float, compound: str, pit_lap: int, fuel: float):
        self.idx, self.grid, self.pace = idx, grid, pace
        self.total = -8.0 * grid          # metres from the start line (negative = behind it)
        self.v = 0.0
        self.accel = 0.0
        self.lap = 1
        self.lap_start = 3.0          # lap 1 is timed from lights out
        self.sector_times = [0, 0]
        self.laps: list[dict] = []
        self.best_ms = 0
        self.compound, self.fitted_lap = compound, 1
        self.stints: list[tuple[int, str]] = []
        self.wear = [0.0, 0.0, 0.0, 0.0]
        self.fuel = fuel
        self.pit_lap, self.pits = pit_lap, 0
        self.in_pit, self.pit_status = False, 0
        self.stop_left, self.stop_ms, self.lane_ms = 0.0, 0, 0
        self.stopped = False
        self.position = grid
        self.finished = self.retired = False
        self.finish_time = 0.0
        self.wing_damage = 0
        self.cc_warnings = 0
        self.flag = 0
        self.top_speed = 0.0


class Simulator:
    def __init__(self, laps: int = 8, seed: int = 7, player_pit_lap: int | None = None):
        self.rng = random.Random(seed)
        self.track = _Track()
        self.total_laps = laps
        self.uid = self.rng.getrandbits(63)
        self.t = 0.0
        self.frame = 0
        self.weather = 1
        self.vsc = False
        self.vsc_until = 0.0
        self.done = False
        self.fastest_ms = 0
        self._script: set[str] = set()
        self._chequered = False
        order = list(range(len(DRIVERS)))
        self.rng.shuffle(order)
        order.remove(PLAYER_IDX)
        order.insert(7, PLAYER_IDX)  # player starts P8
        fuel = laps * FUEL_PER_LAP + 1.2
        self.cars: list[_Car] = [None] * len(DRIVERS)  # type: ignore[list-item]
        for grid, idx in enumerate(order, start=1):
            pace = 1.0 - 0.0022 * grid + self.rng.uniform(-0.002, 0.002)
            pit = self.rng.randint(max(2, laps // 2 - 1), max(2, laps // 2 + 1))
            compound = self.rng.choice(["S", "M", "M"])
            if idx == PLAYER_IDX:
                pace, pit, compound = 0.992, player_pit_lap or max(2, laps // 2 + 1), "M"
            self.cars[idx] = _Car(idx, grid, pace, compound, pit, fuel)

    # ------------------------------------------------------------------ physics

    def step(self, dt: float) -> list[bytes]:
        """Advance the race by dt seconds and return the datagrams the game would send."""
        if self.done:
            return []
        out: list[bytes] = []
        self.frame += 1
        if self.frame == 1:
            out.append(self._event("SSTA"))
        lights_out = self.t >= 3.0
        if lights_out and self._once("lgot"):
            out.append(self._event("LGOT"))
        self.t += dt
        prev_order = sorted(self.cars, key=lambda c: c.position)
        if lights_out:
            for car in self.cars:
                self._move(car, dt, out)
        self._rank(prev_order, out)
        self._run_script(out)
        out += self._packets()
        me = self.cars[PLAYER_IDX]
        if all(c.finished or c.retired for c in self.cars) or (me.finished and self.t > me.finish_time + 6):
            out.append(self._final_classification())
            out.append(self._event("SEND"))
            self.done = True
        return out

    def _move(self, car: _Car, dt: float, out: list[bytes]) -> None:
        if car.finished or car.retired:
            return
        tr = self.track
        s = car.total % tr.length if car.total >= 0 else 0.0
        i = tr.index(s)
        grip = 1.0 - 0.0007 * sum(car.wear) / 4 - (0.04 if self.weather >= 3 else 0.0) - 0.0006 * car.wing_damage
        target = tr.speed[i] * car.pace * grip
        if self.vsc:
            target = min(target, 0.6 * tr.speed[i], 55.0)

        # Pit stop state machine: lane entry -> box -> stationary -> lane exit
        if not car.in_pit and car.lap == car.pit_lap and s > tr.length - 250 and car.total > 0:
            car.in_pit, car.pit_status, car.lane_ms, car.stop_ms, car.stopped = True, 1, 0, 0, False
        if car.in_pit:
            target = min(target, PIT_SPEED)
            car.lane_ms += int(dt * 1000)
            if not car.stopped and 80 <= s < 200:
                car.stopped, car.stop_left, car.pit_status = True, 2.4 + self.rng.random() * 0.8, 2
            if car.stop_left > 0:
                target = 0.0
                car.stop_left -= dt
                car.stop_ms += int(dt * 1000)
                if car.stop_left <= 0:
                    self._change_tyres(car)
                    car.pit_status = 1
            elif car.stopped and 330 <= s < 600:
                car.in_pit, car.pit_status = False, 0

        prev_v = car.v
        car.v = max(target, car.v - 45 * dt) if target < car.v else min(target, car.v + 12 * dt)
        car.accel = (car.v - prev_v) / dt
        car.top_speed = max(car.top_speed, car.v * 3.6)
        before = car.total
        car.total += car.v * dt
        frac_laps = car.v * dt / tr.length
        rate = COMPOUNDS[car.compound][2] * (1.25 if car.idx == PLAYER_IDX else 1.0)
        for w, k in enumerate((1.0, 0.95, 1.2, 1.1)):
            car.wear[w] = min(100.0, car.wear[w] + rate * k * frac_laps)
        car.fuel = max(0.0, car.fuel - FUEL_PER_LAP * frac_laps)

        if car.total >= 0:
            s_new, lap_ms = car.total % tr.length, int((self.t - car.lap_start) * 1000)
            for n, mark in enumerate((0.33, 0.68)):
                if not car.sector_times[n] and s_new >= mark * tr.length:
                    car.sector_times[n] = lap_ms - (car.sector_times[0] if n else 0)
            if before >= 0 and int(car.total // tr.length) > int(before // tr.length):
                self._finish_lap(car, lap_ms, out)

    def _finish_lap(self, car: _Car, lap_ms: int, out: list[bytes]) -> None:
        s1, s2 = car.sector_times
        car.laps.append({"lap_ms": lap_ms, "s1": s1, "s2": s2, "s3": lap_ms - s1 - s2})
        if not car.best_ms or lap_ms < car.best_ms:
            car.best_ms = lap_ms
        if car.lap > 1 and (not self.fastest_ms or lap_ms < self.fastest_ms):
            self.fastest_ms = lap_ms
            out.append(self._event("FTLP", {"vehicle_idx": car.idx, "lap_time": lap_ms / 1000}))
        if car.lap >= self.total_laps or self._chequered:
            car.finished, car.finish_time, car.v = True, self.t, 0.0
            if not self._chequered:
                self._chequered = True
                out.append(self._event("RCWN", {"vehicle_idx": car.idx}))
            if car.idx == PLAYER_IDX:
                out.append(self._event("CHQF"))
            return
        car.lap += 1
        car.lap_start = self.t
        car.sector_times = [0, 0]

    def _change_tyres(self, car: _Car) -> None:
        car.stints.append((car.lap - 1, car.compound))
        car.compound = "H" if car.compound != "H" else "M"
        car.fitted_lap, car.wear, car.pits = car.lap, [0.0] * 4, car.pits + 1
        car.wing_damage = 0
        car.pit_lap = 0

    def _rank(self, prev_order: list[_Car], out: list[bytes]) -> None:
        def key(c: _Car):
            if c.retired:
                return (2, 0.0)
            return (0, c.finish_time) if c.finished else (1, -c.total)
        order = sorted(self.cars, key=key)
        prev_pos = {c.idx: c.position for c in prev_order}
        for pos, car in enumerate(order, start=1):
            car.position = pos
        for ahead, behind in zip(order, order[1:]):
            swapped = prev_pos[ahead.idx] > prev_pos[behind.idx]
            if swapped and not (ahead.in_pit or behind.in_pit or behind.retired or ahead.total < 50):
                out.append(self._event("OVTK", {"overtaking_idx": ahead.idx, "overtaken_idx": behind.idx}))

    # ------------------------------------------------------------------ scripted incidents

    def _once(self, key: str) -> bool:
        if key in self._script:
            return False
        self._script.add(key)
        return True

    def _run_script(self, out: list[bytes]) -> None:
        me = self.cars[PLAYER_IDX]
        frac = (me.total % self.track.length) / self.track.length if me.total > 0 else 0.0
        if me.lap == 2 and frac > 0.3 and self._once("warning"):
            me.cc_warnings = 1
            out.append(self._event("PENA", {"penalty_type": 5, "infringement_type": 7, "vehicle_idx": me.idx,
                                            "other_vehicle_idx": 255, "time": 255, "lap_num": 2}))
        if me.lap == 3 and frac > 0.2 and self._once("vsc"):
            self.vsc, self.vsc_until = True, self.t + 20
            out.append(self._event("SCAR", {"safety_car_type": 2, "event_type": 0}))
        if self.vsc and self.t >= self.vsc_until:
            self.vsc = False
            out.append(self._event("SCAR", {"safety_car_type": 2, "event_type": 2}))
        me.flag = 3 if (me.lap == 4 and 0.15 < frac < 0.3) else 0
        if me.lap == 4 and frac > 0.6 and self._once("collision"):
            rival = min((c for c in self.cars if c is not me), key=lambda c: abs(c.total - me.total))
            me.wing_damage = 22
            out.append(self._event("COLL", {"vehicle1_idx": me.idx, "vehicle2_idx": rival.idx}))
        last = max(self.cars, key=lambda c: c.position)
        if me.lap == max(3, self.total_laps - 2) and last is not me and self._once("retire"):
            last.retired, last.v = True, 0.0
            out.append(self._event("RTMT", {"vehicle_idx": last.idx, "reason": 3}))
        self.weather = 3 if self.t >= 420 else 1

    # ------------------------------------------------------------------ packet encoding

    def _packet(self, pid: int, body: bytes) -> bytes:
        return P.build_header(pid, self.uid, self.t, self.frame, PLAYER_IDX) + body

    def _event(self, code: str, details: dict | None = None) -> bytes:
        return self._packet(C.EVENT, P.build_event(code, details))

    def _packets(self) -> list[bytes]:
        f = self.frame
        out = [self._motion(), self._lap_data(), self._telemetry(), self._status()]
        if f % 10 == 1:
            out += [self._session(), self._damage(), self._setups()]
        if f % 3 == 0:
            out.append(self._history(self.cars[(f // 3) % len(self.cars)]))
        if f % 150 == 1:
            out += [self._participants(), self._tyre_sets()]
        return out

    def _s(self, car: _Car) -> float:
        return car.total % self.track.length if car.total >= 0 else car.total

    def _motion(self) -> bytes:
        cars = []
        for car in self.cars:
            i = self.track.index(max(car.total, 0.0))
            (x, z), (nx, nz) = self.track.pts[i], self.track.pts[(i + 1) % self.track.n]
            heading = math.atan2(nz - z, nx - x)
            cars.append({
                "pos": [x, 0.0, z], "vel": [car.v * math.cos(heading), 0.0, car.v * math.sin(heading)],
                "g_lat": car.v ** 2 * self.track.curv[i] / 9.81, "g_lon": car.accel / 9.81, "g_vert": 1.0,
                "yaw": heading,
            })
        return self._packet(C.MOTION, P.build_cars(P.CAR_MOTION, cars))

    def _lap_data(self) -> bytes:
        by_pos = sorted(self.cars, key=lambda c: c.position)
        leader, cars = by_pos[0], []
        for car in self.cars:
            ahead = by_pos[car.position - 2] if car.position > 1 else None

            def gap(other: _Car | None) -> tuple[int, int]:
                if other is None or other is car:
                    return 0, 0
                if other.finished and car.finished:
                    ms = int((car.finish_time - other.finish_time) * 1000)
                else:
                    ms = int(1000 * max(0.0, other.total - car.total) / max(car.v, 25.0))
                return ms % 60000, min(255, ms // 60000)

            front_ms, front_min = gap(ahead)
            lead_ms, lead_min = gap(leader)
            s = self._s(car)
            status = 7 if car.retired else 3 if car.finished else 2
            cars.append({
                "last_lap_ms": car.laps[-1]["lap_ms"] if car.laps else 0,
                "cur_lap_ms": 0 if car.finished else int(max(0.0, self.t - car.lap_start) * 1000),
                "s1_ms": car.sector_times[0] % 60000, "s1_min": car.sector_times[0] // 60000,
                "s2_ms": car.sector_times[1] % 60000, "s2_min": car.sector_times[1] // 60000,
                "delta_front_ms": front_ms, "delta_front_min": front_min,
                "delta_leader_ms": lead_ms, "delta_leader_min": lead_min,
                "lap_distance": s, "total_distance": car.total, "position": car.position, "lap_num": car.lap,
                "pit_status": car.pit_status, "num_pit_stops": car.pits,
                "sector": 0 if s < 0.33 * self.track.length else 1 if s < 0.68 * self.track.length else 2,
                "cc_warnings": car.cc_warnings, "total_warnings": car.cc_warnings, "grid_position": car.grid,
                "driver_status": 4, "result_status": status, "pit_lane_timer_active": int(car.in_pit),
                "pit_lane_time_ms": min(65535, car.lane_ms), "pit_stop_timer_ms": min(65535, car.stop_ms),
                "speed_trap_fastest": car.top_speed, "speed_trap_fastest_lap": 1,
            })
        return self._packet(C.LAP_DATA, P.build_cars(P.LAP, cars, suffix=b"\xff\xff"))

    def _telemetry(self) -> bytes:
        tr, cars = self.track, []
        for car in self.cars:
            i = tr.index(max(car.total, 0.0))
            slope = tr.speed[(i + 4) % tr.n] - tr.speed[i]
            moving = car.v > 1 and not car.in_pit
            kmh = car.v * 3.6
            brake = min(1.0, max(0.0, -slope / 4)) if moving else 0.0
            throttle = (0.0 if brake > 0.05 else 1.0) if moving else (0.3 if car.v > 1 else 0.0)
            if self.vsc and moving:
                throttle = min(throttle, 0.5)
            steer = max(-1.0, min(1.0, tr.curv[i] * 45))
            gear = 0 if car.v < 0.5 else 1 + min(7, int(kmh / 43))
            rpm = 4000 if car.v < 0.5 else int(7000 + (kmh % 43) / 43 * 5200)
            hot = car.wear[2] * 0.3 if car.idx == PLAYER_IDX else 0.0
            heat = abs(steer) * 14
            cars.append({
                "speed": int(kmh), "throttle": throttle, "steer": steer, "brake": brake, "gear": gear, "rpm": rpm,
                "drs": int(kmh > 265 and car.lap > 1 and not self.vsc),
                "rev_lights_pct": max(0, min(100, int((rpm - 7000) / 52))),
                "brake_temp": [int(420 + 380 * brake)] * 2 + [int(520 + 520 * brake)] * 2,
                "tyre_surface_temp": [int(88 + heat), int(88 + heat), int(92 + heat + hot), int(92 + heat + hot)],
                "tyre_inner_temp": [int(94 + car.wear[0] * 0.1), int(94 + car.wear[1] * 0.1),
                                    int(96 + hot), int(96 + hot)],
                "engine_temp": 108, "tyre_pressure": [22.6, 22.6, 24.8, 24.8],
            })
        tail = P.TELEMETRY_TAIL.pack({"mfd_panel": 255, "mfd_panel_secondary": 255, "suggested_gear": 0})
        return self._packet(C.CAR_TELEMETRY, P.build_cars(P.TELEMETRY, cars, suffix=tail))

    def _status(self) -> bytes:
        cars = []
        for car in self.cars:
            laps_left = self.total_laps - car.lap + 1 - (max(car.total, 0) % self.track.length) / self.track.length
            visual, actual, _ = COMPOUNDS[car.compound]
            cars.append({
                "fuel_mix": 1, "front_brake_bias": 56, "pit_limiter": int(car.in_pit),
                "fuel_in_tank": car.fuel, "fuel_capacity": 110.0,
                "fuel_remaining_laps": car.fuel / FUEL_PER_LAP - laps_left,
                "max_rpm": 12500, "idle_rpm": 4000, "max_gears": 9, "drs_allowed": int(car.lap > 1 and not self.vsc),
                "actual_compound": actual, "visual_compound": visual, "tyre_age": car.lap - car.fitted_lap,
                "fia_flag": car.flag, "power_ice": 560000.0, "power_mguk": 120000.0,
                "ers_store": 2.1e6 + 1.6e6 * math.sin(self.t / 11 + car.idx), "ers_mode": 1,
            })
        return self._packet(C.CAR_STATUS, P.build_cars(P.STATUS, cars))

    def _damage(self) -> bytes:
        cars = [{"tyre_wear": car.wear, "fl_wing": car.wing_damage, "ice_wear": 12, "mguh_wear": 9, "mguk_wear": 14,
                 "es_wear": 7, "ce_wear": 6, "tc_wear": 11, "gearbox": 8} for car in self.cars]
        return self._packet(C.CAR_DAMAGE, P.build_cars(P.DAMAGE, cars))

    def _setups(self) -> bytes:
        setup = {
            "front_wing": 28, "rear_wing": 24, "on_throttle": 55, "off_throttle": 50, "front_camber": -3.2,
            "rear_camber": -1.9, "front_toe": 0.04, "rear_toe": 0.12, "front_suspension": 36, "rear_suspension": 12,
            "front_arb": 14, "rear_arb": 8, "front_ride_height": 22, "rear_ride_height": 52, "brake_pressure": 96,
            "brake_bias": 56, "engine_braking": 60, "rl_pressure": 22.6, "rr_pressure": 22.6, "fl_pressure": 24.8,
            "fr_pressure": 24.8, "fuel_load": self.total_laps * FUEL_PER_LAP + 1.2,
        }
        cars = [setup if i == PLAYER_IDX else {} for i in range(len(self.cars))]
        return self._packet(C.CAR_SETUPS, P.build_cars(P.SETUP, cars, suffix=b"\0\0\0\0"))

    def _participants(self) -> bytes:
        cars = [{"ai_controlled": int(i != PLAYER_IDX), "driver_id": i, "team_id": team, "race_number": number,
                 "name": name, "your_telemetry": 1} for i, (name, team, number) in enumerate(DRIVERS)]
        return self._packet(C.PARTICIPANTS, P.build_cars(P.PARTICIPANT, cars, prefix=bytes([len(cars)])))

    def _session(self) -> bytes:
        me = self.cars[PLAYER_IDX]
        zones = [{"start": i / 16, "flag": 0} for i in range(16)]
        if me.flag == 3:
            zones[4]["flag"] = 3
        forecast = []
        for offset in (0, 5, 10, 15, 30):
            rain = self.t + offset * 60 >= 420
            forecast.append({"session_type": 15, "time_offset": offset, "weather": 3 if rain else 1,
                             "track_temp": 31 - (5 if rain else 0), "air_temp": 24, "rain": 70 if rain else 10})
        return self._packet(C.SESSION, P.build_session({
            "weather": self.weather, "track_temp": 26 if self.weather >= 3 else 31, "air_temp": 24,
            "total_laps": self.total_laps, "track_length": int(self.track.length), "session_type": 15,
            "track_id": 11, "pit_speed_limit": 80, "marshal_zones": zones, "safety_car": 2 if self.vsc else 0,
            "forecast": forecast, "pit_ideal_lap": max(2, self.total_laps // 2),
            "pit_latest_lap": max(2, self.total_laps // 2 + 2), "pit_rejoin_pos": 12,
            "s2_start": 0.33 * self.track.length, "s3_start": 0.68 * self.track.length,
        }))

    def _history(self, car: _Car) -> bytes:
        laps = [{"lap_ms": lap["lap_ms"], "s1_ms": lap["s1"] % 60000, "s1_min": lap["s1"] // 60000,
                 "s2_ms": lap["s2"] % 60000, "s2_min": lap["s2"] // 60000, "s3_ms": lap["s3"] % 60000,
                 "s3_min": lap["s3"] // 60000, "valid_flags": 15} for lap in car.laps]

        def best(field: str) -> int:
            return 1 + min(range(len(car.laps)), key=lambda i: car.laps[i][field]) if car.laps else 0

        if not car.finished:
            laps.append({})
        stints = [{"end_lap": end, "actual": COMPOUNDS[c][1], "visual": COMPOUNDS[c][0]} for end, c in car.stints]
        stints.append({"end_lap": 255, "actual": COMPOUNDS[car.compound][1], "visual": COMPOUNDS[car.compound][0]})
        return self._packet(C.SESSION_HISTORY, P.build_history({
            "car_idx": car.idx, "laps": laps[:100], "stints": stints[:8], "best_lap_num": best("lap_ms"),
            "best_s1_lap": best("s1"), "best_s2_lap": best("s2"), "best_s3_lap": best("s3"),
        }))

    def _tyre_sets(self) -> bytes:
        me, sets = self.cars[PLAYER_IDX], []
        for n, (name, life, delta) in enumerate((("S", 12, -600), ("S", 12, -600), ("M", 20, 0), ("M", 20, 0),
                                                 ("H", 30, 500), ("H", 30, 500))):
            visual, actual, _ = COMPOUNDS[name]
            fitted = name == me.compound and n % 2 == 0
            sets.append({"actual": actual, "visual": visual, "wear": int(max(me.wear)) if fitted else 0,
                         "available": int(not fitted), "recommended_session": 15, "life_span": life,
                         "usable_life": life, "lap_delta_ms": delta, "fitted": int(fitted)})
        sets.append({"actual": 7, "visual": 7, "available": 1, "life_span": 25, "usable_life": 25})
        sets.append({"actual": 8, "visual": 8, "available": 1, "life_span": 30, "usable_life": 30})
        fitted_idx = next((i for i, s in enumerate(sets) if s.get("fitted")), 255)
        return self._packet(C.TYRE_SETS, P.build_tyre_sets(PLAYER_IDX, sets, fitted_idx))

    def _final_classification(self) -> bytes:
        points = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
        cars = []
        for car in self.cars:
            cars.append({
                "position": car.position, "num_laps": len(car.laps), "grid_position": car.grid,
                "points": points[car.position - 1] if car.position <= 10 and car.finished else 0,
                "num_pit_stops": car.pits, "result_status": 3 if car.finished else 7 if car.retired else 4,
                "best_lap_ms": car.best_ms, "total_race_time": car.finish_time, "num_tyre_stints": len(car.stints) + 1,
            })
        return self._packet(C.FINAL_CLASSIFICATION, P.build_cars(P.CLASSIFICATION, cars, prefix=bytes([len(cars)])))


def run(host: str = "127.0.0.1", port: int = 20777, speed: float = 1.0, laps: int = 8, loop: bool = True) -> None:
    """Blocking sender loop (30 Hz real time)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seed = 7
    while True:
        sim = Simulator(laps=laps, seed=seed)
        while not sim.done:
            started = time.perf_counter()
            for _ in range(max(1, round(speed))):
                for datagram in sim.step(1 / 30):
                    sock.sendto(datagram, (host, port))
            time.sleep(max(0.0, 1 / 30 - (time.perf_counter() - started)))
        if not loop:
            return
        time.sleep(12)
        seed += 1


def main() -> None:
    parser = argparse.ArgumentParser(description="F1 25 UDP telemetry simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=20777)
    parser.add_argument("--speed", type=float, default=1.0, help="time multiplier (e.g. 5 = five times faster)")
    parser.add_argument("--laps", type=int, default=8)
    args = parser.parse_args()
    run(args.host, args.port, args.speed, args.laps)


if __name__ == "__main__":
    main()
