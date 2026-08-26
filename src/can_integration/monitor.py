"""Background monitoring of CAN telegrams for safety interlocks."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Mapping
from types import MappingProxyType, TracebackType

import can

from .bus import BusConnection, Reading, SignalTimeoutError
from .catalog import DEFAULT_CATALOG, Catalog
from .config import DEFAULT_MAX_AGE, DEFAULT_STARTUP_TIMEOUT, Config
from .signals import InvalidFrameError, Message, Signal, resolve_signal, signal_keys

POLL_INTERVAL = 0.1


class StaleSignalError(TimeoutError):
    """Raised when the newest value is too old to be acted upon."""


class SignalMonitor:
    """Keep the newest telegram of every monitored message available.

    A background thread drains the bus continuously and keeps only the latest
    telegram per message, so a slow caller always reads current values instead
    of a growing backlog. Reading a value never blocks, but fails when no
    fresh one is available: a missing sensor must stop a measurement instead
    of silently freezing the last known value.

    Values are addressed by signal name. A name stays plain as long as only
    one monitored message provides it, and becomes qualified
    (``"inverter_status_3.temperature"``) as soon as two do.

    Sending goes through ``connection``: the receiving thread only ever calls
    ``recv``, so a command may be written from the measurement thread while
    the monitor keeps draining the bus.
    """

    def __init__(
        self,
        messages: str | Message | Iterable[str | Message],
        *,
        max_age: float = DEFAULT_MAX_AGE,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        bus: can.BusABC | None = None,
        interface: str | None = None,
        channel: str | None = None,
        bitrate: int | None = None,
        catalog: Catalog = DEFAULT_CATALOG,
    ) -> None:
        if max_age <= 0:
            raise ValueError("max_age must be greater than zero")
        if startup_timeout < 0:
            raise ValueError("startup_timeout must not be negative")

        self._connection = BusConnection(
            messages,
            bus=bus,
            interface=interface,
            channel=channel,
            bitrate=bitrate,
            catalog=catalog,
        )
        self._keys = signal_keys(self._connection.messages)
        self._max_age = max_age
        self._startup_timeout = startup_timeout

        self._lock = threading.Lock()
        self._readings: dict[str, Reading] = {}
        self._decode_errors: dict[str, InvalidFrameError] = {}
        self._failure: Exception | None = None
        self._complete = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_config(
        cls,
        config: Config,
        *,
        bus: can.BusABC | None = None,
    ) -> SignalMonitor:
        """Build a monitor from a configuration, optionally on a shared bus."""
        return cls(
            config.definitions,
            max_age=config.max_age,
            startup_timeout=config.startup_timeout,
            bus=bus,
            interface=None if bus is not None else config.interface,
            channel=None if bus is not None else config.channel,
            bitrate=None if bus is not None else config.bitrate,
            catalog=config.catalog,
        )

    @property
    def messages(self) -> tuple[Message, ...]:
        return self._connection.messages

    @property
    def connection(self) -> BusConnection:
        """The bus underneath -- the way out for sending while monitoring."""
        return self._connection

    @property
    def signal_names(self) -> tuple[str, ...]:
        """Every readable name, in message order. Stable as a CSV header."""
        return tuple(self._keys)

    @property
    def max_age(self) -> float:
        """Age in seconds beyond which a value is refused."""
        return self._max_age

    def signal(self, name: str) -> Signal:
        """The definition behind a name -- unit, offset, scaling."""
        return resolve_signal(self.messages, name)[1]

    def value(self, name: str) -> float:
        """The newest value of one signal, if it is fresh enough to be trusted.

        Raises ``StaleSignalError`` if the carrying telegram did not arrive
        within ``max_age``, and re-raises a failure of the receiving thread.
        """
        message, signal = self._locate(name)
        reading = self._fresh_reading(message)
        return reading.values[signal.name]

    def values(self) -> dict[str, float]:
        """Every monitored signal at once, under the names of this monitor.

        Fails as a whole if any telegram is stale, which is what a measurement
        row needs: half a row of fresh values and half a row of old ones is
        worse than no row at all.
        """
        if self._failure is not None:
            raise self._failure

        readings = {
            message.name: self._fresh_reading(message) for message in self.messages
        }
        return {
            key: readings[message.name].values[signal.name]
            for key, (message, signal) in self._keys.items()
        }

    def reading(self, name: str) -> Reading | None:
        """The newest telegram carrying ``name``, at any age. Never raises."""
        message, _ = self._locate(name)
        with self._lock:
            return self._readings.get(message.name)

    def readings(self) -> Mapping[str, Reading]:
        """The newest telegram per message, at any age. Never raises."""
        with self._lock:
            return MappingProxyType(dict(self._readings))

    def age(self, name: str) -> float:
        """Seconds since the telegram carrying ``name``, inf if none came."""
        reading = self.reading(name)
        if reading is None:
            return float("inf")
        return time.monotonic() - reading.monotonic

    def start(self) -> None:
        """Open the bus and wait for one telegram of every message.

        Blocking here means a wrong ID, bitrate or cabling surfaces before the
        measurement starts rather than in the middle of it.
        """
        if self._thread is not None:
            raise RuntimeError("monitor is already running")

        self._stop.clear()
        self._complete.clear()
        self._failure = None
        self._connection.connect()

        self._thread = threading.Thread(
            target=self._receive,
            name=f"can-integration-{'+'.join(self._connection.message_names)}",
            daemon=True,
        )
        self._thread.start()

        complete = self._complete.wait(self._startup_timeout)
        failure = self._failure
        if failure is not None or not complete:
            missing = self._missing()
            self.stop()
            if failure is not None:
                raise failure
            raise SignalTimeoutError(
                f"no telegram received within {self._startup_timeout:g} s for "
                f"{self._describe(missing)}{self._decode_hint(missing)}"
            )

    def stop(self) -> None:
        """Stop the receiving thread before releasing an owned bus."""
        self._stop.set()

        thread = self._thread
        if thread is not None:
            thread.join(timeout=POLL_INTERVAL * 10)
            self._thread = None

        self._connection.close()

    def _receive(self) -> None:
        bus = self._connection.connect()
        try:
            while not self._stop.is_set():
                frame = bus.recv(timeout=POLL_INTERVAL)
                if frame is None:
                    continue

                message = self._connection.match(frame)
                if message is None:
                    continue

                try:
                    values = message.decode(frame.data)
                except InvalidFrameError as error:
                    # Deliberately keep the previous telegram: it ages out and
                    # the staleness check stops the measurement.
                    self._decode_errors[message.name] = error
                    continue

                reading = Reading(
                    message=message.name,
                    values=values,
                    timestamp=frame.timestamp or time.time(),
                    monotonic=time.monotonic(),
                )
                with self._lock:
                    self._readings[message.name] = reading
                    complete = len(self._readings) == len(self.messages)
                if complete:
                    self._complete.set()
        except Exception as error:
            # Keep the failure instead of letting the thread die unnoticed.
            self._failure = error
            self._complete.set()

    def _locate(self, name: str) -> tuple[Message, Signal]:
        located = self._keys.get(name)
        if located is not None:
            return located
        return resolve_signal(self.messages, name)

    def _fresh_reading(self, message: Message) -> Reading:
        if self._failure is not None:
            raise self._failure

        with self._lock:
            reading = self._readings.get(message.name)

        if reading is None:
            raise StaleSignalError(
                f"no telegram received yet for {self._describe([message.name])}"
                f"{self._decode_hint([message.name])}"
            )

        age = time.monotonic() - reading.monotonic
        if age > self._max_age:
            raise StaleSignalError(
                f"newest telegram of {self._describe([message.name])} is "
                f"{age:.3f} s old, allowed are {self._max_age:g} s"
                f"{self._decode_hint([message.name])}"
            )

        return reading

    def _missing(self) -> list[str]:
        with self._lock:
            return [
                message.name
                for message in self.messages
                if message.name not in self._readings
            ]

    def _describe(self, names: Iterable[str]) -> str:
        lookup = {message.name: message for message in self.messages}
        return ", ".join(lookup[name].label for name in names)

    def _decode_hint(self, names: Iterable[str]) -> str:
        errors = [
            f"{name}: {self._decode_errors[name]}"
            for name in names
            if name in self._decode_errors
        ]
        if not errors:
            return ""
        return f" (last decoding error -- {'; '.join(errors)})"

    def __enter__(self) -> SignalMonitor:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()
