"""Synchronous access to temperature telegrams on a CAN bus."""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any

import can

from .protocol import decode_temperature

DEFAULT_INTERFACE = "pcan"
DEFAULT_CHANNEL = "PCAN_USBBUS1"
DEFAULT_BITRATE = 1_000_000
EXTENDED_ID_MASK = 0x1FFFFFFF


class TemperatureTimeoutError(TimeoutError):
    """Raised when no matching temperature telegram arrives in time."""


class TemperatureSensor:
    """Read a temperature from one extended CAN arbitration ID.

    If ``bus`` is supplied, its lifecycle remains with the caller. Otherwise
    the sensor opens a python-can bus lazily and shuts it down on ``close``.
    """

    def __init__(
        self,
        arbitration_id: int,
        *,
        bus: can.BusABC | None = None,
        interface: str = DEFAULT_INTERFACE,
        channel: str = DEFAULT_CHANNEL,
        bitrate: int = DEFAULT_BITRATE,
    ) -> None:
        if not 0 <= arbitration_id <= EXTENDED_ID_MASK:
            raise ValueError("arbitration_id must be a valid 29-bit CAN ID")
        if bitrate <= 0:
            raise ValueError("bitrate must be greater than zero")

        self.arbitration_id = arbitration_id
        self._bus = bus
        self._owns_bus = bus is None
        self._bus_config: dict[str, Any] = {
            "interface": interface,
            "channel": channel,
            "bitrate": bitrate,
            "can_filters": [
                {
                    "can_id": arbitration_id,
                    "can_mask": EXTENDED_ID_MASK,
                    "extended": True,
                }
            ],
        }

    def connect(self) -> None:
        """Open the configured CAN bus if it is not open yet."""
        if self._bus is None:
            self._bus = can.Bus(**self._bus_config)

    def read_temperature(self, timeout: float = 1.0) -> float:
        """Wait for the next matching frame and return degrees Celsius."""
        if timeout < 0:
            raise ValueError("timeout must not be negative")

        self.connect()
        assert self._bus is not None

        deadline = time.monotonic() + timeout
        first_receive = True

        while first_receive or time.monotonic() < deadline:
            first_receive = False
            remaining = max(0.0, deadline - time.monotonic())
            message = self._bus.recv(timeout=remaining)

            if message is None:
                break
            if message.arbitration_id != self.arbitration_id:
                continue
            if not message.is_extended_id:
                continue
            if message.is_error_frame or message.is_remote_frame:
                continue

            return decode_temperature(message.data)

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

