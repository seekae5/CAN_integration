#!/usr/bin/env python3
"""Diagnosewerkzeug: Telegramme anzeigen, Katalog auflisten, Sollwert setzen.

Dient dazu, Verkabelung, Bitrate und Arbitration-IDs gegen die echte Hardware
zu pruefen, bevor eine Messautomatisierung darauf aufbaut. Nutzt denselben
Katalog und dieselbe Konfiguration wie eine spaetere Messanwendung.

Nutzung:
    can-integration --list
    can-integration --config config.json
    can-integration --config config.json --timeout 2.0
    can-integration --config config.json --set rpm_target=1000
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .bus import DEFAULT_BITRATE, DEFAULT_CHANNEL, SignalTimeoutError
from .config import Config
from .device import Device
from .reader import SignalReader
from .signals import InvalidFrameError, InvalidValueError, Message


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="can-integration", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "--config",
        help="Pfad zur JSON-Konfiguration; ohne Angabe gilt --messages",
    )
    parser.add_argument(
        "--messages",
        nargs="+",
        metavar="NAME",
        help="Katalognamen, wenn keine Konfigurationsdatei benutzt wird",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="Wartezeit pro Telegramm in Sekunden (Vorgabe: 2.0)",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="SIGNAL=WERT",
        help="Sollwert senden und beenden; mehrfach angebbar",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="bekannte CAN-IDs auflisten und beenden",
    )
    return parser.parse_args(argv)


def load_config(args: argparse.Namespace) -> Config:
    """Konfiguration aus der Datei oder aus --messages."""
    if args.config:
        try:
            return Config.from_json(args.config)
        except FileNotFoundError as error:
            raise SystemExit(f"Datei nicht gefunden: {error.filename}")
        except ValueError as error:
            raise SystemExit(f"Ungueltige Konfiguration: {error}")

    if not args.messages:
        raise SystemExit(
            "weder --config noch --messages angegeben; "
            "--list zeigt die bekannten Namen"
        )
    try:
        return Config(messages=tuple(args.messages))
    except ValueError as error:
        raise SystemExit(f"Ungueltige Auswahl: {error}")


def parse_assignment(text: str) -> tuple[str, float]:
    """``"rpm_target=1000"`` -> ``("rpm_target", 1000.0)``."""
    name, separator, raw = text.partition("=")
    if not separator or not name.strip():
        raise SystemExit(f"--set erwartet SIGNAL=WERT, nicht {text!r}")
    try:
        return name.strip(), float(raw)
    except ValueError:
        raise SystemExit(f"--set {text!r}: {raw!r} ist keine Zahl")


def format_values(definition: Message, values: dict[str, float]) -> str:
    """Eine Zeile je Telegramm, Einheiten aus dem Katalog."""
    parts = []
    for signal in definition.signals:
        unit = f" {signal.unit}" if signal.unit else ""
        parts.append(f"{signal.name}={values[signal.name]:g}{unit}")
    return "  ".join(parts)


def send_setpoints(config: Config, assignments: list[str]) -> int:
    """Sollwerte senden und beenden -- die Inbetriebnahme des Schreibwegs."""
    values = [parse_assignment(text) for text in assignments]
    with Device.from_config(config) as device:
        for name, value in values:
            try:
                device.set(name, value)
            except (LookupError, ValueError) as error:
                print(f"{name}: {error}", file=sys.stderr)
                return 1
            print(f"gesendet: {name} = {value:g}")
    return 0


def watch(config: Config, timeout: float) -> int:
    """Jedes empfangene Telegramm ausgeben, bis Strg+C kommt."""
    definitions = {message.name: message for message in config.definitions}
    print(
        f"Oeffne {config.channel or DEFAULT_CHANNEL} bei "
        f"{config.bitrate or DEFAULT_BITRATE} bit/s, warte auf "
        f"{', '.join(message.label for message in config.definitions)} ..."
    )

    with SignalReader.from_config(config) as reader:
        print("Verbunden. Strg+C zum Beenden.\n")

        while True:
            now = time.strftime("%H:%M:%S")
            try:
                reading = reader.read(timeout=timeout)
            except SignalTimeoutError:
                print(f"{now}  kein Telegramm innerhalb {timeout:g} s")
                continue
            except InvalidFrameError as error:
                print(f"{now}  ungueltiges Telegramm: {error}")
                continue

            values = format_values(definitions[reading.message], reading.values)
            print(f"{now}  {reading.message:<20} {values}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.list:
        # Der Katalog der Konfiguration, falls eine angegeben ist: nur so
        # erscheinen auch die Definitionen aus der JSON-Erweiterung.
        catalog = load_config(args).catalog if args.config else None
        if catalog is None:
            from .catalog import DEFAULT_CATALOG

            catalog = DEFAULT_CATALOG
        print(catalog.describe())
        return 0

    config = load_config(args)
    if args.set:
        return send_setpoints(config, args.set)
    return watch(config, args.timeout)


def run() -> None:
    """Einstiegspunkt des Konsolenbefehls."""
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nBeendet.")
        sys.exit(0)
    except InvalidValueError as error:
        sys.exit(f"Ungueltiger Wert: {error}")


if __name__ == "__main__":
    run()
