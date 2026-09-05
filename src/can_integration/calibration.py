"""Tara und Kalibrierfaktor: was zwischen Rohwert und Messwert steht.

Eine Waegezelle liefert ein Gewicht, das nur so gut ist wie ihr Nullpunkt.
Der verschiebt sich mit Temperatur, mit der Montage und damit, was gerade am
Ausleger haengt -- er gehoert vor jeden Lauf neu gemessen. Der Faktor daneben
korrigiert die Spanne gegen ein bekanntes Pruefgewicht.

    physikalischer Wert = (gemeldeter Wert - offset) * factor

**Nicht im Katalog.** Der Katalog beschreibt das Telegramm: welche Bytes
welche Groesse tragen. Das ist eine Eigenschaft des Geraets und wird einmal
geprueft. Eine Tara ist eine Eigenschaft *dieses Laufs an diesem Aufbau* und
aendert sich taeglich. Beides in dieselbe Datei zu schreiben hiesse, den
Katalog bei jeder Messung anzufassen -- und damit das aufzugeben, was ihn
brauchbar macht.

Gilt nicht nur fuer die Waegezelle: der Drehmomentsensor am
Drehmomentpruefstand braucht denselben Nullabgleich im Stillstand.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .signals import Message, resolve_signal


class CalibrationError(RuntimeError):
    """Raised when a tare or a span calibration cannot be trusted."""


@dataclass(frozen=True)
class Calibration:
    """Nullpunkt und Spanne eines Signals, mit ihrer Herkunft.

    ``offset`` steht in der Einheit des Signals und wird abgezogen, bevor
    ``factor`` skaliert -- die uebliche Reihenfolge einer Waegezelle: erst
    den Leerwert weg, dann die Spanne korrigieren.

    ``reference`` ist Text und trotzdem wichtig: „500 g Pruefgewicht,
    2026-09-06" gehoert in den Kopf der Messdatei. Ein Kalibrierfaktor ohne
    Angabe, wogegen er gemessen wurde, ist eine Zahl ohne Aussage.
    """

    signal: str
    offset: float = 0.0
    factor: float = 1.0
    reference: str = ""

    def __post_init__(self) -> None:
        if not self.signal:
            raise ValueError("a calibration needs a signal name")
        for name, value in (("offset", self.offset), ("factor", self.factor)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"calibration for {self.signal!r}: {name} must be a "
                    f"number, got {value!r}"
                )
        if self.factor == 0:
            raise ValueError(
                f"calibration for {self.signal!r}: factor must not be zero -- "
                f"every reading would become zero"
            )

    @property
    def is_identity(self) -> bool:
        """Ob diese Kalibrierung nichts tut."""
        return self.offset == 0.0 and self.factor == 1.0

    def apply(self, value: float) -> float:
        return (value - self.offset) * self.factor

    def undo(self, value: float) -> float:
        """Zurueck auf den gemeldeten Wert -- fuer Tara und Spanne."""
        return value / self.factor + self.offset

    def with_offset(self, offset: float, *, reference: str = "") -> Calibration:
        return replace(self, offset=offset, reference=reference or self.reference)

    def with_factor(self, factor: float, *, reference: str = "") -> Calibration:
        return replace(self, factor=factor, reference=reference or self.reference)

    def describe(self) -> str:
        """Eine Zeile fuer den Kopf einer Messdatei."""
        text = f"{self.signal}: offset={self.offset:g} factor={self.factor:g}"
        return f"{text} ({self.reference})" if self.reference else text


@dataclass(frozen=True)
class TareResult:
    """Was der Nullabgleich gesehen hat -- nicht nur sein Ergebnis.

    ``deviation`` und ``spread`` sind der Beleg, dass der Aufbau beim
    Abgleich wirklich in Ruhe war. Eine Tara, die waehrend des Anlaufs
    genommen wurde, vergiftet still jeden Schubwert des ganzen Laufs.
    """

    signal: str
    offset: float
    samples: int
    deviation: float
    spread: float
    duration: float

    def describe(self) -> str:
        return (
            f"{self.signal}: offset={self.offset:g} aus {self.samples} Werten "
            f"in {self.duration:.2f} s (s={self.deviation:g}, "
            f"Spanne={self.spread:g})"
        )


def summarise(signal: str, samples: Sequence[float], duration: float) -> TareResult:
    """Aus einer Messreihe im Ruhezustand einen Nullpunkt machen."""
    if not samples:
        raise CalibrationError(
            f"no reading of {signal!r} arrived during the tare; the sensor has "
            f"to be sending before its zero can be measured"
        )
    return TareResult(
        signal=signal,
        offset=statistics.fmean(samples),
        samples=len(samples),
        deviation=statistics.stdev(samples) if len(samples) > 1 else 0.0,
        spread=max(samples) - min(samples),
        duration=duration,
    )


def check_at_rest(result: TareResult, tolerance: float | None) -> None:
    """Sicherstellen, dass der Aufbau beim Abgleich stillstand."""
    if tolerance is None:
        return
    if tolerance < 0:
        raise ValueError("tolerance must not be negative")
    if result.spread > tolerance:
        raise CalibrationError(
            f"{result.signal} moved by {result.spread:g} during the tare, "
            f"allowed are {tolerance:g}: the bench was not at rest, and a zero "
            f"taken now would be wrong for the whole run"
        )


def calibrations_from_dict(
    declaration: Mapping[str, Any], definitions: Sequence[Message]
) -> tuple[Calibration, ...]:
    """Kalibrierungen aus der JSON-Form bauen und gegen den Katalog pruefen.

        "calibration": {
          "weight": {"offset": 12.5, "factor": 1.002,
                     "reference": "500 g Pruefgewicht, 2026-09-06"}
        }
    """
    calibrations: list[Calibration] = []
    for name, value in declaration.items():
        try:
            resolve_signal(definitions, name)
        except LookupError as error:
            raise ValueError(f"calibration for {name!r}: {error}") from None

        if not isinstance(value, Mapping):
            raise ValueError(
                f"calibration for {name!r} must be an object with offset, "
                f"factor and optionally reference"
            )
        unknown = sorted(set(value) - {"offset", "factor", "reference"})
        if unknown:
            raise ValueError(
                f"calibration for {name!r}: unknown key(s) "
                f"{', '.join(unknown)}; expected offset, factor, reference"
            )
        try:
            calibrations.append(
                Calibration(
                    name,
                    offset=value.get("offset", 0.0),
                    factor=value.get("factor", 1.0),
                    reference=value.get("reference", ""),
                )
            )
        except ValueError as error:
            raise ValueError(str(error)) from None

    return tuple(calibrations)
