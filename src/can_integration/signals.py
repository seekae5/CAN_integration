"""Pure coding of CAN payloads: signal and message definitions.

This module knows nothing about buses or hardware. Everything here can be
exercised with synthetic payloads, which is what keeps the byte-level
assumptions testable without a PCAN adapter attached.

A :class:`Message` describes one arbitration ID, a :class:`Signal` describes
one value inside its payload. Adding support for a new CAN function means
writing one more :class:`Message` into the catalog -- no code changes here.

Both directions live here: ``decode`` turns a received payload into physical
values, ``encode`` turns physical values into a payload to be sent. The two
are exact inverses of ``raw * scale + bias``, so one catalog entry describes a
telegram completely, whether it is read or written.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from math import isfinite
from struct import Struct
from struct import error as StructError
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

#: Separator for qualified signal names, ``"<message>.<signal>"``. Needed when
#: two monitored messages provide a signal of the same name.
QUALIFIER = "."

#: ``struct`` codes that store an integer. Everything else (``f``, ``d``) is
#: written as a float and is therefore not rounded to a raw step.
_INTEGER_CODES = frozenset("bBhHiIlLqQnNP")

#: Bit masks of the two CAN addressing schemes.
EXTENDED_ID_MASK = 0x1FFFFFFF
STANDARD_ID_MASK = 0x7FF


class InvalidFrameError(ValueError):
    """Raised when a matching CAN frame cannot contain the expected signal."""


class InvalidValueError(ValueError):
    """Raised when a physical value cannot be encoded into its signal."""


class ReadOnlyMessageError(ValueError):
    """Raised when a message that is not declared writable should be sent.

    Sending onto a status ID of a running inverter is the one mistake this
    package can make that damages hardware, so a telegram must say in the
    catalog that it is a command before it can leave the adapter.
    """


class UnknownSignalError(LookupError):
    """Raised when a signal name does not exist in the given messages."""


class AmbiguousSignalError(LookupError):
    """Raised when a plain signal name is provided by several messages."""


@runtime_checkable
class CanFrame(Protocol):
    """The part of ``can.Message`` this package actually relies on.

    Stated structurally so that decoding stays independent of python-can and
    can be driven with plain stand-ins in tests.
    """

    arbitration_id: int
    data: bytes
    is_extended_id: bool
    is_error_frame: bool
    is_remote_frame: bool
    timestamp: float


@lru_cache(maxsize=None)
def _struct_for(format: str) -> Struct:
    """Compile and cache a struct format that describes exactly one value."""
    try:
        packer = Struct(format)
    except StructError as error:
        raise ValueError(f"invalid struct format {format!r}: {error}") from None

    if len(packer.unpack(bytes(packer.size))) != 1:
        raise ValueError(
            f"struct format {format!r} must describe exactly one value; "
            f"declare one Signal per value instead"
        )
    return packer


def format_can_id(arbitration_id: int, *, extended: bool = True) -> str:
    """Render a CAN ID at the width of its addressing scheme."""
    width = 8 if extended else 3
    return f"0x{arbitration_id:0{width}X}"


def parse_can_id(value: object) -> int:
    """Accept both a JSON number and a string such as ``"0x1A000003"``."""
    if isinstance(value, bool):
        raise ValueError("arbitration_id must be a number or a string")
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            raise ValueError(f"{value!r} is not a valid CAN ID") from None
    if isinstance(value, int):
        return value

    raise ValueError("arbitration_id must be a number or a string")


@dataclass(frozen=True)
class Signal:
    """One value inside a CAN payload.

    ``format`` is a :mod:`struct` format describing a single value, so byte
    order and signedness are stated explicitly per signal instead of being
    assumed globally: ``"<H"`` is the little-endian ``uint16`` of the inverter
    telegrams, ``">i"`` the big-endian ``int32`` of the load cell.

    The physical value is ``raw * scale + bias``. ``bias`` covers the common
    case of a sensor that transmits, say, degrees Celsius shifted by -40.

    ``default`` only matters when the signal is written. A command telegram
    usually carries several fields while a call such as ``set("rpm_target",
    1000)`` names only one; every other signal must then say what it should
    contain. A signal without a default has to be passed explicitly, so no
    field of a command is ever filled with a silent zero.
    """

    name: str
    offset: int
    format: str = "<H"
    scale: float = 1.0
    bias: float = 0.0
    unit: str = ""
    description: str = ""
    default: float | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a signal needs a name")
        if QUALIFIER in self.name:
            raise ValueError(
                f"signal name {self.name!r} must not contain {QUALIFIER!r}: "
                f"the character separates message from signal in qualified names"
            )
        if self.offset < 0:
            raise ValueError(f"signal {self.name!r}: offset must not be negative")
        if self.scale == 0:
            raise ValueError(f"signal {self.name!r}: scale must not be zero")
        _struct_for(self.format)
        if self.default is not None:
            # Fail while the catalog is being built, not on the first send.
            self.raw(self.default)

    @property
    def size(self) -> int:
        """Width of the encoded value in bytes."""
        return _struct_for(self.format).size

    @property
    def end(self) -> int:
        """Payload length this signal requires."""
        return self.offset + self.size

    def decode(self, payload: bytes | bytearray | memoryview) -> float:
        """Decode this signal from a payload, scaled into its unit."""
        packer = _struct_for(self.format)
        if len(payload) < self.end:
            raise InvalidFrameError(
                f"CAN payload too short for signal {self.name!r}: expected at "
                f"least {self.end} bytes for {self.format!r} at offset "
                f"{self.offset}, got {len(payload)}"
            )

        (raw,) = packer.unpack_from(payload, self.offset)
        return raw * self.scale + self.bias

    @property
    def is_integer(self) -> bool:
        """Whether the payload holds this value as an integer."""
        return self.format.lstrip("<>=!@").lstrip("0123456789") in _INTEGER_CODES

    def raw(self, value: float) -> int | float:
        """Inverse of :meth:`decode`: physical value -> raw payload value.

        An integer signal rounds to the nearest representable step; the step
        is ``scale``, so a 0.01 unit/bit signal cannot carry more resolution
        than that no matter what the caller passes.
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidValueError(
                f"signal {self.name!r}: value must be a number, got {value!r}"
            )
        if not isfinite(value):
            # NaN and inf survive neither round() nor pack() with a useful
            # message, and as a setpoint they mean nothing anyway.
            raise InvalidValueError(
                f"signal {self.name!r}: value must be finite, got {value}"
            )

        raw = (value - self.bias) / self.scale
        if self.is_integer:
            raw = round(raw)

        packer = _struct_for(self.format)
        try:
            packer.pack(raw)
        except (StructError, OverflowError) as error:
            # struct raises OverflowError rather than struct.error when a
            # float format cannot hold the value.
            unit = f" {self.unit}" if self.unit else ""
            raise InvalidValueError(
                f"signal {self.name!r}: {value:g}{unit} does not fit its "
                f"format {self.format!r} (raw value {raw:g}): {error}"
            ) from None
        return raw

    def encode(self, value: float, payload: bytearray) -> None:
        """Write one physical value into an existing payload buffer."""
        if len(payload) < self.end:
            raise InvalidValueError(
                f"payload too short for signal {self.name!r}: needs "
                f"{self.end} bytes, got {len(payload)}"
            )
        _struct_for(self.format).pack_into(payload, self.offset, self.raw(value))


@dataclass(frozen=True)
class Message:
    """One CAN telegram: an arbitration ID and the signals in its payload.

    ``source`` records where the layout comes from -- a vendor document, an
    original script or a measurement at the test bench. None of the layouts in
    this project are confirmed against vendor documentation, so an entry that
    cannot name its origin should not be trusted with a safety decision.

    ``writable`` marks a command telegram: only such a message may be sent.
    Receiving from a misread status ID yields a wrong number, but writing to
    one drives a real device, so the direction has to be declared rather than
    assumed. ``length`` is the payload length to send when the device expects
    a fixed DLC (usually 8) that is longer than the declared signals.

    ``cycle_time_ms`` is the repetition time a device sends this telegram at,
    where it is known. Nothing in the receiving path depends on it -- it is
    recorded so that a simulated device can reproduce the timing, and so that
    a plausible staleness limit can be derived per message instead of guessed
    globally.
    """

    name: str
    arbitration_id: int
    signals: tuple[Signal, ...]
    extended: bool = True
    description: str = ""
    source: str = ""
    writable: bool = False
    length: int | None = None
    cycle_time_ms: float | None = None
    _by_name: Mapping[str, Signal] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a message needs a name")
        if QUALIFIER in self.name:
            raise ValueError(
                f"message name {self.name!r} must not contain {QUALIFIER!r}"
            )

        object.__setattr__(self, "signals", tuple(self.signals))
        if not self.signals:
            raise ValueError(f"message {self.name!r} needs at least one signal")

        duplicates = sorted(
            name
            for name, count in Counter(signal.name for signal in self.signals).items()
            if count > 1
        )
        if duplicates:
            raise ValueError(
                f"message {self.name!r} has duplicate signals: "
                f"{', '.join(duplicates)}"
            )

        limit = EXTENDED_ID_MASK if self.extended else STANDARD_ID_MASK
        if not 0 <= self.arbitration_id <= limit:
            kind = "29-bit extended" if self.extended else "11-bit standard"
            raise ValueError(
                f"message {self.name!r}: arbitration_id "
                f"0x{self.arbitration_id:X} is not a valid {kind} CAN ID"
            )

        if self.length is not None:
            minimum = max(signal.end for signal in self.signals)
            if self.length < minimum:
                raise ValueError(
                    f"message {self.name!r}: length {self.length} is shorter "
                    f"than the {minimum} bytes its signals occupy"
                )

        if self.cycle_time_ms is not None and self.cycle_time_ms <= 0:
            raise ValueError(
                f"message {self.name!r}: cycle_time_ms must be greater than "
                f"zero, got {self.cycle_time_ms}"
            )

        object.__setattr__(
            self,
            "_by_name",
            MappingProxyType({signal.name: signal for signal in self.signals}),
        )

    @property
    def signals_by_name(self) -> Mapping[str, Signal]:
        return self._by_name

    @property
    def signal_names(self) -> tuple[str, ...]:
        return tuple(signal.name for signal in self.signals)

    @property
    def label(self) -> str:
        """``"motor_temperature (0x1A000013)"``, for error messages."""
        return f"{self.name} ({format_can_id(self.arbitration_id, extended=self.extended)})"

    @property
    def key(self) -> tuple[int, bool]:
        """Identity on the bus: a standard and an extended ID may coincide."""
        return (self.arbitration_id, self.extended)

    @property
    def minimum_length(self) -> int:
        """Payload length required to decode every signal."""
        return max(signal.end for signal in self.signals)

    @property
    def can_filter(self) -> dict[str, Any]:
        """Hardware filter for python-can, so foreign traffic never arrives."""
        return {
            "can_id": self.arbitration_id,
            "can_mask": EXTENDED_ID_MASK if self.extended else STANDARD_ID_MASK,
            "extended": self.extended,
        }

    def signal(self, name: str) -> Signal:
        """Look up one signal of this message by name."""
        try:
            return self._by_name[name]
        except KeyError:
            raise UnknownSignalError(
                f"message {self.name!r} has no signal {name!r}; "
                f"it provides {', '.join(self.signal_names)}"
            ) from None

    def matches(self, frame: CanFrame) -> bool:
        """Return whether a received frame belongs to this message."""
        return (
            frame.arbitration_id == self.arbitration_id
            and bool(frame.is_extended_id) == self.extended
            and not frame.is_error_frame
            and not frame.is_remote_frame
        )

    @property
    def payload_length(self) -> int:
        """Length of a payload this message sends."""
        return self.minimum_length if self.length is None else self.length

    def decode(self, payload: bytes | bytearray | memoryview) -> dict[str, float]:
        """Decode every signal of this message from one payload."""
        return {signal.name: signal.decode(payload) for signal in self.signals}

    def encode(self, values: Mapping[str, float]) -> bytes:
        """Build the payload of a telegram that is about to be sent.

        Refuses a message that is not declared ``writable``: writing to a
        misread status ID drives a real device. Laying out the bytes of a
        status telegram -- what a simulated device does when it plays the
        other side of the bus -- goes through :meth:`build_payload`, which
        carries no such restriction because nothing is put on the wire.
        """
        if not self.writable:
            raise ReadOnlyMessageError(
                f"message {self.label} is not declared writable and must not "
                f"be sent; set writable=True in its catalog entry once the "
                f"command layout is confirmed"
            )
        return self.build_payload(values)

    def build_payload(self, values: Mapping[str, float]) -> bytes:
        """Lay out physical values as this message's payload.

        Every signal must either be named in ``values`` or declare a
        ``default``. Bytes that no signal covers stay zero. This is the byte
        layout on its own -- the direction of the telegram is decided by
        :meth:`encode`.
        """
        unknown = sorted(set(values) - set(self._by_name))
        if unknown:
            raise UnknownSignalError(
                f"message {self.name!r} has no signal(s) {', '.join(unknown)}; "
                f"it provides {', '.join(self.signal_names)}"
            )

        complete: dict[str, float] = {}
        missing: list[str] = []
        for signal in self.signals:
            if signal.name in values:
                complete[signal.name] = values[signal.name]
            elif signal.default is not None:
                complete[signal.name] = signal.default
            else:
                missing.append(signal.name)

        if missing:
            raise InvalidValueError(
                f"message {self.name!r} needs a value for {', '.join(missing)}; "
                f"pass it or give the signal a default in the catalog"
            )

        payload = bytearray(self.payload_length)
        for signal in self.signals:
            signal.encode(complete[signal.name], payload)
        return bytes(payload)

    def describe(self) -> str:
        """One human-readable block, used by the catalog listing."""
        scheme = "extended" if self.extended else "standard"
        direction = "lesen+senden" if self.writable else "nur lesen"
        header = (
            f"{self.name}  "
            f"{format_can_id(self.arbitration_id, extended=self.extended)}  "
            f"{scheme}  {direction}"
        )
        lines = [header]
        if self.description:
            lines.append(f"    {self.description}")
        if self.source:
            lines.append(f"    Herkunft: {self.source}")
        for signal in self.signals:
            unit = f" {signal.unit}" if signal.unit else ""
            default = (
                f", Vorgabe {signal.default:g}" if signal.default is not None else ""
            )
            lines.append(
                f"    - {signal.name}: Byte {signal.offset}..{signal.end - 1}, "
                f"{signal.format}, x{signal.scale:g}{unit}{default}"
            )
        return "\n".join(lines)


def signal_keys(messages: Sequence[Message]) -> dict[str, tuple[Message, Signal]]:
    """Map every signal of ``messages`` to a name that is unique among them.

    A signal keeps its plain name unless a second message provides the same
    name; then both become qualified (``"inverter_status_3.temperature"``).
    The result is stable for a given set of messages, so it can be used as a
    CSV header.
    """
    counts = Counter(signal.name for message in messages for signal in message.signals)

    keys: dict[str, tuple[Message, Signal]] = {}
    for message in messages:
        for signal in message.signals:
            key = (
                signal.name
                if counts[signal.name] == 1
                else f"{message.name}{QUALIFIER}{signal.name}"
            )
            keys[key] = (message, signal)
    return keys


def resolve_signal(
    messages: Sequence[Message], name: str
) -> tuple[Message, Signal]:
    """Find the message and signal a (possibly qualified) name refers to."""
    if QUALIFIER in name:
        message_name, _, signal_name = name.partition(QUALIFIER)
        for message in messages:
            if message.name == message_name:
                return message, message.signal(signal_name)
        raise UnknownSignalError(
            f"unknown message {message_name!r} in signal name {name!r}; "
            f"available: {', '.join(message.name for message in messages)}"
        )

    matches = [
        (message, message.signals_by_name[name])
        for message in messages
        if name in message.signals_by_name
    ]
    if not matches:
        available = sorted(signal_keys(messages))
        raise UnknownSignalError(
            f"unknown signal {name!r}; available: {', '.join(available)}"
        )
    if len(matches) > 1:
        qualified = ", ".join(
            f"{message.name}{QUALIFIER}{name}" for message, _ in matches
        )
        raise AmbiguousSignalError(
            f"signal {name!r} is provided by several messages; use {qualified}"
        )

    return matches[0]

