"""Race strategy maths: tyre life projection, pit window, fuel and rejoin estimate."""
from __future__ import annotations

from .state import RaceState
from .udp import constants as C

WEAR_CLIFF_PCT = 70.0       # beyond this the tyre loses pace quickly and puncture risk grows
WEAR_COMFORT_PCT = 60.0     # we aim to stop around here
PIT_LOSS_GREEN_S = 22.0     # typical time lost by a stop under green
PIT_LOSS_SC_S = 12.0        # under (virtual) safety car the field is slow, the stop is cheaper


def compute(state: RaceState) -> dict:
    me, session = state.player, state.session
    if not me.lap or not session:
        return {}
    wear_now = max(me.damage.get("tyre_wear", [0.0]))
    rate = state.wear_rate()
    laps_left = state.laps_remaining()
    lap = me.lap["lap_num"]
    sc_active = session.get("safety_car", 0) in (1, 2)
    out = {
        "lapsLeft": round(laps_left, 1), "wearNow": round(wear_now, 1),
        "wearRate": round(rate, 2) if rate is not None else None,
        "fuelLaps": round(me.status.get("fuel_remaining_laps", 0.0), 2),
        "fuelPerLap": _round(state.fuel_per_lap(), 2),
        "gameIdealLap": session.get("pit_ideal_lap", 0), "gameLatestLap": session.get("pit_latest_lap", 0),
        "gameRejoinPos": session.get("pit_rejoin_pos", 0),
        "pitLoss": PIT_LOSS_SC_S if sc_active else PIT_LOSS_GREEN_S,
        "wearAtFinish": None, "lapsToCliff": None, "pitNeeded": False, "pitLap": None,
        "rejoinPos": None, "nextCompound": None, "twoCompoundsDone": None, "stints": _stints(state),
    }
    if not state.is_race:
        return out

    if rate:
        out["wearAtFinish"] = round(wear_now + rate * laps_left, 1)
        out["lapsToCliff"] = round(max(0.0, (WEAR_CLIFF_PCT - wear_now) / rate), 1)
    dry_used = {s["visual"] for s in me.stints if s["visual"] not in C.WET_COMPOUNDS}
    wet_used = any(s["visual"] in C.WET_COMPOUNDS for s in me.stints)
    out["twoCompoundsDone"] = wet_used or len(dry_used) >= 2
    # The mandatory-compound rule only applies to races long enough to have a pit window
    must_stop = not out["twoCompoundsDone"] and session.get("pit_ideal_lap", 0) > 0
    tyres_wont_last = rate is not None and out["wearAtFinish"] > WEAR_CLIFF_PCT + 5
    out["pitNeeded"] = bool(laps_left > 1 and (must_stop or tyres_wont_last))

    if out["pitNeeded"]:
        if rate and tyres_wont_last:
            target = lap + max(0, int((WEAR_COMFORT_PCT - wear_now) / rate))
        else:
            target = session.get("pit_ideal_lap", 0) or lap
        latest = session.get("pit_latest_lap", 0)
        if latest:
            target = min(target, latest)
        out["pitLap"] = int(max(lap, min(target, session.get("total_laps", lap) - 1)))
        out["nextCompound"] = _next_compound(state, laps_left - max(0, out["pitLap"] - lap))
    out["rejoinPos"] = _rejoin_position(state, out["pitLoss"])
    return out


def _round(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


def _rejoin_position(state: RaceState, pit_loss_s: float) -> int | None:
    """Where we would come out if we pitted now: everybody within the pit loss gets past."""
    me = state.player
    cars = [c for c in state.active_cars() if c.lap.get("result_status") == 2]
    if me not in cars:
        return None
    my_gap = me.gap_leader_ms + pit_loss_s * 1000
    return 1 + sum(1 for c in cars if c is not me and c.gap_leader_ms < my_gap and c.lap["pit_status"] == 0)


def _next_compound(state: RaceState, laps_after_stop: float) -> dict | None:
    """Pick the fastest available set that survives to the flag (wet tyres when it rains)."""
    raining = state.session.get("weather", 0) >= 3
    candidates = []
    for i, tyre_set in enumerate(state.tyre_sets):
        if not tyre_set["available"] or tyre_set["fitted"] or i == state.fitted_set:
            continue
        if (tyre_set["visual"] in C.WET_COMPOUNDS) != raining:
            continue
        lasts = tyre_set["life_span"] >= laps_after_stop
        candidates.append((not lasts, tyre_set["lap_delta_ms"], tyre_set["wear"], tyre_set))
    if not candidates:
        return None
    best = min(candidates, key=lambda c: c[:3])[3]
    short, name, colour = C.VISUAL_COMPOUNDS.get(best["visual"], ("?", "neznámé", "#888"))
    return {"short": short, "name": name, "color": colour, "life": best["life_span"], "wear": best["wear"]}


def _stints(state: RaceState) -> list[dict]:
    out, start = [], 1
    for stint in state.player.stints:
        short, _, colour = C.VISUAL_COMPOUNDS.get(stint["visual"], ("?", "", "#888"))
        end = stint["end_lap"] if stint["end_lap"] != 255 else state.player.lap.get("lap_num", start)
        out.append({"short": short, "color": colour, "from": start, "to": end})
        start = end + 1
    return out
