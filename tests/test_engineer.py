import json

import pytest

from race_engineer import snapshots, strategy
from race_engineer.engineer import setup_advisor
from race_engineer.engineer.engine import fmt_lap, plural, to_speech
from race_engineer.engineer.rules import Engineer
from race_engineer.simulator import Simulator
from race_engineer.state import RaceState
from race_engineer.udp import constants as C
from race_engineer.udp.packets import build_event, build_header, parse_packet


@pytest.fixture(scope="module")
def race():
    """Runs a full simulated race through parser -> state -> engineer."""
    sim, state, messages = Simulator(laps=5, player_pit_lap=3), RaceState(), []
    engineer = Engineer(state, messages.append, clock=lambda: sim.t)
    engineer.verbosity = 3
    mid = {}
    while not sim.done:
        for datagram in sim.step(0.05):
            engineer.on_events(state.apply(*parse_packet(datagram)))
        if sim.frame % 4 == 0:
            engineer.tick()
        if sim.frame == 2400:
            mid = {"slow": snapshots.slow(state), "fast": snapshots.fast(state), "plan": strategy.compute(state)}
    return {"state": state, "messages": messages, "engineer": engineer, "mid": mid, "sim": sim}


def texts(race, cat=None):
    return [m["text"] for m in race["messages"] if cat is None or m["cat"] == cat]


def test_state_tracks_the_race(race):
    state = race["state"]
    assert state.is_race and state.num_active == 20
    assert [rec["lap"] for rec in state.lap_log] == [1, 2, 3, 4, 5]
    assert state.player.best_ms > 0 and state.traces["best"] is not None
    assert state.outline_filled > 0.9 * len(state.outline)
    assert state.player.lap["num_pit_stops"] == 1
    assert state.final is not None


def test_snapshots_are_json_and_complete(race):
    slow, fast = race["mid"]["slow"], race["mid"]["fast"]
    json.dumps(slow), json.dumps(fast)
    assert slow["ready"] and len(slow["cars"]) == 20
    assert [c["pos"] for c in slow["cars"]] == list(range(1, 21))
    assert sum(c["me"] for c in slow["cars"]) == 1
    assert slow["setupView"] and slow["player"]["tyreSets"]
    assert 0 <= fast["thr"] <= 1 and len(fast["tI"]) == 4
    assert race["mid"]["plan"]["lapsLeft"] > 0


def test_engineer_covers_the_key_moments(race):
    everything = " | ".join(texts(race))
    for expected in ("Rádio check", "Virtuální safety car", "Zelená, zelená", "Žluté vlajky", "Box, box",
                     "Pit limiter", "Zastávka", "Poškození: přední křídlo vlevo 22 %", "Varování za limity trati",
                     "Poslední kolo", "Šachovnicová vlajka"):
        assert expected in everything, expected
    assert len(texts(race, "lap")) >= 3


def test_critical_messages_are_not_delayed(race):
    by_text = {m["text"]: m for m in race["messages"]}
    assert by_text["Virtuální safety car. Hlídej deltu, nepředjíždět."]["prio"] == 1


def test_driver_questions_and_wheel_buttons(race):
    engineer, state, messages = race["engineer"], race["state"], race["messages"]
    for query in ("gaps", "tyres", "fuel", "strategy", "weather", "damage", "pace", "radio_check", "nonsense"):
        before = len(messages)
        engineer.answer(query)
        assert len(messages) == before + 1 and messages[-1]["cat"] == "reply"
    # "UDP Action 2" pressed on the wheel -> tyre report
    press = build_header(C.EVENT, state.session_uid, 0.0, 1, state.player_idx) + build_event(
        "BUTN", {"buttons": C.UDP_ACTION_BITS[2]})
    race["sim"].t += 5
    engineer.on_events(state.apply(*parse_packet(press)))
    assert messages[-1]["text"].startswith("Pneumatiky")


def test_verbosity_filters_chatter():
    state, out = RaceState(), []
    engineer = Engineer(state, out.append, clock=lambda: 0.0)
    engineer.verbosity = 1
    assert not engineer.say("a", "pokec", prio=3)
    assert not engineer.say("b", "normál", prio=2)
    assert engineer.say("c", "kritické", prio=1) and out[-1]["text"] == "kritické"
    assert not engineer.say("c", "kritické znovu", prio=1)  # cooldown


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
    assert to_speech("Kolo 1:23.456, 40 %") == "Kolo 1 23,4, 40 procent"
