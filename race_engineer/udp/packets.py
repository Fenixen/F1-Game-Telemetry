"""Binary layouts and parsers for the F1 25 UDP telemetry format (packet format 2025).

Every layout is declared once and used for both decoding (game -> app) and
encoding (simulator -> app), so the two can never drift apart.
All data is little-endian and packed (no padding).
"""
from __future__ import annotations

import struct
from typing import Any

from .constants import NUM_CARS, PACKET_FORMAT, PACKET_SIZES

HEADER_SIZE = 29


class Layout:
    """Declarative struct: list of (name, struct_code); codes like '4f' become lists."""

    __slots__ = ("struct", "plan", "size")

    def __init__(self, *fields: tuple[str, str]):
        fmt, plan, i = "<", [], 0
        for name, code in fields:
            fmt += code
            kind = code[-1]
            count = int(code[:-1]) if len(code) > 1 else 1
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

CAR_MOTION = Layout(
    ("pos", "3f"), ("vel", "3f"), ("fwd", "3h"), ("right", "3h"),
    ("g_lat", "f"), ("g_lon", "f"), ("g_vert", "f"), ("yaw", "f"), ("pitch", "f"), ("roll", "f"),
)

SESSION_HEAD = Layout(
    ("weather", "B"), ("track_temp", "b"), ("air_temp", "b"), ("total_laps", "B"), ("track_length", "H"),
    ("session_type", "B"), ("track_id", "b"), ("formula", "B"), ("time_left", "H"), ("duration", "H"),
    ("pit_speed_limit", "B"), ("paused", "B"), ("is_spectating", "B"), ("spectator_idx", "B"),
    ("sli_pro", "B"), ("num_marshal_zones", "B"),
)
MARSHAL_ZONE = Layout(("start", "f"), ("flag", "b"))
SESSION_MID = Layout(("safety_car", "B"), ("network_game", "B"), ("num_forecast", "B"))
FORECAST = Layout(
    ("session_type", "B"), ("time_offset", "B"), ("weather", "B"), ("track_temp", "b"),
    ("track_temp_change", "b"), ("air_temp", "b"), ("air_temp_change", "b"), ("rain", "B"),
)
SESSION_TAIL = Layout(
    ("forecast_accuracy", "B"), ("ai_difficulty", "B"), ("season_link", "I"), ("weekend_link", "I"),
    ("session_link", "I"), ("pit_ideal_lap", "B"), ("pit_latest_lap", "B"), ("pit_rejoin_pos", "B"),
)
SESSION_SECTORS = Layout(("s2_start", "f"), ("s3_start", "f"))
# Offsets inside the session body (after the header)
_OFF_ZONES, _OFF_MID, _OFF_FORECAST, _OFF_TAIL, _OFF_SECTORS = 19, 124, 127, 639, 716
SESSION_BODY_SIZE = 724

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

PARTICIPANT = Layout(
    ("ai_controlled", "B"), ("driver_id", "B"), ("network_id", "B"), ("team_id", "B"), ("my_team", "B"),
    ("race_number", "B"), ("nationality", "B"), ("name", "32s"), ("your_telemetry", "B"),
    ("show_online_names", "B"), ("tech_level", "H"), ("platform", "B"), ("num_colours", "B"),
    ("livery_colours", "12B"),
)

SETUP = Layout(
    ("front_wing", "B"), ("rear_wing", "B"), ("on_throttle", "B"), ("off_throttle", "B"),
    ("front_camber", "f"), ("rear_camber", "f"), ("front_toe", "f"), ("rear_toe", "f"),
    ("front_suspension", "B"), ("rear_suspension", "B"), ("front_arb", "B"), ("rear_arb", "B"),
    ("front_ride_height", "B"), ("rear_ride_height", "B"), ("brake_pressure", "B"), ("brake_bias", "B"),
    ("engine_braking", "B"), ("rl_pressure", "f"), ("rr_pressure", "f"), ("fl_pressure", "f"),
    ("fr_pressure", "f"), ("ballast", "B"), ("fuel_load", "f"),
)

TELEMETRY = Layout(
    ("speed", "H"), ("throttle", "f"), ("steer", "f"), ("brake", "f"), ("clutch", "B"), ("gear", "b"),
    ("rpm", "H"), ("drs", "B"), ("rev_lights_pct", "B"), ("rev_lights_bits", "H"),
    ("brake_temp", "4H"), ("tyre_surface_temp", "4B"), ("tyre_inner_temp", "4B"), ("engine_temp", "H"),
    ("tyre_pressure", "4f"), ("surface_type", "4B"),
)
TELEMETRY_TAIL = Layout(("mfd_panel", "B"), ("mfd_panel_secondary", "B"), ("suggested_gear", "b"))

STATUS = Layout(
    ("traction_control", "B"), ("abs", "B"), ("fuel_mix", "B"), ("front_brake_bias", "B"),
    ("pit_limiter", "B"), ("fuel_in_tank", "f"), ("fuel_capacity", "f"), ("fuel_remaining_laps", "f"),
    ("max_rpm", "H"), ("idle_rpm", "H"), ("max_gears", "B"), ("drs_allowed", "B"),
    ("drs_activation_distance", "H"), ("actual_compound", "B"), ("visual_compound", "B"),
    ("tyre_age", "B"), ("fia_flag", "b"), ("power_ice", "f"), ("power_mguk", "f"),
    ("ers_store", "f"), ("ers_mode", "B"), ("ers_harvest_mguk", "f"), ("ers_harvest_mguh", "f"),
    ("ers_deployed", "f"), ("network_paused", "B"),
)

DAMAGE = Layout(
    ("tyre_wear", "4f"), ("tyre_damage", "4B"), ("brake_damage", "4B"), ("tyre_blisters", "4B"),
    ("fl_wing", "B"), ("fr_wing", "B"), ("rear_wing", "B"), ("floor", "B"), ("diffuser", "B"),
    ("sidepod", "B"), ("drs_fault", "B"), ("ers_fault", "B"), ("gearbox", "B"), ("engine", "B"),
    ("mguh_wear", "B"), ("ce_wear", "B"), ("es_wear", "B"), ("ice_wear", "B"), ("mguk_wear", "B"),
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

# Event detail layouts keyed by 4-char event code (codes without details are absent)
EVENT_DETAILS = {
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
    "COLL": Layout(("vehicle1_idx", "B"), ("vehicle2_idx", "B")),
}
_EVENT_BODY_SIZE = 16  # 4-byte code + 12-byte union

# Fail fast if a layout ever drifts from the published packet sizes
assert HEADER.size == HEADER_SIZE
assert HEADER_SIZE + NUM_CARS * CAR_MOTION.size == PACKET_SIZES[0]
assert HEADER_SIZE + SESSION_BODY_SIZE == PACKET_SIZES[1]
assert HEADER_SIZE + NUM_CARS * LAP.size + 2 == PACKET_SIZES[2]
assert HEADER_SIZE + _EVENT_BODY_SIZE == PACKET_SIZES[3]
assert HEADER_SIZE + 1 + NUM_CARS * PARTICIPANT.size == PACKET_SIZES[4]
assert HEADER_SIZE + NUM_CARS * SETUP.size + 4 == PACKET_SIZES[5]
assert HEADER_SIZE + NUM_CARS * TELEMETRY.size + TELEMETRY_TAIL.size == PACKET_SIZES[6]
assert HEADER_SIZE + NUM_CARS * STATUS.size == PACKET_SIZES[7]
assert HEADER_SIZE + 1 + NUM_CARS * CLASSIFICATION.size == PACKET_SIZES[8]
assert HEADER_SIZE + NUM_CARS * DAMAGE.size == PACKET_SIZES[10]
assert (HEADER_SIZE + HISTORY_HEAD.size + _HISTORY_MAX_LAPS * HISTORY_LAP.size
        + _HISTORY_MAX_STINTS * HISTORY_STINT.size) == PACKET_SIZES[11]
assert HEADER_SIZE + 1 + _NUM_TYRE_SETS * TYRE_SET.size + 1 == PACKET_SIZES[12]


def _per_car(layout: Layout, data: bytes, offset: int = HEADER_SIZE) -> list[dict]:
    return [layout.unpack(data, offset + i * layout.size) for i in range(NUM_CARS)]


def _parse_session(data: bytes) -> dict:
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
    out.update(SESSION_SECTORS.unpack(data, base + _OFF_SECTORS))
    return out


def _parse_event(data: bytes) -> dict:
    code = data[HEADER_SIZE:HEADER_SIZE + 4].decode("ascii", "replace")
    layout = EVENT_DETAILS.get(code)
    out = layout.unpack(data, HEADER_SIZE + 4) if layout else {}
    out["code"] = code
    return out


def _parse_history(data: bytes) -> dict:
    out = HISTORY_HEAD.unpack(data, HEADER_SIZE)
    off = HEADER_SIZE + HISTORY_HEAD.size
    laps = min(out["num_laps"], _HISTORY_MAX_LAPS)
    out["laps"] = [HISTORY_LAP.unpack(data, off + i * HISTORY_LAP.size) for i in range(laps)]
    off += _HISTORY_MAX_LAPS * HISTORY_LAP.size
    stints = min(out["num_tyre_stints"], _HISTORY_MAX_STINTS)
    out["stints"] = [HISTORY_STINT.unpack(data, off + i * HISTORY_STINT.size) for i in range(stints)]
    return out


def _parse_tyre_sets(data: bytes) -> dict:
    off = HEADER_SIZE + 1
    return {
        "car_idx": data[HEADER_SIZE],
        "sets": [TYRE_SET.unpack(data, off + i * TYRE_SET.size) for i in range(_NUM_TYRE_SETS)],
        "fitted_idx": data[off + _NUM_TYRE_SETS * TYRE_SET.size],
    }


def _parse_telemetry(data: bytes) -> dict:
    out = TELEMETRY_TAIL.unpack(data, HEADER_SIZE + NUM_CARS * TELEMETRY.size)
    out["cars"] = _per_car(TELEMETRY, data)
    return out


_PARSERS = {
    0: lambda d: {"cars": _per_car(CAR_MOTION, d)},
    1: _parse_session,
    2: lambda d: {"cars": _per_car(LAP, d)},
    3: _parse_event,
    4: lambda d: {"num_active": d[HEADER_SIZE], "cars": _per_car(PARTICIPANT, d, HEADER_SIZE + 1)},
    5: lambda d: {"cars": _per_car(SETUP, d)},
    6: _parse_telemetry,
    7: lambda d: {"cars": _per_car(STATUS, d)},
    8: lambda d: {"num_cars": d[HEADER_SIZE], "cars": _per_car(CLASSIFICATION, d, HEADER_SIZE + 1)},
    10: lambda d: {"cars": _per_car(DAMAGE, d)},
    11: _parse_history,
    12: _parse_tyre_sets,
}


class UnsupportedFormat(ValueError):
    """Raised when the game is sending a different UDP format year."""


def parse_packet(data: bytes) -> tuple[dict, dict] | None:
    """Decode one datagram. Returns (header, body) or None for packets we do not use.

    Datagrams come from the network, so everything is length-checked before decoding.
    """
    if len(data) < HEADER_SIZE:
        return None
    header = HEADER.unpack(data)
    if header["packet_format"] != PACKET_FORMAT:
        raise UnsupportedFormat(header["packet_format"])
    pid = header["packet_id"]
    parser = _PARSERS.get(pid)
    if parser is None or len(data) < PACKET_SIZES[pid]:
        return None
    return header, parser(data)


# ---------------------------------------------------------------- encoding (simulator / tests)

def build_header(packet_id: int, session_uid: int, session_time: float, frame: int, player_idx: int) -> bytes:
    return HEADER.pack({
        "packet_format": PACKET_FORMAT, "game_year": 25, "game_major": 1, "packet_version": 1,
        "packet_id": packet_id, "session_uid": session_uid, "session_time": session_time,
        "frame": frame, "overall_frame": frame, "player_idx": player_idx, "secondary_player_idx": 255,
    })


def _pad(n: int, items: list[dict]) -> list[dict]:
    return items + [{}] * (n - len(items))


def build_cars(layout: Layout, cars: list[dict], prefix: bytes = b"", suffix: bytes = b"") -> bytes:
    return prefix + b"".join(layout.pack(c) for c in _pad(NUM_CARS, cars)) + suffix


def build_session(s: dict) -> bytes:
    body = bytearray(SESSION_BODY_SIZE)
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
    body[_OFF_SECTORS:_OFF_SECTORS + SESSION_SECTORS.size] = SESSION_SECTORS.pack(s)
    return bytes(body)


def build_event(code: str, details: dict | None = None) -> bytes:
    layout = EVENT_DETAILS.get(code)
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
