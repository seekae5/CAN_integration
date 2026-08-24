"""Public API for CAN temperature measurements."""

from .config import Config
from .monitor import Reading, TemperatureMonitor, TemperatureStaleError
from .protocol import (
    DEFAULT_TEMPERATURE_OFFSET,
    KNOWN_TEMPERATURE_IDS,
    InvalidTemperatureFrameError,
    decode_temperature,
)
from .sensor import TemperatureSensor, TemperatureTimeoutError

__all__ = [
    "DEFAULT_TEMPERATURE_OFFSET",
    "KNOWN_TEMPERATURE_IDS",
    "Config",
    "InvalidTemperatureFrameError",
    "Reading",
    "TemperatureMonitor",
    "TemperatureSensor",
    "TemperatureStaleError",
    "TemperatureTimeoutError",
    "decode_temperature",
]
