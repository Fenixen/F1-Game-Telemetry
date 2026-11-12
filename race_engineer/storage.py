"""Small on-disk memory per track: reference lap, track outline and the setups you drove.

Stored as one JSON file per track in the user's data folder:
  Windows: %LOCALAPPDATA%\\RaceEngineer\\tracks\\<track_id>.json
  other:   ~/.race_engineer/tracks/<track_id>.json
Override with the RACE_ENGINEER_DATA environment variable.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("race_engineer")

MAX_SETUPS_PER_TRACK = 20
# Fuel and ballast change with the session plan, not with how the car is set up to handle
_SETUP_IGNORE = {"fuel_load", "ballast"}


def default_dir() -> Path:
    if os.environ.get("RACE_ENGINEER_DATA"):
        return Path(os.environ["RACE_ENGINEER_DATA"])
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "RaceEngineer"
    return Path.home() / ".race_engineer"


def setup_key(setup: dict) -> str:
    return json.dumps({k: round(v, 2) if isinstance(v, float) else v
                       for k, v in sorted(setup.items()) if k not in _SETUP_IGNORE})


class Store:
    """root=None keeps everything in memory only (tests, read-only folders)."""

    def __init__(self, root: Path | None):
        self.root = root / "tracks" if root else None
        self._cache: dict[int, dict] = {}

    def _path(self, track_id: int) -> Path | None:
        return self.root / f"{int(track_id)}.json" if self.root else None

    def track(self, track_id: int) -> dict:
        if track_id in self._cache:
            return self._cache[track_id]
        data = {"reference": None, "outline": None, "setups": []}
        path = self._path(track_id)
        if path and path.is_file():
            try:
                loaded = json.loads(path.read_text("utf-8"))
                if isinstance(loaded, dict):
                    data.update({k: loaded.get(k, data[k]) for k in data})
            except (OSError, ValueError):
                log.warning("Soubor %s je poškozený, začínám pro tuhle trať od nuly.", path)
        self._cache[track_id] = data
        return data

    def _save(self, track_id: int) -> None:
        path = self._path(track_id)
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache[track_id], separators=(",", ":")), "utf-8")
            tmp.replace(path)  # atomic: a crash mid-write never leaves a half-written file
        except OSError as exc:
            log.warning("Nepodařilo se uložit data trati: %s", exc)

    def save_reference(self, track_id: int, trace: dict, track_length: int) -> bool:
        """Keeps the fastest valid lap ever driven here. Returns True when it was a new record."""
        data = self.track(track_id)
        ref = data["reference"]
        if ref and ref.get("track_length") == track_length and ref["ms"] <= trace["ms"]:
            return False
        data["reference"] = trace | {"track_length": track_length, "date": time.strftime("%Y-%m-%d")}
        self._save(track_id)
        return True

    def save_outline(self, track_id: int, points: list, track_length: int) -> None:
        data = self.track(track_id)
        old = data["outline"]
        filled = sum(p is not None for p in points)
        if old and old.get("track_length") == track_length and sum(p is not None for p in old["pts"]) >= filled:
            return
        data["outline"] = {"pts": points, "track_length": track_length}
        self._save(track_id)

    def record_setup(self, track_id: int, setup: dict, lap_ms: int, session_type: int) -> None:
        if not setup or lap_ms <= 0:
            return
        data, key = self.track(track_id), setup_key(setup)
        entry = next((e for e in data["setups"] if e["key"] == key), None)
        if entry is None:
            entry = {"key": key, "setup": setup, "best_ms": lap_ms, "laps": 0, "session": session_type}
            data["setups"].append(entry)
        entry["laps"] += 1
        entry["date"] = time.strftime("%Y-%m-%d")
        if lap_ms < entry["best_ms"]:
            entry["best_ms"], entry["session"] = lap_ms, session_type
        data["setups"].sort(key=lambda e: e["best_ms"])
        del data["setups"][MAX_SETUPS_PER_TRACK:]
        self._save(track_id)
