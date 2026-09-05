"""Grenzwerte, die greifen, und ein Zustand, in den der Prüfstand fällt.

Zwei Dinge, die zusammengehören. Ein Grenzwert, den niemand auswertet, ist
eine Notiz; und eine Messung, die beim Fehler nur *aufhört*, lässt einen
laufenden Prüfstand laufen -- der Bus wird geschlossen, der Inverter steht
weiter auf seinem letzten Sollwert. Deshalb hat ein Grenzwert eine Aktion, und
die Aktion hat ein Ziel: den sicheren Zustand.

    from can_integration import Device, Limit, SafeCommand, SafeState

    with Device(
        ["motor_temperature", "inverter_speed"],
        limits=[Limit("temperature", maximum=80.0)],
        safe_state=SafeState([SafeCommand("broadcast_command", {"command": 0})]),
    ) as device:
        ...

**Was das nicht ist.** Der sichere Zustand geht über denselben CAN-Bus, der
gerade ausgefallen sein kann, und über dieselbe Software, die gerade
abstürzt. Er ersetzt keine mechanische oder elektrische Not-Aus-Kette. Was er
kann, ist: melden, ob er durchkam.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .catalog import DEFAULT_CATALOG, Catalog
from .signals import Message, resolve_signal

#: ``abort`` bricht die Messung ab und löst den sicheren Zustand aus,
#: ``warn`` schreibt die Überschreitung nur mit.
ACTIONS = ("abort", "warn")

DEFAULT_SEND_TIMEOUT = 0.5
DEFAULT_ATTEMPTS = 3


class LimitError(RuntimeError):
    """Raised when a monitored signal leaves its declared range."""


class SafeStateError(RuntimeError):
    """Raised when the safe state could not be put on the bus."""


@dataclass(frozen=True)
class Limit:
    """Der zulässige Bereich eines Signals.

    Mindestens eine Grenze muss gesetzt sein. Beide Richtungen zählen: eine
    Zwischenkreisspannung, die *einbricht*, ist genauso ein Abbruchgrund wie
    eine, die zu hoch wird, und ein Drehmoment, das ins Negative geht, heißt
    am Drehmomentprüfstand, dass die Lastmaschine treibt statt zu bremsen.
    """

    signal: str
    minimum: float | None = None
    maximum: float | None = None
    action: str = "abort"

    def __post_init__(self) -> None:
        if not self.signal:
            raise ValueError("a limit needs a signal name")
        if self.action not in ACTIONS:
            raise ValueError(
                f"limit for {self.signal!r}: unknown action {self.action!r}; "
                f"expected one of {', '.join(ACTIONS)}"
            )
        if self.minimum is None and self.maximum is None:
            raise ValueError(
                f"limit for {self.signal!r} needs a minimum, a maximum or both"
            )
        for bound, value in (("minimum", self.minimum), ("maximum", self.maximum)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise ValueError(
                    f"limit for {self.signal!r}: {bound} must be a number, "
                    f"got {value!r}"
                )
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError(
                f"limit for {self.signal!r}: minimum {self.minimum:g} is above "
                f"maximum {self.maximum:g}"
            )

    @property
    def aborts(self) -> bool:
        return self.action == "abort"

    @property
    def range_text(self) -> str:
        if self.minimum is None:
            return f"at most {self.maximum:g}"
        if self.maximum is None:
            return f"at least {self.minimum:g}"
        return f"between {self.minimum:g} and {self.maximum:g}"

    def check(self, value: float) -> str | None:
        """``None`` solange der Wert im Bereich liegt, sonst der Grund."""
        if self.maximum is not None and value > self.maximum:
            return (
                f"{self.signal} = {value:g} is above its limit of "
                f"{self.maximum:g}"
            )
        if self.minimum is not None and value < self.minimum:
            return (
                f"{self.signal} = {value:g} is below its limit of "
                f"{self.minimum:g}"
            )
        return None


@dataclass(frozen=True)
class Violation:
    """Eine festgestellte Verletzung, mit dem Zeitpunkt ihrer Feststellung.

    ``limit`` ist ``None``, wenn nicht ein Wert den Bereich verlassen hat,
    sondern ein Telegramm ausgeblieben ist. Ein Sensor, der schweigt, ist
    kein unkritischer Sensor -- deshalb bricht das ebenfalls ab.
    """

    reason: str
    monotonic: float
    signal: str | None = None
    value: float | None = None
    limit: Limit | None = None

    @property
    def aborts(self) -> bool:
        return self.limit is None or self.limit.aborts

    def __str__(self) -> str:
        kind = "limit" if self.limit is not None else "watchdog"
        return f"[{kind}] {self.reason}"


@dataclass(frozen=True)
class SafeCommand:
    """Ein Telegramm des sicheren Zustands."""

    message: str
    values: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError("a safe command needs a message name")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


@dataclass(frozen=True)
class SafeStateResult:
    """Was beim Auslösen tatsächlich auf den Bus ging."""

    sent: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()

    @property
    def complete(self) -> bool:
        return not self.failed

    def __str__(self) -> str:
        if self.complete:
            return f"safe state reached: {', '.join(self.sent) or 'nothing to send'}"
        broken = "; ".join(f"{name}: {error}" for name, error in self.failed)
        delivered = ", ".join(self.sent) or "none"
        return (
            f"safe state INCOMPLETE -- delivered: {delivered}; failed: {broken}"
        )


@dataclass(frozen=True)
class SafeState:
    """Die Telegramme, die den Prüfstand in einen ungefährlichen Zustand bringen.

    **Die Reihenfolge ist die Sendereihenfolge**, und sie ist Teil der
    Sicherheit: am Drehmomentprüfstand muss erst der Prüfling momentfrei
    werden und danach die Lastmaschine herunterfahren -- umgekehrt
    beschleunigt der Prüfling gegen eine wegfallende Last.

    Ein fehlgeschlagenes Telegramm hält die Kette nicht auf: die übrigen
    werden trotzdem versucht, und :class:`SafeStateResult` sagt hinterher,
    was durchkam.
    """

    commands: tuple[SafeCommand, ...]
    timeout: float = DEFAULT_SEND_TIMEOUT
    attempts: int = DEFAULT_ATTEMPTS

    def __init__(
        self,
        commands: Iterable[SafeCommand | Mapping[str, Any]],
        *,
        timeout: float = DEFAULT_SEND_TIMEOUT,
        attempts: int = DEFAULT_ATTEMPTS,
    ) -> None:
        resolved = tuple(
            command
            if isinstance(command, SafeCommand)
            else _safe_command_from_dict(command)
            for command in commands
        )
        if not resolved:
            raise ValueError(
                "a safe state without commands would do nothing; leave it "
                "unset instead of declaring an empty one"
            )
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if attempts < 1:
            raise ValueError("attempts must be at least one")
        object.__setattr__(self, "commands", resolved)
        object.__setattr__(self, "timeout", timeout)
        object.__setattr__(self, "attempts", attempts)

    @property
    def message_names(self) -> tuple[str, ...]:
        return tuple(command.message for command in self.commands)

    def validate(self, catalog: Catalog = DEFAULT_CATALOG) -> None:
        """Prüfen, dass jedes Telegramm existiert, schreibbar und baubar ist.

        Wird beim Aufbau der Messung aufgerufen, nicht im Notfall: ein
        sicherer Zustand, der erst beim Auslösen auffliegt, ist keiner.
        """
        for command in self.commands:
            definition = catalog[command.message]
            # encode() lehnt eine Statusmeldung ab und prüft Defaults,
            # Wertebereich und Skalierung -- alles jetzt statt später.
            definition.encode(command.values)

    def apply(
        self,
        send: Callable[[str, Mapping[str, float], float], None],
    ) -> SafeStateResult:
        """Alle Telegramme senden und melden, was ankam.

        ``send(message, values, timeout)`` ist die Sendefunktion; jedes
        Telegramm wird bis zu ``attempts`` mal versucht.
        """
        sent: list[str] = []
        failed: list[tuple[str, str]] = []

        for command in self.commands:
            error: BaseException | None = None
            for attempt in range(self.attempts):
                try:
                    send(command.message, command.values, self.timeout)
                except Exception as problem:  # noqa: BLE001 - gemeldet, nicht geschluckt
                    error = problem
                    if attempt + 1 < self.attempts:
                        time.sleep(0.01)
                    continue
                error = None
                break

            if error is None:
                sent.append(command.message)
            else:
                failed.append((command.message, f"{type(error).__name__}: {error}"))

        return SafeStateResult(sent=tuple(sent), failed=tuple(failed))


def _safe_command_from_dict(values: Mapping[str, Any]) -> SafeCommand:
    if not isinstance(values, Mapping):
        raise ValueError(
            f"a safe state entry must be an object with 'message' and "
            f"'values', got {values!r}"
        )
    unknown = sorted(set(values) - {"message", "values"})
    if unknown:
        raise ValueError(
            f"unknown key(s) in safe state entry: {', '.join(unknown)}"
        )
    if "message" not in values:
        raise ValueError("a safe state entry requires 'message'")
    return SafeCommand(values["message"], values.get("values", {}))


def safe_state_from_list(
    entries: Any, *, catalog: Catalog = DEFAULT_CATALOG
) -> SafeState:
    """``[{"message": "broadcast_command", "values": {"command": 0}}]``."""
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise ValueError("'safe_state' must be a list of command objects")
    state = SafeState(entries)
    state.validate(catalog)
    return state


def limits_from_dict(
    declaration: Mapping[str, Any], definitions: Sequence[Message]
) -> tuple[Limit, ...]:
    """Grenzwerte aus der JSON-Form bauen und gegen den Katalog prüfen.

    Zwei Schreibweisen. Die kurze nennt nur eine Zahl und meint eine
    Obergrenze, weil das der Fall ist, für den sie bisher benutzt wurde::

        "limits": {"temperature": 80.0}

    Die lange nennt beide Richtungen und die Aktion::

        "limits": {"u_dc": {"min": 300, "max": 420, "action": "warn"}}
    """
    limits: list[Limit] = []
    for name, value in declaration.items():
        try:
            resolve_signal(definitions, name)
        except LookupError as error:
            raise ValueError(f"limit for {name!r}: {error}") from None

        if isinstance(value, Mapping):
            unknown = sorted(set(value) - {"min", "max", "action"})
            if unknown:
                raise ValueError(
                    f"limit for {name!r}: unknown key(s) {', '.join(unknown)}; "
                    f"expected min, max, action"
                )
            try:
                limits.append(
                    Limit(
                        name,
                        minimum=value.get("min"),
                        maximum=value.get("max"),
                        action=value.get("action", "abort"),
                    )
                )
            except ValueError as error:
                raise ValueError(str(error)) from None
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"limit for {name!r} must be a number or an object")
        else:
            limits.append(Limit(name, maximum=float(value)))

    return tuple(limits)


def upper_limits(limits: Iterable[Limit]) -> dict[str, float]:
    """Nur die Obergrenzen, nach Signalnamen -- die kurze Sicht auf die Regeln."""
    return {
        limit.signal: limit.maximum
        for limit in limits
        if limit.maximum is not None
    }
