"""Read a CL1000 text log into frames the rest of the package understands.

The CSS Electronics CL1000 writes one line per frame behind a block of ``#``
header lines that declare how the line is put together -- the value separator,
the time separator and whether milliseconds carry one of their own. Those
declarations are honoured instead of hard-coding the shape of the recordings
at hand, because the same logger writes a different shape once it is
configured differently.

A recording is a passive object: it parses, reports what it contains and
hands out frames. Putting them on a bus is :mod:`can_integration.sim.replay`.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..catalog import DEFAULT_CATALOG, Catalog
from ..signals import STANDARD_ID_MASK, Message, format_can_id

#: Identity of a telegram on the bus, the same key :attr:`Message.key` uses.
FrameKey = tuple[int, bool]

SECONDS_PER_DAY = 24 * 60 * 60

#: Column header the frame lines follow.
_COLUMNS = ("Timestamp", "Type", "ID", "Data")

#: Defaults for a header that does not declare a separator.
_DEFAULT_HEADER = {
    "Value separator": ";",
    "Time separator": ":",
    "Time separator ms": "",
    "Date separator": "",
    "Time and date separator": "T",
}


class LogFormatError(ValueError):
    """Raised when a line does not fit the format the header declares."""


@dataclass(frozen=True)
class LogFrame:
    """One recorded frame, timed relative to the start of the recording.

    ``frame_type`` keeps the raw value of the logger's ``Type`` column. Only
    its lowest bit is interpreted -- as the identifier width -- because that
    is the only part these recordings pin down; keeping the raw value means a
    later reader can still tell a remote frame from a data frame once the
    logger documentation settles what the other bits mean.
    """

    t: float
    arbitration_id: int
    extended: bool
    data: bytes
    frame_type: int = 1
    line: int = 0

    @property
    def key(self) -> FrameKey:
        return (self.arbitration_id, self.extended)

    @property
    def label(self) -> str:
        return format_can_id(self.arbitration_id, extended=self.extended)


@dataclass(frozen=True)
class Coverage:
    """Which recorded telegrams the catalog can decode, and which it cannot."""

    known: Mapping[FrameKey, Message]
    unknown: tuple[FrameKey, ...]
    short: Mapping[FrameKey, int]
    counts: Mapping[FrameKey, int]

    def report(self) -> str:
        """Kurzfassung fuer die Kommandozeile: was eine Messung sehen wuerde.

        Deutsch wie die uebrige Benutzerausgabe des Pakets; die Diagnose
        richtet sich an den Menschen vor dem Pruefstand, nicht an den Code.
        """
        lines = [
            f"{len(self.known)} von {len(self.counts)} Telegrammtypen sind im "
            f"Katalog beschrieben und werden dekodiert."
        ]
        if self.unknown:
            frames = sum(self.counts[key] for key in self.unknown)
            lines.append(
                f"{len(self.unknown)} Telegrammtyp(en) ohne Katalogeintrag "
                f"({frames} Frames) bleiben undekodiert: "
                f"{format_keys(self.unknown)}"
            )
        for key, length in sorted(self.short.items()):
            message = self.known[key]
            lines.append(
                f"Warnung: {message.name} braucht "
                f"{message.minimum_length} Bytes, die Aufzeichnung liefert "
                f"nur {length}."
            )
        return "\n".join(lines)


class Recording:
    """The frames of one log file, plus what its header says about them."""

    def __init__(
        self,
        frames: Iterable[LogFrame],
        *,
        header: Mapping[str, str] | None = None,
        path: Path | None = None,
    ) -> None:
        self.frames = tuple(frames)
        self.header = dict(header or {})
        self.path = path

    def __len__(self) -> int:
        return len(self.frames)

    def __iter__(self) -> Iterator[LogFrame]:
        return iter(self.frames)

    @classmethod
    def from_file(cls, path: str | Path) -> Recording:
        """Parse a CL1000 text log."""
        path = Path(path)
        return parse_log(path.read_text(encoding="utf-8", errors="replace"), path=path)

    @property
    def duration(self) -> float:
        """Seconds between the first and the last recorded frame."""
        if not self.frames:
            return 0.0
        return self.frames[-1].t - self.frames[0].t

    @property
    def bitrate(self) -> int | None:
        """Bitrate the logger recorded at, if the header states one."""
        raw = self.header.get("Bit-rate")
        return int(raw) if raw and raw.isdigit() else None

    @property
    def start_time(self) -> datetime | None:
        """Wall-clock start of the session, if the header states one."""
        raw = self.header.get("Time")
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y%m%dT%H%M%S")
        except ValueError:
            return None

    def counts(self) -> dict[FrameKey, int]:
        """How many frames each telegram type contributed, busiest first."""
        counter = Counter(frame.key for frame in self.frames)
        return dict(counter.most_common())

    def cycle_times(self) -> dict[FrameKey, float]:
        """Median repetition time per telegram type, in milliseconds.

        The median rather than the mean: a logger that drops a frame or a bus
        that arbitrates a telegram away leaves single large gaps, and those
        would pull a mean far away from the cycle the device actually keeps.
        """
        times: dict[FrameKey, list[float]] = defaultdict(list)
        for frame in self.frames:
            times[frame.key].append(frame.t)

        cycles: dict[FrameKey, float] = {}
        for key, stamps in times.items():
            if len(stamps) < 2:
                continue
            gaps = [
                (later - earlier) * 1000.0
                for earlier, later in zip(stamps, stamps[1:])
            ]
            cycles[key] = statistics.median(gaps)
        return cycles

    def first_payloads(self) -> dict[FrameKey, bytes]:
        """The first payload seen per telegram -- a plausible initial state."""
        payloads: dict[FrameKey, bytes] = {}
        for frame in self.frames:
            payloads.setdefault(frame.key, frame.data)
        return payloads

    def last_payloads(self) -> dict[FrameKey, bytes]:
        """The last payload seen per telegram -- the state the log ends in."""
        return {frame.key: frame.data for frame in self.frames}

    def select(
        self,
        *,
        include: Iterable[FrameKey] | None = None,
        exclude: Iterable[FrameKey] = (),
    ) -> Recording:
        """A recording with the same timing but a subset of the telegrams."""
        allowed = None if include is None else set(include)
        blocked = set(exclude)
        return Recording(
            (
                frame
                for frame in self.frames
                if frame.key not in blocked
                and (allowed is None or frame.key in allowed)
            ),
            header=self.header,
            path=self.path,
        )

    def coverage(self, catalog: Catalog = DEFAULT_CATALOG) -> Coverage:
        """Match the recorded telegrams against a catalog.

        An unmatched telegram is reported rather than dropped in silence: a
        recording that carries IDs the catalog does not know is the normal
        state of an unfinished protocol, and the list of them is exactly the
        work still to be done on the catalog.
        """
        counts = self.counts()
        by_key = {message.key: message for message in catalog.values()}

        known: dict[FrameKey, Message] = {}
        unknown: list[FrameKey] = []
        for key in counts:
            message = by_key.get(key)
            if message is None:
                unknown.append(key)
            else:
                known[key] = message

        shortest: dict[FrameKey, int] = {}
        for frame in self.frames:
            message = known.get(frame.key)
            if message is None:
                continue
            length = len(frame.data)
            if length < message.minimum_length:
                shortest[frame.key] = min(
                    length, shortest.get(frame.key, length)
                )

        return Coverage(
            known=known,
            unknown=tuple(unknown),
            short=shortest,
            counts=counts,
        )


def parse_log(text: str, *, path: Path | None = None) -> Recording:
    """Parse the contents of a CL1000 text log."""
    header: dict[str, str] = {}
    frames: list[LogFrame] = []

    separators = dict(_DEFAULT_HEADER)
    day_offset = 0.0
    previous = None
    origin: float | None = None

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue

        if line.startswith("#"):
            key, sep, value = line[1:].partition(":")
            if sep:
                header[key.strip()] = _unquote(value.strip())
                if key.strip() in separators:
                    separators[key.strip()] = _unquote(value.strip())
            continue

        fields = line.split(separators["Value separator"])
        if tuple(field.strip() for field in fields[:4]) == _COLUMNS:
            continue

        if len(fields) < 4:
            raise LogFormatError(
                f"line {number}: expected "
                f"{separators['Value separator'].join(_COLUMNS)}, got {line!r}"
            )

        stamp, frame_type, identifier, data = (field.strip() for field in fields[:4])
        seconds = _parse_timestamp(stamp, separators, number)

        # A recording that runs past midnight starts counting from zero again.
        if previous is not None and seconds + day_offset < previous:
            day_offset += SECONDS_PER_DAY
        previous = seconds + day_offset

        if origin is None:
            origin = previous

        frames.append(
            _frame(
                t=previous - origin,
                frame_type=frame_type,
                identifier=identifier,
                data=data,
                number=number,
            )
        )

    return Recording(frames, header=header, path=path)


def _frame(
    *, t: float, frame_type: str, identifier: str, data: str, number: int
) -> LogFrame:
    try:
        type_value = int(frame_type)
    except ValueError:
        raise LogFormatError(
            f"line {number}: frame type {frame_type!r} is not a number"
        ) from None

    try:
        arbitration_id = int(identifier, 16)
    except ValueError:
        raise LogFormatError(
            f"line {number}: identifier {identifier!r} is not hexadecimal"
        ) from None

    try:
        payload = bytes.fromhex(data)
    except ValueError:
        raise LogFormatError(
            f"line {number}: payload {data!r} is not a hexadecimal byte string"
        ) from None

    # The logger states the width in its type column; an identifier that does
    # not fit into 11 bits settles the question regardless of what it says.
    extended = bool(type_value & 1) or arbitration_id > STANDARD_ID_MASK

    return LogFrame(
        t=t,
        arbitration_id=arbitration_id,
        extended=extended,
        data=payload,
        frame_type=type_value,
        line=number,
    )


def _parse_timestamp(
    stamp: str, separators: Mapping[str, str], number: int
) -> float:
    """Turn one timestamp into seconds since midnight.

    Shape according to the header: an optional date, then hours, minutes and
    seconds, then milliseconds either behind their own separator or simply
    appended to the seconds.
    """
    datetime_separator = separators["Time and date separator"]
    if datetime_separator and datetime_separator in stamp:
        _, _, stamp = stamp.rpartition(datetime_separator)

    time_separator = separators["Time separator"]
    if time_separator:
        parts = stamp.split(time_separator)
    else:
        parts = [stamp[0:2], stamp[2:4], stamp[4:]]

    if len(parts) != 3:
        raise LogFormatError(
            f"line {number}: timestamp {stamp!r} does not have three parts "
            f"separated by {time_separator!r}"
        )

    hours, minutes, rest = parts
    ms_separator = separators["Time separator ms"]
    if ms_separator and ms_separator in rest:
        seconds, _, milliseconds = rest.partition(ms_separator)
    else:
        seconds, milliseconds = rest[:2], rest[2:]

    try:
        total = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        if milliseconds:
            total += int(milliseconds) / 10 ** len(milliseconds)
    except ValueError:
        raise LogFormatError(
            f"line {number}: timestamp {stamp!r} is not a time"
        ) from None
    return total


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def format_keys(keys: Sequence[FrameKey]) -> str:
    """``0x1A000006, 0x1A000007`` -- for error messages and reports."""
    return ", ".join(
        format_can_id(identifier, extended=extended) for identifier, extended in keys
    )
