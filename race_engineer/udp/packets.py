"""Binary layouts and parsers for the F1 25 UDP telemetry formats 2025 and 2026.

Format 2025 = F1 25 base game. Format 2026 = F1 25 with the 2026 Season Pack
(24 cars, active aero, overtake mode). The game lets you pick the format in
Settings -> Telemetry, and the app detects it from every packet header.

Every layout is declared once and used for both decoding (game -> app) and
encoding (simulator -> app), so the two can never drift apart. Bodies are
normalised so the rest of the app does not care which format arrived
(e.g. g-forces are always floats in g). All data is little-endian and packed.
"""
from __future__ import annotations

import struct
from typing import Any, Callable

from . import constants as C

HEADER_SIZE = 29


class Layout:
    """Declarative struct: list of (name, struct_code); '4f' becomes a list, '5x' is skipped padding."""

    __slots__ = ("struct", "plan", "size")

    def __init__(self, *fields: tuple[str, str]):
        fmt, plan, i = "<", [], 0
        for name, code in fields:
            fmt += code
            kind = code[-1]
            count = int(code[:-1]) if len(code) > 1 else 1
            if kind == "x":
                continue  # padding: struct skips it, no value
            if kind == "s":
                plan.append((name, i, -1))  # fixed-size string
                i += 1
            elif len(code) == 1:
                plan.append((name, i, 0))  # scalar
                i += 1
            else:
                plan.append((name, i, count))  # array
                i += count
        self.struct = struct.Struct(fmt)
        self.plan = plan
        self.size = self.struct.size

    def unpack(self, buf: bytes, offset: int = 0) -> dict[str, Any]:
        raw = self.struct.unpack_from(buf, offset)
        out = {}
        for name, i, count in self.plan:
            if count == 0:
                out[name] = raw[i]
            elif count == -1:
                out[name] = raw[i].split(b"\0", 1)[0].decode("utf-8", "replace")
            else:
                out[name] = list(raw[i:i + count])
        return out

    def pack(self, values: dict[str, Any]) -> bytes:
        flat: list[Any] = []
        for name, _, count in self.plan:
            v = values.get(name)
            if count == 0:
                flat.append(v if v is not None else 0)
            elif count == -1:
                flat.append((v or "").encode("utf-8"))
            else:
                flat.extend(v if v is not None else [0] * count)
        return self.struct.pack(*flat)


HEADER = Layout(
    ("packet_format", "H"), ("game_year", "B"), ("game_major", "B"), ("game_minor", "B"),
    ("packet_version", "B"), ("packet_id", "B"), ("session_uid", "Q"), ("session_time", "f"),
    ("frame", "I"), ("overall_frame", "I"), ("player_idx", "B"), ("secondary_player_idx", "B"),
)

# ---------------------------------------------------------------- layouts shared by both formats

MARSHAL_ZONE = Layout(("start", "f"), ("flag", "b"))
FORECAST = Layout(
    ("session_type", "B"), ("time_offset", "B"), ("weather", "B"), ("track_temp", "b"),
    ("track_temp_change", "b"), ("air_temp", "b"), ("air_temp_change", "b"), ("rain", "B"),
)
SESSION_HEAD = Layout(
    ("weather", "B"), ("track_temp", "b"), ("air_temp", "b"), ("total_laps", "B"), ("track_length", "H"),
    ("session_type", "B"), ("track_id", "b"), ("formula", "B"), ("time_left", "H"), ("duration", "H"),
    ("pit_speed_limit", "B"), ("paused", "B"), ("is_spectating", "B"), ("spectator_idx", "B"),
    ("sli_pro", "B"), ("num_marshal_zones", "B"),
)
SESSION_MID = Layout(("safety_car", "B"), ("network_game", "B"), ("num_forecast", "B"))
SESSION_TAIL = Layout(
    ("forecast_accuracy", "B"), ("ai_difficulty", "B"), ("season_link", "I"), ("weekend_link", "I"),
    ("session_link", "I"), ("pit_ideal_lap", "B"), ("pit_latest_lap", "B"), ("pit_rejoin_pos", "B"),
    ("_assists", "9x"), ("game_mode", "B"), ("rule_set", "B"), ("time_of_day", "I"), ("session_length", "B"),
    ("_units", "4x"), ("num_sc_periods", "B"), ("num_vsc_periods", "B"), ("num_red_flag_periods", "B"),
    ("_rules", "25x"), ("_weekend", "12x"),
    ("s2_start", "f"), ("s3_start", "f"),
)
AERO_ZONE = Layout(("start", "f"), ("end", "f"))
# Offsets inside the session body (after the header)
_OFF_ZONES, _OFF_MID, _OFF_FORECAST, _OFF_TAIL = 19, 124, 127, 639
_SESSION_BODY_2025 = 724
# 2026 only: active aero / DRS zones and the start reaction time follow the sector distances
SESSION_AERO_HEAD = Layout(("aero_track_status", "B"), ("num_aero_full", "B"))

LAP = Layout(
    ("last_lap_ms", "I"), ("cur_lap_ms", "I"), ("s1_ms", "H"), ("s1_min", "B"), ("s2_ms", "H"), ("s2_min", "B"),
    ("delta_front_ms", "H"), ("delta_front_min", "B"), ("delta_leader_ms", "H"), ("delta_leader_min", "B"),
    ("lap_distance", "f"), ("total_distance", "f"), ("sc_delta", "f"),
    ("position", "B"), ("lap_num", "B"), ("pit_status", "B"), ("num_pit_stops", "B"), ("sector", "B"),
    ("lap_invalid", "B"), ("penalties", "B"), ("total_warnings", "B"), ("cc_warnings", "B"),
    ("unserved_dt", "B"), ("unserved_sg", "B"), ("grid_position", "B"), ("driver_status", "B"),
    ("result_status", "B"), ("pit_lane_timer_active", "B"), ("pit_lane_time_ms", "H"),
    ("pit_stop_timer_ms", "H"), ("pit_stop_serve_pen", "B"), ("speed_trap_fastest", "f"),
    ("speed_trap_fastest_lap", "B"),
)

SETUP = Layout(
    ("front_wing", "B"), ("rear_wing", "B"), ("on_throttle", "B"), ("off_throttle", "B"),
    ("front_camber", "f"), ("rear_camber", "f"), ("front_toe", "f"), ("rear_toe", "f"),
    ("front_suspension", "B"), ("rear_suspension", "B"), ("front_arb", "B"), ("rear_arb", "B"),
    ("front_ride_height", "B"), ("rear_ride_height", "B"), ("brake_pressure", "B"), ("brake_bias", "B"),
    ("engine_braking", "B"), ("rl_pressure", "f"), ("rr_pressure", "f"), ("fl_pressure", "f"),
    ("fr_pressure", "f"), ("ballast", "B"), ("fuel_load", "f"),
)
TELEMETRY_TAIL = Layout(("mfd_panel", "B"), ("mfd_panel_secondary", "B"), ("suggested_gear", "b"))

DAMAGE = Layout(
    ("tyre_wear", "4f"), ("tyre_damage", "4B"), ("brake_damage", "4B"), ("tyre_blisters", "4B"),
    ("fl_wing", "B"), ("fr_wing", "B"), ("rear_wing", "B"), ("floor", "B"), ("diffuser", "B"),
    ("sidepod", "B"), ("drs_fault", "B"), ("ers_fault", "B"), ("gearbox", "B"), ("engine", "B"),
    ("mguh_wear", "B"), ("es_wear", "B"), ("ce_wear", "B"), ("ice_wear", "B"), ("mguk_wear", "B"),
    ("tc_wear", "B"), ("engine_blown", "B"), ("engine_seized", "B"),
)

CLASSIFICATION = Layout(
    ("position", "B"), ("num_laps", "B"), ("grid_position", "B"), ("points", "B"), ("num_pit_stops", "B"),
    ("result_status", "B"), ("result_reason", "B"), ("best_lap_ms", "I"), ("total_race_time", "d"),
    ("penalties_time", "B"), ("num_penalties", "B"), ("num_tyre_stints", "B"),
    ("stints_actual", "8B"), ("stints_visual", "8B"), ("stints_end_laps", "8B"),
)

HISTORY_HEAD = Layout(
    ("car_idx", "B"), ("num_laps", "B"), ("num_tyre_stints", "B"), ("best_lap_num", "B"),
    ("best_s1_lap", "B"), ("best_s2_lap", "B"), ("best_s3_lap", "B"),
)
HISTORY_LAP = Layout(
    ("lap_ms", "I"), ("s1_ms", "H"), ("s1_min", "B"), ("s2_ms", "H"), ("s2_min", "B"),
    ("s3_ms", "H"), ("s3_min", "B"), ("valid_flags", "B"),
)
HISTORY_STINT = Layout(("end_lap", "B"), ("actual", "B"), ("visual", "B"))
_HISTORY_MAX_LAPS, _HISTORY_MAX_STINTS = 100, 8

TYRE_SET = Layout(
    ("actual", "B"), ("visual", "B"), ("wear", "B"), ("available", "B"), ("recommended_session", "B"),
    ("life_span", "B"), ("usable_life", "B"), ("lap_delta_ms", "h"), ("fitted", "B"),
)
_NUM_TYRE_SETS = 20

# Player car only. Wheel arrays are RL, RR, FL, FR.
MOTION_EX = Layout(
    ("susp_pos", "4f"), ("susp_vel", "4f"), ("susp_acc", "4f"), ("wheel_speed", "4f"),
    ("slip_ratio", "4f"), ("slip_angle", "4f"), ("lat_force", "4f"), ("long_force", "4f"),
    ("cog_height", "f"), ("local_vel", "3f"), ("ang_vel", "3f"), ("ang_acc", "3f"), ("front_wheels_angle", "f"),
    ("vert_force", "4f"), ("front_aero_height", "f"), ("rear_aero_height", "f"), ("front_roll", "f"),
    ("rear_roll", "f"), ("chassis_yaw", "f"), ("chassis_pitch", "f"), ("wheel_camber", "4f"),
    ("wheel_camber_gain", "4f"),
)

# 2026 only, packet 16
TELEMETRY2 = Layout(
    ("aero_mode", "B"), ("aero_available", "B"), ("aero_activation_distance", "H"),
    ("overtake_available", "B"), ("overtake_active", "B"), ("overtake_activation_distance", "H"),
    ("regs_2026", "B"), ("wrong_way", "B"),
)

_EVENT_BODY_SIZE = 16  # 4-byte code + 12-byte union


def _event_layouts(collision: Layout) -> dict[str, Layout]:
    return {
        "FTLP": Layout(("vehicle_idx", "B"), ("lap_time", "f")),
        "RTMT": Layout(("vehicle_idx", "B"), ("reason", "B")),
        "DRSD": Layout(("reason", "B")),
        "TMPT": Layout(("vehicle_idx", "B")),
        "RCWN": Layout(("vehicle_idx", "B")),
        "PENA": Layout(
            ("penalty_type", "B"), ("infringement_type", "B"), ("vehicle_idx", "B"),
            ("other_vehicle_idx", "B"), ("time", "B"), ("lap_num", "B"), ("places_gained", "B"),
        ),
        "SPTP": Layout(
            ("vehicle_idx", "B"), ("speed", "f"), ("is_overall_fastest", "B"), ("is_driver_fastest", "B"),
            ("fastest_vehicle_idx", "B"), ("fastest_speed", "f"),
        ),
        "STLG": Layout(("num_lights", "B")),
        "DTSV": Layout(("vehicle_idx", "B")),
        "SGSV": Layout(("vehicle_idx", "B"), ("stop_time", "f")),
        "FLBK": Layout(("flashback_frame", "I"), ("flashback_session_time", "f")),
        "BUTN": Layout(("buttons", "I")),
        "OVTK": Layout(("overtaking_idx", "B"), ("overtaken_idx", "B")),
        "SCAR": Layout(("safety_car_type", "B"), ("event_type", "B")),
        "COLL": collision,
    }


# ---------------------------------------------------------------- per-format specification

class Spec:
    """Everything that differs between UDP formats."""

    def __init__(self, fmt: int, num_cars: int, sizes: dict[int, int], motion: Layout, participant: Layout,
                 telemetry: Layout, status: Layout, events: dict[str, Layout], session_body: int):
        self.fmt, self.num_cars, self.sizes = fmt, num_cars, sizes
        self.motion, self.participant, self.telemetry, self.status = motion, participant, telemetry, status
        self.events, self.session_body = events, session_body


_MOTION_COMMON = (("pos", "3f"), ("vel", "3f"), ("fwd", "3h"), ("right", "3h"))
_PARTICIPANT_TAIL = (("my_team", "B"), ("race_number", "B"), ("nationality", "B"), ("name", "32s"),
                     ("your_telemetry", "B"), ("show_online_names", "B"), ("tech_level", "H"), ("platform", "B"),
                     ("num_colours", "B"), ("livery_colours", "12B"))
_TELEMETRY_HEAD = (("speed", "H"), ("throttle", "f"), ("steer", "f"), ("brake", "f"), ("clutch", "B"), ("gear", "b"),
                   ("rpm", "H"), ("drs", "B"), ("rev_lights_pct", "B"), ("rev_lights_bits", "H"),
                   ("brake_temp", "4H"), ("tyre_surface_temp", "4B"), ("tyre_inner_temp", "4B"))
_STATUS_HEAD = (("traction_control", "B"), ("abs", "B"), ("fuel_mix", "B"), ("front_brake_bias", "B"),
                ("pit_limiter", "B"), ("fuel_in_tank", "f"), ("fuel_capacity", "f"), ("fuel_remaining_laps", "f"),
                ("max_rpm", "H"), ("idle_rpm", "H"), ("max_gears", "B"), ("drs_allowed", "B"),
                ("drs_activation_distance", "H"), ("actual_compound", "B"), ("visual_compound", "B"),
                ("tyre_age", "B"), ("fia_flag", "b"), ("power_ice", "f"), ("power_mguk", "f"),
                ("ers_store", "f"), ("ers_mode", "B"), ("ers_harvest_mguk", "f"), ("ers_harvest_mguh", "f"))

SPEC_2025 = Spec(
    2025, 22,
    {0: 1349, 1: 753, 2: 1285, 3: 45, 4: 1284, 5: 1133, 6: 1352, 7: 1239,
     8: 1042, 9: 954, 10: 1041, 11: 1460, 12: 231, 13: 273, 14: 101, 15: 1131},
    motion=Layout(*_MOTION_COMMON, ("g_lat", "f"), ("g_lon", "f"), ("g_vert", "f"),
                  ("yaw", "f"), ("pitch", "f"), ("roll", "f")),
    participant=Layout(("ai_controlled", "B"), ("driver_id", "B"), ("network_id", "B"), ("team_id", "B"),
                       *_PARTICIPANT_TAIL),
    telemetry=Layout(*_TELEMETRY_HEAD, ("engine_temp", "H"), ("tyre_pressure", "4f"), ("surface_type", "4B")),
    status=Layout(*_STATUS_HEAD, ("ers_deployed", "f"), ("network_paused", "B")),
    events=_event_layouts(Layout(("vehicle1_idx", "B"), ("vehicle2_idx", "B"))),
    session_body=_SESSION_BODY_2025,
)

SPEC_2026 = Spec(
    2026, 24,
    {0: 1325, 1: 926, 2: 1399, 3: 45, 4: 1470, 5: 1233, 6: 1448, 7: 1445,
     8: 1134, 9: 1062, 10: 1133, 11: 1460, 12: 231, 13: 273, 14: 104, 15: 1231, 16: 269},
    # g-forces are quantised to int16 thousandths of g in 2026; normalised to float g when parsing
    motion=Layout(*_MOTION_COMMON, ("g_lat_q", "h"), ("g_lon_q", "h"), ("g_vert_q", "h"),
                  ("yaw", "f"), ("pitch", "f"), ("roll", "f")),
    participant=Layout(("ai_controlled", "B"), ("driver_id", "H"), ("network_id", "H"), ("team_id", "H"),
                       *_PARTICIPANT_TAIL),
    telemetry=Layout(*_TELEMETRY_HEAD, ("engine_temp", "B"), ("tyre_pressure", "4f"), ("surface_type", "4B")),
    status=Layout(*_STATUS_HEAD, ("ers_harvest_limit", "f"), ("ers_deployed", "f"), ("network_paused", "B")),
    events=_event_layouts(Layout(("vehicle1_idx", "B"), ("vehicle2_idx", "B"), ("severity", "B"))),
    session_body=897,
)
SPECS = {2025: SPEC_2025, 2026: SPEC_2026}

# Fail fast if a layout ever drifts from the published packet sizes
for _s in SPECS.values():
    _n = _s.num_cars
    assert HEADER.size == HEADER_SIZE
    assert HEADER_SIZE + _n * _s.motion.size == _s.sizes[0]
    assert HEADER_SIZE + _s.session_body == _s.sizes[1]
    assert HEADER_SIZE + _n * LAP.size + 2 == _s.sizes[2]
    assert HEADER_SIZE + _EVENT_BODY_SIZE == _s.sizes[3]
    assert HEADER_SIZE + 1 + _n * _s.participant.size == _s.sizes[4]
    assert HEADER_SIZE + _n * SETUP.size + 4 == _s.sizes[5]
    assert HEADER_SIZE + _n * _s.telemetry.size + TELEMETRY_TAIL.size == _s.sizes[6]
    assert HEADER_SIZE + _n * _s.status.size == _s.sizes[7]
    assert HEADER_SIZE + 1 + _n * CLASSIFICATION.size == _s.sizes[8]
    assert HEADER_SIZE + _n * DAMAGE.size == _s.sizes[10]
    assert (HEADER_SIZE + HISTORY_HEAD.size + _HISTORY_MAX_LAPS * HISTORY_LAP.size
            + _HISTORY_MAX_STINTS * HISTORY_STINT.size) == _s.sizes[11]
    assert HEADER_SIZE + 1 + _NUM_TYRE_SETS * TYRE_SET.size + 1 == _s.sizes[12]
    assert HEADER_SIZE + MOTION_EX.size == _s.sizes[13]
    assert _OFF_TAIL + SESSION_TAIL.size == _SESSION_BODY_2025
assert HEADER_SIZE + SPEC_2026.num_cars * TELEMETRY2.size == SPEC_2026.sizes[16]
assert _SESSION_BODY_2025 + 2 + 8 * 8 + 1 + 8 * 8 + 1 + 4 * 8 + 4 + 5 == SPEC_2026.session_body


# ---------------------------------------------------------------- decoding

def _per_car(spec: Spec, layout: Layout, data: bytes, offset: int = HEADER_SIZE) -> list[dict]:
    return [layout.unpack(data, offset + i * layout.size) for i in range(spec.num_cars)]


def _parse_motion(spec: Spec, data: bytes) -> dict:
    cars = _per_car(spec, spec.motion, data)
    if spec.fmt >= 2026:
        for car in cars:
            car["g_lat"] = car.pop("g_lat_q") / 1000.0
            car["g_lon"] = car.pop("g_lon_q") / 1000.0
            car["g_vert"] = car.pop("g_vert_q") / 1000.0
    return {"cars": cars}


def _parse_session(spec: Spec, data: bytes) -> dict:
    base = HEADER_SIZE
    out = SESSION_HEAD.unpack(data, base)
    zones = min(out["num_marshal_zones"], 21)
    out["marshal_zones"] = [
        MARSHAL_ZONE.unpack(data, base + _OFF_ZONES + i * MARSHAL_ZONE.size) for i in range(zones)
    ]
    out.update(SESSION_MID.unpack(data, base + _OFF_MID))
    samples = min(out["num_forecast"], 64)
    out["forecast"] = [
        FORECAST.unpack(data, base + _OFF_FORECAST + i * FORECAST.size) for i in range(samples)
    ]
    out.update(SESSION_TAIL.unpack(data, base + _OFF_TAIL))
    out["aero_zones"], out["drs_zones"] = [], []
    if spec.fmt >= 2026:
        off = base + _SESSION_BODY_2025
        out.update(SESSION_AERO_HEAD.unpack(data, off))
        off += SESSION_AERO_HEAD.size
        full = [AERO_ZONE.unpack(data, off + i * 8) for i in range(min(out["num_aero_full"], 8))]
        off += 8 * 8
        partial = [AERO_ZONE.unpack(data, off + 1 + i * 8) for i in range(min(data[off], 8))]
        off += 1 + 8 * 8
        drs = [AERO_ZONE.unpack(data, off + 1 + i * 8) for i in range(min(data[off], 4))]
        out["aero_zones"] = [z | {"kind": "full"} for z in full] + [z | {"kind": "partial"} for z in partial]
        out["drs_zones"] = drs
    return out


def _parse_event(spec: Spec, data: bytes) -> dict:
    code = data[HEADER_SIZE:HEADER_SIZE + 4].decode("ascii", "replace")
    layout = spec.events.get(code)
    out = layout.unpack(data, HEADER_SIZE + 4) if layout else {}
    out["code"] = code
    return out


def _parse_history(spec: Spec, data: bytes) -> dict:
    out = HISTORY_HEAD.unpack(data, HEADER_SIZE)
    off = HEADER_SIZE + HISTORY_HEAD.size
    laps = min(out["num_laps"], _HISTORY_MAX_LAPS)
    out["laps"] = [HISTORY_LAP.unpack(data, off + i * HISTORY_LAP.size) for i in range(laps)]
    off += _HISTORY_MAX_LAPS * HISTORY_LAP.size
    stints = min(out["num_tyre_stints"], _HISTORY_MAX_STINTS)
    out["stints"] = [HISTORY_STINT.unpack(data, off + i * HISTORY_STINT.size) for i in range(stints)]
    return out


def _parse_tyre_sets(spec: Spec, data: bytes) -> dict:
    off = HEADER_SIZE + 1
    return {
        "car_idx": data[HEADER_SIZE],
        "sets": [TYRE_SET.unpack(data, off + i * TYRE_SET.size) for i in range(_NUM_TYRE_SETS)],
        "fitted_idx": data[off + _NUM_TYRE_SETS * TYRE_SET.size],
    }


def _parse_telemetry(spec: Spec, data: bytes) -> dict:
    out = TELEMETRY_TAIL.unpack(data, HEADER_SIZE + spec.num_cars * spec.telemetry.size)
    out["cars"] = _per_car(spec, spec.telemetry, data)
    return out


_PARSERS: dict[int, Callable[[Spec, bytes], dict]] = {
    C.MOTION: _parse_motion,
    C.SESSION: _parse_session,
    C.LAP_DATA: lambda s, d: {"cars": _per_car(s, LAP, d)},
    C.EVENT: _parse_event,
    C.PARTICIPANTS: lambda s, d: {"num_active": d[HEADER_SIZE], "cars": _per_car(s, s.participant, d, HEADER_SIZE + 1)},
    C.CAR_SETUPS: lambda s, d: {"cars": _per_car(s, SETUP, d)},
    C.CAR_TELEMETRY: _parse_telemetry,
    C.CAR_STATUS: lambda s, d: {"cars": _per_car(s, s.status, d)},
    C.FINAL_CLASSIFICATION: lambda s, d: {"num_cars": d[HEADER_SIZE],
                                          "cars": _per_car(s, CLASSIFICATION, d, HEADER_SIZE + 1)},
    C.CAR_DAMAGE: lambda s, d: {"cars": _per_car(s, DAMAGE, d)},
    C.SESSION_HISTORY: _parse_history,
    C.TYRE_SETS: _parse_tyre_sets,
    C.MOTION_EX: lambda s, d: MOTION_EX.unpack(d, HEADER_SIZE),
    C.CAR_TELEMETRY_2: lambda s, d: {"cars": _per_car(s, TELEMETRY2, d)},
}


class UnsupportedFormat(ValueError):
    """Raised when the game is sending a UDP format year we do not know."""


def parse_packet(data: bytes) -> tuple[dict, dict] | None:
    """Decode one datagram. Returns (header, body) or None for packets we do not use.

    Datagrams come from the network, so everything is length-checked before decoding.
    """
    if len(data) < HEADER_SIZE:
        return None
    header = HEADER.unpack(data)
    spec = SPECS.get(header["packet_format"])
    if spec is None:
        raise UnsupportedFormat(header["packet_format"])
    pid = header["packet_id"]
    parser = _PARSERS.get(pid)
    if parser is None or pid not in spec.sizes or len(data) < spec.sizes[pid]:
        return None
    return header, parser(spec, data)


# ---------------------------------------------------------------- encoding (simulator / tests)

def build_header(packet_id: int, session_uid: int, session_time: float, frame: int, player_idx: int,
                 fmt: int = 2025) -> bytes:
    return HEADER.pack({
        "packet_format": fmt, "game_year": 25, "game_major": 1, "packet_version": 1,
        "packet_id": packet_id, "session_uid": session_uid, "session_time": session_time,
        "frame": frame, "overall_frame": frame, "player_idx": player_idx, "secondary_player_idx": 255,
    })


def _pad(n: int, items: list[dict]) -> list[dict]:
    return items + [{}] * (n - len(items))


def build_cars(spec: Spec, layout: Layout, cars: list[dict], prefix: bytes = b"", suffix: bytes = b"") -> bytes:
    return prefix + b"".join(layout.pack(c) for c in _pad(spec.num_cars, cars)) + suffix


def build_motion(spec: Spec, cars: list[dict]) -> bytes:
    if spec.fmt >= 2026:
        cars = [c | {f"{k}_q": round(c.get(k, 0.0) * 1000) for k in ("g_lat", "g_lon", "g_vert")} for c in cars]
    return build_cars(spec, spec.motion, cars)


def build_session(spec: Spec, s: dict) -> bytes:
    body = bytearray(spec.session_body)
    zones, forecast = s.get("marshal_zones", []), s.get("forecast", [])
    body[0:SESSION_HEAD.size] = SESSION_HEAD.pack({**s, "num_marshal_zones": len(zones)})
    for i, z in enumerate(zones):
        o = _OFF_ZONES + i * MARSHAL_ZONE.size
        body[o:o + MARSHAL_ZONE.size] = MARSHAL_ZONE.pack(z)
    body[_OFF_MID:_OFF_MID + SESSION_MID.size] = SESSION_MID.pack({**s, "num_forecast": len(forecast)})
    for i, f in enumerate(forecast):
        o = _OFF_FORECAST + i * FORECAST.size
        body[o:o + FORECAST.size] = FORECAST.pack(f)
    body[_OFF_TAIL:_OFF_TAIL + SESSION_TAIL.size] = SESSION_TAIL.pack(s)
    if spec.fmt >= 2026:
        off = _SESSION_BODY_2025
        full = [z for z in s.get("aero_zones", []) if z.get("kind") == "full"][:8]
        partial = [z for z in s.get("aero_zones", []) if z.get("kind") == "partial"][:8]
        body[off:off + 2] = bytes([s.get("aero_track_status", 0), len(full)])
        off += 2
        for i, z in enumerate(full):
            body[off + i * 8:off + i * 8 + 8] = AERO_ZONE.pack(z)
        off += 64
        body[off] = len(partial)
        for i, z in enumerate(partial):
            body[off + 1 + i * 8:off + 9 + i * 8] = AERO_ZONE.pack(z)
        off += 65
        drs = s.get("drs_zones", [])[:4]
        body[off] = len(drs)
        for i, z in enumerate(drs):
            body[off + 1 + i * 8:off + 9 + i * 8] = AERO_ZONE.pack(z)
    return bytes(body)


def build_event(spec: Spec, code: str, details: dict | None = None) -> bytes:
    layout = spec.events.get(code)
    payload = code.encode("ascii") + (layout.pack(details or {}) if layout else b"")
    return payload.ljust(_EVENT_BODY_SIZE, b"\0")


def build_history(h: dict) -> bytes:
    laps, stints = h.get("laps", []), h.get("stints", [])
    head = HISTORY_HEAD.pack({**h, "num_laps": len(laps), "num_tyre_stints": len(stints)})
    lap_bytes = b"".join(HISTORY_LAP.pack(x) for x in _pad(_HISTORY_MAX_LAPS, laps))
    stint_bytes = b"".join(HISTORY_STINT.pack(x) for x in _pad(_HISTORY_MAX_STINTS, stints))
    return head + lap_bytes + stint_bytes


def build_tyre_sets(car_idx: int, sets: list[dict], fitted_idx: int) -> bytes:
    body = b"".join(TYRE_SET.pack(x) for x in _pad(_NUM_TYRE_SETS, sets))
    return bytes([car_idx]) + body + bytes([fitted_idx])
