"""Minimal HTTP + WebSocket server on asyncio, standard library only.

The app needs very little from a web framework: serve a handful of static
files and keep a few WebSocket connections open. Doing that ourselves means
no third-party packages, so nothing has to be downloaded or compiled for
the user's Python version (compiled wheels are what break on brand-new or
pre-release Python builds).

WebSocket framing follows RFC 6455. Everything coming from the network is
size-limited and validated before use.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable
from urllib.parse import unquote, urlparse

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HEADER_BYTES = 16 * 1024
MAX_WS_MESSAGE = 4096       # clients only ever send tiny JSON commands
SEND_TIMEOUT_S = 2.0        # a client that cannot take data this fast is dropped, not waited for
PING_INTERVAL_S = 20.0
IDLE_TIMEOUT_S = 60.0       # no frame at all (not even a pong) for this long = dead connection

_OP_CONT, _OP_TEXT, _OP_BINARY, _OP_CLOSE, _OP_PING, _OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml",
    ".png": "image/png", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json",
}

_REASONS = {101: "Switching Protocols", 200: "OK", 400: "Bad Request", 403: "Forbidden",
            404: "Not Found", 405: "Method Not Allowed", 431: "Request Header Fields Too Large"}


class WebSocketClosed(ConnectionError):
    pass


class WebSocket:
    """One accepted WebSocket connection (server side)."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader, self._writer = reader, writer
        self._send_lock = asyncio.Lock()
        self.closed = False

    # ------------------------------------------------------------------ sending

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.closed:
            raise WebSocketClosed()
        n = len(payload)
        if n < 126:
            header = struct.pack("!BB", 0x80 | opcode, n)
        elif n < 1 << 16:
            header = struct.pack("!BBH", 0x80 | opcode, 126, n)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 127, n)
        # Lock: frames from concurrent senders must not interleave on the wire
        async with self._send_lock:
            try:
                self._writer.write(header + payload)
                await asyncio.wait_for(self._writer.drain(), SEND_TIMEOUT_S)
            except (OSError, asyncio.TimeoutError) as exc:
                self._abort()
                raise WebSocketClosed() from exc

    async def send_str(self, text: str) -> None:
        await self._send_frame(_OP_TEXT, text.encode("utf-8"))

    async def send_json(self, data) -> None:
        await self.send_str(json.dumps(data, separators=(",", ":")))

    async def close(self, code: int = 1000) -> None:
        if not self.closed:
            try:
                await self._send_frame(_OP_CLOSE, struct.pack("!H", code))
            except WebSocketClosed:
                pass
        self._abort()

    def _abort(self) -> None:
        self.closed = True
        self._writer.close()

    # ------------------------------------------------------------------ receiving

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        head = await self._reader.readexactly(2)
        fin, opcode = head[0] & 0x80, head[0] & 0x0F
        masked, length = head[1] & 0x80, head[1] & 0x7F
        if head[0] & 0x70 or not masked:
            raise WebSocketClosed("protocol error")  # no extensions negotiated; clients must mask
        if length == 126:
            (length,) = struct.unpack("!H", await self._reader.readexactly(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", await self._reader.readexactly(8))
        if length > MAX_WS_MESSAGE:
            raise WebSocketClosed("message too big")
        mask = await self._reader.readexactly(4)
        data = await self._reader.readexactly(length)
        if length:
            # XOR the whole payload at once via big integers (much faster than a per-byte loop)
            key = (mask * (length // 4 + 1))[:length]
            data = (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(length, "big")
        return bool(fin), opcode, data

    async def messages(self) -> AsyncIterator[str]:
        """Yields incoming text messages until the connection ends."""
        pinger = asyncio.create_task(self._ping_loop())
        parts: list[bytes] = []
        try:
            while not self.closed:
                fin, opcode, data = await asyncio.wait_for(self._read_frame(), IDLE_TIMEOUT_S)
                if opcode == _OP_CLOSE:
                    break
                if opcode == _OP_PING:
                    await self._send_frame(_OP_PONG, data)
                elif opcode in (_OP_TEXT, _OP_BINARY, _OP_CONT):
                    if opcode != _OP_CONT:
                        parts = []
                    parts.append(data)
                    if sum(map(len, parts)) > MAX_WS_MESSAGE:
                        break
                    if fin:
                        message, parts = b"".join(parts), []
                        if opcode == _OP_TEXT or opcode == _OP_CONT:
                            yield message.decode("utf-8", "replace")
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, OSError, WebSocketClosed):
            pass
        finally:
            pinger.cancel()
            await self.close()

    async def _ping_loop(self) -> None:
        try:
            while not self.closed:
                await asyncio.sleep(PING_INTERVAL_S)
                await self._send_frame(_OP_PING, b"")
        except WebSocketClosed:
            pass


WsHandler = Callable[[WebSocket], Awaitable[None]]


class WebServer:
    """Routes: GET / -> index.html, GET /static/* -> files, GET /ws -> WebSocket."""

    def __init__(self, static_dir: Path, ws_handler: WsHandler):
        self.static_dir = static_dir.resolve()
        self.ws_handler = ws_handler

    async def start(self, host: str, port: int) -> asyncio.base_events.Server:
        return await asyncio.start_server(self._handle, host, port, limit=MAX_HEADER_BYTES)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._serve(reader, writer)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, OSError, ValueError):
            pass
        finally:
            writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        except asyncio.LimitOverrunError:
            await self._respond(writer, 431)
            return
        lines = raw.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3:
            await self._respond(writer, 400)
            return
        method, target, _ = parts
        headers = {}
        for line in lines[1:]:
            name, sep, value = line.partition(":")
            if sep:
                headers[name.strip().lower()] = value.strip()
        if method != "GET":
            await self._respond(writer, 405)
            return
        path = urlparse(target).path

        if path == "/ws":
            await self._upgrade(reader, writer, headers)
        elif path == "/":
            await self._send_file(writer, self.static_dir / "index.html")
        elif path.startswith("/static/"):
            await self._send_file(writer, self._safe_path(path[len("/static/"):]))
        else:
            await self._respond(writer, 404)

    def _safe_path(self, relative: str) -> Path | None:
        """Resolve a URL path inside the static dir; anything escaping it (../, %2e%2e/) is refused."""
        candidate = (self.static_dir / unquote(relative)).resolve()
        return candidate if candidate.is_relative_to(self.static_dir) and candidate.is_file() else None

    async def _send_file(self, writer: asyncio.StreamWriter, path: Path | None) -> None:
        if path is None or not path.is_file():
            await self._respond(writer, 404)
            return
        content_type = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        # no-cache = always revalidate, so the iPad picks up app updates immediately
        await self._respond(writer, 200, path.read_bytes(), content_type, {"Cache-Control": "no-cache"})

    async def _respond(self, writer: asyncio.StreamWriter, status: int, body: bytes = b"",
                       content_type: str = "text/plain; charset=utf-8", extra: dict | None = None) -> None:
        if not body and status >= 400:
            body = _REASONS.get(status, "Error").encode()
        head = [f"HTTP/1.1 {status} {_REASONS.get(status, 'OK')}", f"Content-Type: {content_type}",
                f"Content-Length: {len(body)}", "Connection: close", "X-Content-Type-Options: nosniff"]
        head += [f"{k}: {v}" for k, v in (extra or {}).items()]
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body)
        await writer.drain()

    async def _upgrade(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, headers: dict) -> None:
        key = headers.get("sec-websocket-key", "")
        try:
            key_ok = len(base64.b64decode(key, validate=True)) == 16
        except ValueError:
            key_ok = False
        if headers.get("upgrade", "").lower() != "websocket" or not key_ok:
            await self._respond(writer, 400)
            return
        # Browsers always send Origin: refuse other web pages trying to read our telemetry
        origin = headers.get("origin")
        if origin and urlparse(origin).netloc != headers.get("host", ""):
            await self._respond(writer, 403)
            return
        accept = base64.b64encode(hashlib.sha1(key.encode("ascii") + WS_GUID).digest()).decode("ascii")
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode("ascii"))
        await writer.drain()
        await self.ws_handler(WebSocket(reader, writer))
