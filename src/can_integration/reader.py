"""Blocking access to CAN telegrams, for commissioning and diagnostics.

Waits for the next matching telegram and returns it. This is the right tool
for checking cabling, bitrate and arbitration IDs, and the wrong one for a
running measurement -- see :class:`~can_integration.monitor.SignalMonitor`.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from types import TracebackType

import can

from .bus import BusConnection, Reading, SignalTimeoutError
from .catalog import DEFAULT_CATALOG, Catalog
from .config import Config
from .signals import Message, resolve_signal, signal_keys


class SignalReader:
    """Read decoded telegrams of one or more messages, one at a time."""

    def __init__(
        self,
        messages: str | Message | Iterable[str | Message],
        *,
        bus: can.BusABC | None = None,
        interface: str | None = None,
        channel: str | None = None,
        bitrate: int | None = None,
        catalog: Catalog = DEFAULT_CATALOG,
    ) -> None:
        self._connection = BusConnection(
            messages,
            bus=bus,
            interface=interface,
            channel=channel,
            bitrate=bitrate,
            catalog=catalog,
        )

    @classmethod
    def from_config(
        cls,
        config: Config,
        *,
        bus: can.BusABC | None = None,
    ) -> SignalReader:
        """Build a reader from a configuration, optionally on a shared bus."""
        return cls(
            config.definitions,
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
    def signal_names(self) -> tuple[str, ...]:
        """Names this reader accepts, qualified where a name is ambiguous."""
        return tuple(signal_keys(self.messages))

    def connect(self) -> can.BusABC:
        """Open the configured CAN bus if it is not open yet and return it."""
        return self._connection.connect()

    def read(self, timeout: float = 1.0) -> Reading:
        """Wait for the next telegram of any configured message.

        Raises ``SignalTimeoutError`` if nothing matching arrives within
        ``timeout`` and ``InvalidFrameError`` if a matching telegram is too
        short for its declared signals.
        """
        if timeout < 0:
            raise ValueError("timeout must not be negative")

        reading = self._connection.read(timeout)
        if reading is None:
            raise SignalTimeoutError(
                f"no telegram of {', '.join(self._connection.message_names)} "
                f"received within {timeout:g} s"
            )
        return reading

    def read_signal(self, name: str, timeout: float = 1.0) -> float:
        """Wait for the telegram carrying ``name`` and return that value.

        Telegrams of the other configured messages are skipped, so the timeout
        applies to the requested signal and not to bus traffic in general.
        """
        if timeout < 0:
            raise ValueError("timeout must not be negative")

        message, signal = resolve_signal(self.messages, name)
        deadline = time.monotonic() + timeout

        while True:
            reading = self._connection.read(max(0.0, deadline - time.monotonic()))
            if reading is not None and reading.message == message.name:
                return reading.values[signal.name]
            if reading is None or time.monotonic() >= deadline:
                raise SignalTimeoutError(
                    f"no {signal.name!r} received within {timeout:g} s: "
                    f"{message.label} stayed away"
                )

    def close(self) -> None:
        """Release a CAN bus opened by this reader."""
        self._connection.close()

    def __enter__(self) -> SignalReader:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
