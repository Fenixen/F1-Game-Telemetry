"""Telemetry analysis: corner detection, corner-by-corner lap comparison and car balance.

Corners are detected from the speed trace of a reference lap, so they are
"our" numbering (Z1, Z2, ...) in driving order, not the official corner
numbers of the circuit. Fast kinks without a speed drop are not corners here.

Car balance comes from the player's tyre slip angles (Motion Ex packet):
a front axle sliding more than the rear means understeer, and the reverse
means oversteer. Thresholds are heuristics, so results are presented as hints.
"""
from __future__ import annotations

from collections import deque

# ---------------------------------------------------------------- corners

MIN_DROP_KMH = 20          # speed has to fall at least this much from the preceding straight
APEX_WINDOW = 6            # buckets on each side that must not be slower (local minimum)
MERGE_BUCKETS = 15         # minima closer than this are one corner (chicanes stay one complex)
LOOKBACK_BUCKETS = 60      # how far back the preceding straight / brake point is searched


def _filled(values: list) -> list[float]:
    """Replace gaps (None) with the nearest known value so maths can run on partial laps."""
    out, last = [], None
    for v in values:
        last = v if v is not None else last
        out.append(last)
    first = next((v for v in out if v is not None), 0)
    return [first if v is None else v for v in out]


def detect_corners(trace: dict | None, bucket: int) -> list[dict]:
    if not trace or not any(v is not None for v in trace["speed"]):
        return []
    speed, brake = _filled(trace["speed"]), _filled(trace["brake"])
    n = len(speed)
    smooth = [sum(speed[max(0, i - 1):i + 2]) / len(speed[max(0, i - 1):i + 2]) for i in range(n)]
    apexes: list[int] = []
    for i in range(1, n - 1):
        window = smooth[max(0, i - APEX_WINDOW):i + APEX_WINDOW + 1]
        if smooth[i] > min(window) or smooth[i] >= 290:
            continue
        peak = max(smooth[max(0, i - LOOKBACK_BUCKETS):i + 1])
        if peak - smooth[i] < MIN_DROP_KMH:
            continue
        if apexes and i - apexes[-1] < MERGE_BUCKETS:
            if smooth[i] < smooth[apexes[-1]]:
                apexes[-1] = i
            continue
        apexes.append(i)

    corners = []
    for num, apex in enumerate(apexes, start=1):
        brake_at = None
        for j in range(apex, max(0, apex - LOOKBACK_BUCKETS), -1):
            if brake[j] >= 15 and brake[j - 1] < 15:
                brake_at = j
                break
        start = max(0, (brake_at if brake_at is not None else apex - 20) - 8)
        if corners:
            start = max(start, corners[-1]["end_i"])
        end = min(n - 1, apex + 25)
        corners.append({
            "num": num, "name": f"Z{num}", "apex_i": apex, "start_i": start, "end_i": end,
            "apex_m": apex * bucket, "start_m": start * bucket, "end_m": end * bucket,
            "brake_m": brake_at * bucket if brake_at is not None else None,
            "min_kmh": round(min(speed[max(0, apex - 3):apex + 4])),
        })
    return corners


def _brake_point(trace: dict, corner: dict) -> int | None:
    brake = trace["brake"]
    for j in range(corner["apex_i"], max(0, corner["apex_i"] - LOOKBACK_BUCKETS), -1):
        if brake[j] is not None and brake[j] >= 15 and (brake[j - 1] is None or brake[j - 1] < 15):
            return j
    return None


def _at(values: list, i: int):
    return values[i] if 0 <= i < len(values) else None


def compare_corners(ref: dict | None, lap: dict | None, corners: list[dict], bucket: int) -> list[dict]:
    """Per corner: time lost vs the reference lap and the most likely reason (in Czech)."""
    if not ref or not lap or len(ref["t"]) != len(lap["t"]):
        return []
    out = []
    for c in corners:
        s, e, a = c["start_i"], c["end_i"], c["apex_i"]
        t_vals = [_at(lap["t"], s), _at(ref["t"], s), _at(lap["t"], e), _at(ref["t"], e)]
        if any(v is None for v in t_vals):
            continue
        loss = (t_vals[2] - t_vals[3]) - (t_vals[0] - t_vals[1])
        lap_min = min((v for v in lap["speed"][max(0, a - 3):a + 4] if v is not None), default=None)
        ref_min = min((v for v in ref["speed"][max(0, a - 3):a + 4] if v is not None), default=None)
        lap_exit, ref_exit = _at(lap["speed"], e), _at(ref["speed"], e)
        lap_bp, ref_bp = _brake_point(lap, c), _brake_point(ref, c)
        brake_diff = (lap_bp - ref_bp) * bucket if lap_bp is not None and ref_bp is not None else None
        min_diff = lap_min - ref_min if lap_min is not None and ref_min is not None else None
        exit_diff = lap_exit - ref_exit if lap_exit is not None and ref_exit is not None else None
        out.append({
            "name": c["name"], "apex_m": c["apex_m"], "loss_ms": loss,
            "brake_diff_m": brake_diff, "min_diff_kmh": min_diff, "exit_diff_kmh": exit_diff,
            "tip": _corner_tip(loss, brake_diff, min_diff, exit_diff),
        })
    return out


def _corner_tip(loss: int, brake_diff: int | None, min_diff: float | None, exit_diff: float | None) -> str:
    if loss <= -40:
        return "Tady jsi rychlejší než na referenčním kole."
    if loss < 40:
        return "Stejně jako na referenčním kole."
    reasons = []
    if brake_diff is not None and brake_diff <= -8:
        reasons.append(f"brzdíš o {-brake_diff} m dřív")
    if brake_diff is not None and brake_diff >= 8 and (min_diff or 0) < -3:
        reasons.append(f"brzdíš o {brake_diff} m později a nestíháš apex")
    if min_diff is not None and min_diff <= -4:
        reasons.append(f"apexem projíždíš o {-min_diff:.0f} km/h pomaleji")
    if exit_diff is not None and exit_diff <= -5:
        reasons.append(f"horší výjezd, o {-exit_diff:.0f} km/h pomaleji")
    if not reasons:
        reasons.append("ztráta je rozložená v celé zatáčce, zkus čistší stopu")
    text = ", ".join(reasons)
    return text[0].upper() + text[1:] + "."


# ---------------------------------------------------------------- balance from wheel slip

UNDER, OVER = 1.3, 1 / 1.3   # front/rear slip angle ratio that counts as under/oversteer
LOCK_SLIP = -0.15            # slip ratio below this while braking = locked wheel
SPIN_SLIP = 0.15             # slip ratio above this on throttle = wheelspin


def _phase(tel: dict) -> str | None:
    steer = abs(tel["steer"])
    if tel["brake"] > 0.2:
        return "entry"
    if tel["throttle"] > 0.5 and steer > 0.08:
        return "exit"
    if tel["brake"] < 0.05 and tel["throttle"] <= 0.5 and steer > 0.12:
        return "mid"
    return None


class BalanceTracker:
    """Accumulates slip data per corner phase for the current lap and keeps the last few laps."""

    def __init__(self, keep_laps: int = 6):
        self.laps: deque[dict] = deque(maxlen=keep_laps)
        self._new_lap()

    def _new_lap(self) -> None:
        self.cur = {ph: [0.0, 0.0, 0] for ph in ("entry", "mid", "exit")}
        self.cur_events = {"lock_front": [], "lock_rear": [], "spin": []}
        self._active = {"lock_front": False, "lock_rear": False, "spin": False}

    def reset(self) -> None:
        self.laps.clear()
        self._new_lap()

    def feed(self, tel: dict, mex: dict, lap_distance: float) -> None:
        if not tel or tel["speed"] < 60:
            return
        sa, sr = mex["slip_angle"], mex["slip_ratio"]   # RL, RR, FL, FR
        phase = _phase(tel)
        if phase:
            acc = self.cur[phase]
            acc[0] += (abs(sa[2]) + abs(sa[3])) / 2
            acc[1] += (abs(sa[0]) + abs(sa[1])) / 2
            acc[2] += 1
        braking, throttle = tel["brake"] > 0.2, tel["throttle"] > 0.3
        for key, active in (("lock_front", braking and min(sr[2], sr[3]) < LOCK_SLIP),
                            ("lock_rear", braking and min(sr[0], sr[1]) < LOCK_SLIP),
                            ("spin", throttle and max(sr[0], sr[1]) > SPIN_SLIP)):
            if active and not self._active[key]:
                self.cur_events[key].append(round(lap_distance))  # count each event once, at its start
            self._active[key] = active

    def finish_lap(self, valid: bool) -> None:
        if valid and sum(acc[2] for acc in self.cur.values()) > 50:
            self.laps.append({"phases": self.cur, "events": self.cur_events})
        self._new_lap()

    def summary(self) -> dict | None:
        if not self.laps:
            return None
        n = len(self.laps)
        ratios = {}
        for ph in ("entry", "mid", "exit"):
            front = sum(lap["phases"][ph][0] for lap in self.laps)
            rear = sum(lap["phases"][ph][1] for lap in self.laps)
            samples = sum(lap["phases"][ph][2] for lap in self.laps)
            ratios[ph] = round(front / rear, 2) if rear > 0 and samples >= 30 else None
        events = {k: [d for lap in self.laps for d in lap["events"][k]] for k in ("lock_front", "lock_rear", "spin")}
        return {"laps": n, "ratios": ratios, "per_lap": {k: round(len(v) / n, 1) for k, v in events.items()},
                "where": events}

    def diagnose(self) -> list[dict]:
        """Handling problems the data points to, strongest first: [{issue, reason, score}]."""
        s = self.summary()
        if not s:
            return []
        r, per_lap, out = s["ratios"], s["per_lap"], []
        names = {"entry": "na vjezdu do zatáček", "mid": "uprostřed zatáček", "exit": "na výjezdu"}
        for ph, under_issue, over_issue in (("entry", "understeer_entry", "oversteer_entry"),
                                            ("mid", "understeer_mid", "oversteer_mid"),
                                            ("exit", "understeer_mid", "oversteer_exit")):
            ratio = r[ph]
            if ratio is None:
                continue
            if ratio >= UNDER:
                out.append({"issue": under_issue, "score": ratio,
                            "reason": f"Přední kola klouzají {names[ph]} víc než zadní (poměr {ratio:.2f}) – nedotáčivost."})
            elif ratio <= OVER:
                out.append({"issue": over_issue, "score": 1 / ratio,
                            "reason": f"Zadní kola klouzají {names[ph]} víc než přední (poměr {ratio:.2f}) – přetáčivost."})
        if per_lap["spin"] >= 2:
            out.append({"issue": "oversteer_exit", "score": 1 + per_lap["spin"] / 4,
                        "reason": f"Prokluz zadních kol na plynu {per_lap['spin']:.1f}× za kolo."})
        if per_lap["lock_front"] + per_lap["lock_rear"] >= 1.5:
            which = "přední" if per_lap["lock_front"] >= per_lap["lock_rear"] else "zadní"
            out.append({"issue": "lockups", "score": 1 + (per_lap["lock_front"] + per_lap["lock_rear"]) / 4,
                        "reason": f"Blokuješ {which} kola, {per_lap['lock_front'] + per_lap['lock_rear']:.1f}× za kolo."})
        # one entry per issue, keeping the strongest reason
        best: dict[str, dict] = {}
        for item in out:
            if item["issue"] not in best or item["score"] > best[item["issue"]]["score"]:
                best[item["issue"]] = item
        return sorted(best.values(), key=lambda x: -x["score"])


def corner_name_at(corners: list[dict], distance_m: float) -> str | None:
    """Which detected corner a lap distance belongs to (for 'you lock up in Z3')."""
    for c in corners:
        if c["start_m"] <= distance_m <= c["end_m"]:
            return c["name"]
    return None
