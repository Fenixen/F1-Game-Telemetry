"""Entry point: `python -m race_engineer` starts the UDP listener and the web server."""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys
import threading

from . import simulator
from .server import serve


def lan_addresses() -> list[str]:
    """Best-effort list of this PC's IPv4 addresses reachable from the iPad."""
    found = set()
    try:
        # No packet is sent: connecting a UDP socket only asks the OS which interface it would use
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    try:
        found.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127."))


def main() -> None:
    if sys.version_info < (3, 10):
        sys.exit("Závodní inženýr potřebuje Python 3.10 nebo novější (python.org).")
    parser = argparse.ArgumentParser(prog="race_engineer", description="Závodní inženýr pro F1 25")
    parser.add_argument("--http-port", type=int, default=8080, help="port webového rozhraní (výchozí 8080)")
    parser.add_argument("--udp-port", type=int, default=20777, help="UDP port telemetrie ze hry (výchozí 20777)")
    parser.add_argument("--udp-host", default="0.0.0.0", help="adresa pro příjem UDP (výchozí všechna rozhraní)")
    parser.add_argument("--forward", metavar="HOST:PORT", help="přeposílat UDP dál (např. pro SimHub)")
    parser.add_argument("--demo", action="store_true", help="spustit i simulátor závodu (bez hry)")
    parser.add_argument("--demo-speed", type=float, default=1.0, help="zrychlení simulátoru")
    parser.add_argument("--demo-laps", type=int, default=8, help="počet kol simulovaného závodu")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    forward = None
    if args.forward:
        host, _, port = args.forward.rpartition(":")
        forward = (host, int(port))

    print("\n  ZÁVODNÍ INŽENÝR · F1 25")
    print("  ───────────────────────────────────────────")
    print(f"  Telemetrie:  UDP port {args.udp_port} (formát 2025)")
    for address in lan_addresses() or ["<IP-tohoto-PC>"]:
        print(f"  iPad:        http://{address}:{args.http_port}")
    print(f"  Tento PC:    http://localhost:{args.http_port}")
    print("  Ukončení:    Ctrl+C\n")

    if args.demo:
        threading.Thread(
            target=simulator.run, kwargs={"port": args.udp_port, "speed": args.demo_speed, "laps": args.demo_laps},
            daemon=True).start()
        print("  DEMO: běží simulovaný závod.\n")

    try:
        asyncio.run(serve("0.0.0.0", args.http_port, args.udp_host, args.udp_port, forward))
    except KeyboardInterrupt:
        print("\n  Inženýr končí. Díky za závod!")
    except OSError as exc:
        # Most common case: the port is taken (another copy of the app, or SimHub on 20777)
        print(f"\n  Nepodařilo se otevřít port: {exc}")
        print("  Běží už aplikace jinde? Případně zkus jiný port: --http-port / --udp-port.")
        sys.exit(1)


if __name__ == "__main__":
    main()
