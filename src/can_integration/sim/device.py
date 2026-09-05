"""Ein Geraet, das antwortet -- die Gegenseite der Bibliothek auf dem Bus.

Der Replay in :mod:`can_integration.sim.replay` ist ein Tonband: er spielt
gemessene Telegramme, aber ein geschriebener Sollwert aendert daran nichts.
Hier steht das Gegenstueck: ein Zustand, der zyklisch gesendet und von
Kommandotelegrammen veraendert wird.

Das Modell erfindet keine Physik. Sein Anfangszustand und sein Ruhezustand
werden aus einer echten Aufzeichnung genommen -- die erste und die letzte
Nutzlast je Telegramm -- und die Kommandos schalten zwischen dem, was das Log
vorher und nachher zeigt. Was darueber hinausgeht, ist eine Annahme und als
solche benannt.

    from can_integration.sim import Recording, SimulatedDevice

    recording = Recording.from_file("CAN-Logs/0000309.TXT")
    with SimulatedDevice.from_recording(recording, catalog=catalog) as device:
        ...
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import can

from ..catalog import DEFAULT_CATALOG, Catalog
from ..signals import (
    CanFrame,
    InvalidFrameError,
    InvalidValueError,
    Message,
    UnknownSignalError,
    resolve_signal,
    signal_keys,
)
from .behaviour import BehaviourStep, Noise, as_behaviour
from .logfile import Recording
from .replay import host_sent_keys
from .transport import BusOwner

#: Zykluszeit fuer ein Telegramm, dessen Wiederholrate weder gemessen noch im
#: Katalog hinterlegt ist. Bewusst langsam: lieber zu selten senden als eine
#: Rate vorspiegeln, die niemand beobachtet hat.
DEFAULT_PERIOD = 1.0

#: ``broadcast_command``: die einzige Kommandobedeutung dieses Protokolls, die
#: aus der Herstellerdokumentation stammt (Katalogeintrag, Abschnitt 5/15).
BROADCAST_DISARM = 0
BROADCAST_ARM = 1

#: ``inverter_command``: Parameterauswahl in Byte 0..1. 0x0110 ist laut
#: Herstellerdoku der Drehzahlsollwert.
COMMAND_RPM_TARGET = 0x0110

#: Die beiden Kommandos, nach denen Log 0000309 in den Ruhezustand faellt.
#: Welches der beiden es ausloest, laesst sich daraus nicht sagen: sie liegen
#: 3 ms auseinander, der zyklische Verkehr kippt 3 ms nach dem zweiten. Die
#: Simulation reagiert deshalb auf beide.
STOP_COMMAND_IDS = (0x0010, 0x0220)

#: Ein Kommandobehandler bekommt das Geraet, das erkannte Telegramm und
#: dessen dekodierte Werte. Was er damit tut, ist Sache des Modells.
CommandHandler = Callable[["SimulatedDevice", Message, Mapping[str, float]], None]


@dataclass(frozen=True)
class Cycle:
    """Ein Telegramm, das das Geraet zyklisch sendet.

    ``template`` ist eine aufgezeichnete Nutzlast. Sie legt zweierlei fest,
    das sich aus den Signalen allein nicht ergibt: die gesendete Laenge -- der
    Prüfstand schickt durchgaengig DLC 8, auch wo der Katalog nur zwei Bytes
    beschreibt -- und den Inhalt der Bytes, die kein Signal abdeckt.
    """

    message: Message
    period: float
    template: bytes = b""
    measured: bool = False

    def __post_init__(self) -> None:
        if self.period <= 0:
            raise ValueError(
                f"cycle time of {self.message.name!r} must be greater than "
                f"zero, got {self.period}"
            )
        if self.template and len(self.template) < self.message.minimum_length:
            raise ValueError(
                f"template for {self.message.name!r} is "
                f"{len(self.template)} bytes, the signals need "
                f"{self.message.minimum_length}"
            )

    @property
    def payload_length(self) -> int:
        return len(self.template) or self.message.payload_length


class SimulatedDevice:
    """Sendet einen Zustand zyklisch und laesst ihn von Kommandos aendern.

    Der Zustand ist nach Signalnamen benannt, und zwar nach denselben, unter
    denen :meth:`can_integration.Device.values` sie liefert: was hier gesetzt
    wird, liest die Messseite unter demselben Namen zurueck.

    Empfangen wird alles, was der Katalog ``writable`` nennt -- die Definition
    eines Kommandotelegramms in diesem Paket. Enthaelt der Katalog keines,
    sendet das Geraet nur.
    """

    def __init__(
        self,
        cycles: Iterable[Cycle],
        state: Mapping[str, float],
        *,
        bus: can.BusABC | None = None,
        interface: str | None = None,
        channel: str | None = None,
        bitrate: int | None = None,
        commands: CommandHandler | None = None,
        behaviour: BehaviourStep | Sequence[BehaviourStep] | None = None,
        noise: Noise | None = None,
        armed: bool = True,
        catalog: Catalog = DEFAULT_CATALOG,
    ) -> None:
        self.cycles = tuple(cycles)
        if not self.cycles:
            raise ValueError("a simulated device needs at least one telegram")

        duplicates = sorted(
            {
                cycle.message.name
                for index, cycle in enumerate(self.cycles)
                if cycle.message.key
                in {other.message.key for other in self.cycles[:index]}
            }
        )
        if duplicates:
            raise ValueError(
                f"telegram(s) scheduled twice: {', '.join(duplicates)}"
            )

        self.catalog = catalog
        self.commands = commands
        self.behaviour = as_behaviour(behaviour)
        self.noise = noise
        self._messages = tuple(cycle.message for cycle in self.cycles)
        self._keys = signal_keys(self._messages)
        self._state_key = {
            (message.name, signal.name): key
            for key, (message, signal) in self._keys.items()
        }

        self._lock = threading.Lock()
        self._state = dict(state)
        self._armed = bool(armed)
        self._require_complete_state()

        self._accepted = {
            message.key: message
            for message in catalog.values()
            if message.writable
        }

        self._owner = BusOwner(
            bus=bus, interface=interface, channel=channel, bitrate=bitrate
        )
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._failure: BaseException | None = None

        #: Zaehler fuer Tests und die Konsolenausgabe.
        self.sent = 0
        self.received = 0

    # -- Aufbau aus einer Aufzeichnung ------------------------------------

    @classmethod
    def from_recording(
        cls,
        recording: Recording,
        *,
        catalog: Catalog = DEFAULT_CATALOG,
        commands: CommandHandler | None = None,
        running_at: float | None = None,
        **kwargs: object,
    ) -> SimulatedDevice:
        """Zeitplan, Anfangszustand und Kommandoverhalten aus einem Log.

        Der Anfangszustand ist der Zustand, den die Aufzeichnung kurz vor
        ihrem Stopp-Kommando zeigt -- der laufende Antrieb also, nicht der
        Anfang der Datei, der ihn oft noch gar nicht zeigt. ``running_at``
        waehlt stattdessen einen eigenen Zeitpunkt in Sekunden.

        Ohne ``commands`` wird :class:`RecordedInverter` benutzt: das
        Kommandoverhalten, das genau diese Aufzeichnung zeigt.
        """
        cycles = schedule_from_recording(recording, catalog=catalog)
        if running_at is None:
            running_at = running_moment(recording, catalog=catalog)
        state = state_from_recording(recording, catalog=catalog, at=running_at)
        if commands is None:
            commands = RecordedInverter.from_recording(
                recording, catalog=catalog, running_at=running_at
            )
        return cls(cycles, state, catalog=catalog, commands=commands, **kwargs)  # type: ignore[arg-type]

    # -- Zustand ----------------------------------------------------------

    @property
    def messages(self) -> tuple[Message, ...]:
        return self._messages

    @property
    def signal_names(self) -> tuple[str, ...]:
        return tuple(self._keys)

    @property
    def armed(self) -> bool:
        """Ob das Geraet laeuft. Verhalten rechnen nur, solange es das tut.

        Ein Disarm-Kommando setzt das Flag; danach steht der Zustand still --
        so, wie die Aufzeichnung ihn nach ihrem Stopp zeigt. Gesendet wird
        weiter: ein gestopptes Geraet schweigt nicht, es meldet Ruhe.
        """
        with self._lock:
            return self._armed

    @armed.setter
    def armed(self, value: bool) -> None:
        with self._lock:
            self._armed = bool(value)

    def values(self) -> dict[str, float]:
        """Eine Kopie des Zustands, benannt wie bei ``Device.values``."""
        with self._lock:
            return dict(self._state)

    def get(self, name: str) -> float:
        """Einen Zustandswert lesen; ``name`` darf qualifiziert sein."""
        with self._lock:
            return self._state[self._resolve(name)]

    def set(self, name: str, value: float) -> None:
        """Einen Zustandswert setzen -- das naechste Telegramm traegt ihn."""
        key = self._resolve(name)
        with self._lock:
            self._state[key] = float(value)

    def update(self, values: Mapping[str, float]) -> None:
        """Mehrere Werte auf einmal setzen, etwa einen ganzen Ruhezustand."""
        resolved = {self._resolve(name): float(v) for name, v in values.items()}
        with self._lock:
            self._state.update(resolved)

    def _resolve(self, name: str) -> str:
        if name in self._keys:
            return name
        # Erlaubt auch den unqualifizierten Namen, wenn er eindeutig ist.
        message, signal = resolve_signal(self._messages, name)
        return self._state_key[(message.name, signal.name)]

    def _require_complete_state(self) -> None:
        missing = sorted(key for key in self._keys if key not in self._state)
        if missing:
            raise ValueError(
                f"the initial state is missing a value for "
                f"{', '.join(missing)}; a simulated device has to know every "
                f"signal it sends"
            )

    # -- Bus --------------------------------------------------------------

    def connect(self) -> can.BusABC:
        return self._owner.connect()

    def close(self) -> None:
        self._owner.close()

    @property
    def bitrate(self) -> int:
        return self._owner.bitrate

    def payload(self, cycle: Cycle) -> bytes:
        """Die Nutzlast, die dieses Telegramm gerade tragen wuerde.

        Der Wert-Teil kommt aus :meth:`Message.build_payload`, damit Defaults,
        Skalierung und Wertebereich genau wie beim Senden geprueft werden.
        Ein eingestelltes :class:`~can_integration.sim.behaviour.Noise` wirkt
        hier -- auf den gesendeten Wert, nicht auf den Zustand.
        Danach werden nur die von Signalen belegten Bytes in die
        aufgezeichnete Nutzlast kopiert -- der Rest bleibt, wie er gemessen
        wurde.
        """
        message = cycle.message
        with self._lock:
            values = {
                signal.name: self._state[self._state_key[(message.name, signal.name)]]
                for signal in message.signals
            }

        if self.noise is None:
            body = message.build_payload(values)
        else:
            noisy = {
                signal.name: self.noise(
                    self._state_key[(message.name, signal.name)],
                    values[signal.name],
                )
                for signal in message.signals
            }
            try:
                body = message.build_payload(noisy)
            except InvalidValueError:
                # Rauschen, das aus dem Format faellt, wird verworfen statt
                # das Telegramm zu verlieren.
                body = message.build_payload(values)
        if not cycle.template:
            return body

        payload = bytearray(cycle.template)
        for signal in message.signals:
            payload[signal.offset : signal.end] = body[signal.offset : signal.end]
        return bytes(payload)

    def frame(self, cycle: Cycle) -> can.Message:
        return can.Message(
            arbitration_id=cycle.message.arbitration_id,
            is_extended_id=cycle.message.extended,
            data=self.payload(cycle),
        )

    # -- Betrieb ----------------------------------------------------------

    def run(self, stop: threading.Event | None = None) -> None:
        """Senden und empfangen, bis ``stop`` gesetzt wird.

        Vor jedem Durchgang bekommt das Verhalten die seit dem letzten
        Durchgang vergangene Zeit -- aber nur, solange das Geraet ``armed``
        ist. Die Faelligkeiten laufen auf einer eigenen Uhr weiter, nicht auf
        der Sendezeit: sonst wuerde jede Verzoegerung den Zyklus dauerhaft
        verschieben. Faellt der Lauf trotzdem hinter einen ganzen Zyklus
        zurueck, wird die Faelligkeit neu gesetzt statt aufgeholt -- ein
        Simulator soll nicht in einen Sendesturm laufen.
        """
        if stop is None:
            stop = self._stop
        bus = self.connect()

        now = time.monotonic()
        due = [now] * len(self.cycles)
        last = now

        while not stop.is_set():
            now = time.monotonic()
            if self.behaviour is not None and self.armed:
                self.behaviour(self, now - last)
            last = now

            for index, cycle in enumerate(self.cycles):
                if due[index] > now:
                    continue
                bus.send(self.frame(cycle))
                self.sent += 1
                due[index] += cycle.period
                if due[index] <= now:
                    due[index] = now + cycle.period

            timeout = max(0.0, min(due) - time.monotonic())
            frame = bus.recv(timeout=timeout)
            if frame is not None:
                self.handle(frame)

    def handle(self, frame: CanFrame) -> Message | None:
        """Ein empfangenes Kommandotelegramm auswerten.

        Gibt die erkannte Nachricht zurueck, oder ``None``, wenn der Rahmen
        kein Kommando ist -- fremder Verkehr wird verworfen, nicht geraten.
        """
        if frame.is_error_frame or frame.is_remote_frame:
            return None
        message = self._accepted.get(
            (frame.arbitration_id, bool(frame.is_extended_id))
        )
        if message is None:
            return None

        try:
            values = message.decode(frame.data)
        except InvalidFrameError:
            # Ein zu kurzes Kommando ist ein Fehler des Absenders; das Geraet
            # darf daran nicht sterben, aber es fuehrt es auch nicht aus.
            return None

        self.received += 1
        if self.commands is not None:
            self.commands(self, message, values)
        return message

    def start(self) -> None:
        """Das Geraet in einem Hintergrundthread laufen lassen."""
        if self._thread is not None:
            raise RuntimeError("device is already running")

        self._stop.clear()
        self._failure = None
        self.connect()

        self._thread = threading.Thread(
            target=self._serve, name="can-integration-sim-device", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Anhalten, einen eigenen Bus schliessen und Fehler weiterreichen."""
        self._stop.set()

        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
            self._thread = None

        self.close()
        failure, self._failure = self._failure, None
        if failure is not None:
            raise failure

    def _serve(self) -> None:
        try:
            self.run(self._stop)
        except BaseException as error:  # noqa: BLE001 - an stop() weitergereicht
            self._failure = error

    def __enter__(self) -> SimulatedDevice:
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()


@dataclass
class RecordedInverter:
    """Das Kommandoverhalten, das Log 0000309 zeigt -- und nur das.

    Zwei Zustaende, beide gemessen: ``running`` ist die erste Nutzlast je
    Telegramm, ``idle`` die letzte, nach dem aufgezeichneten Stopp. Die
    Kommandos schalten zwischen ihnen. Ein Drehzahlsollwert wird in den
    Zustand uebernommen, ohne dass eine Drehzahl daraus folgt: welchen
    Verlauf der reale Antrieb daraufhin faehrt, sagt die Aufzeichnung nicht,
    und ein erfundener Verlauf waere schlechter als gar keiner.
    """

    running: Mapping[str, float]
    idle: Mapping[str, float]
    stop_commands: tuple[int, ...] = STOP_COMMAND_IDS
    setpoints: Mapping[int, str] = field(
        default_factory=lambda: {COMMAND_RPM_TARGET: "rpm_target"}
    )
    #: Kommandos, die erkannt, aber bewusst nicht nachgebildet werden.
    ignored: list[tuple[str, int]] = field(default_factory=list)

    @classmethod
    def from_recording(
        cls,
        recording: Recording,
        *,
        catalog: Catalog = DEFAULT_CATALOG,
        running_at: float | None = None,
    ) -> RecordedInverter:
        if running_at is None:
            running_at = running_moment(recording, catalog=catalog)
        return cls(
            running=state_from_recording(recording, catalog=catalog, at=running_at),
            idle=state_from_recording(recording, catalog=catalog),
        )

    def __call__(
        self,
        device: SimulatedDevice,
        message: Message,
        values: Mapping[str, float],
    ) -> None:
        if message.name == "broadcast_command":
            self._broadcast(device, int(values["command"]))
        elif message.name == "inverter_command":
            self._inverter(device, int(values["command_id"]), values["value"])
        else:
            self.ignored.append((message.name, 0))

    def _broadcast(self, device: SimulatedDevice, command: int) -> None:
        if command == BROADCAST_ARM:
            self._apply(device, self.running, armed=True)
        elif command == BROADCAST_DISARM:
            self._apply(device, self.idle, armed=False)
        else:
            # 2 = trigger_Errorlog: dokumentiert, aber ohne beobachtete Wirkung.
            self.ignored.append(("broadcast_command", command))

    def _inverter(
        self, device: SimulatedDevice, command_id: int, value: float
    ) -> None:
        if command_id in self.stop_commands:
            self._apply(device, self.idle, armed=False)
            return

        name = self.setpoints.get(command_id)
        if name is None:
            self.ignored.append(("inverter_command", command_id))
            return
        try:
            device.set(name, value)
        except (UnknownSignalError, KeyError):
            # Der Sollwert gehoert zu einem Telegramm, das dieses Geraet gar
            # nicht sendet: annehmen waere gelogen, abstuerzen unbrauchbar.
            self.ignored.append(("inverter_command", command_id))

    def _apply(
        self,
        device: SimulatedDevice,
        state: Mapping[str, float],
        *,
        armed: bool,
    ) -> None:
        known = set(device.signal_names)
        device.update({k: v for k, v in state.items() if k in known})
        device.armed = armed


def schedule_from_recording(
    recording: Recording,
    *,
    catalog: Catalog = DEFAULT_CATALOG,
    exclude_host: bool = True,
    default_period: float = DEFAULT_PERIOD,
) -> tuple[Cycle, ...]:
    """Welche Telegramme das Geraet sendet, und wie oft.

    Genommen wird, was die Aufzeichnung enthaelt *und* der Katalog beschreibt;
    was im Log der Host gesendet hat, faellt weg -- ein Geraet sendet keine
    Kommandos an sich selbst. Die Zykluszeit kommt aus der Messung, ersatzweise
    aus dem Katalog, ersatzweise aus ``default_period``.
    """
    coverage = recording.coverage(catalog)
    measured = recording.cycle_times()
    templates = recording.first_payloads()
    host = host_sent_keys(catalog) if exclude_host else frozenset()

    cycles: list[Cycle] = []
    for key, message in coverage.known.items():
        if key in host:
            continue
        milliseconds = measured.get(key)
        if milliseconds is not None:
            period, from_measurement = milliseconds / 1000.0, True
        elif message.cycle_time_ms is not None:
            period, from_measurement = message.cycle_time_ms / 1000.0, False
        else:
            period, from_measurement = default_period, False
        cycles.append(
            Cycle(
                message=message,
                period=period,
                template=templates[key],
                measured=from_measurement,
            )
        )
    return tuple(cycles)


def state_from_recording(
    recording: Recording,
    *,
    catalog: Catalog = DEFAULT_CATALOG,
    at: float | None = None,
    exclude_host: bool = True,
) -> dict[str, float]:
    """Den Zustand aus den Nutzlasten einer Aufzeichnung dekodieren.

    ``at`` waehlt den Zeitpunkt in Sekunden seit Aufzeichnungsbeginn; ohne
    Angabe der Endzustand. So laesst sich der laufende Antrieb ebenso
    entnehmen wie der gestoppte -- beides gemessen, keines modelliert.
    Telegramme, die zu diesem Zeitpunkt noch nicht aufgetreten sind, tragen
    ihre erste aufgezeichnete Nutzlast.

    ``exclude_host`` haelt dieselbe Auswahl wie
    :func:`schedule_from_recording`. Das ist keine Bequemlichkeit: die
    Zustandsnamen kommen aus :func:`signal_keys` und haengen davon ab, welche
    Nachrichten zusammen betrachtet werden -- ``temperature`` bleibt schlicht
    ``temperature``, solange nur ein Telegramm es fuehrt. Zwei verschiedene
    Auswahlen ergaeben also zwei verschiedene Namen fuer dasselbe Signal.
    """
    if at is None:
        payloads = recording.last_payloads()
    else:
        # Ein langsames Telegramm kann zum gewaehlten Zeitpunkt noch gar nicht
        # aufgetreten sein. Dann gilt seine erste aufgezeichnete Nutzlast:
        # sonst faehrt das simulierte Geraet mit einer Luecke im Zustand los,
        # obwohl die Aufzeichnung den Wert sehr wohl kennt.
        payloads = recording.first_payloads() | recording.payloads_at(at)
    known = recording.coverage(catalog).known
    host = host_sent_keys(catalog) if exclude_host else frozenset()
    messages = tuple(
        message for key, message in known.items() if key not in host
    )

    state: dict[str, float] = {}
    for name, (message, signal) in signal_keys(messages).items():
        payload = payloads.get(message.key)
        if payload is None:
            continue
        try:
            state[name] = signal.decode(payload)
        except InvalidFrameError:
            # Ein Telegramm, das kuerzer aufgezeichnet ist als der Katalog
            # erwartet: die Abdeckung meldet es, hier wird es uebersprungen.
            continue
    return state


def running_moment(
    recording: Recording, *, catalog: Catalog = DEFAULT_CATALOG
) -> float:
    """Der letzte Zeitpunkt, zu dem die Aufzeichnung das Geraet laufend zeigt.

    Das ist der Augenblick unmittelbar vor dem ersten aufgezeichneten
    Stopp-Kommando. Enthaelt die Aufzeichnung keines, bleibt es beim Anfang:
    ohne Kommando ist nicht zu erkennen, welcher Abschnitt der "laufende"
    sein soll, und der Anfang ist die Annahme, die am wenigsten behauptet.
    """
    try:
        command = catalog["inverter_command"]
    except LookupError:
        return 0.0

    for frame in recording.frames:
        if frame.key != command.key:
            continue
        try:
            values = command.decode(frame.data)
        except InvalidFrameError:
            continue
        if int(values["command_id"]) in STOP_COMMAND_IDS:
            return frame.t
    return 0.0
