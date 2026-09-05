"""Ein simulierter Prüfstand: dieselben Telegramme, ohne Hardware.

Zwei Betriebsarten, beide über denselben Katalog wie die Bibliothek:

* :class:`LogPlayer` spielt eine echte Aufzeichnung zeitrichtig auf einen Bus.
  Das ist die realistischste Quelle, die es ohne Prüfstand gibt -- die Werte
  stammen aus einer Messung, nicht aus einem Modell -- aber sie reagiert
  nicht auf geschriebene Sollwerte.
* :class:`SimulatedDevice` sendet einen Zustand zyklisch und lässt ihn von
  Kommandotelegrammen verändern. Anfangs- und Ruhezustand stammen wieder aus
  der Aufzeichnung, sodass auch die Schreibrichtung ohne Prüfstand geprüft
  werden kann.

    from can_integration.sim import Recording, LogPlayer

    recording = Recording.from_file("CAN-Logs/0000309.TXT")
    print(recording.coverage().report())
"""

from .logfile import (
    Coverage,
    FrameKey,
    LogFormatError,
    LogFrame,
    Recording,
    parse_log,
)
from .device import (
    BROADCAST_ARM,
    BROADCAST_DISARM,
    COMMAND_RPM_TARGET,
    STOP_COMMAND_IDS,
    CommandHandler,
    Cycle,
    RecordedInverter,
    SimulatedDevice,
    running_moment,
    schedule_from_recording,
    state_from_recording,
)
from .replay import (
    DIRECTIONS,
    SIM_CHANNEL,
    SIM_INTERFACE,
    LogPlayer,
    host_sent_keys,
)

__all__ = [
    "BROADCAST_ARM",
    "BROADCAST_DISARM",
    "COMMAND_RPM_TARGET",
    "STOP_COMMAND_IDS",
    "CommandHandler",
    "Coverage",
    "Cycle",
    "FrameKey",
    "LogFormatError",
    "LogFrame",
    "LogPlayer",
    "RecordedInverter",
    "Recording",
    "SimulatedDevice",
    "DIRECTIONS",
    "SIM_CHANNEL",
    "SIM_INTERFACE",
    "host_sent_keys",
    "parse_log",
    "running_moment",
    "schedule_from_recording",
    "state_from_recording",
]
