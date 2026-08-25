#!/usr/bin/env python3
"""Diagnosewerkzeug: gibt die dekodierten Telegramme in der Konsole aus.

Dient dazu, Verkabelung, Bitrate und Arbitration-IDs gegen die echte Hardware
zu pruefen, bevor eine Messautomatisierung darauf aufbaut. Nutzt dieselbe
Config- und Reader-API wie eine spaetere Messanwendung.

Nutzung:
    python examples/print_signals.py --list
    python examples/print_signals.py --config config.json
    python examples/print_signals.py --config config.json --timeout 2.0
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from can_integration import (
    Config,
    InvalidFrameError,
    Message,
    SignalReader,
    SignalTimeoutError,
)

# Repo-Root, nicht das aktuelle Arbeitsverzeichnis: der Default muss auch
# funktionieren, wenn das Skript von anderswo aus gestartet wird (z. B. aus
# einer PyCharm-Laufkonfiguration mit abweichendem Arbeitsverzeichnis).
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.example.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"Pfad zur JSON-Konfiguration (Vorgabe: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="Wartezeit pro Telegramm in Sekunden (Vorgabe: 2.0)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="bekannte CAN-IDs auflisten und beenden",
    )
    return parser.parse_args()


def load_config(path: str) -> Config:
    try:
        return Config.from_json(path)
    except FileNotFoundError as error:
        raise SystemExit(f"Datei nicht gefunden: {error.filename}")
    except ValueError as error:
        raise SystemExit(f"Ungueltige Konfiguration: {error}")


def format_values(definition: Message, values: dict[str, float]) -> str:
    """Eine Zeile je Telegramm, Einheiten aus dem Katalog."""
    parts = []
    for signal in definition.signals:
        unit = f" {signal.unit}" if signal.unit else ""
        parts.append(f"{signal.name}={values[signal.name]:g}{unit}")
    return "  ".join(parts)


def main() -> int:
    args = parse_args()
    config = load_config(args.config)

    if args.list:
        print(config.catalog.describe())
        return 0

    definitions = {message.name: message for message in config.definitions}
    print(
        f"Oeffne {config.channel or 'PCAN_USBBUS1'} bei "
        f"{config.bitrate or 1_000_000} bit/s, warte auf "
        f"{', '.join(message.label for message in config.definitions)} ..."
    )

    with SignalReader.from_config(config) as reader:
        print("Verbunden. Strg+C zum Beenden.\n")

        while True:
            now = time.strftime("%H:%M:%S")
            try:
                reading = reader.read(timeout=args.timeout)
            except SignalTimeoutError:
                print(f"{now}  kein Telegramm innerhalb {args.timeout:g} s")
                continue
            except InvalidFrameError as error:
                print(f"{now}  ungueltiges Telegramm: {error}")
                continue

            values = format_values(definitions[reading.message], reading.values)
            print(f"{now}  {reading.message:<20} {values}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nBeendet.")
        sys.exit(0)
