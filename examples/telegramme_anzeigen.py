#!/usr/bin/env python3
"""Empfangene CAN-Telegramme in der Konsole mitlesen.

Zeigt beide Haelften des Pakets zusammen: die Simulation spielt das Geraet,
`SignalReader` liest mit -- derselbe Code, der spaeter am Pruefstand laeuft.

    python examples/telegramme_anzeigen.py
    python examples/telegramme_anzeigen.py --messages inverter_speed --intervall 0
    python examples/telegramme_anzeigen.py --sekunden 5

Gegen die echte Hardware ist es dasselbe Skript ohne Simulation:

    python examples/telegramme_anzeigen.py --ohne-simulation \
        --interface pcan --channel PCAN_USBBUS1
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from can_integration import (
    InvalidFrameError,
    Reading,
    SignalReader,
    SignalTimeoutError,
    load_json,
)
from can_integration.sim import FromRecording, Recording, SimulatedDevice

#: Das Projekt liegt eine Ebene ueber diesem Skript.
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "CAN-Logs" / "0000309.TXT"
DEFAULT_CATALOG = ROOT / "catalog.example.json"

#: Zwei Telegramme reichen, um etwas zu sehen: eines mit Drehzahl und Moment,
#: eines mit Strom, Spannung und Temperatur. Alle sechs zyklischen Telegramme
#: waeren rund 550 Zeilen je Sekunde.
DEFAULT_MESSAGES = ("inverter_speed", "inverter_status_3")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--messages",
        nargs="+",
        default=list(DEFAULT_MESSAGES),
        metavar="NAME",
        help=f"Katalognamen (Vorgabe: {' '.join(DEFAULT_MESSAGES)})",
    )
    parser.add_argument(
        "--intervall",
        type=float,
        default=0.25,
        metavar="SEKUNDEN",
        help=(
            "hoechstens eine Zeile je Telegramm und Intervall; 0 zeigt jedes "
            "empfangene Telegramm (Vorgabe: 0.25)"
        ),
    )
    parser.add_argument(
        "--sekunden",
        type=float,
        default=0.0,
        help="nach dieser Zeit beenden; 0 laeuft bis Strg+C (Vorgabe: 0)",
    )
    parser.add_argument("--log", default=str(DEFAULT_LOG), help="Aufzeichnung")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG), help="Katalog")
    parser.add_argument("--interface", default="virtual", help="python-can-Interface")
    parser.add_argument("--channel", default="beispiel", help="Kanal des Interfaces")
    parser.add_argument(
        "--ohne-simulation",
        action="store_true",
        dest="ohne_simulation",
        help="nicht simulieren, sondern einen vorhandenen Bus mitlesen",
    )
    return parser.parse_args()


def start_simulation(args: argparse.Namespace, catalog) -> SimulatedDevice:
    """Das simulierte Geraet, angetrieben vom gemessenen Verlauf."""
    recording = Recording.from_file(args.log)
    device = SimulatedDevice.from_recording(
        recording,
        catalog=catalog,
        behaviour=FromRecording.from_recording(recording, catalog=catalog),
        interface=args.interface,
        channel=args.channel,
    )
    device.start()
    return device


def format_values(message, reading: Reading) -> str:
    """Eine Zeile je Telegramm, Einheiten aus dem Katalog."""
    parts = []
    for signal in message.signals:
        unit = f" {signal.unit}" if signal.unit else ""
        parts.append(f"{signal.name}={reading.values[signal.name]:g}{unit}")
    return "  ".join(parts)


def main() -> int:
    args = parse_args()
    catalog = load_json(args.catalog)

    simulation = None
    if not args.ohne_simulation:
        simulation = start_simulation(args, catalog)
        print(
            f"Simulation laeuft auf {args.interface}/{args.channel}, "
            f"gespeist aus {Path(args.log).name}."
        )

    definitions = {name: catalog[name] for name in args.messages}
    print(f"Lese mit: {', '.join(m.label for m in definitions.values())}")
    print("Strg+C zum Beenden.\n")

    empfangen = 0
    gezeigt = 0
    zuletzt: dict[str, float] = {}
    ende = time.monotonic() + args.sekunden if args.sekunden else None

    try:
        with SignalReader(
            args.messages,
            interface=args.interface,
            channel=args.channel,
            catalog=catalog,
        ) as reader:
            while ende is None or time.monotonic() < ende:
                try:
                    reading = reader.read(timeout=1.0)
                except SignalTimeoutError:
                    print("  kein Telegramm innerhalb 1 s")
                    continue
                except InvalidFrameError as error:
                    print(f"  ungueltiges Telegramm: {error}")
                    continue

                empfangen += 1
                jetzt = time.monotonic()
                if jetzt - zuletzt.get(reading.message, -1e9) < args.intervall:
                    continue
                zuletzt[reading.message] = jetzt
                gezeigt += 1

                print(
                    f"{time.strftime('%H:%M:%S')}  {reading.message:<20} "
                    f"{format_values(definitions[reading.message], reading)}"
                )
    except KeyboardInterrupt:
        print()
    finally:
        if simulation is not None:
            simulation.stop()

    uebersprungen = empfangen - gezeigt
    print(
        f"\n{empfangen} Telegramme empfangen, {gezeigt} angezeigt"
        + (f", {uebersprungen} wegen --intervall uebersprungen." if uebersprungen else ".")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
