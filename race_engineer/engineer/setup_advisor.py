"""Setup engineer: turns driver feedback + telemetry into concrete setup changes.

The game does not accept setup changes over UDP (the feed is read-only), so the
advisor proposes values and the driver enters them in the garage.
"""
from __future__ import annotations

from ..state import RaceState

# key -> (label, unit, min, max, decimals); ranges are approximate garage limits (used only to clamp advice)
PARAMS = {
    "front_wing": ("Přední křídlo", "", 0, 50, 0),
    "rear_wing": ("Zadní křídlo", "", 0, 50, 0),
    "on_throttle": ("Diferenciál na plynu", "%", 10, 100, 0),
    "off_throttle": ("Diferenciál bez plynu", "%", 10, 100, 0),
    "front_camber": ("Odklon vpředu", "°", -3.5, -2.5, 2),
    "rear_camber": ("Odklon vzadu", "°", -2.2, -1.0, 2),
    "front_toe": ("Sbíhavost vpředu", "°", 0.0, 0.5, 2),
    "rear_toe": ("Sbíhavost vzadu", "°", 0.0, 0.5, 2),
    "front_suspension": ("Odpružení vpředu", "", 1, 41, 0),
    "rear_suspension": ("Odpružení vzadu", "", 1, 41, 0),
    "front_arb": ("Stabilizátor vpředu", "", 1, 21, 0),
    "rear_arb": ("Stabilizátor vzadu", "", 1, 21, 0),
    "front_ride_height": ("Světlá výška vpředu", "", 10, 40, 0),
    "rear_ride_height": ("Světlá výška vzadu", "", 40, 100, 0),
    "brake_pressure": ("Brzdný tlak", "%", 80, 100, 0),
    "brake_bias": ("Rozdělení brzd dopředu", "%", 50, 70, 0),
    "engine_braking": ("Brzdění motorem", "%", 0, 100, 0),
    "fl_pressure": ("Tlak PL", "psi", 22.5, 29.5, 1),
    "fr_pressure": ("Tlak PP", "psi", 22.5, 29.5, 1),
    "rl_pressure": ("Tlak ZL", "psi", 20.5, 26.5, 1),
    "rr_pressure": ("Tlak ZP", "psi", 20.5, 26.5, 1),
    "fuel_load": ("Palivo", "kg", 0, 110, 1),
}

GROUPS = [
    ("Aerodynamika", ["front_wing", "rear_wing"]),
    ("Převody", ["on_throttle", "off_throttle", "engine_braking"]),
    ("Geometrie", ["front_camber", "rear_camber", "front_toe", "rear_toe"]),
    ("Zavěšení", ["front_suspension", "rear_suspension", "front_arb", "rear_arb",
                  "front_ride_height", "rear_ride_height"]),
    ("Brzdy", ["brake_pressure", "brake_bias"]),
    ("Pneumatiky", ["fl_pressure", "fr_pressure", "rl_pressure", "rr_pressure"]),
    ("Palivo", ["fuel_load"]),
]

# issue -> (label, [(param, delta, why)]) ordered from the most effective change
ISSUES = {
    "understeer_entry": ("Nedotáčivost na vjezdu", [
        ("front_wing", +2, "víc přítlaku na přední nápravě pomůže autu zatočit"),
        ("off_throttle", -10, "volnější diferenciál bez plynu nechá auto snáz rotovat"),
        ("brake_bias", -2, "méně brzd vpředu = méně přetížené přední pneumatiky při trail brakingu"),
        ("front_arb", -1, "měkčí přední stabilizátor dá přední nápravě víc gripu"),
    ]),
    "understeer_mid": ("Nedotáčivost v pomalých zatáčkách", [
        ("front_arb", -2, "měkčí přední stabilizátor = víc mechanického gripu vpředu"),
        ("rear_arb", +1, "tužší zadek pomůže autu rotovat"),
        ("front_suspension", -2, "měkčí přední pružiny zlepší přilnavost v pomalých zatáčkách"),
        ("front_toe", +0.02, "víc rozbíhavosti zrychlí reakci na natočení volantu"),
    ]),
    "understeer_fast": ("Nedotáčivost v rychlých zatáčkách", [
        ("front_wing", +3, "v rychlých zatáčkách rozhoduje aero, přidej přední křídlo"),
        ("front_ride_height", -1, "nižší předek posune aerodynamickou rovnováhu dopředu"),
        ("front_camber", -0.1, "větší negativní odklon zvýší boční grip v dlouhých zatáčkách"),
    ]),
    "oversteer_entry": ("Přetáčivost na vjezdu", [
        ("off_throttle", +10, "pevnější diferenciál bez plynu stabilizuje zadek při brzdění"),
        ("brake_bias", +2, "víc brzd dopředu uklidní zadní nápravu"),
        ("engine_braking", -10, "méně brzdění motorem = menší riziko zablokování zadních kol"),
        ("rear_wing", +2, "víc zadního přítlaku pro stabilitu"),
    ]),
    "oversteer_exit": ("Přetáčivost na výjezdu / prokluz", [
        ("on_throttle", -10, "volnější diferenciál na plynu zlepší trakci a šetří zadní pneumatiky"),
        ("rear_arb", -2, "měkčí zadní stabilizátor = lepší trakce"),
        ("rear_suspension", -2, "měkčí zadní pružiny pomohou dostat výkon na zem"),
        ("rear_toe", +0.03, "víc sbíhavosti vzadu stabilizuje auto při akceleraci"),
    ]),
    "oversteer_mid": ("Přetáčivost uprostřed zatáček", [
        ("rear_arb", -2, "měkčí zadní stabilizátor dá zadní nápravě víc gripu"),
        ("front_arb", +1, "tužší předek přesune část náklonu dopředu a uklidní zadek"),
        ("rear_wing", +2, "víc zadního přítlaku stabilizuje auto"),
        ("rear_toe", +0.03, "víc sbíhavosti vzadu = stabilnější zadní náprava"),
    ]),
    "oversteer_fast": ("Přetáčivost v rychlých zatáčkách", [
        ("rear_wing", +3, "víc zadního přítlaku stabilizuje auto ve vysoké rychlosti"),
        ("front_wing", -1, "posun aero rovnováhy dozadu"),
        ("rear_ride_height", -2, "nižší zadek zvýší přítlak z difuzoru"),
    ]),
    "slow_straights": ("Pomalý na rovinkách", [
        ("rear_wing", -3, "menší odpor = vyšší maximálka"),
        ("front_wing", -2, "ubrat i vpředu, ať zůstane rovnováha"),
        ("rear_ride_height", -2, "nižší auto má menší odpor vzduchu"),
    ]),
    "front_tyres_hot": ("Přehřívání / opotřebení předních pneumatik", [
        ("front_camber", +0.1, "menší negativní odklon rozloží zátěž po celé šířce běhounu"),
        ("front_toe", -0.02, "menší rozbíhavost = méně smýkání a tepla na rovinkách"),
        ("fl_pressure", -0.5, "nižší tlak sníží teplotu jádra pneumatiky"),
        ("fr_pressure", -0.5, "nižší tlak sníží teplotu jádra pneumatiky"),
        ("front_arb", -1, "měkčí stabilizátor méně namáhá vnější přední pneumatiku"),
    ]),
    "rear_tyres_hot": ("Přehřívání / opotřebení zadních pneumatik", [
        ("on_throttle", -10, "méně prokluzu zadních kol při akceleraci"),
        ("rear_camber", +0.1, "menší negativní odklon rozloží zátěž po běhounu"),
        ("rl_pressure", -0.5, "nižší tlak sníží teplotu jádra pneumatiky"),
        ("rr_pressure", -0.5, "nižší tlak sníží teplotu jádra pneumatiky"),
        ("rear_toe", -0.03, "menší sbíhavost = méně smýkání"),
    ]),
    "lockups": ("Blokování kol při brzdění", [
        ("brake_pressure", -3, "nižší tlak dá větší rezervu před zablokováním"),
        ("brake_bias", -2, "blokují-li přední, posuň brzdy dozadu (blokují-li zadní, naopak)"),
        ("front_suspension", +1, "tužší předek omezí předklon a odlehčení zadku"),
    ]),
    "kerbs": ("Neklid na obrubnících / dosedání", [
        ("front_suspension", -3, "měkčí pružiny lépe pohltí obrubníky"),
        ("rear_suspension", -3, "měkčí pružiny lépe pohltí obrubníky"),
        ("front_ride_height", +2, "vyšší auto nedosedá na podlahu"),
        ("rear_ride_height", +2, "vyšší auto nedosedá na podlahu"),
    ]),
    "wet": ("Příprava na déšť", [
        ("front_wing", +4, "na mokru potřebuješ maximum přítlaku"),
        ("rear_wing", +5, "víc zadního křídla pro stabilitu a trakci"),
        ("on_throttle", -10, "volnější diferenciál omezí prokluz"),
        ("front_ride_height", +2, "vyšší auto lépe zvládne stojící vodu"),
        ("rear_ride_height", +3, "vyšší auto lépe zvládne stojící vodu"),
        ("brake_pressure", -5, "na mokru se kola blokují mnohem dřív"),
    ]),
}


def _fmt(key: str, value: float) -> str:
    _, unit, _, _, decimals = PARAMS[key]
    return f"{value:.{decimals}f}{(' ' + unit) if unit else ''}"


def describe(setup: dict) -> list[dict]:
    """Current setup grouped for display."""
    if not setup:
        return []
    return [
        {"group": title, "items": [{"key": k, "label": PARAMS[k][0], "value": _fmt(k, setup.get(k, 0))} for k in keys]}
        for title, keys in GROUPS
    ]


def advise(setup: dict, issue: str) -> dict:
    """Concrete changes for one handling complaint, clamped to the garage limits."""
    if issue not in ISSUES:
        return {"issue": issue, "label": "?", "changes": []}
    label, recipe = ISSUES[issue]
    changes = []
    for key, delta, why in recipe:
        name, _, low, high, decimals = PARAMS[key]
        item = {"key": key, "label": name, "why": why, "delta": f"{delta:+.{decimals}f}"}
        if setup:
            current = setup.get(key, 0)
            target = round(min(high, max(low, current + delta)), decimals)
            if abs(target - current) < 10 ** -decimals / 2:
                continue  # already at the limit, nothing to gain here
            item |= {"from": _fmt(key, current), "to": _fmt(key, target)}
        changes.append(item)
    return {"issue": issue, "label": label, "changes": changes, "hasSetup": bool(setup)}


def observations(state: RaceState) -> list[dict]:
    """What the telemetry itself says about the balance of the car."""
    me, notes = state.player, []
    stint = state.current_stint()
    if stint:
        wear = stint[-1]["wear"]
        front, rear = (wear[2] + wear[3]) / 2, (wear[0] + wear[1]) / 2
        if front > 8 and front > rear * 1.35:
            notes.append({"issue": "front_tyres_hot", "text":
                          f"Přední pneumatiky se opotřebovávají výrazně rychleji ({front:.0f} % vs {rear:.0f} % vzadu) "
                          "– auto je nejspíš nedotáčivé a přední náprava dře."})
        elif rear > 8 and rear > front * 1.35:
            notes.append({"issue": "rear_tyres_hot", "text":
                          f"Zadní pneumatiky se opotřebovávají výrazně rychleji ({rear:.0f} % vs {front:.0f} % vpředu) "
                          "– typicky prokluz na výjezdech."})
    inner = me.tel.get("tyre_inner_temp")
    if inner and me.on_track:
        front, rear = (inner[2] + inner[3]) / 2, (inner[0] + inner[1]) / 2
        if front > 103:
            notes.append({"issue": "front_tyres_hot", "text": f"Jádro předních pneumatik má {front:.0f} °C, to je nad oknem."})
        if rear > 103:
            notes.append({"issue": "rear_tyres_hot", "text": f"Jádro zadních pneumatik má {rear:.0f} °C, to je nad oknem."})
    traps = sorted((c.lap.get("speed_trap_fastest", 0.0) for c in state.active_cars()), reverse=True)
    mine = me.lap.get("speed_trap_fastest", 0.0)
    if mine > 0 and len(traps) >= 6 and traps[0] > 0:
        rank = traps.index(mine) + 1 if mine in traps else len(traps)
        if rank > len(traps) * 0.75:
            notes.append({"issue": "slow_straights", "text":
                          f"Na speed trapu jsi {rank}. z {len(traps)} ({mine:.0f} km/h, nejrychlejší {traps[0]:.0f} km/h) "
                          "– vozíš hodně křídla."})
        elif rank <= 3:
            notes.append({"issue": None, "text":
                          f"Na speed trapu jsi {rank}. z {len(traps)} ({mine:.0f} km/h) – na rovinkách máš rezervu, "
                          "můžeš si dovolit přidat přítlak, pokud ztrácíš v zatáčkách."})
    if state.session.get("weather", 0) >= 3 or any(
            f["weather"] >= 3 and f["time_offset"] <= 30 for f in state.session.get("forecast", [])
            if f["session_type"] == state.session_type):
        notes.append({"issue": "wet", "text": "Prší nebo je déšť v předpovědi – zvaž mokrou variantu nastavení."})
    return notes


def catalog() -> list[dict]:
    return [{"issue": key, "label": label} for key, (label, _) in ISSUES.items()]


def recommend(state: RaceState) -> dict:
    """Setup changes the telemetry asks for: wheel-slip balance + tyre temperatures/wear + speed trap."""
    from collections import Counter

    from ..analysis import corner_name_at, detect_corners
    from ..state import TRACE_BUCKET_M

    found: dict[str, dict] = {}

    def add(issue: str, reason: str, score: float) -> None:
        item = found.setdefault(issue, {"issue": issue, "label": ISSUES[issue][0], "reasons": [], "score": 0.0})
        item["reasons"].append(reason)
        item["score"] = max(item["score"], score)

    summary = state.balance.summary()
    corners = detect_corners(state.reference_trace(), TRACE_BUCKET_M)
    for d in state.balance.diagnose():
        reason = d["reason"]
        if d["issue"] == "lockups" and summary and corners:
            spots = Counter(corner_name_at(corners, m) for m in summary["where"]["lock_front"] + summary["where"]["lock_rear"])
            top = [name for name, _ in spots.most_common(3) if name]
            if top:
                reason += f" Nejčastěji v {', '.join(top)}."
        add(d["issue"], reason, d["score"])
    notes = []
    for o in observations(state):
        if o["issue"] in ISSUES:
            add(o["issue"], o["text"], 1.2)
        else:
            notes.append(o["text"])
    items = sorted(found.values(), key=lambda x: -x["score"])
    for item in items:
        item["changes"] = advise(state.setup, item["issue"])["changes"][:3]
    return {"items": items, "notes": notes, "laps": summary["laps"] if summary else 0}


def library(state: RaceState) -> list[dict]:
    """Setups driven on this track, fastest first, with what differs from the current one."""
    from ..storage import setup_key

    if not state.session:
        return []
    current_key = setup_key(state.setup) if state.setup else None
    out = []
    for entry in state.store.track(state.session["track_id"])["setups"][:8]:
        diff = []
        for key, (label, *_rest) in PARAMS.items():
            if key == "fuel_load" or not state.setup:
                continue
            theirs, mine = entry["setup"].get(key), state.setup.get(key)
            if theirs is not None and mine is not None and abs(theirs - mine) > 1e-6:
                diff.append({"label": label, "from": _fmt(key, mine), "to": _fmt(key, theirs)})
        out.append({"best_ms": entry["best_ms"], "laps": entry["laps"], "date": entry.get("date", ""),
                    "session": entry.get("session", 0), "current": entry["key"] == current_key,
                    "diff": diff, "view": describe(entry["setup"])})
    return out
