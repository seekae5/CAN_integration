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

from collections.abc import Iterable, Mapping
from types import TracebackType

import can

from .bus import Reading
from .catalog import DEFAULT_CATALOG, Catalog
from .config import DEFAULT_MAX_AGE, DEFAULT_STARTUP_TIMEOUT, Config
from .monitor import SignalMonitor
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
        catalog: Catalog = DEFAULT_CATALOG,
    ) -> None:
        self._monitor = SignalMonitor(
            messages,
            max_age=max_age,
            startup_timeout=startup_timeout,
            bus=bus,
            interface=interface,
            channel=channel,
            bitrate=bitrate,
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
            catalog=config.catalog,
        )

    # -- Lebenszyklus ------------------------------------------------------

    def start(self) -> Device:
        """Bus öffnen und auf das erste Telegramm jeder Nachricht warten."""
        self._monitor.start()
        return self

    def stop(self) -> None:
        """Empfang beenden und einen selbst geöffneten Bus schließen."""
        self._monitor.stop()

    def __enter__(self) -> Device:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
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
