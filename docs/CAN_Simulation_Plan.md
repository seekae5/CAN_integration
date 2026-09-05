# Umsetzungsplan: CAN-Simulation

> Ziel: an `can_integration` weiterarbeiten koennen, ohne den realen Pruefstand.
> Referenzmessung: `CAN-Logs/0000309.TXT` (CL1000, Session 286).

## 1. Was das Referenzlog hergibt

Ausgewertet mit dem Parser aus Phase 1; alle Zahlen stammen aus der Datei selbst.

| Eigenschaft | Wert |
|---|---|
| Frames / Dauer | 13.117 Frames ueber 23,276 s (00:00:07,156 – 00:00:30,432) |
| Format | `Timestamp;Type;ID;Data`, `;`-getrennt, Kopfzeilen mit `#` |
| Zeitstempel | `hh:mm:ss` + 3-stellige ms ohne Trenner (`00:00:07156`) |
| `Type` | durchgaengig `1` = 29-Bit Extended ID |
| DLC | durchgaengig 8 |
| Bitrate | 1 Mbit/s (Kopfzeile) |

### Verkehr nach Arbitration-ID

| ID | n | Zykluszeit (Median) | verschiedene Payloads | Rolle |
|---|---|---|---|---|
| `0x1A000003` | 2175 | 11 ms | 2000 | Geraet → Host, dynamisch |
| `0x1A000006` | 2175 | 11 ms | 1754 | Geraet → Host, dynamisch (`current_control_dq`) |
| `0x1A000007` | 2175 | 11 ms | 1752 | Geraet → Host, dynamisch (`voltage_control_dq`) |
| `0x1A00000C` | 2175 | 11 ms | 380 | Geraet → Host |
| `0x1A000008` | 2175 | 11 ms | 2 | Geraet → Host, quasi-statisch (`motion_control_state`) |
| `0x1A000013` | 2175 | 11 ms | 1 | Geraet → Host, konstant 0 |
| `0x01000001` | 46 | 500 ms | 1 | **Host → Geraet**, ASCII `discover` |
| `0x01100000` | 16 | ~1043 ms | 16 | Geraet → Host, langsame Diagnose |
| `0x0A000000` | 2 | – | 2 | **Host → Geraet**, Kommando |
| `0x1A000010/11/12` | je 1 | – | 1 | Geraet → Host, einmalig bei t≈7,54 s (float32-Nutzlast) |

### Das entscheidende Ereignis

```
00:00:26210;1;a000000;1000000000000000     command_id=0x0010, value=0
00:00:26213;1;a000000;2002000000000000     command_id=0x0220, value=0
00:00:26216;1;1a000008;00000000790c0000    motion_ctrl_state: 0xF3 -> 0x00
00:00:26218;1;1a000007;0000000000000000    alle Reglergroessen -> 0
00:00:26219;1;1a000006;11d2e53700000000    Sollwerte -> 0, Istwerte eingefroren
00:00:26220;1;1a00000c;0000000000800000    Ruhemuster
```

Drei Millisekunden nach dem zweiten Kommando kippt der gesamte zyklische
Verkehr in den Ruhezustand und bleibt bis zum Logende dort. Damit enthaelt das
Log genau die zwei Verhaltensweisen, die eine Simulation beweisen muss:
**zyklisch senden** und **auf ein geschriebenes Kommando reagieren**.

### Katalogabdeckung

Der Katalog besteht aus zwei Teilen: `BUILTIN_MESSAGES` im Code und der
Erweiterung `catalog.example.json`. Zusammen (`--catalog catalog.example.json`,
in `Config` das Feld `"catalog"`) deckt er die Aufzeichnung fast vollstaendig:

| | IDs |
|---|---|
| eingebauter Katalog allein | 3 von 12 Telegrammtypen (`0x1A000003`, `0x1A00000C`, `0x1A000013`) |
| eingebaut + `catalog.example.json` | 9 von 12 |
| in keinem von beiden | `0x1A000010`, `0x1A000011`, `0x1A000012` -- je ein Frame bei t≈7,54 s, float32-Nutzlast |
| im Katalog, nicht im Log | `0x1A000004`, `0x1A000005`, `0x1A00000B`, `0x1A00000D`, `0x1A00000F`, `0x0A00000A` |

Dass eine Messung ohne `--catalog` nur ein Viertel der Aufzeichnung dekodiert,
ist keine Randnotiz: der Replay muss unbekannte IDs *melden*, nicht
stillschweigend verwerfen -- die Liste ist genau die offene Arbeit am Katalog.

## 2. Ort und Struktur

Im selben Repository, als Subpackage — Begruendung: der Katalog ist die einzige
Wahrheitsquelle, und der Simulator kodiert exakt das, was die Bibliothek
dekodiert. Ein zweites Projekt hiesse Katalogduplikat oder Versionsmatrix.

```
src/can_integration/sim/
  __init__.py      SimulatedDevice, LogPlayer, Recording  (oeffentliche Namen)
  logfile.py       CL1000-Textlog -> Recording
  replay.py        LogPlayer: Frames zeitrichtig auf einen Bus
  device.py        SimulatedDevice: Zustand, Zyklen, Kommandoempfang
  behaviour.py     Constant, Ramp, FromRecording, Noise
  cli.py           can-integration-sim
tests/
  data/0000309_excerpt.TXT   ~300 Zeilen Fixture (das Original bleibt in CAN-Logs/)
  test_sim_logfile.py
  test_sim_replay.py
  test_sim_device.py
```

`pyproject.toml`:

```toml
[project.scripts]
can-integration-sim = "can_integration.sim.cli:run"

[project.optional-dependencies]
sim = ["msgpack"]   # nur fuer das udp_multicast-Interface, siehe Phase 5
```

## 3. Phasen

### Phase 0 — Naht in der Bibliothek (klein, aber Voraussetzung)

`Message.encode` verweigert nicht-`writable` Nachrichten. Das ist fuer die
Host-Rolle richtig und fuer die Geraeterolle hinderlich: der Simulator *muss*
Statustelegramme senden, die zu Recht nicht `writable` sind.

- `Message.build_payload(values) -> bytes` aus dem Rumpf von `encode`
  herausziehen (Defaults, Unbekannt-Pruefung, Laenge — unveraendert).
- `encode()` = `ReadOnlyMessageError`-Wache + `build_payload()`.

Verhalten fuer alle bestehenden Aufrufer identisch; `test_encoding.py` bleibt
gruen. Der Simulator ruft `build_payload`, die Bibliothek weiter `encode`.

Ausserdem empfohlen: optionales Feld `cycle_time_ms: float | None` auf
`Message`. Die Zykluszeiten stehen heute nur als Prosa im `source`-Text; der
Simulator braucht sie strukturiert, und `max_age` liesse sich spaeter je
Nachricht daraus ableiten statt global mit 1,0 s.

### Phase 1 — Logdatei lesen (`sim/logfile.py`)

```python
@dataclass(frozen=True)
class LogFrame:
    t: float               # Sekunden seit Logbeginn
    arbitration_id: int
    extended: bool
    data: bytes

class Recording:
    frames: tuple[LogFrame, ...]
    header: Mapping[str, str]     # Bit-rate, Logger ID, Session No., ...
    def duration(self) -> float
    def ids(self) -> Mapping[int, int]                 # ID -> Anzahl
    def cycle_times(self) -> Mapping[int, float]       # ID -> Median-dt
    def coverage(self, catalog) -> tuple[known, unknown]
    def first_payloads(self) -> Mapping[int, bytes]
    def last_payloads(self) -> Mapping[int, bytes]
```

- Kopfzeilen auswerten statt Trennzeichen und Zeitformat hart zu kodieren
  (`Value separator`, `Time format`, `Bit-rate`) — die CL1000 kann anders
  konfiguriert sein.
- `Type` → `extended`; die Datei kennt hier nur `1`, das Feld trotzdem lesen.
- Tagesueberlauf (`23:59:59` → `00:00:00`) und Split-Dateien beruecksichtigen.
- Fehlerhafte Zeilen: Zeilennummer nennen, nicht stumm ueberspringen.

### Phase 2 — Replay (`sim/replay.py`) — der Beispielbetrieb

```python
class LogPlayer:
    def __init__(self, recording, bus, *, speed=1.0, loop=False, direction="device")
    def run(self, stop: threading.Event | None = None) -> None
```

- Zeitrichtig senden ueber eine monotone Sollzeit (`start + frame.t / speed`),
  nicht ueber aufsummierte `sleep`-Aufrufe — sonst driftet der Replay bei
  13.000 Frames sichtbar.
- `direction="device"` (Vorgabe) laesst `0x01000001` und `0x0A000000` weg: die
  hat im Log der Host gesendet: die Bibliothek soll ihre eigene Richtung nicht
  zurueckgespielt bekommen. `direction="all"` fuer Protokollanalysen.
- `speed=0` = so schnell wie moeglich, fuer deterministische Tests.
- Beim Start einen Deckungsbericht ausgeben: *n* Frames, *m* IDs, davon *k* dem
  Katalog unbekannt (mit Liste).

Ergebnis dieser Phase — der erste sichtbare Nutzen:

```bash
can-integration-sim replay CAN-Logs/0000309.TXT --interface virtual
can-integration --messages current_control_dq voltage_control_dq
```

### Phase 3 — Reagierendes Geraet (`sim/device.py`)

Der Replay ist eine Aufzeichnung; er reagiert nicht. Fuer `set_signal` braucht
es ein Modell:

```python
class SimulatedDevice:
    def __init__(self, schedule, *, bus, state=None, behaviour=None,
                 on_command=None, catalog=DEFAULT_CATALOG)
    def start(self) / stop(self)          # Thread, wie SignalMonitor
    def run(self)                          # blockierend
```

Schleife: faellige Telegramme senden (`build_payload`) → Verhalten einen
Zeitschritt weiterrechnen → RX leeren, `writable`-Nachrichten dekodieren, an
`on_command` geben.

**Der Anfangszustand kommt aus dem Log**, nicht aus erfundenen Zahlen:
`Recording.first_payloads()` liefert die Startnutzlast je ID,
`last_payloads()` das Ruhemuster nach dem Disarm. Der eingebaute
Standard-Kommandobehandler bildet das beobachtete Verhalten nach:
`command_id=0x0220, value=0` → `armed=False` → dynamische Signale fallen
innerhalb eines Zyklus auf das Ruhemuster.

### Phase 4 — Verhalten (`sim/behaviour.py`)

- `Constant(value)`
- `Ramp(target, rate)` — Sollwertfolge, sobald `rpm_target` geschrieben wird
- `FromRecording(recording, signal)` — realistischer Verlauf aus dem Log
- `Noise(sigma)` — als Dekorator ueber die anderen, damit Filter und
  `max_age`-Logik unter unruhigen Werten getestet werden

### Phase 5 — Transport und CLI (`sim/cli.py`)

| Zweck | Interface | Anmerkung |
|---|---|---|
| Tests, ein Prozess | `virtual` | eingebaut, prozesslokal |
| Sim in Terminal A, CLI in Terminal B | `udp_multicast` | auf macOS der einzige gangbare Weg (`vcan` gibt es nur unter Linux); braucht `msgpack` → Extra `[sim]` |
| spaeter gegen echte Hardware | `pcan` u. a. | `--interface/--channel` durchreichen |

```
can-integration-sim replay <LOG> [--speed 1.0] [--loop] [--direction device|all]
can-integration-sim device [--from-log <LOG>] [--behaviour ramp]
can-integration-sim inspect <LOG>        # Deckungsbericht, Zykluszeiten, IDs
```

`inspect` ist nebenbei das Werkzeug, mit dem die offenen Katalogeintraege aus
Abschnitt 1 gefuellt werden.

### Phase 6 — Tests

1. **Parser** gegen `tests/data/0000309_excerpt.TXT`: Framezahl, erster und
   letzter Zeitstempel, Zykluszeiten, Kopfzeilen.
2. **Roundtrip**: `SimulatedDevice` auf `virtual` + echtes `Device` →
   `get("iq_flt")` liefert den gesetzten Wert. Schliesst die Luecke, die
   `FakeBus` offen laesst: Bit-Layouts gegen ein reagierendes Gegenueber.
3. **Kommandopfad**: `set_signal("rpm_target", 1000)` → Sim empfaengt,
   Zustand aendert sich, `Device` sieht den neuen Status.
4. **Disarm aus dem Log**: das beobachtete Kommando senden → dynamische
   Signale gehen auf Ruhewerte.
5. **Veralterung**: Sim anhalten → `StaleSignalError` nach `max_age`.
6. **Replay deterministisch** bei `speed=0` gegen eine Sollwertliste.
7. **Deckungsbericht**: unbekannte IDs werden gemeldet, nicht verworfen.

## 4. Offene Entscheidungen

1. **`discover`-Heartbeat.** Laut Protokolldokument faellt der Inverter nach
   >2 s ohne `0x01000001` auf sein gespeichertes Protokoll zurueck. Der Host
   sendet ihn im Log alle 500 ms — `can_integration` sendet ihn heute nie. Das
   ist eine echte Luecke der Bibliothek. Vorschlag: die Simulation kann das
   Verhalten nachbilden, aber nur hinter `--strict-discovery`, standardmaessig
   aus. Sonst blockiert der erste Simulationslauf jede andere Arbeit.
2. **Katalogeintraege** fuer die Float-Telegramme `0x1A000010/11/12`. Sie
   erscheinen je einmal kurz nach Aufzeichnungsbeginn und tragen little-endian
   float32-Werte (`0x1A000010` z. B. 0,0601 und 0,4). `sim inspect` liefert die
   Datenbasis; die Benennung braucht das Herstellerdokument.
3. **Log im Repository.** 516 KB, derzeit ungetrackt. Vorschlag: `CAN-Logs/`
   committen (nuetzlich als Referenz), ins Wheel aber nur den Testausschnitt.
4. **`0x0A000000` command_id 0x0010 / 0x0220.** Der Katalog nennt bislang nur
   `0x0110` (RPM) und `0x0D13`. Die beiden beobachteten Werte gehoeren mit der
   Quellenangabe „aus Log 0000309, t=26,21 s" dokumentiert.


## 5. Stand

| Phase | Stand |
|---|---|
| 0 Naht in der Bibliothek | **fertig** -- `Message.build_payload`, `Message.cycle_time_ms` |
| 1 Logdatei lesen | **fertig** -- `sim/logfile.py`, `tests/test_sim_logfile.py` |
| 2 Replay | **fertig** -- `sim/replay.py`, `sim/cli.py`, `tests/test_sim_replay.py` |
| 3 Reagierendes Geraet | **fertig** -- `sim/device.py`, `tests/test_sim_device.py` |
| 4 Verhalten | **fertig** -- `sim/behaviour.py`, `tests/test_sim_behaviour.py` |
| 5 Transport und CLI | teilweise -- `inspect`, `replay` und `device` stehen; `udp_multicast` ungetestet, weil `msgpack` hier fehlt |
| 6 Tests | teilweise -- Parser, Replay, Kommandos, Verhalten, Rundlauf und Veralterung stehen |

Beide Richtungen laufen damit ohne Hardware durch die echte Bibliothek; siehe
den Abschnitt „Simulation ohne Pruefstand" in der README.

Abweichungen vom Plan, jeweils weil die Umsetzung es verlangt hat:

* **`Cycle.template`** kam dazu. Der Katalog beschreibt `motor_temperature`
  mit zwei Bytes, der Pruefstand sendet acht; ohne die aufgezeichnete Nutzlast
  als Vorlage haette das simulierte Geraet eine falsche DLC gesendet. Die
  Vorlage legt zugleich fest, was in den Bytes steht, die kein Signal abdeckt.
* **`running_moment`** statt „erste Nutzlast je Telegramm". Log 0000309
  beginnt vor dem Anlauf: die erste Nutzlast zeigt einen stehenden Antrieb.
  Der Anfangszustand ist deshalb der Augenblick unmittelbar vor dem
  aufgezeichneten Stopp-Kommando.
* **`sim/transport.py`** haelt den Busbesitz, den Replay und Geraet teilen,
  statt ihn ein drittes Mal neben `BusConnection` zu schreiben.
* **`Noise` ist kein Verhalten**, wie in Phase 4 vorgesehen, sondern wirkt beim
  Bauen der Nutzlast. Als Dekorator ueber einem Verhalten haette es in den
  Zustand geschrieben und sich von Schritt zu Schritt aufaddiert -- das Signal
  waere als Zufallsbewegung davongelaufen statt um seinen Wert zu streuen.
* **`SimulatedDevice.armed`** kam dazu. Ohne dieses Tor haette ein Verhalten
  den Ruhezustand sofort wieder ueberschrieben, den ein Disarm-Kommando gerade
  gesetzt hat.
* **`Follow` statt eines Drehmomentmodells.** Wie Drehzahl und Moment an
  diesem Pruefstand zusammenhaengen, gibt die Aufzeichnung nicht her; eine
  feste Kopplung mit Faktor und Versatz behauptet wenigstens nur das, was der
  Aufrufer selbst hineinschreibt.

## 6. Reihenfolge

Phase 0 → 1 → 2 liefert bereits einen nutzbaren Ersatzpruefstand fuer alles
Lesende (Reader, Monitor, Device, CLI). Phase 3 → 4 ergaenzt die Schreibrichtung.
Phase 5 → 6 macht daraus ein Werkzeug und einen Regressionsschutz.
