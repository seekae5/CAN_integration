#!/usr/bin/env python3
"""Diagnosewerkzeug: gibt die Temperatur fortlaufend in der Konsole aus.

Dient dazu, Verkabelung, Bitrate und Arbitration-ID gegen die echte Hardware
zu pruefen, bevor eine Messautomatisierung darauf aufbaut. Nutzt dieselbe
Config- und TemperatureSensor-API wie eine spaetere Messanwendung.

Nutzung:
    python examples/print_temperature.py
    python examples/print_temperature.py --config config.json --timeout 2.0
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from can_temperature import (
    Config,
    InvalidTemperatureFrameError,
    TemperatureSensor,
    TemperatureTimeoutError,
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        config = Config.from_json(args.config)
    except FileNotFoundError:
        print(f"Konfigurationsdatei nicht gefunden: {args.config}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"Ungueltige Konfiguration: {error}", file=sys.stderr)
        return 1

    print(
        f"Oeffne {config.channel or 'PCAN_USBBUS1'} bei "
        f"{config.bitrate or 1_000_000} bit/s, warte auf "
        f"ID 0x{config.arbitration_id:08X} ..."
    )

    with TemperatureSensor(
        config.arbitration_id,
        interface=config.interface,
        channel=config.channel,
        bitrate=config.bitrate,
        temperature_offset=config.temperature_offset,
    ) as sensor:
        print("Verbunden. Strg+C zum Beenden.\n")

        while True:
            now = time.strftime("%H:%M:%S")
            try:
                celsius = sensor.read_temperature(timeout=args.timeout)
            except TemperatureTimeoutError:
                print(f"{now}  kein Telegramm innerhalb {args.timeout:g} s")
                continue
            except InvalidTemperatureFrameError as error:
                print(f"{now}  ungueltiges Telegramm: {error}")
                continue

            print(f"{now}  {celsius:6.2f} °C")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nBeendet.")
        sys.exit(0)
