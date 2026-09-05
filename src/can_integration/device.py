"""Die einfache Schnittstelle: ein Objekt, benannte Werte, kurze Aufrufe.

Ein Messskript soll nicht mit Bus, Filtern und Threads zu tun haben, sondern
mit Messgrößen:

    from can_integration import connect, get, set_signal

    connect(["motor_temperature", "inverter_speed"])
    temperatur = get("temperature")
    set_signal("rpm_target", 1000)

Darunter liegt derselbe Katalog wie überall im Package. Eine neue CAN-Funktion
wird deshalb *nur* im Katalog eingetragen und ist danach ohne eine Zeile Code
über ihren Signalnamen erreichbar -- gelesen mit :func:`get`, geschrieben mit
:func:`set_signal`.

Wer mehrere Busse oder mehrere Geräte gleichzeitig braucht, benutzt statt der
Modulfunktionen direkt :class:`Device`; die Modulfunktionen sind nichts
anderes als ein Gerät, das das Modul für ein Skript mitführt.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Mapping
from types import TracebackType

import can

from .bus import Reading
from .catalog import DEFAULT_CATALOG, Catalog
from .calibration import (
    Calibration,
    CalibrationError,
    TareResult,
    check_at_rest,
    summarise,
)
from .config import DEFAULT_MAX_AGE, DEFAULT_STARTUP_TIMEOUT, Config
from .monitor import SignalMonitor
from .safety import (
    Limit,
    SafeState,
    SafeStateError,
    SafeStateResult,
    Violation,
)
from .signals import Message, Signal


class NotConnectedError(RuntimeError):
    """Raised when a module-level call happens before :func:`connect`."""


class Device:
    """Ein Prüfstand am CAN-Bus, angesprochen über Signalnamen.

    Liest über einen :class:`~can_integration.monitor.SignalMonitor`, hält
    also immer den *neuesten* Wert bereit und blockiert beim Lesen nicht. Ein
    veralteter Wert wird nicht geliefert, sondern führt zu einem Fehler --
    siehe die Fehlerbehandlung des Monitors.

    Schreiben geht denselben Weg zurück: :meth:`set` kodiert den physikalischen
    Wert nach der Katalogdefinition und sendet das Telegramm. Nur als
    ``writable`` deklarierte Nachrichten dürfen gesendet werden.
    """

    def __init__(
        self,
        messages: str | Message | Iterable[str | Message],
        *,
        max_age: float = DEFAULT_MAX_AGE,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        bus: can.BusABC | None = None,
        interface: str | None = None,
        channel: str | None = None,
        bitrate: int | None = None,
        limits: Iterable[Limit] = (),
        calibrations: Iterable[Calibration] = (),
        safe_state: SafeState | None = None,
        catalog: Catalog = DEFAULT_CATALOG,
    ) -> None:
        limits = tuple(limits)
        if safe_state is not None:
            # Jetzt prüfen, nicht im Notfall: ein sicherer Zustand, der erst
            # beim Auslösen auffliegt, ist keiner.
            safe_state.validate(catalog)

        self._safe_state = safe_state
        self._safe_lock = threading.Lock()
        self._safe_triggered = False
        #: Ergebnis des letzten Auslösens, für Protokoll und Nachschau.
        self.last_safe_state: SafeStateResult | None = None

        self._monitor = SignalMonitor(
            messages,
            max_age=max_age,
            startup_timeout=startup_timeout,
            bus=bus,
            interface=interface,
            channel=channel,
            bitrate=bitrate,
            limits=limits,
            calibrations=calibrations,
            on_violation=self._on_violation,
            watchdog=safe_state is not None,
            catalog=catalog,
        )

    @classmethod
    def from_config(
        cls,
        config: Config,
        *,
        bus: can.BusABC | None = None,
    ) -> Device:
        """Ein Gerät aus einer JSON-Konfiguration, wahlweise auf fremdem Bus."""
        return cls(
            config.definitions,
            max_age=config.max_age,
            startup_timeout=config.startup_timeout,
            bus=bus,
            interface=None if bus is not None else config.interface,
            channel=None if bus is not None else config.channel,
            bitrate=None if bus is not None else config.bitrate,
            limits=config.limit_rules,
            calibrations=config.calibration_rules,
            safe_state=config.safe_state,
            catalog=config.catalog,
        )

    # -- Lebenszyklus ------------------------------------------------------

    def start(self) -> Device:
        """Bus öffnen und auf das erste Telegramm jeder Nachricht warten."""
        self._monitor.start()
        return self

    def stop(self) -> None:
        """Sicheren Zustand senden, Empfang beenden, eigenen Bus schließen.

        Der sichere Zustand geht **vor** dem Schließen des Busses raus, und er
        geht bei jedem Ende raus -- auch beim geplanten. Eine Messung, die
        sauber endet, soll den Prüfstand ebenso entwaffnet zurücklassen wie
        eine, die abbricht.

        Wirft ``SafeStateError``, wenn nicht alles durchkam. Der Empfang wird
        in jedem Fall beendet.
        """
        result = None
        try:
            if self._safe_state is not None:
                result = self.safe()
        finally:
            self._monitor.stop()

        if result is not None and not result.complete:
            raise SafeStateError(str(result))

    def safe(self) -> SafeStateResult:
        """Den sicheren Zustand jetzt senden und melden, was ankam.

        Ohne konfigurierten sicheren Zustand ein leeres, vollständiges
        Ergebnis -- der Aufruf ist dann eine Aussage über nichts, kein Fehler.
        """
        if self._safe_state is None:
            return SafeStateResult()

        result = self._safe_state.apply(self._send_safe)
        self.last_safe_state = result
        return result

    def _send_safe(
        self, message: str, values: Mapping[str, float], timeout: float
    ) -> None:
        self._monitor.connection.send(message, values, timeout=timeout)

    def _on_violation(self, violation: Violation) -> None:
        """Aus dem Empfangsthread: Grenzwert verletzt oder Telegramm weg.

        Nur ein Abbruch löst aus, und nur einmal -- danach ist die Messung
        ohnehin zu Ende, und ein zweites Auslösen brächte den Prüfstand nicht
        sicherer.
        """
        if not violation.aborts:
            return
        with self._safe_lock:
            if self._safe_triggered:
                return
            self._safe_triggered = True
        self.safe()

    @property
    def safe_state(self) -> SafeState | None:
        return self._safe_state

    @property
    def limits(self) -> tuple[Limit, ...]:
        return self._monitor.limits

    @property
    def violations(self) -> tuple[Violation, ...]:
        """Alles, was seit dem Start aufgefallen ist, in der Reihenfolge."""
        return self._monitor.violations

    @property
    def tripped(self) -> Violation | None:
        """Die Verletzung, die die Messung abgebrochen hat, falls es eine gab."""
        return self._monitor.tripped

    def __enter__(self) -> Device:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # Ein fehlgeschlagener sicherer Zustand wird auch dann gemeldet, wenn
        # der Block ohnehin mit einem Fehler endet: die ursprüngliche Ursache
        # bleibt als __context__ in der Kette sichtbar, aber "der Prüfstand
        # liess sich nicht entwaffnen" ist die dringendere Nachricht.
        self.stop()

    # -- Lesen -------------------------------------------------------------

    def get(self, name: str) -> float:
        """Der aktuelle Wert eines Signals, z. B. ``get("temperature")``."""
        return self._monitor.value(name)

    def values(self) -> dict[str, float]:
        """Alle überwachten Signale auf einmal, als Messzeile."""
        return self._monitor.values()

    def age(self, name: str) -> float:
        """Sekunden seit dem Telegramm, ``inf`` wenn noch keins kam."""
        return self._monitor.age(name)

    def reading(self, name: str) -> Reading | None:
        """Das jüngste Telegramm zu diesem Signal, ungeprüft auf Alter."""
        return self._monitor.reading(name)

    def signal(self, name: str) -> Signal:
        """Die Definition hinter einem Namen: Einheit, Offset, Skalierung."""
        return self._monitor.signal(name)

    # -- Nullpunkt und Spanne ----------------------------------------------

    @property
    def calibrations(self) -> tuple[Calibration, ...]:
        """Alle wirksamen Kalibrierungen -- gehören in den Kopf der Messdatei."""
        return self._monitor.calibrations

    def calibration(self, name: str) -> Calibration | None:
        """Nullpunkt und Spanne eines Signals, oder ``None``."""
        return self._monitor.calibration(name)

    def set_calibration(self, calibration: Calibration) -> None:
        """Eine Kalibrierung setzen oder ersetzen, auch im laufenden Betrieb."""
        self._monitor.set_calibration(calibration)

    def tare(
        self,
        name: str,
        *,
        duration: float = 1.0,
        minimum_samples: int = 5,
        tolerance: float | None = None,
        reference: str = "",
    ) -> TareResult:
        """Den Nullpunkt eines Signals im Ruhezustand messen und setzen.

        Nimmt ``duration`` Sekunden lang Werte auf und legt deren Mittel als
        Tara fest. Gemittelt wird, weil eine Wägezelle rauscht: ein einzelner
        Wert wäre ein Zufallsnullpunkt für den ganzen Lauf.

        ``tolerance`` -- in der Einheit des Telegramms -- ist die Sicherung
        dagegen, versehentlich im Betrieb zu tarieren. Streuen die Werte
        weiter als erlaubt, stand der Aufbau nicht still, und der Abgleich
        wird abgelehnt statt still übernommen.

        Der Prüfstand muss dafür laufen und senden; der Nullpunkt gilt ab dem
        nächsten Telegramm.
        """
        current = self.calibration(name) or Calibration(name)
        samples, elapsed = self._sample_raw(name, duration, minimum_samples)

        result = summarise(name, samples, elapsed)
        check_at_rest(result, tolerance)

        self.set_calibration(
            current.with_offset(result.offset, reference=reference)
        )
        return result

    def calibrate(
        self,
        name: str,
        expected: float,
        *,
        duration: float = 1.0,
        minimum_samples: int = 5,
        tolerance: float | None = None,
        reference: str = "",
    ) -> Calibration:
        """Die Spanne gegen einen bekannten Wert abgleichen.

        Ablauf am Schubprüfstand: unbelastet :meth:`tare` rufen, dann das
        Prüfgewicht auflegen und ``calibrate("weight", 500.0)``. Der Faktor
        wird so gesetzt, dass die Messung den bekannten Wert liefert.

        ``reference`` sollte sagen, wogegen abgeglichen wurde -- ein Faktor
        ohne diese Angabe ist eine Zahl ohne Aussage.
        """
        if expected == 0:
            raise ValueError(
                "calibrating against zero would say nothing about the span; "
                "use tare() for the zero point"
            )

        current = self.calibration(name) or Calibration(name)
        samples, elapsed = self._sample_raw(name, duration, minimum_samples)
        result = summarise(name, samples, elapsed)
        check_at_rest(result, tolerance)

        tared = result.offset - current.offset
        if tared == 0:
            raise CalibrationError(
                f"{name} reads its zero point with the reference applied: "
                f"either nothing is loaded or the tare is wrong, and a span "
                f"cannot be derived from it"
            )

        calibration = current.with_factor(expected / tared, reference=reference)
        self.set_calibration(calibration)
        return calibration

    def _sample_raw(
        self, name: str, duration: float, minimum_samples: int
    ) -> tuple[list[float], float]:
        """Werte sammeln, wie das Gerät sie meldet -- vor jeder Kalibrierung.

        Gezählt werden nur *neue* Telegramme: schneller abzufragen als der
        Sensor sendet, würde denselben Wert mehrfach mitteln und eine
        Genauigkeit vortäuschen, die es nicht gibt.
        """
        if duration <= 0:
            raise ValueError("duration must be greater than zero")
        if minimum_samples < 1:
            raise ValueError("minimum_samples must be at least one")

        current = self.calibration(name) or Calibration(name)
        signal_name = self.signal(name).name

        samples: list[float] = []
        last: float | None = None
        started = time.monotonic()
        # Reicht die Zeit nicht für die geforderten Werte, wird gewartet --
        # aber nicht endlos, sonst hängt ein Abgleich an einem stummen Sensor.
        give_up = started + duration * 3 + 0.5

        while True:
            self.get(name)  # erzwingt Frische, Grenzwerte und Busfehler
            reading = self.reading(name)
            # Nur Telegramme aus dem Abgleichfenster. Das zuletzt empfangene
            # stammt aus der Zeit *davor* -- beim Kalibrieren also von vor dem
            # Auflegen des Prüfgewichts, und es würde den Mittelwert ziehen.
            if (
                reading is not None
                and reading.monotonic > started
                and reading.monotonic != last
            ):
                last = reading.monotonic
                samples.append(current.undo(reading.values[signal_name]))

            now = time.monotonic()
            if now >= started + duration and len(samples) >= minimum_samples:
                return samples, now - started
            if now >= give_up:
                raise CalibrationError(
                    f"only {len(samples)} reading(s) of {name!r} arrived in "
                    f"{now - started:.2f} s, {minimum_samples} are required; "
                    f"is the sensor sending often enough?"
                )
            time.sleep(0.002)

    # -- Schreiben ---------------------------------------------------------

    def set(self, name: str, value: float, *, timeout: float | None = None) -> None:
        """Einen Sollwert setzen, z. B. ``set("rpm_target", 1000)``.

        Sucht das Kommandotelegramm, das dieses Signal trägt, kodiert den Wert
        und sendet es. Die übrigen Signale des Telegramms müssen im Katalog
        eine Vorgabe (``default``) haben, sonst nennt der Fehler die fehlenden.
        """
        self._monitor.connection.send_signal(name, value, timeout=timeout)

    def send(
        self,
        message: str | Message,
        values: Mapping[str, float] | None = None,
        *,
        timeout: float | None = None,
        **signals: float,
    ) -> None:
        """Ein ganzes Kommandotelegramm senden.

        Werte lassen sich als Mapping oder als Schlüsselwörter übergeben::

            device.send("motor_command", rpm_target=1000, enable=1)
        """
        combined = {**(values or {}), **signals}
        self._monitor.connection.send(message, combined, timeout=timeout)

    # -- Auskunft ----------------------------------------------------------

    @property
    def signal_names(self) -> tuple[str, ...]:
        """Alle lesbaren Namen, in Nachrichtenreihenfolge. CSV-Kopfzeile."""
        return self._monitor.signal_names

    @property
    def messages(self) -> tuple[Message, ...]:
        """Die überwachten Katalogeinträge."""
        return self._monitor.messages

    @property
    def monitor(self) -> SignalMonitor:
        """Der Monitor darunter, für alles jenseits dieser Kurzform."""
        return self._monitor


# --------------------------------------------------------------------------
# Modulfunktionen: ein Gerät, das das Modul für ein einzelnes Skript mitführt.
# --------------------------------------------------------------------------

_device: Device | None = None


def connect(
    messages: str | Message | Iterable[str | Message] | Config,
    *,
    max_age: float = DEFAULT_MAX_AGE,
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
    bus: can.BusABC | None = None,
    interface: str | None = None,
    channel: str | None = None,
    bitrate: int | None = None,
    catalog: Catalog = DEFAULT_CATALOG,
) -> Device:
    """Verbindet und merkt sich das Gerät für die Modulfunktionen.

    ``messages`` sind Katalognamen oder eine fertige :class:`Config`. Gibt das
    Gerät zurück, sodass sich beide Stile mischen lassen.
    """
    global _device
    if _device is not None:
        raise RuntimeError(
            "es ist bereits ein Gerät verbunden; disconnect() aufrufen oder "
            "für mehrere Busse Device(...) direkt benutzen"
        )

    if isinstance(messages, Config):
        device = Device.from_config(messages, bus=bus)
    else:
        device = Device(
            messages,
            max_age=max_age,
            startup_timeout=startup_timeout,
            bus=bus,
            interface=interface,
            channel=channel,
            bitrate=bitrate,
            catalog=catalog,
        )

    device.start()
    _device = device
    return device


def disconnect() -> None:
    """Beendet den Empfang und gibt das gemerkte Gerät frei."""
    global _device
    if _device is not None:
        _device.stop()
        _device = None


def device() -> Device:
    """Das verbundene Gerät, oder ein Fehler mit klarem Hinweis."""
    if _device is None:
        raise NotConnectedError(
            "kein Gerät verbunden: zuerst connect([...]) aufrufen"
        )
    return _device


def get(name: str) -> float:
    """Aktueller Wert eines Signals, z. B. ``get("temperature")``."""
    return device().get(name)


def set_signal(name: str, value: float, *, timeout: float | None = None) -> None:
    """Sollwert setzen, z. B. ``set_signal("rpm_target", 1000)``.

    Heißt nicht ``set``, weil das den eingebauten Typ verdecken würde.
    """
    device().set(name, value, timeout=timeout)


def send(
    message: str | Message,
    values: Mapping[str, float] | None = None,
    *,
    timeout: float | None = None,
    **signals: float,
) -> None:
    """Ein ganzes Kommandotelegramm senden."""
    device().send(message, values, timeout=timeout, **signals)


def values() -> dict[str, float]:
    """Alle überwachten Signale auf einmal."""
    return device().values()


def age(name: str) -> float:
    """Sekunden seit dem Telegramm dieses Signals."""
    return device().age(name)


# --------------------------------------------------------------------------
# Benannte Kurzformen für die Größen, die am Prüfstand täglich gebraucht
# werden. Jede ist eine Zeile über get()/set_signal() -- sie sind Bequemlich-
# keit, keine zweite Wahrheit. Eine neue CAN-ID braucht sie *nicht*: sie ist
# über get("<signalname>") sofort erreichbar, sobald sie im Katalog steht.
# --------------------------------------------------------------------------


def get_temperature() -> float:
    """Temperatur in °C aus der überwachten Nachricht, die sie trägt."""
    return get("temperature")


def get_rpm() -> float:
    """Istdrehzahl in min-1 (``inverter_speed.rpm_actual``)."""
    return get("rpm_actual")


def get_rpm_target() -> float:
    """Vom Inverter gemeldeter Drehzahl-Sollwert in min-1."""
    return get("rpm_target")


def get_torque() -> float:
    """Drehmoment als Rohwert -- die Skalierung ist unbestätigt."""
    return get("torque_actual")


def get_thrust() -> float:
    """Schub bzw. Gewicht der Wägezelle in g."""
    return get("weight")


def set_rpm(value: float, *, timeout: float | None = None) -> None:
    """Drehzahl-Sollwert vorgeben.

    Setzt voraus, dass der Katalog ein Kommandotelegramm mit dem Signal
    ``rpm_target`` und ``writable=True`` enthält. Das eingebaute
    ``inverter_speed`` ist eine *Statusmeldung des Inverters* und deshalb
    bewusst nicht schreibbar; die Kommando-ID muss aus der Herstellerdoku
    ergänzt werden.
    """
    set_signal("rpm_target", value, timeout=timeout)


__all__ = [
    "Device",
    "NotConnectedError",
    "age",
    "connect",
    "device",
    "disconnect",
    "get",
    "get_rpm",
    "get_rpm_target",
    "get_temperature",
    "get_thrust",
    "get_torque",
    "send",
    "set_rpm",
    "set_signal",
    "values",
]
