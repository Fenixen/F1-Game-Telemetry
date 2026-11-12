import json

import pytest

from race_engineer import snapshots, strategy
from race_engineer.engineer import setup_advisor
from race_engineer.engineer.engine import fmt_gap, fmt_lap, plural, to_speech
from race_engineer.engineer.rules import Engineer
from race_engineer.simulator import Simulator
from race_engineer.state import RaceState
from race_engineer.udp import constants as C
from race_engineer.udp import packets as P
from race_engineer.udp.packets import parse_packet


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture(scope="module", params=[2025, 2026])
def race(request):
    """Runs a full simulated race through parser -> state -> engineer."""
    sim, state, messages, clock = Simulator(laps=5, player_pit_lap=3, fmt=request.param), RaceState(), [], Clock()
    engineer = Engineer(state, messages.append, clock=clock)
    engineer.rng.seed(3)
    engineer.verbosity = 3
    mid = {}
    while not sim.done:
        for datagram in sim.step(0.05):
            engineer.on_events(state.apply(*parse_packet(datagram)))
        clock.t = sim.t
        if sim.frame % 4 == 0:
            engineer.tick()
        if sim.frame == 2400:
            mid = {"slow": snapshots.slow(state), "fast": snapshots.fast(state), "plan": strategy.compute(state)}
    for _ in range(20):  # let delayed reports (lap, chequered flag) go out
        clock.t += 0.5
        engineer.tick()
    return {"state": state, "messages": messages, "engineer": engineer, "mid": mid, "clock": clock,
            "fmt": request.param}


def keys(race):
    return [m["key"] for m in race["messages"]]


def test_state_tracks_the_race(race):
    state = race["state"]
    assert state.is_race and state.num_active == 20 and state.fmt == race["fmt"]
    assert [rec["lap"] for rec in state.lap_log] == [1, 2, 3, 4, 5]
    assert state.player.best_ms > 0 and state.traces["best"] is not None
    assert state.outline_filled > 0.9 * len(state.outline)
    assert state.player.lap["num_pit_stops"] == 1
    assert state.final is not None
    assert state.player.team[0] == "Williams"
    assert bool(state.aero) == (race["fmt"] == 2026)


def test_snapshots_are_json_and_complete(race):
    slow, fast = race["mid"]["slow"], race["mid"]["fast"]
    json.dumps(slow), json.dumps(fast)
    assert slow["ready"] and len(slow["cars"]) == 20
    assert [c["pos"] for c in slow["cars"]] == list(range(1, 21))
    assert sum(c["me"] for c in slow["cars"]) == 1
    assert slow["setupView"] and slow["player"]["tyreSets"]
    assert 0 <= fast["thr"] <= 1 and len(fast["tI"]) == 4
    assert (fast["aero"] is not None) == (race["fmt"] == 2026)
    assert race["mid"]["plan"]["lapsLeft"] > 0
    traces = snapshots.traces(race["state"])
    json.dumps(traces)
    assert traces["corners"] and traces["corners"][0]["name"] == "Z1"
    info = snapshots.setup_info(race["state"])
    json.dumps(info)
    assert info["rec"]["items"][0]["issue"] == "understeer_entry"  # the simulator's car understeers on entry
    assert info["library"][0]["current"]


def test_engineer_covers_the_key_moments(race):
    said = keys(race)
    for key in ("greet", "vsc_deploy", "green", "flag", "box", "pit_in", "pit_out", "dmg_fl_wing", "cc",
                "laps_left", "finish", "lap"):
        assert key in said, key
    finish = next(m for m in race["messages"] if m["key"] == "finish")
    assert f"P{race['state'].player.position}" in finish["text"] and finish["prio"] == 1
    assert said.count("finish") == 1  # chequered flag and results screen do not both speak


def test_texts_read_naturally(race):
    texts = " ".join(m["text"] for m in race["messages"])
    assert "středníchch" not in texts and "  " not in texts
    assert "0,0" not in texts  # tiny gaps are 'pár setin', not a zero


def test_driver_questions_and_wheel_buttons(race):
    engineer, state, messages, clock = race["engineer"], race["state"], race["messages"], race["clock"]
    for query in ("gaps", "tyres", "fuel", "strategy", "weather", "damage", "pace", "radio_check", "nonsense"):
        before = len(messages)
        clock.t += 2
        engineer.answer(query)
        assert len(messages) == before + 1 and messages[-1]["cat"] == "reply"
    spec = P.SPECS[race["fmt"]]
    press = P.build_header(C.EVENT, state.session_uid, 0.0, 1, state.player_idx, spec.fmt) + P.build_event(
        spec, "BUTN", {"buttons": C.UDP_ACTION_BITS[2]})
    clock.t += 5
    engineer.on_events(state.apply(*parse_packet(press)))
    assert messages[-1]["key"] == "q_tyres"


# ---------------------------------------------------------------- scripted scenarios

class Session:
    """Builds the minimum packet stream for a hand-made scenario."""

    def __init__(self, session_type: int, fmt: int = 2025):
        self.spec, self.state, self.out, self.clock = P.SPECS[fmt], RaceState(), [], Clock()
        self.engineer = Engineer(self.state, self.out.append, clock=self.clock)
        self.session_type = session_type
        self.laps = [{"lap_num": 1, "position": i + 1, "result_status": 2, "driver_status": 4, "grid_position": i + 1,
                      "lap_distance": 100.0} for i in range(4)]

    def send(self, pid: int, body: bytes) -> None:
        header = P.build_header(pid, 42, self.clock.t, 1, 0, self.spec.fmt)
        self.engineer.on_events(self.state.apply(*parse_packet(header + body)))

    def boot(self, safety_car: int = 0) -> None:
        names = [{"name": n, "team_id": i} for i, n in enumerate(("PLAYER", "NORRIS", "LECLERC", "RUSSELL"))]
        self.send(C.PARTICIPANTS, P.build_cars(self.spec, self.spec.participant, names, prefix=bytes([4])))
        self.session(safety_car)
        self.send(C.CAR_STATUS, P.build_cars(self.spec, self.spec.status, [{"visual_compound": 16}] * 4))
        self.send(C.CAR_TELEMETRY, P.build_cars(self.spec, self.spec.telemetry,
                                                [{"speed": 250, "throttle": 1.0}] * 4, suffix=b"\0\0\0"))
        self.send(C.CAR_DAMAGE, P.build_cars(self.spec, P.DAMAGE, [{}] * 4))
        self.lap_data()
        self.tick()

    def session(self, safety_car: int = 0, red_flags: int = 0) -> None:
        self.send(C.SESSION, P.build_session(self.spec, {
            "session_type": self.session_type, "track_id": 11, "track_length": 5000, "total_laps": 10,
            "time_left": 600, "safety_car": safety_car, "num_red_flag_periods": red_flags}))

    def lap_data(self) -> None:
        self.send(C.LAP_DATA, P.build_cars(self.spec, P.LAP, self.laps, suffix=b"\0\0"))

    def event(self, code: str, **details) -> None:
        self.send(C.EVENT, P.build_event(self.spec, code, details))

    def best_lap(self, idx: int, ms: int) -> None:
        self.send(C.SESSION_HISTORY, P.build_history({"car_idx": idx, "best_lap_num": 1, "laps": [{"lap_ms": ms}]}))

    def tick(self, seconds: float = 0.2) -> None:
        self.clock.t += seconds
        self.engineer.tick()

    def keys(self) -> list[str]:
        return [m["key"] for m in self.out]


def test_qualifying_lap_reports_position_after_the_game_resorts():
    q = Session(session_type=7)  # Q3
    for car, pos in zip(q.laps, (3, 1, 2, 4)):  # player starts the lap in P3
        car["position"] = pos
    q.boot()
    q.best_lap(1, 80000)
    q.best_lap(2, 80500)
    q.laps[0] |= {"driver_status": 1}
    q.lap_data()
    # The lap ends, but the game still shows the old position for a moment
    q.laps[0] |= {"lap_num": 2, "last_lap_ms": 79500}
    q.lap_data()
    q.tick(0.5)
    assert "lap" not in q.keys()  # not reported yet: the position would be wrong
    q.laps[0]["position"], q.laps[1]["position"], q.laps[2]["position"] = 1, 2, 3
    q.lap_data()
    for _ in range(30):  # normal calls keep a few seconds of spacing after the previous one
        q.tick()
    report = next(m["text"] for m in q.out if m["key"] == "lap")
    assert "z P3 na P1" in report or "P3 na P1" in report


@pytest.mark.parametrize("fmt", [2025, 2026])
def test_safety_car_and_red_flag_from_events_alone(fmt):
    r = Session(session_type=15, fmt=fmt)
    r.boot()
    r.event("SCAR", safety_car_type=1, event_type=0)
    assert r.out[-1]["key"] == "sc_deploy" and "Safety car" in r.out[-1]["text"]
    r.tick(5)
    r.event("SCAR", safety_car_type=1, event_type=1)
    assert r.out[-1]["key"] == "sc_in"
    r.tick(5)
    r.event("SCAR", safety_car_type=1, event_type=3)
    assert r.out[-1]["key"] == "green"
    r.tick(5)
    r.event("SCAR", safety_car_type=2, event_type=0)
    assert "VSC" in r.out[-1]["text"]
    r.tick(30)
    r.event("RDFL")
    assert r.out[-1]["key"] == "red" and "Červená" in r.out[-1]["text"]


def test_safety_car_from_session_status_without_events_is_said_once():
    r = Session(session_type=15)
    r.boot(safety_car=0)
    r.session(safety_car=1)
    r.tick()
    r.event("SCAR", safety_car_type=1, event_type=0)  # the event arriving too must not repeat the call
    assert r.keys().count("sc_deploy") == 1
    r.session(safety_car=0)
    r.tick(25)
    assert r.keys()[-1] == "green"
    r.session(red_flags=1)
    r.tick(5)
    assert r.keys()[-1] == "red"


def test_chequered_flag_gets_an_immediate_finish_call():
    r = Session(session_type=15)
    r.boot()
    r.laps[0] |= {"position": 1, "grid_position": 5}
    r.lap_data()
    r.event("CHQF")
    for _ in range(8):
        r.tick()
    finish = next(m for m in r.out if m["key"] == "finish")
    assert finish["prio"] == 1 and "P5" in finish["text"]  # winner from P5 mentions the places gained


def test_verbosity_filters_chatter():
    state, out = RaceState(), []
    engineer = Engineer(state, out.append, clock=lambda: 0.0)
    engineer.verbosity = 1
    assert not engineer.say("a", "pokec", prio=3)
    assert not engineer.say("b", "normál", prio=2)
    assert engineer.say("c", "kritické", prio=1) and out[-1]["text"] == "kritické"
    assert not engineer.say("c", "kritické znovu", prio=1)  # cooldown


def test_phrase_variants_do_not_repeat_back_to_back():
    engineer = Engineer(RaceState(), lambda m: None)
    picks = [engineer.pick("k", ["a", "b", "c"]) for _ in range(30)]
    assert all(x != y for x, y in zip(picks, picks[1:]))


def test_setup_advice_is_clamped_to_garage_limits():
    advice = setup_advisor.advise({"front_wing": 49, "off_throttle": 10, "brake_bias": 56, "front_arb": 5},
                                  "understeer_entry")
    changes = {c["key"]: c for c in advice["changes"]}
    assert changes["front_wing"]["to"] == "50"
    assert "off_throttle" not in changes  # already at the minimum
    assert setup_advisor.advise({}, "understeer_entry")["changes"][0]["delta"] == "+2"
    assert setup_advisor.advise({}, "unknown")["changes"] == []


def test_czech_formatting():
    assert fmt_lap(83456) == "1:23.456"
    assert plural(1, "kolo", "kola", "kol") == "1 kolo"
    assert plural(3, "kolo", "kola", "kol") == "3 kola"
    assert plural(7, "kolo", "kola", "kol") == "7 kol"
    assert fmt_gap(1234) == "1,2 vteřiny" and fmt_gap(1234, False) == "1,2" and fmt_gap(40) == "pár setin"
    assert to_speech("Kolo 1:23.456, 40 %") == "Kolo 1 23,4, 40 procent"
