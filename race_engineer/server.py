"""Web layer: UDP listener -> state -> engineer, and WebSocket fan-out to the iPad."""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import struct
import sys
import time
from pathlib import Path
from typing import BinaryIO, Callable

from . import snapshots
from .engineer import setup_advisor
from .engineer.rules import Engineer
from .state import RaceState
from .storage import Store
from .udp.packets import UnsupportedFormat, parse_packet
from .webserver import WebServer, WebSocket, WebSocketClosed

log = logging.getLogger("race_engineer")

FAST_HZ, SLOW_HZ, ENGINEER_HZ = 20, 4, 5
_RECORD = struct.Struct("<dI")  # recording = gzip stream of (seconds since start, length) + datagram

FRONTEND_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "frontend"


class TelemetryProtocol(asyncio.DatagramProtocol):
    def __init__(self, hub: "Hub"):
        self.hub = hub
        self.forward_transport: asyncio.DatagramTransport | None = None
        self._warned_format = False

    def datagram_received(self, data: bytes, addr) -> None:
        if self.forward_transport is not None:
            self.forward_transport.sendto(data)
        self.hub.feed(data)


class Recorder:
    """Saves the raw UDP stream so a real session can be replayed later (debugging, new game versions)."""

    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / time.strftime("session-%Y%m%d-%H%M%S.f1rec.gz")
        self._file: BinaryIO = gzip.open(self.path, "wb", compresslevel=5)
        self._start = time.monotonic()

    def write(self, data: bytes) -> None:
        self._file.write(_RECORD.pack(time.monotonic() - self._start, len(data)) + data)

    def close(self) -> None:
        self._file.close()


async def replay(hub: "Hub", path: Path, speed: float = 1.0) -> None:
    """Feeds a recording into the hub with its original timing."""
    log.info("Přehrávám záznam %s", path)
    with gzip.open(path, "rb") as f:
        started = time.monotonic()
        while head := f.read(_RECORD.size):
            if len(head) < _RECORD.size:
                break
            at, length = _RECORD.unpack(head)
            data = f.read(length)
            wait = at / speed - (time.monotonic() - started)
            if wait > 0:
                await asyncio.sleep(wait)
            hub.feed(data)
    log.info("Záznam dohrál.")


class Hub:
    """Owns the state and pushes it to every connected browser."""

    def __init__(self, store: Store | None = None, recorder: Recorder | None = None):
        self.state = RaceState(store)
        self.clients: set[WebSocket] = set()
        self.engineer = Engineer(self.state, self._on_radio)
        self.recorder = recorder
        self._outline_sent = -1
        self._warned_format = False
        # Last good views of a session, shown while a new session has no data yet
        self._last_slow: dict | None = None
        self._last_traces: dict | None = None

    def feed(self, data: bytes) -> None:
        """One UDP datagram from the game (or from a recording)."""
        if self.recorder:
            self.recorder.write(data)
        try:
            parsed = parse_packet(data)
        except UnsupportedFormat as exc:
            if not self._warned_format:
                self._warned_format = True
                log.warning("Hra posílá UDP formát %s. V nastavení telemetrie zvol 'UDP Format' 2025 nebo 2026.", exc)
            return
        except (struct.error, IndexError, UnicodeError, ValueError):
            return  # malformed datagram: never let network input crash the listener
        if parsed:
            self.engineer.on_events(self.state.apply(*parsed))

    def _on_radio(self, msg: dict) -> None:
        log.info("RADIO  %s", msg["text"])
        self._spawn(self.broadcast("radio", msg))

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        task.add_done_callback(lambda t: t.exception())  # surface nothing, just mark as retrieved

    async def broadcast(self, kind: str, data) -> None:
        if not self.clients:
            return
        payload = json.dumps({"t": kind, "d": data}, separators=(",", ":"))
        for ws in list(self.clients):
            try:
                await ws.send_str(payload)
            except WebSocketClosed:
                self.clients.discard(ws)  # dead or too slow: the rest of the clients keep getting data

    async def _every(self, hz: float, job) -> None:
        while True:
            await asyncio.sleep(1 / hz)
            try:
                await job()
            except Exception:  # a bug in one tick must not kill the loop
                log.exception("Chyba v periodické úloze")

    async def _fast_tick(self) -> None:
        if self.clients and self.state.connected:
            data = snapshots.fast(self.state)
            if data:
                await self.broadcast("fast", data)

    async def _slow_tick(self) -> None:
        if not self.clients:
            return
        snap = snapshots.slow(self.state)
        if snap["ready"]:
            self._last_slow = snap
        elif self._last_slow:
            # New session without data yet (menus, garage): keep showing the last one, marked as old
            snap = self._last_slow | {"stale": True, "connected": self.state.connected}
        await self.broadcast("slow", snap)
        if self.state.outline_version != self._outline_sent:
            self._outline_sent = self.state.outline_version
            await self.broadcast("outline", snapshots.outline(self.state))

    async def _engineer_tick(self) -> None:
        self.engineer.tick()

    async def start(self, udp_host: str, udp_port: int, forward: tuple[str, int] | None) -> Callable[[], None]:
        """Starts the UDP listener and the periodic jobs; returns a function that stops them."""
        tasks = [
            asyncio.create_task(self._every(FAST_HZ, self._fast_tick)),
            asyncio.create_task(self._every(SLOW_HZ, self._slow_tick)),
            asyncio.create_task(self._every(ENGINEER_HZ, self._engineer_tick)),
        ]
        loop = asyncio.get_running_loop()
        protocol = TelemetryProtocol(self)
        transport, _ = await loop.create_datagram_endpoint(lambda: protocol, local_addr=(udp_host, udp_port))
        if forward:
            protocol.forward_transport, _ = await loop.create_datagram_endpoint(
                asyncio.DatagramProtocol, remote_addr=forward)

        def stop() -> None:
            for task in tasks:
                task.cancel()
            transport.close()
            if protocol.forward_transport:
                protocol.forward_transport.close()
        return stop

    # ------------------------------------------------------------------ websocket

    async def ws_handler(self, ws: WebSocket) -> None:
        try:
            await ws.send_json({"t": "hello", "d": {
                "radio": list(self.engineer.history)[-60:], "verbosity": self.engineer.verbosity,
                "issues": setup_advisor.catalog(),
            }})
            await ws.send_json({"t": "outline", "d": snapshots.outline(self.state)})
        except WebSocketClosed:
            return
        self.clients.add(ws)
        try:
            async for text in ws.messages():
                try:
                    await self._on_command(ws, text)
                except WebSocketClosed:
                    break
        finally:
            self.clients.discard(ws)

    async def _on_command(self, ws: WebSocket, raw: str) -> None:
        try:
            cmd = json.loads(raw)
            kind = cmd["t"]
        except (ValueError, KeyError, TypeError):
            return
        if kind == "query" and isinstance(cmd.get("q"), str):
            self.engineer.answer(cmd["q"][:32])
        elif kind == "traces":
            data = snapshots.traces(self.state)
            if data["cur"] or data["last"] or data["best"] or data["ref"]:
                self._last_traces = data
            elif self._last_traces:
                data = self._last_traces | {"stale": True}
            await ws.send_json({"t": "traces", "d": data})
        elif kind == "setup_info":
            await ws.send_json({"t": "setupInfo", "d": snapshots.setup_info(self.state)})
        elif kind == "setup" and isinstance(cmd.get("issue"), str):
            await ws.send_json({"t": "advice", "d": setup_advisor.advise(self.state.setup, cmd["issue"])})
        elif kind == "verbosity" and cmd.get("v") in (1, 2, 3):
            self.engineer.verbosity = cmd["v"]
            await self.broadcast("verbosity", cmd["v"])


async def serve(http_host: str = "0.0.0.0", http_port: int = 8080, udp_host: str = "0.0.0.0",
                udp_port: int = 20777, forward: tuple[str, int] | None = None, store: Store | None = None,
                record_dir: Path | None = None, replay_file: Path | None = None, replay_speed: float = 1.0) -> None:
    """Runs until cancelled (Ctrl+C)."""
    recorder = Recorder(record_dir) if record_dir else None
    if recorder:
        log.info("Nahrávám telemetrii do %s", recorder.path)
    hub = Hub(store, recorder)
    stop = await hub.start(udp_host, udp_port, forward)
    server = await WebServer(FRONTEND_DIR, hub.ws_handler).start(http_host, http_port)
    replaying = asyncio.create_task(replay(hub, replay_file, replay_speed)) if replay_file else None
    try:
        async with server:
            await server.serve_forever()
    finally:
        stop()
        if replaying:
            replaying.cancel()
        if recorder:
            recorder.close()
