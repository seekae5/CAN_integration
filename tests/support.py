"""Shared fakes so the tests run without CAN hardware."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any


def message(
    arbitration_id: int,
    data: bytes,
    *,
    extended: bool = True,
    timestamp: float = 0.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        arbitration_id=arbitration_id,
        data=data,
        is_extended_id=extended,
        is_error_frame=False,
        is_remote_frame=False,
        timestamp=timestamp,
    )


class FakeBus:
    """Hands out prepared messages, then keeps timing out like a quiet bus."""

    def __init__(
        self,
        messages: list[Any],
        *,
        error: Exception | None = None,
        error_gate: threading.Event | None = None,
    ) -> None:
        self.messages = list(messages)
        self.error = error
        # Without a gate the error is raised as soon as the messages run out;
        # with one, the test decides when the bus starts failing.
        self.error_gate = error_gate
        self.shutdown_calls = 0

    def recv(self, timeout: float | None = None) -> Any:
        if self.messages:
            return self.messages.pop(0)
        if self.error is not None and (
            self.error_gate is None or self.error_gate.is_set()
        ):
            raise self.error

        # Do not spin: an empty bus behaves like one that stays silent.
        time.sleep(min(timeout or 0.0, 0.01))
        return None

    def shutdown(self) -> None:
        self.shutdown_calls += 1
