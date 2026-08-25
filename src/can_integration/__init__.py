"""Public API for CAN measurements.

Which CAN ID means what is declared in :mod:`can_integration.catalog`; adding
a new CAN function means adding one entry there.
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
from .monitor import SignalMonitor, StaleSignalError
from .reader import SignalReader
from .signals import (
    AmbiguousSignalError,
    InvalidFrameError,
    Message,
    Signal,
    UnknownSignalError,
    resolve_signal,
    signal_keys,
)

__all__ = [
    "BUILTIN_MESSAGES",
    "DEFAULT_BITRATE",
    "DEFAULT_CATALOG",
    "DEFAULT_CHANNEL",
    "DEFAULT_INTERFACE",
    "AmbiguousSignalError",
    "BusConnection",
    "Catalog",
    "Config",
    "InvalidFrameError",
    "Message",
    "Reading",
    "Signal",
    "SignalMonitor",
    "SignalReader",
    "SignalTimeoutError",
    "StaleSignalError",
    "UnknownMessageError",
    "UnknownSignalError",
    "load_json",
    "resolve_signal",
    "signal_keys",
]
