"""Data that must survive a session change: reference laps, outline, setups, and what the iPad shows."""
import asyncio
import gzip
import json

from race_engineer import analysis
from race_engineer.server import Hub, Recorder, replay
from race_engineer.simulator import Simulator
from race_engineer.state import TRACE_BUCKET_M, RaceState
from race_engineer.storage import Store
from race_engineer.udp.packets import parse_packet


def drive(state: RaceState, laps: int = 3, seed: int = 7) -> Simulator:
    sim = Simulator(laps=laps, seed=seed)
    while not sim.done:
        for datagram in sim.step(0.05):
            state.apply(*parse_packet(datagram))
    return sim


def test_store_keeps_reference_outline_and_setups_on_disk(tmp_path):
    state = RaceState(Store(tmp_path))
    drive(state)
    saved = json.loads((tmp_path / "tracks" / "11.json").read_text("utf-8"))
    assert saved["reference"]["ms"] == state.player.best_ms
    assert sum(p is not None for p in saved["outline"]["pts"]) > 0.9 * len(saved["outline"]["pts"])
    assert saved["setups"][0]["setup"]["front_wing"] == 28 and saved["setups"][0]["laps"] >= 2

    # A brand-new app run at the same track starts with the map and the reference lap already there
    fresh = RaceState(Store(tmp_path))
    sim = Simulator(laps=3, seed=8)
    for _ in range(40):
        for datagram in sim.step(0.05):
            fresh.apply(*parse_packet(datagram))
    assert fresh.traces["ref"]["ms"] == state.player.best_ms
    assert fresh.outline_filled > 0.9 * len(fresh.outline)
    assert fresh.reference_trace() is fresh.traces["ref"]


def test_next_session_at_same_track_keeps_last_and_best_laps():
    state = RaceState()
    drive(state, seed=7)
    best = state.traces["best"]
    sim = Simulator(laps=3, seed=9)  # new session uid, same track
    for _ in range(40):
        for datagram in sim.step(0.05):
            state.apply(*parse_packet(datagram))
    assert state.lap_log == [] and state.traces["best"] is best


def test_hub_shows_last_session_while_new_one_has_no_data():
    async def scenario():
        hub = Hub(Store(None))
        sent = []

        class FakeWs:
            async def send_str(self, text):
                sent.append(json.loads(text))

        hub.clients.add(FakeWs())
        sim = Simulator(laps=2)
        while not sim.done:
            for datagram in sim.step(0.05):
                hub.feed(datagram)
        await hub._slow_tick()
        first = [m for m in sent if m["t"] == "slow"][-1]["d"]
        assert first["ready"] and not first.get("stale")
        # A new session starts: only a header-level packet so far, no lap data yet
        new = Simulator(laps=2, seed=99)
        hub.feed(new.step(0.05)[0])
        await hub._slow_tick()
        snap = [m for m in sent if m["t"] == "slow"][-1]["d"]
        assert snap["ready"] and snap["stale"] and snap["session"]["track"] == "Monza"

    asyncio.run(scenario())


def test_record_and_replay_roundtrip(tmp_path):
    rec = Recorder(tmp_path)
    sim = Simulator(laps=1)
    for _ in range(60):
        for datagram in sim.step(0.05):
            rec.write(datagram)
    rec.close()
    with gzip.open(rec.path, "rb") as f:
        assert f.read(12)  # non-empty

    async def scenario():
        hub = Hub(Store(None))
        await replay(hub, rec.path, speed=1000)
        return hub

    hub = asyncio.run(scenario())
    assert hub.state.player.lap and hub.state.session["track_id"] == 11


def test_corner_detection_and_comparison():
    state = RaceState()
    drive(state, laps=4)
    ref, last = state.reference_trace(), state.traces["last"]
    corners = analysis.detect_corners(ref, TRACE_BUCKET_M)
    assert len(corners) >= 2
    assert all(a["apex_m"] < b["apex_m"] for a, b in zip(corners, corners[1:]))
    assert all(c["start_m"] <= c["apex_m"] <= c["end_m"] for c in corners)
    rows = analysis.compare_corners(ref, last, corners, TRACE_BUCKET_M)
    assert rows and all(isinstance(r["tip"], str) and r["tip"] for r in rows)
    assert analysis.compare_corners(ref, ref, corners, TRACE_BUCKET_M)[0]["loss_ms"] == 0
    assert analysis.corner_name_at(corners, corners[0]["apex_m"]) == "Z1"
