# CAN Integration

Schlankes Python-Package, das einer Messautomatisierung Messwerte vom CAN-Bus
liefert — Motortemperatur, Drehzahl, Drehmoment, Schub — damit die Messung bei
Überschreiten eines Grenzwerts abgebrochen werden kann. Die Bibliothek kapselt
nur Buszugriff und Dekodierung. Messablauf, Yokogawa-Steuerung, Aufzeichnung
und die Entscheidung, was beim Überschreiten passiert, bleiben Aufgabe der
aufrufenden Anwendung.

## Installation

Das Package benötigt Python 3.10 oder neuer, `python-can` und unter Windows den
Treiber des verwendeten CAN-Adapters.

```powershell
python -m pip install -e .
```

## Der Katalog: welche CAN-ID was bedeutet

Alles, was das Package über den Bus weiß, steht in einer einzigen Datei:
[src/can_integration/catalog.py](src/can_integration/catalog.py). Sie ist die
Nachschlagetabelle des Projekts — jede bekannte Arbitration-ID mit ihrer
Bedeutung, ihren Signalen und der Herkunft dieser Information.

```powershell
python examples/print_signals.py --list
```

| Name | CAN-ID | Signale | Herkunft |
|---|---|---|---|
| `inverter_status_1` | `0x1A000001` ext | `iph_rms`, `i_dc_flt`, `u_dc`, `temperature` | `Orientierung/temp_block.py` |
| `inverter_status_3` | `0x1A000003` ext | `iph_rms`, `i_dc_flt`, `u_dc`, `temperature` | `Orientierung/temp.py` |
| `motor_temperature` | `0x1A000013` ext | `temperature` | am Prüfstand gemessen |
| `inverter_speed` | `0x1A00000C` ext | `rpm_actual`, `rpm_target`, `rpm_max`, `torque_actual` | `Orientierung/rpm.py` |
| `thrust` | `0x003` **std** | `weight` | `Orientierung/Schub_CAN.py` |

Jeder Eintrag nennt in `source`, woher sein Layout stammt. Keines ist gegen eine
Herstellerdokumentation verifiziert; ein Eintrag ohne belastbare Herkunft darf
keine Sicherheitsentscheidung tragen.

## Eine neue CAN-Funktion hinzufügen

Ein neues Telegramm bedeutet **einen neuen Eintrag im Katalog** — sonst nichts.
Buszugriff, Hardwarefilter, Konfiguration, Überwachung und Fehlerbehandlung
ergeben sich daraus automatisch.

```python
from can_integration import Message, Signal

DRIVE_STATE = Message(
    name="drive_state",
    arbitration_id=0x1A000007,
    description="Zustand der Leistungsendstufe",
    source="Herstellerdokument XYZ, Rev. 3, Tabelle 12",
    signals=(
        Signal("torque", offset=0, format="<h", scale=0.1, unit="Nm"),
        Signal("housing_temperature", offset=2, format="<H", scale=0.1,
               bias=-40.0, unit="°C"),
    ),
)
```

In `catalog.py` wird der Eintrag zusätzlich in `BUILTIN_MESSAGES` aufgenommen;
danach kennt ihn jede Konfiguration unter seinem Namen.

Ein `Signal` beschreibt genau einen Wert:

| Feld | Bedeutung |
|---|---|
| `name` | Name, unter dem der Wert später gelesen wird |
| `offset` | Byte-Position im Payload |
| `format` | `struct`-Format **eines** Werts: `"<H"`, `"<h"`, `">i"`, `"<f"` … |
| `scale` | Faktor je Bit, Vorgabe `1.0` |
| `bias` | additiver Versatz, für Sensoren mit z. B. −40 °C Nullpunkt |
| `unit`, `description` | Dokumentation, erscheint in `--list` |

Byte-Reihenfolge und Vorzeichen stehen damit **je Signal** fest statt global.
Genau deshalb passen der Little-Endian-`uint16` des Inverters und der
Big-Endian-`int32` der Wägezelle in dieselbe Bibliothek.

### Definitionen nur für einen Prüfstand

Was nur an einem Aufbau gilt, gehört nicht ins Package, sondern in eine
JSON-Datei neben die Messkonfiguration — siehe
[catalog.example.json](catalog.example.json):

```json
{
  "messages": [
    {
      "name": "coolant",
      "arbitration_id": "0x1A000021",
      "source": "Prüfstand Halle 2, gegen Handmessgerät geprüft",
      "signals": [
        {"name": "coolant_temperature", "offset": 0, "format": "<h",
         "scale": 0.1, "unit": "°C"}
      ]
    }
  ]
}
```

Die Konfiguration verweist mit `"catalog"` darauf; der Pfad wird relativ zur
Konfigurationsdatei aufgelöst, das Messverzeichnis bleibt also umziehbar.
Namen und IDs, die der eingebaute Katalog schon belegt, werden **abgelehnt**
statt still überschrieben — eine geprüfte Definition darf nicht unbemerkt von
einer Datei verdeckt werden.

## Verwendung: Überwachung während einer Messung

`SignalMonitor` liest den Bus in einem Hintergrundthread und hält immer nur die
**neuesten** Telegramme bereit. Der Zugriff blockiert nicht und passt damit in
eine Messschleife, die vom Messgerät getaktet wird:

```python
from can_integration import Config, SignalMonitor

config = Config.from_json("config.json")

with SignalMonitor.from_config(config) as can_bus:
    wt3000.start()
    try:
        while not fertig:
            werte = wt3000.read()

            if can_bus.value("temperature") > config.limit("temperature"):
                break

            writer.writerow((*werte, *can_bus.values().values()))
    finally:
        wt3000.stop()
```

`values()` liefert alle überwachten Signale auf einmal und scheitert als
Ganzes, sobald **ein** Telegramm veraltet ist: eine halb frische Messzeile ist
schlechter als keine. `monitor.signal_names` ist die dazu passende, stabile
CSV-Kopfzeile.

### Warum ein Hintergrundthread

`bus.recv()` liefert die **älteste** gepufferte Nachricht. Wer den Bus in einer
langsamen Messschleife direkt abfragt, arbeitet deshalb mit einem wachsenden
Rückstand: Der gelesene Wert wird mit jedem Durchlauf älter, bis der
Empfangspuffer des Adapters überläuft. Für eine Abschaltung ist das der
gefährlichste Fehler. Der Monitor räumt den Bus mit voller Rate leer und
verwirft alles außer dem jüngsten Telegramm je ID.

### Verhalten im Fehlerfall

Der Monitor ist bewusst *fail-closed* — im Zweifel bricht die Messung ab:

| Situation | Verhalten |
|---|---|
| Ein Sensor sendet beim Start nicht | `start()` bzw. `with` wirft `SignalTimeoutError` und nennt die fehlende ID |
| Wert älter als `max_age` | `value()` und `values()` werfen `StaleSignalError` |
| Busfehler, Adapter abgezogen | `value()` wirft die `can.CanError` des Empfangsthreads |
| Telegramm zu kurz oder unlesbar | Wert wird nicht aktualisiert und altert aus; die Meldung nennt den Dekodierfehler |

Der Startzeitpunkt ist damit ein Selbsttest: Nach dem Betreten des
`with`-Blocks steht fest, dass **jede** konfigurierte ID sendet und dass
Bitrate und Verkabelung stimmen — noch bevor der Prüfstand läuft.

Wichtig: Wird die Exception nicht behandelt, endet das Messskript. Das ist
Absicht. Ein stiller „letzter bekannter Wert" wäre gefährlich, weil ein
ausgefallener Sensor sonst dauerhaft einen unkritischen Wert meldet, während
der Motor heiß wird.

### Signalnamen

Werte werden über ihren Signalnamen gelesen, nicht über IDs und Byte-Offsets:

```python
monitor.value("temperature")     # float, wirft bei veraltetem Wert oder Busfehler
monitor.values()                 # alle Signale auf einmal, dieselbe Prüfung
monitor.reading("temperature")   # Reading(message, values, timestamp, monotonic) oder None
monitor.age("temperature")       # Sekunden seit dem Telegramm, inf wenn keins kam
monitor.signal("temperature")    # die Signaldefinition: Einheit, Offset, Skalierung
```

Ein Name bleibt schlicht, solange ihn nur eine überwachte Nachricht anbietet.
Überwacht eine Messung zwei Telegramme mit einem `temperature`-Signal, werden
beide qualifiziert: `"inverter_status_3.temperature"`. Ein mehrdeutiger
Kurzname wirft `AmbiguousSignalError` und nennt die gültigen Alternativen, statt
sich stillschweigend für eines der beiden zu entscheiden.

`Reading.timestamp` stammt vom CAN-Backend und dient der Protokollierung. Das
Alter wird über `time.monotonic` bestimmt, damit weder eine Zeitumstellung noch
ein Backend ohne Zeitstempel es verfälschen kann.

## Konfiguration per JSON

```json
{
  "messages": ["motor_temperature", "inverter_speed"],
  "catalog": "catalog.example.json",
  "interface": "pcan",
  "channel": "PCAN_USBBUS1",
  "bitrate": 1000000,
  "max_age": 1.0,
  "startup_timeout": 5.0,
  "limits": {
    "temperature": 50.0
  }
}
```

`config.example.json` im Projektverzeichnis dient als Vorlage.

| Schlüssel | Pflicht | Bedeutung |
|---|---|---|
| `messages` | ja | Namen aus dem Katalog; ein einzelner Name darf ohne Liste stehen |
| `catalog` | nein | JSON-Datei mit zusätzlichen Definitionen, relativ zu dieser Datei |
| `interface` | nein | python-can-Backend, Vorgabe `pcan` |
| `channel` | nein | Vorgabe `PCAN_USBBUS1` |
| `bitrate` | nein | Vorgabe `1000000` |
| `max_age` | nein | Höchstalter eines Werts in Sekunden, Vorgabe `1.0` |
| `startup_timeout` | nein | Wartezeit auf das erste Telegramm jeder ID, Vorgabe `5.0` |
| `limits` | nein | Grenzwerte je Signalname für das Messskript |

Arbitration-IDs und Byte-Offsets stehen **nicht** in der Konfiguration. Sie
gehören in den Katalog, wo sie einmal deklariert und einmal geprüft werden.

`limits` wird von der Bibliothek **nicht** durchgesetzt — sie trägt die Werte
nur mit, damit Grenzwerte und CAN-Parameter in derselben Datei stehen. Die
Abbruchentscheidung trifft das Messskript. Ein Grenzwert für ein Signal, das
gar nicht überwacht wird, ist ein Fehler und kein stiller No-op.

Unbekannte Schlüssel führen ebenfalls zu einem Fehler. Ein Tippfehler wie
`maxage` würde sonst still auf die Vorgabe zurückfallen und das
Sicherheitsfenster verstellen.

Soll die Messkonfiguration in einer größeren Datei stehen, liest
`Config.from_dict` auch einen Abschnitt daraus:

```python
document = json.loads(Path("messung.json").read_text(encoding="utf-8"))
config = Config.from_dict(document["can"])
```

## Einzelabruf und Inbetriebnahme

Für Verkabelungstests und zum Ausmessen der Senderate gibt es den blockierenden
Einzelabruf. Für die laufende Messung ist er ungeeignet — siehe oben.

```python
from can_integration import SignalReader


with SignalReader("motor_temperature") as reader:
    print(f"{reader.read_signal('temperature'):.2f} °C")
```

`read()` liefert das nächste Telegramm irgendeiner konfigurierten Nachricht,
`read_signal(name)` wartet gezielt auf die Nachricht, die dieses Signal trägt.

Fertig lauffähig als Konsolenskript:
[examples/print_signals.py](examples/print_signals.py) liest eine
JSON-Konfiguration und gibt jedes empfangene Telegramm mit Uhrzeit, Signalnamen
und Einheiten aus, bis es mit Strg+C beendet wird.

```powershell
python examples/print_signals.py --config config.json
```

Ohne Angabe gelten `pcan`, `PCAN_USBBUS1` und `1_000_000 bit/s`.

## Vorhandenen CAN-Bus verwenden

Monitor und Reader können einen bereits geöffneten `python-can`-Bus übernehmen;
die Bibliothek schließt einen solchen Bus nicht selbst:

```python
import can

from can_integration import SignalMonitor


with can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=1_000_000) as bus:
    with SignalMonitor(["motor_temperature", "thrust"], bus=bus) as monitor:
        ...
```

Mehrere IDs brauchen dafür keinen gemeinsamen Bus mehr: Ein Monitor überwacht
beliebig viele Nachrichten über einen Kanal und setzt die Hardwarefilter für
alle. Ein fertig geöffneter Bus lässt sich nicht mehr umkonfigurieren; `bus`
und die Parameter `interface`, `channel` und `bitrate` schließen sich deshalb
gegenseitig aus.

## Decoder ohne Hardware verwenden

Die Dekodierung in [signals.py](src/can_integration/signals.py) kennt weder Bus
noch Adapter und lässt sich mit künstlichen Payloads prüfen:

```python
from can_integration import DEFAULT_CATALOG


payload = bytes.fromhex("64 00 C8 00 2C 01 88 13")
assert DEFAULT_CATALOG["inverter_status_3"].decode(payload)["temperature"] == 50.0
```

## Aktuelle Protokollannahmen

- Payload: mindestens so lang wie das letzte deklarierte Signal (`minimum_length`)
- Byte-Reihenfolge und Vorzeichen: je Signal über `format` festgelegt
- CAN-Bitrate der vorhandenen Skripte: `1_000_000 bit/s`

Nicht gegen eine Herstellerdokumentation verifiziert sind weiterhin: die
Vorzeichenbehaftung der Temperaturen, der Faktor `0.01 °C/Bit`, die
Skalierungen von `iph_rms`, `i_dc_flt`, `u_dc` und `torque_actual` sowie die
Frage, welche physische Temperatur `0x1A000001` und `0x1A000003` genau melden.
Diese Signale sind im Katalog deshalb als Rohwerte ohne Einheit deklariert.

`max_age` muss über der tatsächlichen Sendeperiode der langsamsten überwachten
Nachricht liegen. Für `0x1A000013` liegen am Prüfstand beobachtete ~100
Telegramme in 2,5 s vor, also grob 40 ms Periode; `max_age = 1.0` liegt
komfortabel darüber.

## Aufbau des Packages

| Modul | Rolle |
|---|---|
| [catalog.py](src/can_integration/catalog.py) | **Die Nachschlagetabelle**: bekannte IDs, Katalogverwaltung, JSON-Erweiterung |
| [signals.py](src/can_integration/signals.py) | `Signal`, `Message`, reine Dekodierung ohne Hardwarezugriff |
| [bus.py](src/can_integration/bus.py) | Öffnen, Filtern und Schließen des python-can-Busses, `Reading` |
| [monitor.py](src/can_integration/monitor.py) | `SignalMonitor` für die laufende Messung |
| [reader.py](src/can_integration/reader.py) | `SignalReader` für Inbetriebnahme und Diagnose |
| [config.py](src/can_integration/config.py) | `Config` aus JSON |

## Tests

Die Tests benötigen keine angeschlossene CAN-Hardware, aber ein installiertes
Package (siehe Installation):

```powershell
python -m unittest discover -s tests -v
```

Die technischen Erkenntnisse aus den Ursprungsskripten sind unter
`docs/CAN_Temperaturauswertung.md` zusammengefasst.
