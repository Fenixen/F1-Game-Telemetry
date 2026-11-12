"""Entry point: `python -m race_engineer` starts the UDP listener and the web server."""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys
import threading

from pathlib import Path

from . import simulator
from .server import serve
from .storage import Store, default_dir


def lan_addresses() -> tuple[str | None, list[str]]:
    """(address the iPad should use, other candidates) for this PC.

    The primary one is the interface the OS routes outgoing traffic through,
    which on a home PC is the Wi-Fi/Ethernet connected to the router.
    """
    primary = None
    try:
        # No packet is sent: connecting a UDP socket only asks the OS which interface it would use
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            primary = probe.getsockname()[0]
    except OSError:
        pass
    others = set()
    try:
        others.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    # 127.x = this PC only, 169.254.x = adapter without a router (unusable from the iPad)
    usable = sorted(a for a in others if not a.startswith(("127.", "169.254.")) and a != primary)
    if primary and primary.startswith(("127.", "169.254.", "0.")):
        primary = None
    return primary, usable


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
    parser.add_argument("--demo-format", type=int, choices=(2025, 2026), default=2025,
                        help="UDP formát simulátoru (2026 = sezónní balíček 2026)")
    parser.add_argument("--record", action="store_true",
                        help="nahrávat telemetrii ze hry do souboru (pro ladění, cca 100–300 MB/h)")
    parser.add_argument("--replay", metavar="SOUBOR", type=Path, help="přehrát dříve nahraný záznam")
    parser.add_argument("--replay-speed", type=float, default=1.0, help="rychlost přehrávání záznamu")
    parser.add_argument("--data-dir", type=Path, default=None, help="složka s uloženými daty tratí")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    forward = None
    if args.forward:
        host, _, port = args.forward.rpartition(":")
        forward = (host, int(port))

    print("\n  ZÁVODNÍ INŽENÝR · F1 25")
    print("  ───────────────────────────────────────────")
    print(f"  Telemetrie:  UDP port {args.udp_port} (formát 2025)")
    primary, others = lan_addresses()
    if primary:
        print(f"  iPad:        http://{primary}:{args.http_port}")
    else:
        print(f"  iPad:        http://<IP-tohoto-PC>:{args.http_port}  (zjisti příkazem ipconfig)")
    for address in others:
        print(f"  (jiná síť:   http://{address}:{args.http_port} – použij jen když ta nahoře nejde)")
    print(f"  Tento PC:    http://localhost:{args.http_port}")
    print("  Ukončení:    Ctrl+C\n")

    if args.demo:
        threading.Thread(
            target=simulator.run, kwargs={"port": args.udp_port, "speed": args.demo_speed, "laps": args.demo_laps,
                                          "fmt": args.demo_format},
            daemon=True).start()
        print("  DEMO: běží simulovaný závod.\n")

    try:
        data_dir = args.data_dir or default_dir()
        # The demo must not overwrite your real reference laps and setups
        store = Store(None if args.demo else data_dir)
        print(f"  Data tratí:  {data_dir}" if not args.demo else "  Data tratí:  v demu se neukládají")
        record_dir = data_dir / "recordings" if args.record else None
        if args.replay and not args.replay.is_file():
            sys.exit(f"Záznam {args.replay} neexistuje.")
        asyncio.run(serve("0.0.0.0", args.http_port, args.udp_host, args.udp_port, forward, store,
                          record_dir, args.replay, args.replay_speed))
    except KeyboardInterrupt:
        print("\n  Inženýr končí. Díky za závod!")
    except OSError as exc:
        # Most common case: the port is taken (another copy of the app, or SimHub on 20777)
        print(f"\n  Nepodařilo se otevřít port: {exc}")
        print("  Běží už aplikace jinde? Případně zkus jiný port: --http-port / --udp-port.")
        sys.exit(1)


if __name__ == "__main__":
    main()
