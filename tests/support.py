"""Shared fakes so the tests run without CAN hardware."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

#: Payloads of the <4H inverter telegrams, temperature in bytes 6-7.
PAYLOAD_50_CELSIUS = bytes.fromhex("64 00 C8 00 2C 01 88 13")
PAYLOAD_82_CELSIUS = bytes.fromhex("00 00 00 00 00 00 66 20")

#: motor_temperature carries its temperature in bytes 0-1.
PAYLOAD_MOTOR_35_CELSIUS = bytes.fromhex("C8 0D 00 00 00 00 00 00")


def frame(
    arbitration_id: int,
    data: bytes,
    *,
    extended: bool = True,
    timestamp: float = 0.0,
    error: bool = False,
    remote: bool = False,
) -> SimpleNamespace:
    """A stand-in for ``can.Message`` with only the attributes we read."""
    return SimpleNamespace(
        arbitration_id=arbitration_id,
        data=data,
        is_extended_id=extended,
        is_error_frame=error,
        is_remote_frame=remote,
        timestamp=timestamp,
    )


class FakeBus:
    """Hands out prepared frames, then keeps timing out like a quiet bus."""

    def __init__(
        self,
        frames: list[Any],
        *,
        error: Exception | None = None,
        error_gate: threading.Event | None = None,
    ) -> None:
        self.frames = list(frames)
        self.error = error
        # Without a gate the error is raised as soon as the frames run out;
        # with one, the test decides when the bus starts failing.
        self.error_gate = error_gate
        self.shutdown_calls = 0
        #: Frames handed to ``send``, so a test can assert what went out.
        self.sent: list[Any] = []
        self.send_error: Exception | None = None

    def recv(self, timeout: float | None = None) -> Any:
        if self.frames:
            return self.frames.pop(0)
        if self.error is not None and (
            self.error_gate is None or self.error_gate.is_set()
        ):
            raise self.error

        # Do not spin: an empty bus behaves like one that stays silent.
        time.sleep(min(timeout or 0.0, 0.01))
        return None

    def send(self, message: Any, timeout: float | None = None) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(message)

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def wait_for(condition, timeout: float = 2.0) -> bool:
    """Give the receiving thread a bounded amount of time to catch up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return False
