"""Core of the race engineer: message queue, priorities, cooldowns and radio timing.

Like a real engineer, non-urgent messages wait until the driver is on a
straight; critical ones (priority 1) go out immediately.
"""
from __future__ import annotations

import itertools
import re
import time
from collections import deque
from typing import Callable

from ..state import RaceState

CRITICAL, NORMAL, CHATTY = 1, 2, 3

MIN_SPACING_S = 3.5      # pause between two non-critical messages
MAX_WAIT_S = 7.0         # after this we talk even in a corner
STALE_AFTER_S = 25.0     # chatty messages older than this are dropped

_LAP_TIME = re.compile(r"(\d+):(\d\d)\.(\d)\d\d")


def fmt_lap(ms: int) -> str:
    if ms <= 0:
        return "bez času"
    return f"{ms // 60000}:{ms % 60000 // 1000:02d}.{ms % 1000:03d}"


def fmt_gap(ms: float) -> str:
    seconds = abs(ms) / 1000
    if seconds >= 60:
        return "přes minutu"
    return f"{seconds:.1f}".replace(".", ",") + " sekundy"


def fmt_num(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Czech plural: 1 kolo, 2-4 kola, 5+ kol."""
    return f"{n} {one if n == 1 else few if 2 <= n <= 4 else many}"


def to_speech(text: str) -> str:
    """Rewrite display text so a Czech TTS voice reads it naturally."""
    text = _LAP_TIME.sub(r"\1 \2,\3", text)
    for written, spoken in ((" %", " procent"), ("%", " procent"), ("km/h", "kilometrů v hodině"), ("°C", "stupňů")):
        text = text.replace(written, spoken)
    return text


class Radio:
    def __init__(self, state: RaceState, emit: Callable[[dict], None],
                 clock: Callable[[], float] = time.monotonic):
        self.state = state
        self.emit = emit
        self.clock = clock
        self.verbosity = NORMAL
        self.history: deque[dict] = deque(maxlen=150)
        self._ids = itertools.count(1)
        self._queue: list[tuple[dict, float]] = []
        self._cooldowns: dict[str, float] = {}
        self._last_emit = -1e9

    def clear(self) -> None:
        self._queue.clear()
        self._cooldowns.clear()

    def say(self, key: str, text: str, prio: int = NORMAL, cat: str = "info",
            cooldown: float = 30.0, now: bool = False) -> bool:
        """Queue a radio message. `key` identifies the topic for cooldown purposes."""
        t = self.clock()
        if prio > self.verbosity and prio != CRITICAL:
            return False
        if t < self._cooldowns.get(key, -1e9):
            return False
        self._cooldowns[key] = t + cooldown
        lap = self.state.player.lap.get("lap_num", 0)
        msg = {
            "id": next(self._ids), "time": "", "lap": lap, "prio": prio,
            "cat": cat, "text": text, "say": to_speech(text),
        }
        if now or prio == CRITICAL:
            self._deliver(msg)
        else:
            self._queue.append((msg, t))
        return True

    def _deliver(self, msg: dict) -> None:
        self._last_emit = self.clock()
        msg["time"] = time.strftime("%H:%M:%S")  # stamp when it actually goes on air, not when queued
        self.history.append(msg)
        self.emit(msg)

    def _driver_can_listen(self) -> bool:
        me = self.state.player
        tel = me.tel
        if not tel or not me.on_track or tel["speed"] < 60:
            return True
        return tel["throttle"] > 0.85 and abs(tel["steer"]) < 0.12

    def flush(self) -> None:
        """Release at most one queued message if the timing is right."""
        if not self._queue:
            return
        t = self.clock()
        self._queue = [(m, at) for m, at in self._queue if m["prio"] < CHATTY or t - at < STALE_AFTER_S]
        if not self._queue or t - self._last_emit < MIN_SPACING_S:
            return
        self._queue.sort(key=lambda item: (item[0]["prio"], item[1]))
        msg, queued_at = self._queue[0]
        if self._driver_can_listen() or t - queued_at > MAX_WAIT_S:
            self._queue.pop(0)
            self._deliver(msg)
