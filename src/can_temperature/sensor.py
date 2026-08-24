"""Synchronous access to temperature telegrams on a CAN bus."""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any

import can

from .protocol import DEFAULT_TEMPERATURE_OFFSET, decode_temperature

DEFAULT_INTERFACE = "pcan"
DEFAULT_CHANNEL = "PCAN_USBBUS1"
DEFAULT_BITRATE = 1_000_000
EXTENDED_ID_MASK = 0x1FFFFFFF


class TemperatureTimeoutError(TimeoutError):
    """Raised when no matching temperature telegram arrives in time."""


def is_temperature_frame(message: can.Message, arbitration_id: int) -> bool:
    """Return whether the message can carry a temperature for that ID."""
    return (
        message.arbitration_id == arbitration_id
        and message.is_extended_id
        and not message.is_error_frame
        and not message.is_remote_frame
    )


class TemperatureSensor:
    """Read a temperature from one extended CAN arbitration ID.

    If ``bus`` is supplied, its lifecycle remains with the caller and the bus
    parameters must not be given, because an existing bus cannot be
    reconfigured. Otherwise the sensor opens a python-can bus lazily and shuts
    it down on ``close``.

    ``temperature_offset`` selects where in the eight-byte payload the
    temperature lives; it varies by arbitration ID and must be confirmed
    against the real telegram (see ``KNOWN_TEMPERATURE_IDS`` in ``protocol``).
    """

    def __init__(
        self,
        arbitration_id: int,
        *,
        bus: can.BusABC | None = None,
        interface: str | None = None,
        channel: str | None = None,
        bitrate: int | None = None,
        temperature_offset: int = DEFAULT_TEMPERATURE_OFFSET,
    ) -> None:
        if not 0 <= arbitration_id <= EXTENDED_ID_MASK:
            raise ValueError("arbitration_id must be a valid 29-bit CAN ID")
        if bus is not None and (interface, channel, bitrate) != (None, None, None):
            raise TypeError(
                "bus cannot be combined with interface, channel or bitrate"
            )
        if bitrate is None:
            bitrate = DEFAULT_BITRATE
        if bitrate <= 0:
            raise ValueError("bitrate must be greater than zero")
        if temperature_offset < 0:
            raise ValueError("temperature_offset must not be negative")

        self.arbitration_id = arbitration_id
        self.temperature_offset = temperature_offset
        self._bus = bus
        self._owns_bus = bus is None
        self._bus_config: dict[str, Any] = {
            "interface": DEFAULT_INTERFACE if interface is None else interface,
            "channel": DEFAULT_CHANNEL if channel is None else channel,
            "bitrate": bitrate,
            "can_filters": [
                {
                    "can_id": arbitration_id,
                    "can_mask": EXTENDED_ID_MASK,
                    "extended": True,
                }
            ],
        }

    def connect(self) -> can.BusABC:
        """Open the configured CAN bus if it is not open yet and return it."""
        if self._bus is None:
            self._bus = can.Bus(**self._bus_config)
        return self._bus

    def read_temperature(self, timeout: float = 1.0) -> float:
        """Wait for the next matching frame and return degrees Celsius.

        Raises ``TemperatureTimeoutError`` if no matching frame arrives within
        ``timeout`` and ``InvalidTemperatureFrameError`` if a matching frame is
        too short to contain a temperature.
        """
        if timeout < 0:
            raise ValueError("timeout must not be negative")

        bus = self.connect()
        deadline = time.monotonic() + timeout
        first_receive = True

        while first_receive or time.monotonic() < deadline:
            first_receive = False
            remaining = max(0.0, deadline - time.monotonic())
            message = bus.recv(timeout=remaining)

            if message is None:
                break
            if not is_temperature_frame(message, self.arbitration_id):
                continue

            return decode_temperature(message.data, offset=self.temperature_offset)

        raise TemperatureTimeoutError(
            f"no extended CAN frame 0x{self.arbitration_id:08X} "
            f"received within {timeout:g} s"
        )

    def close(self) -> None:
        """Release a CAN bus opened by this sensor."""
        if self._bus is not None and self._owns_bus:
            self._bus.shutdown()
            self._bus = None

    def __enter__(self) -> TemperatureSensor:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
