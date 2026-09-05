"""Put a recorded log back on a bus, with its original timing.

A replay is the most honest source of test data there is without the test
bench: every payload was measured, none was invented. What it cannot do is
react -- a recording answers no command. That is what a state model is for;
this module deliberately stays a tape deck.

One thing a replay has to get right is *direction*. A log records both sides
of the bus. Playing the host's own telegrams back at the library would have it
listen to its own role, so by default they are left out.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable

import can

from ..catalog import DEFAULT_CATALOG, Catalog, UnknownMessageError
from .logfile import FrameKey, LogFrame, Recording, format_keys
from .transport import SIM_CHANNEL, SIM_INTERFACE, BusOwner

__all__ = [
    "DIRECTIONS",
    "HOST_TELEGRAM_NAMES",
    "SIM_CHANNEL",
    "SIM_INTERFACE",
    "LogPlayer",
    "host_sent_keys",
]

#: Telegrams the host puts on the bus although the catalog does not declare
#: them writable. ``discovery_request`` carries a constant ASCII payload
#: rather than a value anyone sets, so ``writable`` would be the wrong flag
#: for it -- but the GUI, not the device, is what repeats it every 500 ms.
HOST_TELEGRAM_NAMES = ("discovery_request",)

#: ``device`` plays only what a device sends, ``all`` replays the recording
#: unchanged -- useful to look at a protocol, not to feed a measurement.
DIRECTIONS = ("device", "all")


def host_sent_keys(catalog: Catalog = DEFAULT_CATALOG) -> frozenset[FrameKey]:
    """Telegrams that travel host -> device according to the catalog.

    A message declared ``writable`` is by definition one the host sends: that
    is what the flag means everywhere else in this package.
    """
    keys = {message.key for message in catalog.values() if message.writable}
    for name in HOST_TELEGRAM_NAMES:
        try:
            keys.add(catalog[name].key)
        except UnknownMessageError:
            continue
    return frozenset(keys)


class LogPlayer:
    """Sends the frames of a :class:`Recording` on a CAN bus.

    If ``bus`` is supplied its lifecycle stays with the caller, exactly as in
    :class:`~can_integration.bus.BusConnection`; otherwise a bus is opened
    lazily and shut down by :meth:`close`.

    ``speed`` scales the original timing -- ``2.0`` runs twice as fast,
    ``0`` sends everything as fast as the bus takes it, which is what a test
    wants when it cares about values rather than about timing.
    """

    def __init__(
        self,
        recording: Recording,
        *,
        bus: can.BusABC | None = None,
        interface: str | None = None,
        channel: str | None = None,
        bitrate: int | None = None,
        speed: float = 1.0,
        loop: bool = False,
        gap: float = 0.0,
        direction: str = "device",
        catalog: Catalog = DEFAULT_CATALOG,
        exclude: Iterable[FrameKey] = (),
    ) -> None:
        if speed < 0:
            raise ValueError("speed must not be negative")
        if gap < 0:
            raise ValueError("gap must not be negative")
        if direction not in DIRECTIONS:
            raise ValueError(
                f"unknown direction {direction!r}; expected one of "
                f"{', '.join(DIRECTIONS)}"
            )

        self.recording = recording
        self.speed = speed
        self.loop = loop
        self.gap = gap
        self.direction = direction
        self.catalog = catalog

        self.skipped: tuple[FrameKey, ...] = ()
        blocked = set(exclude)
        if direction == "device":
            recorded = set(recording.counts())
            host = host_sent_keys(catalog) & recorded
            self.skipped = tuple(sorted(host))
            blocked |= host
        self.frames: tuple[LogFrame, ...] = recording.select(exclude=blocked).frames

        self._owner = BusOwner(
            bus=bus,
            interface=interface,
            channel=channel,
            bitrate=bitrate if bus is not None else _bitrate(bitrate, recording),
        )
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._failure: BaseException | None = None
        self.sent = 0

    @property
    def bitrate(self) -> int:
        """Bitrate the bus is opened at -- the recorded one unless overridden."""
        return self._owner.bitrate

    @property
    def duration(self) -> float:
        """How long one pass takes at the configured speed."""
        if not self.frames or self.speed == 0:
            return 0.0
        return (self.frames[-1].t - self.frames[0].t) / self.speed

    def describe_skipped(self) -> str:
        """Was der Richtungsfilter weggelassen hat -- Ausgabe fuer die CLI."""
        if self.direction == "all":
            return "Richtungsfilter aus: die Aufzeichnung wird unveraendert gespielt."
        if not self.skipped:
            return "Keine Host-Telegramme in der Aufzeichnung."
        return (
            f"{len(self.skipped)} Telegrammtyp(en) sendet der Host, nicht das "
            f"Geraet, und werden nicht gespielt: {format_keys(self.skipped)}"
        )

    def connect(self) -> can.BusABC:
        """Open the configured bus if it is not open yet and return it."""
        return self._owner.connect()

    def close(self) -> None:
        """Release a bus this player opened."""
        self._owner.close()

    def run(self, stop: threading.Event | None = None) -> int:
        """Play the recording, blocking, and return the number of frames sent.

        Returns early when ``stop`` is set. Timing is measured against a fixed
        starting point rather than by sleeping between frames: over the 13,000
        frames of a real recording, accumulated sleep overshoot would stretch
        the replay noticeably.
        """
        if stop is None:
            stop = self._stop
        bus = self.connect()
        if not self.frames:
            return 0

        origin = self.frames[0].t
        pass_start = time.monotonic()
        span = self.frames[-1].t - origin

        while True:
            for frame in self.frames:
                if self.speed and _wait_until(
                    pass_start + (frame.t - origin) / self.speed, stop
                ):
                    return self.sent
                if stop.is_set():
                    return self.sent

                bus.send(
                    can.Message(
                        arbitration_id=frame.arbitration_id,
                        is_extended_id=frame.extended,
                        data=frame.data,
                    )
                )
                self.sent += 1

            if not self.loop:
                return self.sent
            pass_start += (span + self.gap) / self.speed if self.speed else 0.0

    def start(self) -> None:
        """Play the recording in a background thread."""
        if self._thread is not None:
            raise RuntimeError("player is already running")

        self._stop.clear()
        self._failure = None
        self.sent = 0
        self.connect()

        self._thread = threading.Thread(
            target=self._play, name="can-integration-sim-replay", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the playing thread, release an owned bus and re-raise faults."""
        self._stop.set()

        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
            self._thread = None

        self.close()
        failure, self._failure = self._failure, None
        if failure is not None:
            raise failure

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for a non-looping replay to finish. True if it did."""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def _play(self) -> None:
        try:
            self.run(self._stop)
        except BaseException as error:  # noqa: BLE001 - handed to stop()
            # Keep the failure instead of letting the thread die unnoticed.
            self._failure = error

    def __enter__(self) -> LogPlayer:
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()


def _wait_until(due: float, stop: threading.Event) -> bool:
    """Sleep until ``due``. True when the wait was cut short by ``stop``."""
    delay = due - time.monotonic()
    if delay <= 0:
        return stop.is_set()
    return stop.wait(delay)


def _bitrate(bitrate: int | None, recording: Recording) -> int | None:
    """Explicit bitrate, else the recorded one, else the caller's default."""
    return bitrate if bitrate is not None else recording.bitrate
