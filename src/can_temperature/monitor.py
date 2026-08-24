"""Background monitoring of one temperature telegram for safety interlocks."""

from __future__ import annotations

import threading
import time
from types import TracebackType
from typing import NamedTuple

import can

from .config import DEFAULT_MAX_AGE, DEFAULT_STARTUP_TIMEOUT, Config
from .protocol import (
    DEFAULT_TEMPERATURE_OFFSET,
    InvalidTemperatureFrameError,
    decode_temperature,
)
from .sensor import TemperatureSensor, TemperatureTimeoutError, is_temperature_frame

POLL_INTERVAL = 0.1


class Reading(NamedTuple):
    """One decoded temperature with a bus and a local time reference.

    ``timestamp`` comes from the CAN backend and is meant for logging; its
    epoch depends on the backend. ``monotonic`` is taken from
    ``time.monotonic`` on reception and is what the age is measured against,
    so a backend without timestamps or a system clock change cannot distort it.
    """

    timestamp: float
    celsius: float
    monotonic: float


class TemperatureStaleError(TimeoutError):
    """Raised when the newest temperature is too old to be acted upon."""


class TemperatureMonitor:
    """Keep the newest temperature of one arbitration ID available.

    A background thread drains the bus continuously and keeps only the latest
    frame, so a slow caller always reads the current temperature instead of a
    growing backlog. Reading a temperature never blocks, but fails when no
    fresh value is available: a missing sensor must stop a measurement instead
    of silently freezing the last known value.
    """

    def __init__(
        self,
        arbitration_id: int,
        *,
        max_age: float = DEFAULT_MAX_AGE,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        bus: can.BusABC | None = None,
        interface: str | None = None,
        channel: str | None = None,
        bitrate: int | None = None,
        temperature_offset: int = DEFAULT_TEMPERATURE_OFFSET,
    ) -> None:
        if max_age <= 0:
            raise ValueError("max_age must be greater than zero")
        if startup_timeout < 0:
            raise ValueError("startup_timeout must not be negative")

        self._sensor = TemperatureSensor(
            arbitration_id,
            bus=bus,
            interface=interface,
            channel=channel,
            bitrate=bitrate,
            temperature_offset=temperature_offset,
        )
        self._max_age = max_age
        self._startup_timeout = startup_timeout
        self._latest: Reading | None = None
        self._failure: Exception | None = None
        self._decode_error: InvalidTemperatureFrameError | None = None
        self._received = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_config(
        cls,
        config: Config,
        *,
        bus: can.BusABC | None = None,
    ) -> TemperatureMonitor:
        """Build a monitor from a configuration, optionally on a shared bus."""
        return cls(
            config.arbitration_id,
            max_age=config.max_age,
            startup_timeout=config.startup_timeout,
            bus=bus,
            interface=config.interface,
            channel=config.channel,
            bitrate=config.bitrate,
            temperature_offset=config.temperature_offset,
        )

    @property
    def arbitration_id(self) -> int:
        return self._sensor.arbitration_id

    @property
    def temperature_offset(self) -> int:
        return self._sensor.temperature_offset

    @property
    def max_age(self) -> float:
        """Age in seconds beyond which a reading is refused."""
        return self._max_age

    @property
    def celsius(self) -> float:
        """The newest temperature, if it is fresh enough to be trusted.

        Raises ``TemperatureStaleError`` if no frame arrived within
        ``max_age``, and re-raises a failure of the receiving thread.
        """
        if self._failure is not None:
            raise self._failure

        reading = self._latest
        if reading is None:
            raise TemperatureStaleError(
                f"no temperature received yet from "
                f"0x{self.arbitration_id:08X}{self._decode_hint()}"
            )

        age = time.monotonic() - reading.monotonic
        if age > self._max_age:
            raise TemperatureStaleError(
                f"newest temperature of 0x{self.arbitration_id:08X} is "
                f"{age:.3f} s old, allowed are {self._max_age:g} s"
                f"{self._decode_hint()}"
            )

        return reading.celsius

    @property
    def latest(self) -> Reading | None:
        """The newest reading regardless of its age, or None. Never raises."""
        return self._latest

    @property
    def age(self) -> float:
        """Seconds since the newest reading, or infinity if there is none."""
        reading = self._latest
        if reading is None:
            return float("inf")
        return time.monotonic() - reading.monotonic

    def start(self) -> None:
        """Open the bus and wait for the first temperature.

        Blocking here means a wrong ID, bitrate or cabling surfaces before the
        measurement starts rather than in the middle of it.
        """
        if self._thread is not None:
            raise RuntimeError("monitor is already running")

        self._stop.clear()
        self._received.clear()
        self._failure = None
        self._sensor.connect()

        self._thread = threading.Thread(
            target=self._receive,
            name=f"can-temperature-0x{self.arbitration_id:08X}",
            daemon=True,
        )
        self._thread.start()

        received = self._received.wait(self._startup_timeout)
        failure = self._failure
        if failure is not None or not received:
            self.stop()
            if failure is not None:
                raise failure
            raise TemperatureTimeoutError(
                f"no extended CAN frame 0x{self.arbitration_id:08X} received "
                f"within {self._startup_timeout:g} s{self._decode_hint()}"
            )

    def stop(self) -> None:
        """Stop the receiving thread before releasing an owned bus."""
        self._stop.set()

        thread = self._thread
        if thread is not None:
            thread.join(timeout=POLL_INTERVAL * 10)
            self._thread = None

        self._sensor.close()

    def _receive(self) -> None:
        bus = self._sensor.connect()
        try:
            while not self._stop.is_set():
                message = bus.recv(timeout=POLL_INTERVAL)
                if message is None:
                    continue
                if not is_temperature_frame(message, self.arbitration_id):
                    continue

                try:
                    celsius = decode_temperature(
                        message.data, offset=self._sensor.temperature_offset
                    )
                except InvalidTemperatureFrameError as error:
                    # Deliberately keep the previous reading: it ages out and
                    # the staleness check stops the measurement.
                    self._decode_error = error
                    continue

                self._latest = Reading(
                    timestamp=message.timestamp or time.time(),
                    celsius=celsius,
                    monotonic=time.monotonic(),
                )
                self._received.set()
        except Exception as error:
            # Keep the failure instead of letting the thread die unnoticed.
            self._failure = error
            self._received.set()

    def _decode_hint(self) -> str:
        if self._decode_error is None:
            return ""
        return f" (last decoding error: {self._decode_error})"

    def __enter__(self) -> TemperatureMonitor:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()
