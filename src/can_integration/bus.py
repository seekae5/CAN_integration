"""Bus lifecycle shared by the blocking reader and the background monitor.

Both directions of the bus run through here: :meth:`BusConnection.read`
receives one filtered telegram, :meth:`BusConnection.send` puts a command
telegram on the wire. Only messages the catalog declares ``writable`` can be
sent.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

import can

from .catalog import DEFAULT_CATALOG, Catalog
from .signals import CanFrame, Message, resolve_signal

DEFAULT_INTERFACE = "pcan"
DEFAULT_CHANNEL = "PCAN_USBBUS1"
DEFAULT_BITRATE = 1_000_000


class SignalTimeoutError(TimeoutError):
    """Raised when no matching telegram arrives in time."""


class Reading(NamedTuple):
    """One decoded telegram with a bus and a local time reference.

    ``timestamp`` comes from the CAN backend and is meant for logging; its
    epoch depends on the backend. ``monotonic`` is taken from
    ``time.monotonic`` on reception and is what the age is measured against,
    so a backend without timestamps or a system clock change cannot distort it.
    """

    message: str
    values: Mapping[str, float]
    timestamp: float
    monotonic: float


class BusConnection:
    """Owns or borrows a python-can bus filtered to a set of messages.

    If ``bus`` is supplied, its lifecycle remains with the caller and the bus
    parameters must not be given, because an existing bus cannot be
    reconfigured. Otherwise the bus is opened lazily and shut down on
    ``close``.
    """

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
        if bus is not None and (interface, channel, bitrate) != (None, None, None):
            raise TypeError(
                "bus cannot be combined with interface, channel or bitrate"
            )
        if bitrate is None:
            bitrate = DEFAULT_BITRATE
        if bitrate <= 0:
            raise ValueError("bitrate must be greater than zero")

        definitions = catalog.resolve(_as_names(messages))
        if not definitions:
            raise ValueError("at least one message must be monitored")

        seen: dict[str, Message] = {}
        for definition in definitions:
            if definition.name in seen:
                raise ValueError(f"message {definition.name!r} is listed twice")
            seen[definition.name] = definition

        self.messages = definitions
        self.catalog = catalog
        self._by_key = {definition.key: definition for definition in definitions}
        self._bus = bus
        self._owns_bus = bus is None
        # Gesendet wird aus dem Messthread und -- beim Auslösen des sicheren
        # Zustands -- aus dem Empfangsthread. Zwei Telegramme dürfen sich
        # dabei nicht überholen.
        self._send_lock = threading.Lock()
        self._bus_config: dict[str, Any] = {
            "interface": DEFAULT_INTERFACE if interface is None else interface,
            "channel": DEFAULT_CHANNEL if channel is None else channel,
            "bitrate": bitrate,
            "can_filters": [
                definition.can_filter for definition in definitions
            ],
        }

    @property
    def message_names(self) -> tuple[str, ...]:
        return tuple(definition.name for definition in self.messages)

    def connect(self) -> can.BusABC:
        """Open the configured CAN bus if it is not open yet and return it."""
        if self._bus is None:
            self._bus = can.Bus(**self._bus_config)
        return self._bus

    def close(self) -> None:
        """Release a CAN bus opened by this connection."""
        if self._bus is not None and self._owns_bus:
            self._bus.shutdown()
            self._bus = None

    def match(self, frame: CanFrame) -> Message | None:
        """Return the message a received frame belongs to, if any.

        The hardware filter already rejects foreign traffic, but a filter is
        an optimisation, not a guarantee: some backends widen it, and an
        injected bus may carry no filter at all.
        """
        if frame.is_error_frame or frame.is_remote_frame:
            return None
        return self._by_key.get((frame.arbitration_id, bool(frame.is_extended_id)))

    def message(self, message: str | Message) -> Message:
        """Resolve a name against the catalog; a ready-made message passes."""
        if isinstance(message, Message):
            return message
        return self.catalog[message]

    def send(
        self,
        message: str | Message,
        values: Mapping[str, float],
        *,
        timeout: float | None = None,
    ) -> None:
        """Encode ``values`` into ``message`` and put it on the bus.

        The message need not be one of the monitored ones -- receive filters
        do not restrict sending. It does have to be declared ``writable``,
        which is what keeps a command from being written onto a status ID.
        """
        definition = self.message(message)
        payload = definition.encode(values)
        frame = can.Message(
            arbitration_id=definition.arbitration_id,
            is_extended_id=definition.extended,
            data=payload,
        )
        with self._send_lock:
            self.connect().send(frame, timeout=timeout)

    def send_signal(
        self,
        name: str,
        value: float,
        *,
        timeout: float | None = None,
    ) -> Message:
        """Set one signal by name and send its message. Returns the message."""
        writable = [
            definition
            for definition in self.catalog.values()
            if definition.writable
        ]
        if not writable:
            raise ValueError(
                f"cannot set {name!r}: the catalog contains no writable "
                f"message; declare the command telegram with writable=True"
            )

        definition, signal = resolve_signal(writable, name)
        self.send(definition, {signal.name: value}, timeout=timeout)
        return definition

    def read(self, timeout: float) -> Reading | None:
        """Wait up to ``timeout`` for one matching frame and decode it.

        Returns ``None`` when the bus stays silent. Frames that match but
        cannot be decoded raise ``InvalidFrameError``.
        """
        bus = self.connect()
        deadline = time.monotonic() + timeout
        first_receive = True

        while first_receive or time.monotonic() < deadline:
            first_receive = False
            frame = bus.recv(timeout=max(0.0, deadline - time.monotonic()))
            if frame is None:
                return None

            definition = self.match(frame)
            if definition is None:
                continue

            return Reading(
                message=definition.name,
                values=definition.decode(frame.data),
                timestamp=frame.timestamp or time.time(),
                monotonic=time.monotonic(),
            )

        return None


def _as_names(
    messages: str | Message | Iterable[str | Message],
) -> Sequence[str | Message]:
    """Accept a single message as well as a list of them."""
    if isinstance(messages, (str, Message)):
        return (messages,)
    return tuple(messages)
