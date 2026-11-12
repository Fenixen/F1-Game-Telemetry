"""JSON-friendly views of the race state that are pushed to the iPad frontend.

Two rates keep the WebSocket light: `fast` carries what changes every frame
(driver inputs, temperatures), `slow` carries everything else.
"""
from __future__ import annotations

from . import analysis, strategy
from .engineer import setup_advisor
from .state import OUTLINE_BUCKET_M, TRACE_BUCKET_M, TRACE_CHANNELS, RaceState
from .udp import constants as C

_SC_NAMES = {0: "", 1: "SC", 2: "VSC", 3: "FORMOVACÍ KOLO"}
_ENGINE_PARTS = ("ice_wear", "mguh_wear", "mguk_wear", "es_wear", "ce_wear", "tc_wear")
_DAMAGE_PARTS = ("fl_wing", "fr_wing", "rear_wing", "floor", "diffuser", "sidepod", "gearbox", "engine",
                 "drs_fault", "ers_fault")


def fast(state: RaceState) -> dict:
    me = state.player
    tel, status, lap, damage = me.tel, me.status, me.lap, me.damage
    if not tel or not status or not lap:
        return {}
    return {
        "spd": tel["speed"], "gear": tel["gear"], "rpm": tel["rpm"], "rev": tel["rev_lights_pct"],
        "thr": round(tel["throttle"], 3), "brk": round(tel["brake"], 3), "steer": round(tel["steer"], 3),
        "drs": tel["drs"], "drsOk": status["drs_allowed"],
        "ers": round(100 * status["ers_store"] / C.ERS_MAX_J, 1), "ersMode": C.ERS_MODES.get(status["ers_mode"], "?"),
        "fuelLaps": round(status["fuel_remaining_laps"], 2), "fuelKg": round(status["fuel_in_tank"], 1),
        "fuelMix": C.FUEL_MIXES.get(status["fuel_mix"], "?"),
        "tS": tel["tyre_surface_temp"], "tI": tel["tyre_inner_temp"], "bT": tel["brake_temp"],
        "tP": [round(p, 1) for p in tel["tyre_pressure"]],
        "wear": [round(w, 1) for w in damage.get("tyre_wear", [0, 0, 0, 0])],
        "lapMs": lap["cur_lap_ms"], "delta": state.live_delta_ms(), "dist": round(lap["lap_distance"]),
        "sector": lap["sector"], "invalid": lap["lap_invalid"],
        "gLat": round(state.g_lat, 2), "gLon": round(state.g_lon, 2),
        "pitLim": status["pit_limiter"], "sugGear": state.suggested_gear, "bias": status["front_brake_bias"],
        "engT": tel["engine_temp"], "aero": _aero(state),
    }


def _aero(state: RaceState) -> dict | None:
    """2026 regulations: active aero mode and the overtake (manual override) energy boost."""
    a = state.aero
    if not a or not a.get("regs_2026"):
        return None
    return {"mode": a["aero_mode"], "avail": a["aero_available"], "otAvail": a["overtake_available"],
            "otActive": a["overtake_active"]}


def _car_status(state: RaceState, lap: dict) -> str:
    result = lap.get("result_status", 0)
    if result >= 3:
        return {3: "CÍL", 4: "DNF", 5: "DSQ", 6: "NC", 7: "OUT"}.get(result, "")
    if lap.get("pit_status"):
        return "BOX"
    if not state.is_race:
        return {0: "GARÁŽ", 1: "RYCHLÉ", 2: "IN", 3: "OUT"}.get(lap.get("driver_status", 4), "")
    return ""


def _cars(state: RaceState) -> list[dict]:
    cars = state.active_cars()
    leader_best = min((c.best_ms for c in cars if c.best_ms), default=0)
    rows, prev_best = [], 0
    for car in cars:
        lap, (short, _, colour) = car.lap, car.compound
        if state.is_race:
            gap, interval = car.gap_leader_ms, car.gap_front_ms
        else:
            gap = car.best_ms - leader_best if car.best_ms else None
            interval = car.best_ms - prev_best if car.best_ms and prev_best else None
        prev_best = car.best_ms or prev_best
        rows.append({
            "i": car.idx, "pos": car.position, "name": car.name, "team": car.team[0], "color": car.team[1],
            "num": car.part.get("race_number", 0), "gap": gap, "int": interval,
            "last": lap.get("last_lap_ms", 0), "best": car.best_ms, "sectors": car.last_sectors,
            "bestSectors": car.best_sectors, "cmp": short, "cmpColor": colour,
            "age": car.status.get("tyre_age", 0), "pits": lap.get("num_pit_stops", 0),
            "st": _car_status(state, lap), "pen": lap.get("penalties", 0), "lap": lap.get("lap_num", 0),
            "x": round(car.x, 1), "z": round(car.z, 1), "me": car.idx == state.player_idx,
            "grid": lap.get("grid_position", 0), "laps": car.lap_times[-60:],
        })
    return rows


def _session(state: RaceState) -> dict:
    s, length = state.session, state.track_length or 1
    session_type = s.get("session_type", 0)
    forecast = [
        {"t": f["time_offset"], "w": f["weather"], "rain": f["rain"], "tt": f["track_temp"], "at": f["air_temp"]}
        for f in s.get("forecast", []) if f["session_type"] == session_type
    ]
    return {
        "type": session_type, "typeName": C.SESSION_TYPES.get(session_type, "—"), "isRace": state.is_race,
        "isQuali": state.is_quali, "track": C.TRACKS.get(s.get("track_id", -1), "Neznámá trať"),
        "totalLaps": s.get("total_laps", 0), "timeLeft": s.get("time_left", 0),
        "weather": s.get("weather", 0), "weatherName": C.WEATHER.get(s.get("weather", 0), "?"),
        "trackTemp": s.get("track_temp", 0), "airTemp": s.get("air_temp", 0),
        "sc": _SC_NAMES.get(s.get("safety_car", 0), ""), "forecast": forecast,
        "zones": [{"s": round(z["start"], 3), "f": z["flag"]} for z in s.get("marshal_zones", [])],
        "pitSpeed": s.get("pit_speed_limit", 0), "trackLen": s.get("track_length", 0),
        "s2": round(s.get("s2_start", 0) / length, 4), "s3": round(s.get("s3_start", 0) / length, 4),
        "paused": s.get("paused", 0), "fmt": state.fmt,
        "aeroZones": [{"s": round(z["start"]), "e": round(z["end"]), "k": z["kind"]} for z in s.get("aero_zones", [])],
        "drsZones": [{"s": round(z["start"]), "e": round(z["end"])} for z in s.get("drs_zones", [])],
    }


def _player(state: RaceState) -> dict:
    me = state.player
    lap, status, damage = me.lap, me.status, me.damage
    short, name, colour = me.compound
    sets = []
    for i, tyre_set in enumerate(state.tyre_sets):
        s_short, _, s_colour = C.VISUAL_COMPOUNDS.get(tyre_set["visual"], ("?", "", "#888"))
        if tyre_set["actual"] == 0:
            continue
        sets.append({
            "short": s_short, "color": s_colour, "actual": C.ACTUAL_COMPOUNDS.get(tyre_set["actual"], ""),
            "wear": tyre_set["wear"], "avail": tyre_set["available"], "life": tyre_set["life_span"],
            "delta": tyre_set["lap_delta_ms"], "fitted": bool(tyre_set["fitted"]) or i == state.fitted_set,
        })
    return {
        "idx": me.idx, "name": me.name, "team": me.team[0], "color": me.team[1],
        "pos": lap.get("position", 0), "lap": lap.get("lap_num", 0), "grid": lap.get("grid_position", 0),
        "last": lap.get("last_lap_ms", 0), "best": me.best_ms,
        "sectors": me.last_sectors, "bestSectors": me.best_sectors,
        "pens": lap.get("penalties", 0), "warnings": lap.get("total_warnings", 0),
        "ccWarnings": lap.get("cc_warnings", 0), "unservedDt": lap.get("unserved_dt", 0),
        "unservedSg": lap.get("unserved_sg", 0),
        "cmp": short, "cmpName": name, "cmpColor": colour,
        "actual": C.ACTUAL_COMPOUNDS.get(status.get("actual_compound", 0), ""),
        "tyreAge": status.get("tyre_age", 0), "pits": lap.get("num_pit_stops", 0),
        "flag": status.get("fia_flag", 0), "gaps": state.gaps(),
        "damage": {k: damage.get(k, 0) for k in _DAMAGE_PARTS},
        "tyreDamage": damage.get("tyre_damage", [0, 0, 0, 0]),
        "brakeDamage": damage.get("brake_damage", [0, 0, 0, 0]),
        "blisters": damage.get("tyre_blisters", [0, 0, 0, 0]),
        "engine": {k: damage.get(k, 0) for k in _ENGINE_PARTS},
        "setup": _setup(state.setup), "tyreSets": sets,
        "speedTrap": round(lap.get("speed_trap_fastest", 0.0), 1),
    }


def _setup(setup: dict) -> dict:
    out = {}
    for key, value in setup.items():
        out[key] = round(value, 2) if isinstance(value, float) else value
    return out


def slow(state: RaceState) -> dict:
    if not state.player.lap or not state.session:
        return {"connected": state.connected, "ready": False}
    fl = None
    if state.fastest_lap:
        fl = {"name": state.cars[state.fastest_lap[0]].name, "ms": state.fastest_lap[1]}
    cars = _cars(state)
    purple = [min((c["bestSectors"][i] for c in cars if c["bestSectors"][i]), default=0) for i in range(3)]
    return {
        "connected": state.connected, "ready": True, "session": _session(state), "player": _player(state),
        "cars": cars, "strategy": strategy.compute(state), "lapLog": state.lap_log[-80:],
        "fl": fl, "purple": purple, "outlineV": state.outline_version,
        "setupView": setup_advisor.describe(state.setup),
    }


def outline(state: RaceState) -> dict:
    pts = [[round(p[0], 1), round(p[1], 1), i * OUTLINE_BUCKET_M] for i, p in enumerate(state.outline) if p]
    return {"v": state.outline_version, "pts": pts, "complete": state.outline_filled >= len(state.outline) * 0.97}


def traces(state: RaceState) -> dict:
    out: dict = {"bucket": TRACE_BUCKET_M}
    for key, trace in state.traces.items():
        out[key] = {ch: trace[ch] for ch in TRACE_CHANNELS} | {"ms": trace.get("ms", 0)} if trace else None
    ref = state.reference_trace()
    if out.get("ref") and state.traces["best"] and state.traces["ref"]["ms"] >= state.traces["best"]["ms"]:
        out["ref"] = None  # the all-time best is this session's best: do not draw it twice
    corners = analysis.detect_corners(ref, TRACE_BUCKET_M)
    out["corners"] = [{k: c[k] for k in ("name", "apex_m", "start_m", "end_m", "brake_m", "min_kmh")} for c in corners]
    lap = state.traces["last"]
    out["coaching"] = analysis.compare_corners(ref, lap, corners, TRACE_BUCKET_M) if lap is not ref else []
    out["refKind"] = "best" if state.traces["best"] else "ref" if ref else None
    return out


def setup_info(state: RaceState) -> dict:
    return {"rec": setup_advisor.recommend(state), "library": setup_advisor.library(state),
            "current": setup_advisor.describe(state.setup)}
