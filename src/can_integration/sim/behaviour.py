"""Wie sich die Werte zwischen zwei Kommandos bewegen.

:class:`~can_integration.sim.device.SimulatedDevice` haelt einen Zustand und
sendet ihn; von allein aendert er sich nicht. Ein Verhalten ist das Stueck,
das ihn zwischen den Telegrammen weiterrechnet: eine Rampe, die einem
Sollwert folgt, oder der gemessene Verlauf aus einer Aufzeichnung.

Ein Verhalten ist schlicht ein Aufruf ``(device, dt) -> None``, mit ``dt`` in
Sekunden seit dem letzten Schritt. Es liest und schreibt den Zustand ueber
:meth:`SimulatedDevice.get` und :meth:`SimulatedDevice.set`, also unter
denselben Signalnamen wie alles andere.

Verhalten laufen nur, solange das Geraet ``armed`` ist. Nach einem
Disarm-Kommando steht der Zustand still -- so, wie es die Aufzeichnung nach
ihrem Stopp zeigt.

Was hier steht, ist ausdruecklich *Modell*, nicht Messung. Die einzige
Ausnahme ist :class:`FromRecording`, das gemessene Werte abspielt.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

from ..catalog import DEFAULT_CATALOG, Catalog
from ..signals import InvalidFrameError, Signal, signal_keys
from .logfile import Recording
from .replay import host_sent_keys

if TYPE_CHECKING:  # pragma: no cover - nur fuer die Typpruefung
    from .device import SimulatedDevice

#: Ein Verhalten bekommt das Geraet und die vergangene Zeit in Sekunden.
BehaviourStep = Callable[["SimulatedDevice", float], None]


@runtime_checkable
class Behaviour(Protocol):
    """Was ein Verhalten koennen muss -- mehr als ein Aufruf ist es nicht."""

    def __call__(self, device: SimulatedDevice, dt: float) -> None: ...


@dataclass
class Chain:
    """Mehrere Verhalten in fester Reihenfolge.

    Die Reihenfolge zaehlt: schreiben zwei Verhalten dasselbe Signal, gewinnt
    das spaetere. Das ist kein Nebeneffekt, sondern die Art, ein Grundmodell
    gezielt zu ueberschreiben.
    """

    steps: tuple[BehaviourStep, ...]

    def __init__(self, steps: Iterable[BehaviourStep]) -> None:
        self.steps = tuple(steps)

    def __call__(self, device: SimulatedDevice, dt: float) -> None:
        for step in self.steps:
            step(device, dt)


@dataclass
class Constant:
    """Haelt ein Signal fest -- das Nichtstun, ausdruecklich gemacht.

    Nuetzlich hinter einem anderen Verhalten in einer :class:`Chain`, um einen
    einzelnen Wert von dessen Wirkung auszunehmen. ``value = None`` uebernimmt
    den Wert, den der Zustand beim ersten Schritt hat.
    """

    signal: str
    value: float | None = None

    def __call__(self, device: SimulatedDevice, dt: float) -> None:
        if self.value is None:
            self.value = device.get(self.signal)
        device.set(self.signal, self.value)


@dataclass
class Ramp:
    """Fuehrt ein Signal mit begrenzter Aenderungsrate an ein Ziel heran.

    ``target`` ist entweder eine feste Zahl oder der Name eines anderen
    Signals -- dann folgt das Signal einem Sollwert, den die Messseite
    schreibt::

        Ramp("rpm_actual", target="rpm_target", rate=4000)

    ``rate`` ist die groesste Aenderung je Sekunde. Das ist eine
    Modellannahme: die Aufzeichnung sagt nicht, wie schnell dieser Antrieb
    hochlaeuft. Ein Wert, den ein Test setzt, ist eine Testvorgabe und keine
    Eigenschaft des Pruefstands.
    """

    signal: str
    target: str | float
    rate: float

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise ValueError(
                f"ramp rate for {self.signal!r} must be greater than zero, "
                f"got {self.rate}"
            )

    def __call__(self, device: SimulatedDevice, dt: float) -> None:
        if dt <= 0:
            return
        goal = (
            device.get(self.target)
            if isinstance(self.target, str)
            else float(self.target)
        )
        current = device.get(self.signal)
        difference = goal - current
        step = self.rate * dt
        if abs(difference) <= step:
            device.set(self.signal, goal)
        else:
            device.set(self.signal, current + math.copysign(step, difference))


@dataclass
class Follow:
    """Ein Signal folgt einem anderen unmittelbar, mit Faktor und Versatz.

    Fuer feste Kopplungen, die keine Dynamik haben -- etwa ein Signal, das
    dieselbe Groesse in einer anderen Einheit fuehrt. Wo eine Kopplung nur
    vermutet ist, gehoert sie nicht hierher: dann lieber gar kein Verhalten
    als ein erfundenes.
    """

    signal: str
    source: str
    factor: float = 1.0
    bias: float = 0.0

    def __call__(self, device: SimulatedDevice, dt: float) -> None:
        device.set(self.signal, device.get(self.source) * self.factor + self.bias)


@dataclass
class FromRecording:
    """Treibt den Zustand mit dem gemessenen Verlauf einer Aufzeichnung.

    Der Unterschied zu :class:`~can_integration.sim.replay.LogPlayer`: dort
    gehen die aufgezeichneten Rahmen unveraendert auf den Bus, hier gehen die
    aufgezeichneten *Werte* in den Zustand, und das Geraet sendet sie in
    seinen eigenen Telegrammen weiter. Damit bleiben realistische Werte und
    ein antwortendes Geraet zugleich zu haben.

    Ein Disarm haelt die Wiedergabe an, ein Arm setzt sie fort -- die Uhr des
    Verhaltens laeuft nur, solange das Geraet laeuft.
    """

    #: ``(Zeit in Sekunden, ((Zustandsname, Wert), ...))``, nach Zeit sortiert.
    timeline: tuple[tuple[float, tuple[tuple[str, float], ...]], ...]
    span: float
    loop: bool = True
    #: Zustandsnamen, die das Geraet nicht fuehrt und die uebersprungen werden.
    ignored: tuple[str, ...] = ()
    _clock: float = field(default=0.0, init=False, repr=False)
    _index: int = field(default=0, init=False, repr=False)
    _known: frozenset[str] | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_recording(
        cls,
        recording: Recording,
        *,
        catalog: Catalog = DEFAULT_CATALOG,
        signals: Iterable[str] | None = None,
        loop: bool = True,
        exclude_host: bool = True,
    ) -> FromRecording:
        """Den Verlauf aus einer Aufzeichnung dekodieren.

        ``signals`` schraenkt auf einzelne Zustandsnamen ein; ohne Angabe
        werden alle genommen, die der Katalog beschreibt.
        """
        known = recording.coverage(catalog).known
        host = host_sent_keys(catalog) if exclude_host else frozenset()
        messages = tuple(
            message for key, message in known.items() if key not in host
        )
        wanted = None if signals is None else set(signals)

        columns: dict[tuple[int, bool], list[tuple[str, Signal]]] = {}
        for name, (message, signal) in signal_keys(messages).items():
            if wanted is not None and name not in wanted:
                continue
            columns.setdefault(message.key, []).append((name, signal))

        origin = recording.frames[0].t if recording.frames else 0.0
        timeline: list[tuple[float, tuple[tuple[str, float], ...]]] = []
        for frame in recording.frames:
            fields = columns.get(frame.key)
            if not fields:
                continue
            try:
                values = tuple(
                    (name, signal.decode(frame.data)) for name, signal in fields
                )
            except InvalidFrameError:
                # Ein zu kurzer Rahmen: die Abdeckung meldet ihn, hier faellt
                # er aus dem Verlauf, statt eine Luecke zu erfinden.
                continue
            timeline.append((frame.t - origin, values))

        return cls(tuple(timeline), span=recording.duration, loop=loop)

    def __call__(self, device: SimulatedDevice, dt: float) -> None:
        if not self.timeline:
            return
        if self._known is None:
            self._adopt(device)

        self._clock += max(0.0, dt)
        while (
            self._index < len(self.timeline)
            and self.timeline[self._index][0] <= self._clock
        ):
            _, values = self.timeline[self._index]
            self._index += 1
            update = {
                name: value for name, value in values if name in self._known
            }
            if update:
                device.update(update)

        if self._index >= len(self.timeline):
            if not self.loop:
                return
            self._index = 0
            self._clock = self._clock - self.span if self.span > 0 else 0.0

    def _adopt(self, device: SimulatedDevice) -> None:
        """Beim ersten Schritt gegen die Signale des Geraets abgleichen."""
        known = frozenset(device.signal_names)
        self._known = known
        self.ignored = tuple(
            sorted(
                {
                    name
                    for _, values in self.timeline
                    for name, _ in values
                    if name not in known
                }
            )
        )


@dataclass
class Noise:
    """Legt Rauschen auf die gesendeten Werte, nicht auf den Zustand.

    Der Unterschied ist wesentlich: wuerde das Rauschen in den Zustand
    geschrieben, addierte es sich von Schritt zu Schritt auf und das Signal
    liefe als Zufallsbewegung davon. Ein Messrauschen sitzt in der Messung,
    nicht in der Groesse -- also wird es beim Bauen der Nutzlast aufgelegt und
    ist im naechsten Telegramm wieder vergessen.

    ``sigma`` ist die Standardabweichung je Zustandsname, in der Einheit des
    Signals. ``seed`` macht den Verlauf wiederholbar, was ein Test braucht.

    Ein verrauschter Wert, der nicht mehr in das Format seines Signals passt,
    wird verworfen: dann geht der saubere Wert auf den Bus.
    """

    sigma: Mapping[str, float]
    seed: int | None = None

    def __post_init__(self) -> None:
        for name, deviation in self.sigma.items():
            if deviation < 0:
                raise ValueError(
                    f"sigma for {name!r} must not be negative, got {deviation}"
                )
        self._random = random.Random(self.seed)

    def __call__(self, name: str, value: float) -> float:
        deviation = self.sigma.get(name)
        if not deviation:
            return value
        return value + self._random.gauss(0.0, deviation)


def as_behaviour(
    behaviour: BehaviourStep | Sequence[BehaviourStep] | None,
) -> BehaviourStep | None:
    """Ein einzelnes Verhalten, eine Liste davon oder nichts."""
    if behaviour is None or callable(behaviour):
        return behaviour  # type: ignore[return-value]
    return Chain(behaviour)
