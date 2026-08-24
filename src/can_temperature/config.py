"""Measurement configuration, loadable from a JSON file."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from .protocol import DEFAULT_TEMPERATURE_OFFSET

DEFAULT_MAX_AGE = 1.0
DEFAULT_STARTUP_TIMEOUT = 5.0


@dataclass(frozen=True)
class Config:
    """Parameters of one temperature measurement.

    ``interface``, ``channel`` and ``bitrate`` stay ``None`` unless the JSON
    file overrides the defaults of the CAN backend. ``temperature_offset``
    varies by arbitration ID and must be confirmed against the real telegram.
    ``limit_celsius`` is carried for the calling measurement application; this
    package never enforces a temperature limit on its own.
    """

    arbitration_id: int
    interface: str | None = None
    channel: str | None = None
    bitrate: int | None = None
    temperature_offset: int = DEFAULT_TEMPERATURE_OFFSET
    max_age: float = DEFAULT_MAX_AGE
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT
    limit_celsius: float | None = None

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> Config:
        """Build a configuration from an already parsed JSON object."""
        unknown = sorted(set(values) - {field.name for field in fields(cls)})
        if unknown:
            raise ValueError(f"unknown configuration keys: {', '.join(unknown)}")
        if "arbitration_id" not in values:
            raise ValueError("configuration requires 'arbitration_id'")

        arguments = dict(values)
        arguments["arbitration_id"] = _parse_can_id(values["arbitration_id"])
        return cls(**arguments)

    @classmethod
    def from_json(cls, path: str | Path) -> Config:
        """Load a configuration from a JSON file containing one object."""
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError(f"{path}: configuration must be a JSON object")

        try:
            return cls.from_dict(document)
        except ValueError as error:
            raise ValueError(f"{path}: {error}") from None


def _parse_can_id(value: object) -> int:
    """Accept both a JSON number and a string such as ``"0x1A000003"``."""
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            raise ValueError(
                f"arbitration_id {value!r} is not a valid CAN ID"
            ) from None
    if isinstance(value, int):
        return value

    raise ValueError("arbitration_id must be a number or a string")
