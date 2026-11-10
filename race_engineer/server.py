"""Web layer: UDP listener -> state -> engineer, and WebSocket fan-out to the iPad."""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import sys
from pathlib import Path
from typing import Callable

from . import snapshots
from .engineer import setup_advisor
from .engineer.rules import Engineer
from .state import RaceState
from .udp.packets import UnsupportedFormat, parse_packet
from .webserver import WebServer, WebSocket, WebSocketClosed

log = logging.getLogger("race_engineer")

FAST_HZ, SLOW_HZ, ENGINEER_HZ = 20, 4, 5

FRONTEND_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "frontend"


class TelemetryProtocol(asyncio.DatagramProtocol):
    def __init__(self, hub: "Hub"):
        self.hub = hub
        self.forward_transport: asyncio.DatagramTransport | None = None
        self._warned_format = False

    def datagram_received(self, data: bytes, addr) -> None:
        if self.forward_transport is not None:
            self.forward_transport.sendto(data)
        try:
            parsed = parse_packet(data)
        except UnsupportedFormat as exc:
            if not self._warned_format:
                self._warned_format = True
                log.warning("Hra posílá UDP formát %s. V nastavení telemetrie zvol 'UDP Format: 2025'.", exc)
            return
        except (struct.error, IndexError, UnicodeError):
            return  # malformed datagram: never let network input crash the listener
        if parsed:
            self.hub.engineer.on_events(self.hub.state.apply(*parsed))


class Hub:
    """Owns the state and pushes it to every connected browser."""

    def __init__(self):
        self.state = RaceState()
        self.clients: set[WebSocket] = set()
        self.engineer = Engineer(self.state, self._on_radio)
        self._outline_sent = -1

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
        await self.broadcast("slow", snapshots.slow(self.state))
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
            await ws.send_json({"t": "traces", "d": snapshots.traces(self.state)})
        elif kind == "setup" and isinstance(cmd.get("issue"), str):
            await ws.send_json({"t": "advice", "d": setup_advisor.advise(self.state.setup, cmd["issue"])})
        elif kind == "verbosity" and cmd.get("v") in (1, 2, 3):
            self.engineer.verbosity = cmd["v"]
            await self.broadcast("verbosity", cmd["v"])


async def serve(http_host: str = "0.0.0.0", http_port: int = 8080, udp_host: str = "0.0.0.0",
                udp_port: int = 20777, forward: tuple[str, int] | None = None) -> None:
    """Runs until cancelled (Ctrl+C)."""
    hub = Hub()
    stop = await hub.start(udp_host, udp_port, forward)
    server = await WebServer(FRONTEND_DIR, hub.ws_handler).start(http_host, http_port)
    try:
        async with server:
            await server.serve_forever()
    finally:
        stop()
