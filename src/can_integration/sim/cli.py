#!/usr/bin/env python3
"""Simulierter Pruefstand: eine Aufzeichnung abspielen oder untersuchen.

Ersetzt die CAN-Hardware, solange der reale Aufbau nicht zur Verfuegung steht.
Der abgespielte Verkehr stammt aus einer echten Messung, laeuft ueber denselben
Katalog wie die Bibliothek und wird von `can-integration` gelesen, als kaeme er
vom Geraet.

Nutzung:
    can-integration-sim inspect CAN-Logs/0000309.TXT
    can-integration-sim inspect CAN-Logs/0000309.TXT --catalog catalog.example.json
    can-integration-sim replay CAN-Logs/0000309.TXT --loop

    # in einem zweiten Terminal, gegen dieselbe --interface/--channel-Angabe:
    can-integration --messages inverter_speed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import can

from ..catalog import DEFAULT_CATALOG, Catalog, load_json
from ..signals import format_can_id
from .device import RecordedInverter, SimulatedDevice, running_moment
from .logfile import LogFormatError, Recording
from .replay import DIRECTIONS, LogPlayer
from .transport import SIM_CHANNEL, SIM_INTERFACE


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="can-integration-sim", description=__doc__.splitlines()[0]
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    inspect = subcommands.add_parser(
        "inspect", help="Aufzeichnung auswerten, ohne etwas zu senden"
    )
    inspect.add_argument("log", help="Pfad zur CL1000-Textaufzeichnung")
    inspect.add_argument(
        "--catalog",
        help="JSON-Katalogerweiterung, die zusaetzlich geladen wird",
    )

    replay = subcommands.add_parser(
        "replay", help="Aufzeichnung zeitrichtig auf einen Bus spielen"
    )
    replay.add_argument("log", help="Pfad zur CL1000-Textaufzeichnung")
    replay.add_argument(
        "--catalog",
        help="JSON-Katalogerweiterung, die zusaetzlich geladen wird",
    )
    replay.add_argument(
        "--interface",
        default=SIM_INTERFACE,
        help=f"python-can-Interface (Vorgabe: {SIM_INTERFACE})",
    )
    replay.add_argument(
        "--channel",
        default=SIM_CHANNEL,
        help=f"Kanal des Interfaces (Vorgabe: {SIM_CHANNEL})",
    )
    replay.add_argument(
        "--bitrate",
        type=int,
        help="Bitrate; ohne Angabe die der Aufzeichnung",
    )
    replay.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Zeitraffer; 1.0 = Originalzeit, 0 = so schnell wie moeglich",
    )
    replay.add_argument(
        "--loop", action="store_true", help="am Ende von vorne beginnen"
    )
    replay.add_argument(
        "--gap",
        type=float,
        default=0.0,
        help="Pause zwischen zwei Durchlaeufen in Sekunden",
    )
    replay.add_argument(
        "--direction",
        choices=DIRECTIONS,
        default="device",
        help=(
            "'device' spielt nur, was ein Geraet sendet (Vorgabe); "
            "'all' spielt die Aufzeichnung unveraendert"
        ),
    )
    device = subcommands.add_parser(
        "device",
        help="ein antwortendes Geraet simulieren statt nur abzuspielen",
    )
    device.add_argument("log", help="Aufzeichnung, aus der Zustand und Zyklen stammen")
    device.add_argument(
        "--catalog",
        help="JSON-Katalogerweiterung, die zusaetzlich geladen wird",
    )
    device.add_argument(
        "--interface",
        default=SIM_INTERFACE,
        help=f"python-can-Interface (Vorgabe: {SIM_INTERFACE})",
    )
    device.add_argument(
        "--channel",
        default=SIM_CHANNEL,
        help=f"Kanal des Interfaces (Vorgabe: {SIM_CHANNEL})",
    )
    device.add_argument(
        "--bitrate", type=int, help="Bitrate; ohne Angabe die der Aufzeichnung"
    )
    device.add_argument(
        "--running-at",
        type=float,
        metavar="SEKUNDEN",
        help=(
            "Zeitpunkt in der Aufzeichnung, dessen Zustand als Anfangszustand "
            "gilt; ohne Angabe der Augenblick vor dem aufgezeichneten Stopp"
        ),
    )
    return parser.parse_args(argv)


def load_catalog(path: str | None) -> Catalog:
    """Eingebauter Katalog, bei Bedarf um eine JSON-Datei erweitert."""
    if path is None:
        return DEFAULT_CATALOG
    try:
        return load_json(path)
    except FileNotFoundError as error:
        raise SystemExit(f"Datei nicht gefunden: {error.filename}")
    except ValueError as error:
        raise SystemExit(f"Ungueltiger Katalog: {error}")


def load_recording(path: str) -> Recording:
    try:
        return Recording.from_file(path)
    except FileNotFoundError:
        raise SystemExit(f"Datei nicht gefunden: {path}")
    except LogFormatError as error:
        raise SystemExit(f"Aufzeichnung nicht lesbar: {error}")


def inspect(args: argparse.Namespace) -> int:
    """Was steckt in der Aufzeichnung, und was davon versteht der Katalog?"""
    recording = load_recording(args.log)
    catalog = load_catalog(args.catalog)

    print(f"{Path(args.log).name}: {len(recording)} Frames ueber "
          f"{recording.duration:.3f} s")
    if recording.start_time is not None:
        print(f"Aufzeichnungsbeginn: {recording.start_time:%Y-%m-%d %H:%M:%S}")
    if recording.bitrate is not None:
        print(f"Bitrate: {recording.bitrate} bit/s")

    cycles = recording.cycle_times()
    print("\nTelegramme:")
    print(f"  {'ID':>12}  {'Frames':>7}  {'Zyklus':>9}  Katalogname")
    coverage = recording.coverage(catalog)
    for key, count in recording.counts().items():
        cycle = cycles.get(key)
        cycle_text = f"{cycle:.1f} ms" if cycle is not None else "-"
        message = coverage.known.get(key)
        name = message.name if message is not None else "(nicht im Katalog)"
        print(
            f"  {format_can_id(key[0], extended=key[1]):>12}  {count:>7}  "
            f"{cycle_text:>9}  {name}"
        )

    print()
    print(coverage.report())
    return 0


def replay(args: argparse.Namespace) -> int:
    """Die Aufzeichnung senden, bis sie zu Ende ist oder Strg+C kommt."""
    recording = load_recording(args.log)
    catalog = load_catalog(args.catalog)

    try:
        player = LogPlayer(
            recording,
            interface=args.interface,
            channel=args.channel,
            bitrate=args.bitrate,
            speed=args.speed,
            loop=args.loop,
            gap=args.gap,
            direction=args.direction,
            catalog=catalog,
        )
    except (TypeError, ValueError) as error:
        raise SystemExit(f"Ungueltige Angabe: {error}")

    unknown = recording.coverage(catalog).unknown
    print(
        f"{Path(args.log).name}: {len(player.frames)} von {len(recording)} "
        f"Frames werden gespielt"
    )
    print(f"  {player.describe_skipped()}")
    if unknown:
        print(
            f"  {len(unknown)} Telegrammtyp(en) ohne Katalogeintrag werden "
            f"gesendet, aber von keiner Messung dekodiert"
        )
    if args.speed == 0:
        tempo = ", so schnell wie moeglich"
    elif args.speed != 1.0:
        tempo = f", {args.speed:g}-fache Geschwindigkeit"
    else:
        tempo = ""
    print(f"  Bus: {args.interface} / {args.channel} bei {player.bitrate} bit/s{tempo}")
    if args.interface == SIM_INTERFACE:
        print(
            "  Hinweis: 'virtual' reicht nur innerhalb eines Prozesses. Fuer "
            "ein zweites Terminal --interface udp_multicast benutzen."
        )

    # Den Bus oeffnen, bevor "Spiele ab" auf dem Schirm steht: eine fehlende
    # Abhaengigkeit oder ein belegter Kanal soll sich vorher zeigen.
    try:
        player.connect()
    except can.CanInterfaceNotImplementedError as error:
        raise SystemExit(
            f"Interface {args.interface!r} nicht verfuegbar: {error}\n"
            f"Fuer den Zwei-Prozess-Betrieb: pip install 'can-integration[sim]'"
        )
    except (can.CanError, OSError) as error:
        raise SystemExit(f"Bus nicht verfuegbar: {error}")

    print("\nSpiele ab. Strg+C zum Beenden.")
    try:
        sent = player.run()
    except (can.CanError, OSError) as error:
        raise SystemExit(f"Senden fehlgeschlagen: {error}")
    finally:
        player.close()

    print(f"Fertig: {sent} Frames gesendet.")
    return 0


def announcing(inner: RecordedInverter) -> object:
    """Kommandos mitschreiben, bevor das Modell sie ausfuehrt."""

    def handler(device: SimulatedDevice, message, values) -> None:
        shown = "  ".join(f"{name}={value:g}" for name, value in values.items())
        print(f"  <- {message.label}  {message.name}  {shown}")
        inner(device, message, values)
        if inner.ignored:
            name, code = inner.ignored.pop()
            print(f"     (Kommando {code:#06x} von {name} ist nicht nachgebildet)")

    return handler


def device(args: argparse.Namespace) -> int:
    """Ein antwortendes Geraet, bis Strg+C kommt."""
    recording = load_recording(args.log)
    catalog = load_catalog(args.catalog)

    running_at = args.running_at
    if running_at is None:
        running_at = running_moment(recording, catalog=catalog)

    try:
        simulated = SimulatedDevice.from_recording(
            recording,
            catalog=catalog,
            running_at=running_at,
            interface=args.interface,
            channel=args.channel,
            bitrate=args.bitrate if args.bitrate is not None else recording.bitrate,
        )
    except ValueError as error:
        raise SystemExit(f"Simulation nicht aufbaubar: {error}")

    handler = simulated.commands
    if isinstance(handler, RecordedInverter):
        simulated.commands = announcing(handler)  # type: ignore[assignment]

    print(
        f"{Path(args.log).name}: Anfangszustand bei t = {running_at:.3f} s "
        f"der Aufzeichnung"
    )
    print(f"  {len(simulated.cycles)} Telegramme werden zyklisch gesendet:")
    for cycle in simulated.cycles:
        quelle = "gemessen" if cycle.measured else "geschaetzt"
        print(
            f"    {cycle.message.label:<40} {cycle.period * 1000:8.1f} ms "
            f"({quelle}), DLC {cycle.payload_length}"
        )

    accepted = [message for message in catalog.values() if message.writable]
    if accepted:
        print(
            "  Kommandos werden angenommen von: "
            + ", ".join(message.name for message in accepted)
        )
    else:
        print(
            "  Der Katalog enthaelt kein writable-Telegramm: das Geraet kann "
            "nur senden, nicht antworten."
        )
    print(f"  Bus: {args.interface} / {args.channel} bei {simulated.bitrate} bit/s")

    try:
        simulated.connect()
    except can.CanInterfaceNotImplementedError as error:
        raise SystemExit(
            f"Interface {args.interface!r} nicht verfuegbar: {error}\n"
            f"Fuer den Zwei-Prozess-Betrieb: pip install 'can-integration[sim]'"
        )
    except (can.CanError, OSError) as error:
        raise SystemExit(f"Bus nicht verfuegbar: {error}")

    print("\nGeraet laeuft. Strg+C zum Beenden.")
    try:
        simulated.run()
    except (can.CanError, OSError) as error:
        raise SystemExit(f"Busfehler: {error}")
    finally:
        simulated.close()
        print(
            f"\n{simulated.sent} Telegramme gesendet, "
            f"{simulated.received} Kommandos empfangen."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "inspect":
        return inspect(args)
    if args.command == "device":
        return device(args)
    return replay(args)


def run() -> None:
    """Einstiegspunkt des Konsolenbefehls."""
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nBeendet.")
        sys.exit(0)


if __name__ == "__main__":
    run()
