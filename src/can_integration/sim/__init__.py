"""Ein simulierter Prüfstand: dieselben Telegramme, ohne Hardware.

Zwei Betriebsarten, beide über denselben Katalog wie die Bibliothek:

* :class:`LogPlayer` spielt eine echte Aufzeichnung zeitrichtig auf einen Bus.
  Das ist die realistischste Quelle, die es ohne Prüfstand gibt -- die Werte
  stammen aus einer Messung, nicht aus einem Modell -- aber sie reagiert
  nicht auf geschriebene Sollwerte.
* Ein Zustandsmodell, das auf Kommandos antwortet, kommt in einem späteren
  Schritt dazu.

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
from .replay import (
    DIRECTIONS,
    SIM_CHANNEL,
    SIM_INTERFACE,
    LogPlayer,
    host_sent_keys,
)

__all__ = [
    "Coverage",
    "FrameKey",
    "LogFormatError",
    "LogFrame",
    "LogPlayer",
    "Recording",
    "DIRECTIONS",
    "SIM_CHANNEL",
    "SIM_INTERFACE",
    "host_sent_keys",
    "parse_log",
]
