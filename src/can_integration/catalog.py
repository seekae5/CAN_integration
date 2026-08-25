"""The look-up table: which CAN ID means what.

This is the one file to touch when a new CAN function is added. Everything
else in the package works off the definitions collected here -- configuration,
bus filters, decoding and the names a measurement script reads values by.

Adding a function means appending one :class:`~can_integration.signals.Message`
below and listing it in :data:`BUILTIN_MESSAGES`. Nothing else has to change.
Definitions that only exist on one test bench do not belong here; put them in
a JSON file and load it with :func:`load_json`.

None of the layouts below are confirmed against vendor documentation. Each
entry names its ``source`` so a later reader can tell a documented layout from
one that was merely plausible at the test bench.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from .signals import (
    AmbiguousSignalError,
    Message,
    Signal,
    UnknownSignalError,
    format_can_id,
    parse_can_id,
    resolve_signal,
)


class UnknownMessageError(LookupError):
    """Raised when a message name does not exist in a catalog."""


# --------------------------------------------------------------------------
# Inverter status, 0x1A000001 and 0x1A000003
#
# Both IDs carry the same four little-endian uint16 values. Which physical
# temperature each one reports (MOSFET, power block, heat sink) is unconfirmed,
# so the names stay neutral and carry the last hex digit of the ID.
# The scaling of current and voltage was never established; those signals are
# therefore exposed as raw values without a unit.
# --------------------------------------------------------------------------

def _inverter_status(name: str, arbitration_id: int, source: str) -> Message:
    return Message(
        name=name,
        arbitration_id=arbitration_id,
        description="Inverter-Status: Strom, Zwischenkreisspannung, Temperatur",
        source=source,
        signals=(
            Signal(
                "iph_rms",
                offset=0,
                format="<H",
                description="Phasenstrom als Rohwert, Skalierung unbestätigt",
            ),
            Signal(
                "i_dc_flt",
                offset=2,
                format="<H",
                description="DC-Strom als Rohwert, Skalierung unbestätigt",
            ),
            Signal(
                "u_dc",
                offset=4,
                format="<H",
                description="Zwischenkreisspannung als Rohwert, "
                "Skalierung unbestätigt",
            ),
            Signal(
                "temperature",
                offset=6,
                format="<H",
                scale=0.01,
                unit="°C",
                description="Temperatur, 0.01 °C/Bit, Vorzeichen unbestätigt",
            ),
        ),
    )


INVERTER_STATUS_1 = _inverter_status(
    "inverter_status_1",
    0x1A000001,
    "Orientierung/temp_block.py, Layout <4H",
)

INVERTER_STATUS_3 = _inverter_status(
    "inverter_status_3",
    0x1A000003,
    "Orientierung/temp.py, Layout <4H",
)

#: Motor temperature, 0x1A000013. The only entry whose physical meaning was
#: checked: measured at the test bench and compared against the real motor
#: temperature. The remaining payload bytes are unknown and stay undeclared.
MOTOR_TEMPERATURE = Message(
    name="motor_temperature",
    arbitration_id=0x1A000013,
    description="Motortemperatur",
    source="am Prüfstand gemessen, gegen die reale Motortemperatur "
    "plausibilisiert; übrige Bytes unbekannt",
    signals=(
        Signal(
            "temperature",
            offset=0,
            format="<H",
            scale=0.01,
            unit="°C",
            description="Temperatur, 0.01 °C/Bit",
        ),
    ),
)

#: Speed and torque, 0x1A00000C. Same <4H layout as the status telegrams;
#: the torque scaling was never established.
INVERTER_SPEED = Message(
    name="inverter_speed",
    arbitration_id=0x1A00000C,
    description="Drehzahl-Sollwert, -Istwert, -Grenze und Drehmoment",
    source="Orientierung/rpm.py, Layout <4H",
    signals=(
        Signal("rpm_actual", offset=0, format="<H", unit="rpm"),
        Signal("rpm_target", offset=2, format="<H", unit="rpm"),
        Signal("rpm_max", offset=4, format="<H", unit="rpm"),
        Signal(
            "torque_actual",
            offset=6,
            format="<H",
            description="Drehmoment als Rohwert, Skalierung unbestätigt",
        ),
    ),
)

#: Thrust from the HX711 load cell, 0x003. The only standard 11-bit ID and the
#: only big-endian signed value in this catalog -- a reminder that byte order
#: belongs to the signal, not to the bus.
THRUST = Message(
    name="thrust",
    arbitration_id=0x003,
    extended=False,
    description="Gewicht der HX711-Wägezelle am Schubprüfstand",
    source="Orientierung/Schub_CAN.py, Rohwert aus dem C-Code des Mikrocontrollers",
    signals=(
        Signal(
            "weight",
            offset=0,
            format=">i",
            unit="g",
            description="Gewicht in Gramm, signed 32 Bit Big-Endian",
        ),
    ),
)

#: Every message this package ships with. Append new entries here.
BUILTIN_MESSAGES: tuple[Message, ...] = (
    INVERTER_STATUS_1,
    INVERTER_STATUS_3,
    MOTOR_TEMPERATURE,
    INVERTER_SPEED,
    THRUST,
)


class Catalog(Mapping[str, Message]):
    """A set of message definitions, addressable by name.

    Duplicate names and duplicate arbitration IDs are refused. A test bench
    that needs a corrected layout gives it its own name rather than silently
    shadowing an entry someone else relies on.
    """

    def __init__(self, messages: Iterable[Message] = ()) -> None:
        self._by_name: dict[str, Message] = {}
        self._by_key: dict[tuple[int, bool], Message] = {}
        for message in messages:
            self.add(message)

    def add(self, message: Message) -> None:
        """Register one message definition."""
        if message.name in self._by_name:
            raise ValueError(f"catalog already contains a message {message.name!r}")

        existing = self._by_key.get(message.key)
        if existing is not None:
            kind = "extended" if message.extended else "standard"
            raise ValueError(
                f"message {message.name!r} uses the same {kind} CAN ID "
                f"{format_can_id(message.arbitration_id, extended=message.extended)}"
                f" as {existing.name!r}"
            )

        self._by_name[message.name] = message
        self._by_key[message.key] = message

    def __getitem__(self, name: str) -> Message:
        try:
            return self._by_name[name]
        except KeyError:
            raise UnknownMessageError(
                f"unknown message {name!r}; the catalog knows "
                f"{', '.join(sorted(self._by_name))}"
            ) from None

    # ``Mapping`` implements both of these through ``__getitem__`` and only
    # catches ``KeyError``, which UnknownMessageError deliberately is not --
    # its message would then be printed with an extra pair of quotes.
    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def get(  # type: ignore[override]
        self, name: str, default: Message | None = None
    ) -> Message | None:
        return self._by_name.get(name, default)

    def __iter__(self) -> Iterator[str]:
        return iter(self._by_name)

    def __len__(self) -> int:
        return len(self._by_name)

    def by_id(self, arbitration_id: int, *, extended: bool = True) -> Message:
        """Look up a message by the ID it occupies on the bus."""
        try:
            return self._by_key[(arbitration_id, extended)]
        except KeyError:
            kind = "extended" if extended else "standard"
            raise UnknownMessageError(
                f"no {kind} message "
                f"{format_can_id(arbitration_id, extended=extended)} in the catalog"
            ) from None

    def resolve(self, names: Iterable[str | Message]) -> tuple[Message, ...]:
        """Turn catalog names -- or ready-made messages -- into definitions."""
        return tuple(
            name if isinstance(name, Message) else self[name] for name in names
        )

    def find_signal(self, name: str) -> tuple[Message, Signal]:
        """Find a signal across the whole catalog, qualified if ambiguous."""
        return resolve_signal(tuple(self._by_name.values()), name)

    def extended_with(self, messages: Iterable[Message]) -> Catalog:
        """Return a copy of this catalog with further definitions added."""
        catalog = Catalog(self._by_name.values())
        for message in messages:
            catalog.add(message)
        return catalog

    def describe(self) -> str:
        """A readable listing of the table, for commissioning and support."""
        return "\n".join(message.describe() for message in self._by_name.values())


#: The catalog used everywhere unless a caller passes its own.
DEFAULT_CATALOG = Catalog(BUILTIN_MESSAGES)


_MESSAGE_KEYS = frozenset(
    {"name", "arbitration_id", "signals", "extended", "description", "source"}
)
_SIGNAL_KEYS = frozenset(
    {"name", "offset", "format", "scale", "bias", "unit", "description"}
)


def message_from_dict(values: Mapping[str, Any]) -> Message:
    """Build a message definition from an already parsed JSON object."""
    _reject_unknown(values, _MESSAGE_KEYS, "message")
    for required in ("name", "arbitration_id", "signals"):
        if required not in values:
            raise ValueError(f"message definition requires {required!r}")

    raw_signals = values["signals"]
    if not isinstance(raw_signals, list):
        raise ValueError(f"message {values['name']!r}: 'signals' must be a list")

    arguments = dict(values)
    try:
        arguments["arbitration_id"] = parse_can_id(values["arbitration_id"])
    except ValueError as error:
        raise ValueError(f"message {values['name']!r}: {error}") from None
    arguments["signals"] = tuple(signal_from_dict(signal) for signal in raw_signals)

    return Message(**arguments)


def signal_from_dict(values: Mapping[str, Any]) -> Signal:
    """Build a signal definition from an already parsed JSON object."""
    if not isinstance(values, Mapping):
        raise ValueError("a signal definition must be a JSON object")
    _reject_unknown(values, _SIGNAL_KEYS, "signal")
    for required in ("name", "offset"):
        if required not in values:
            raise ValueError(f"signal definition requires {required!r}")

    return Signal(**values)


def load_json(path: str | Path, *, base: Catalog = DEFAULT_CATALOG) -> Catalog:
    """Load extra message definitions and return ``base`` plus those.

    The file holds one JSON object with a ``"messages"`` list, each entry
    shaped like the definitions in this module::

        {"messages": [{"name": "...", "arbitration_id": "0x...",
                       "signals": [{"name": "...", "offset": 0}]}]}

    Unknown keys are refused, and a name or ID that already exists in ``base``
    is an error rather than a silent override.
    """
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path}: catalog must be a JSON object")

    _reject_unknown(document, frozenset({"messages"}), "catalog", location=str(path))
    if "messages" not in document:
        raise ValueError(f"{path}: catalog requires 'messages'")
    if not isinstance(document["messages"], list):
        raise ValueError(f"{path}: 'messages' must be a list")

    try:
        return base.extended_with(
            message_from_dict(entry) for entry in document["messages"]
        )
    except (ValueError, TypeError) as error:
        raise ValueError(f"{path}: {error}") from None


def _reject_unknown(
    values: Mapping[str, Any],
    allowed: frozenset[str],
    what: str,
    *,
    location: str = "",
) -> None:
    """A typo must not fall back to a default: it changes what is measured."""
    unknown = sorted(set(values) - allowed)
    if unknown:
        prefix = f"{location}: " if location else ""
        raise ValueError(
            f"{prefix}unknown {what} keys: {', '.join(unknown)}"
        )


__all__ = [
    "BUILTIN_MESSAGES",
    "DEFAULT_CATALOG",
    "INVERTER_SPEED",
    "INVERTER_STATUS_1",
    "INVERTER_STATUS_3",
    "MOTOR_TEMPERATURE",
    "THRUST",
    "AmbiguousSignalError",
    "Catalog",
    "UnknownMessageError",
    "UnknownSignalError",
    "load_json",
    "message_from_dict",
    "signal_from_dict",
]
