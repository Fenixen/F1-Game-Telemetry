import asyncio
import base64
import json
import os
import struct

from race_engineer.server import FRONTEND_DIR, Hub
from race_engineer.webserver import WebServer


async def _request(port: int, raw: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    return data


def _get(path: str, host: str = "127.0.0.1") -> bytes:
    return f"GET {path} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()


def _masked_text(text: str) -> bytes:
    payload, mask = text.encode(), os.urandom(4)
    body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    n = len(payload)
    length = struct.pack("!B", 0x80 | n) if n < 126 else struct.pack("!BH", 0x80 | 126, n)
    return b"\x81" + length + mask + body


async def _read_frame(reader):
    b1, b2 = await reader.readexactly(2)
    n = b2 & 0x7F
    if n == 126:
        (n,) = struct.unpack("!H", await reader.readexactly(2))
    elif n == 127:
        (n,) = struct.unpack("!Q", await reader.readexactly(8))
    return b1 & 0x0F, await reader.readexactly(n)


def test_http_and_websocket_roundtrip():
    async def scenario():
        hub = Hub()
        server = await WebServer(FRONTEND_DIR, hub.ws_handler).start("127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            index = await _request(port, _get("/"))
            assert index.startswith(b"HTTP/1.1 200") and b"<!doctype html>" in index
            js = await _request(port, _get("/static/js/app.js"))
            assert b"text/javascript" in js.split(b"\r\n\r\n")[0]

            # Path traversal must never leave the frontend directory
            for evil in ("/static/../race_engineer/server.py", "/static/%2e%2e/race_engineer/server.py",
                         "/static/..%2F..%2Fetc%2Fpasswd", "/nope"):
                assert (await _request(port, _get(evil))).startswith(b"HTTP/1.1 404"), evil
            assert (await _request(port, b"POST / HTTP/1.1\r\n\r\n")).startswith(b"HTTP/1.1 405")
            assert (await _request(port, b"garbage\r\n\r\n")).startswith(b"HTTP/1.1 400")

            key = base64.b64encode(os.urandom(16)).decode()
            upgrade = (f"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
                       f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n")
            # Another website must not be able to open the socket from a visitor's browser
            evil = await _request(port, (upgrade + "Origin: http://evil.example\r\n\r\n").encode())
            assert evil.startswith(b"HTTP/1.1 403")

            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write((upgrade + f"Origin: http://127.0.0.1:{port}\r\n\r\n").encode())
            head = await reader.readuntil(b"\r\n\r\n")
            assert head.startswith(b"HTTP/1.1 101")
            op, payload = await _read_frame(reader)
            hello = json.loads(payload)
            assert op == 1 and hello["t"] == "hello" and hello["d"]["issues"]
            op, payload = await _read_frame(reader)
            assert json.loads(payload)["t"] == "outline"

            writer.write(_masked_text(json.dumps({"t": "traces"})))
            op, payload = await _read_frame(reader)
            assert json.loads(payload)["t"] == "traces"
            assert len(hub.clients) == 1

            writer.write(_masked_text("x" * 5000))  # oversized message -> connection dropped
            op, _ = await _read_frame(reader)
            assert op == 0x8
            await asyncio.sleep(0.05)
            assert not hub.clients
            writer.close()

    asyncio.run(scenario())
