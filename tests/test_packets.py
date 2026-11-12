import struct

import pytest

from race_engineer.simulator import PLAYER_IDX, Simulator
from race_engineer.udp import constants as C
from race_engineer.udp import packets as P


def collect(fmt=2025, steps=400):
    sim, seen = Simulator(laps=3, fmt=fmt), {}
    for _ in range(steps):
        for datagram in sim.step(0.05):
            seen.setdefault(datagram[6], datagram)  # byte 6 of the header is the packet id
    return seen


@pytest.mark.parametrize("fmt", [2025, 2026])
def test_simulated_datagrams_match_official_sizes(fmt):
    seen = collect(fmt)
    expected = {0, 1, 2, 3, 4, 5, 6, 7, 10, 11, 12, 13} | ({16} if fmt == 2026 else set())
    assert expected <= set(seen)
    for pid, datagram in seen.items():
        assert len(datagram) == P.SPECS[fmt].sizes[pid], f"format {fmt}, packet {pid}"


@pytest.mark.parametrize("fmt", [2025, 2026])
def test_roundtrip_keeps_values(fmt):
    seen = collect(fmt)
    header, body = P.parse_packet(seen[C.PARTICIPANTS])
    assert header["packet_format"] == fmt and header["player_idx"] == PLAYER_IDX
    assert body["num_active"] == 20 and len(body["cars"]) == P.SPECS[fmt].num_cars
    assert body["cars"][0]["name"] == "VERSTAPPEN"
    assert body["cars"][0]["team_id"] == (478 if fmt == 2026 else 2)  # Red Bull, 2026 ids are uint16
    _, session = P.parse_packet(seen[C.SESSION])
    assert session["total_laps"] == 3 and session["session_type"] == 15
    assert len(session["marshal_zones"]) == 16 and len(session["forecast"]) == 5
    assert session["s3_start"] > session["s2_start"] > 0
    assert bool(session["aero_zones"]) == (fmt == 2026)
    _, setups = P.parse_packet(seen[C.CAR_SETUPS])
    assert setups["cars"][PLAYER_IDX]["front_wing"] == 28
    assert setups["cars"][PLAYER_IDX]["front_camber"] == pytest.approx(-3.2)
    _, tel = P.parse_packet(seen[C.CAR_TELEMETRY])
    assert tel["cars"][PLAYER_IDX]["engine_temp"] == 108  # uint16 in 2025, uint8 in 2026
    _, mex = P.parse_packet(seen[C.MOTION_EX])
    assert len(mex["slip_angle"]) == 4


def test_2026_g_forces_are_normalised_to_float_g():
    spec = P.SPECS[2026]
    data = P.build_header(C.MOTION, 1, 0.0, 1, 0, 2026) + P.build_motion(spec, [{"g_lat": 3.25, "g_lon": -4.5}])
    _, body = P.parse_packet(data)
    assert body["cars"][0]["g_lat"] == pytest.approx(3.25)
    assert body["cars"][0]["g_lon"] == pytest.approx(-4.5)
    assert "g_lat_q" not in body["cars"][0]


def test_event_details():
    spec = P.SPECS[2025]
    datagram = P.build_header(C.EVENT, 1, 0.0, 1, 0) + P.build_event(
        spec, "PENA", {"penalty_type": 4, "vehicle_idx": 3, "time": 5, "lap_num": 7})
    assert len(datagram) == spec.sizes[C.EVENT]
    _, event = P.parse_packet(datagram)
    assert event == {"code": "PENA", "penalty_type": 4, "infringement_type": 0, "vehicle_idx": 3,
                     "other_vehicle_idx": 0, "time": 5, "lap_num": 7, "places_gained": 0}
    coll = P.build_header(C.EVENT, 1, 0.0, 1, 0, 2026) + P.build_event(
        P.SPECS[2026], "COLL", {"vehicle1_idx": 1, "vehicle2_idx": 2, "severity": 3})
    assert P.parse_packet(coll)[1]["severity"] == 3


def test_hostile_input_is_rejected_not_crashing():
    assert P.parse_packet(b"") is None
    assert P.parse_packet(b"\x00" * 10) is None
    good = collect(steps=5)[C.LAP_DATA]
    assert P.parse_packet(good[:200]) is None  # truncated datagram
    with pytest.raises(P.UnsupportedFormat):
        P.parse_packet(struct.pack("<H", 2024) + good[2:])
    assert P.parse_packet(good[:6] + bytes([99]) + good[7:]) is None  # unknown packet id
    # A 2025 datagram labelled as 2026 is too short for the 24-car layout: ignored, not misread
    assert P.parse_packet(struct.pack("<H", 2026) + good[2:]) is None
