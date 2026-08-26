"""Öffentliche Schnittstelle für CAN-Messungen.

Der kurze Weg -- so sieht ein Messskript aus:

    from can_integration import connect, get, set_signal

    connect(["motor_temperature", "inverter_speed"])
    print(get("temperature"))
    set_signal("rpm_target", 1000)

Welche CAN-ID was bedeutet, steht ausschließlich in
:mod:`can_integration.catalog`. Eine neue CAN-Funktion ist ein Eintrag dort
und danach ohne Codeänderung über ihren Signalnamen erreichbar.

Darunter liegen die ausführlicheren Bausteine für Fälle, die mehr Kontrolle
brauchen: :class:`SignalMonitor` (laufende Messung), :class:`SignalReader`
(blockierender Einzelabruf) und :class:`Config` (JSON-Konfiguration).
"""

from .bus import (
    DEFAULT_BITRATE,
    DEFAULT_CHANNEL,
    DEFAULT_INTERFACE,
    BusConnection,
    Reading,
    SignalTimeoutError,
)
from .catalog import (
    BUILTIN_MESSAGES,
    DEFAULT_CATALOG,
    Catalog,
    UnknownMessageError,
    load_json,
)
from .config import Config
from .device import (
    Device,
    NotConnectedError,
    age,
    connect,
    device,
    disconnect,
    get,
    get_rpm,
    get_rpm_target,
    get_temperature,
    get_thrust,
    get_torque,
    send,
    set_rpm,
    set_signal,
    values,
)
from .monitor import SignalMonitor, StaleSignalError
from .reader import SignalReader
from .signals import (
    AmbiguousSignalError,
    InvalidFrameError,
    InvalidValueError,
    Message,
    ReadOnlyMessageError,
    Signal,
    UnknownSignalError,
    resolve_signal,
    signal_keys,
)

__all__ = [
    # Die einfache Schnittstelle
    "Device",
    "connect",
    "disconnect",
    "device",
    "get",
    "set_signal",
    "send",
    "values",
    "age",
    # Benannte Kurzformen
    "get_temperature",
    "get_rpm",
    "get_rpm_target",
    "get_torque",
    "get_thrust",
    "set_rpm",
    # Katalog: welche CAN-ID was bedeutet
    "BUILTIN_MESSAGES",
    "DEFAULT_CATALOG",
    "Catalog",
    "Message",
    "Signal",
    "load_json",
    "resolve_signal",
    "signal_keys",
    # Bausteine mit mehr Kontrolle
    "BusConnection",
    "Config",
    "Reading",
    "SignalMonitor",
    "SignalReader",
    "DEFAULT_BITRATE",
    "DEFAULT_CHANNEL",
    "DEFAULT_INTERFACE",
    # Fehler
    "AmbiguousSignalError",
    "InvalidFrameError",
    "InvalidValueError",
    "NotConnectedError",
    "ReadOnlyMessageError",
    "SignalTimeoutError",
    "StaleSignalError",
    "UnknownMessageError",
    "UnknownSignalError",
]
