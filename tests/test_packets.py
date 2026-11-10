import struct

import pytest

from race_engineer.simulator import PLAYER_IDX, Simulator
from race_engineer.udp import constants as C
from race_engineer.udp import packets as P


def collect(steps=400):
    sim, seen = Simulator(laps=3), {}
    for _ in range(steps):
        for datagram in sim.step(0.05):
            seen.setdefault(datagram[6], datagram)  # byte 6 of the header is the packet id
    return seen


def test_simulated_datagrams_match_official_sizes():
    seen = collect()
    assert {0, 1, 2, 3, 4, 5, 6, 7, 10, 11, 12} <= set(seen)
    for pid, datagram in seen.items():
        assert len(datagram) == C.PACKET_SIZES[pid], f"packet {pid}"


def test_roundtrip_keeps_values():
    seen = collect()
    header, body = P.parse_packet(seen[C.PARTICIPANTS])
    assert header["packet_format"] == 2025 and header["player_idx"] == PLAYER_IDX
    assert body["num_active"] == 20
    assert body["cars"][0]["name"] == "VERSTAPPEN"
    _, session = P.parse_packet(seen[C.SESSION])
    assert session["total_laps"] == 3 and session["session_type"] == 15
    assert len(session["marshal_zones"]) == 16 and len(session["forecast"]) == 5
    assert session["s3_start"] > session["s2_start"] > 0
    _, setups = P.parse_packet(seen[C.CAR_SETUPS])
    assert setups["cars"][PLAYER_IDX]["front_wing"] == 28
    assert setups["cars"][PLAYER_IDX]["front_camber"] == pytest.approx(-3.2)


def test_event_details():
    datagram = P.build_header(C.EVENT, 1, 0.0, 1, 0) + P.build_event(
        "PENA", {"penalty_type": 4, "vehicle_idx": 3, "time": 5, "lap_num": 7})
    assert len(datagram) == C.PACKET_SIZES[C.EVENT]
    _, event = P.parse_packet(datagram)
    assert event == {"code": "PENA", "penalty_type": 4, "infringement_type": 0, "vehicle_idx": 3,
                     "other_vehicle_idx": 0, "time": 5, "lap_num": 7, "places_gained": 0}


def test_hostile_input_is_rejected_not_crashing():
    assert P.parse_packet(b"") is None
    assert P.parse_packet(b"\x00" * 10) is None
    good = collect(5)[C.LAP_DATA]
    assert P.parse_packet(good[:200]) is None  # truncated datagram
    with pytest.raises(P.UnsupportedFormat):
        P.parse_packet(struct.pack("<H", 2024) + good[2:])
    assert P.parse_packet(good[:6] + bytes([99]) + good[7:]) is None  # unknown packet id
