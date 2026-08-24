# CAN Temperature

Schlankes Python-Package, das einer Messautomatisierung die aktuelle
Motortemperatur von einem CAN-Sensor liefert — damit die Messung bei
Überhitzung abgebrochen werden kann. Die Bibliothek kapselt nur Buszugriff und
Dekodierung. Messablauf, Yokogawa-Steuerung, Aufzeichnung und die Entscheidung,
was beim Überschreiten des Grenzwerts passiert, bleiben Aufgabe der aufrufenden
Anwendung.

## Installation

Das Package benötigt Python 3.10 oder neuer, `python-can` und unter Windows den
Treiber des verwendeten CAN-Adapters.

```powershell
python -m pip install -e .
```

## Verwendung: Temperaturüberwachung während einer Messung

`TemperatureMonitor` liest den Bus in einem Hintergrundthread und hält immer nur
den **neuesten** Wert bereit. Der Zugriff blockiert nicht und passt damit in
eine Messschleife, die vom Messgerät getaktet wird:

```python
from can_temperature import Config, TemperatureMonitor

config = Config.from_json("config.json")

with TemperatureMonitor.from_config(config) as motor:
    wt3000.start()
    try:
        while not fertig:
            werte = wt3000.read()

            if motor.celsius > config.limit_celsius:
                break

            writer.writerow((*werte, motor.celsius))
    finally:
        wt3000.stop()
```

### Warum ein Hintergrundthread

`bus.recv()` liefert die **älteste** gepufferte Nachricht. Wer den Bus in einer
langsamen Messschleife direkt abfragt, arbeitet deshalb mit einem wachsenden
Rückstand: Der gelesene Wert wird mit jedem Durchlauf älter, bis der
Empfangspuffer des Adapters überläuft. Für eine Temperaturabschaltung ist das
der gefährlichste Fehler. Der Monitor räumt den Bus mit voller Rate leer und
verwirft alles außer dem jüngsten Frame.

### Verhalten im Fehlerfall

Der Monitor ist bewusst *fail-closed* — im Zweifel bricht die Messung ab:

| Situation | Verhalten |
|---|---|
| Sensor sendet beim Start nicht | `start()` bzw. `with` wirft `TemperatureTimeoutError` |
| Wert älter als `max_age` | `celsius` wirft `TemperatureStaleError` |
| Busfehler, Adapter abgezogen | `celsius` wirft die `can.CanError` des Empfangsthreads |
| Frame zu kurz oder unlesbar | Wert wird nicht aktualisiert und altert aus; die Meldung nennt den Dekodierfehler |

Der Startzeitpunkt ist damit ein Selbsttest: Nach dem Betreten des
`with`-Blocks steht fest, dass ID, Bitrate und Verkabelung stimmen — noch bevor
der Prüfstand läuft.

Wichtig: Wird die Exception nicht behandelt, endet das Messskript. Das ist
Absicht. Ein stiller „letzter bekannter Wert" wäre gefährlich, weil ein
ausgefallener Sensor sonst dauerhaft eine unkritische Temperatur meldet,
während der Motor heiß wird.

### Weitere Zugriffe

```python
motor.celsius   # float, wirft bei veraltetem Wert oder Busfehler
motor.latest    # Reading(timestamp, celsius, monotonic) oder None, wirft nie
motor.age       # Sekunden seit dem letzten Wert, inf wenn noch keiner kam
```

`Reading.timestamp` stammt vom CAN-Backend und dient der Protokollierung. Das
Alter wird über `time.monotonic` bestimmt, damit weder eine Zeitumstellung noch
ein Backend ohne Zeitstempel es verfälschen kann.

## Konfiguration per JSON

```json
{
  "arbitration_id": "0x1A000003",
  "interface": "pcan",
  "channel": "PCAN_USBBUS1",
  "bitrate": 1000000,
  "max_age": 1.0,
  "startup_timeout": 5.0,
  "limit_celsius": 120.0
}
```

`config.example.json` im Projektverzeichnis dient als Vorlage.

| Schlüssel | Pflicht | Bedeutung |
|---|---|---|
| `arbitration_id` | ja | Extended-CAN-ID; als Zahl oder Hex-String `"0x…"` |
| `interface` | nein | python-can-Backend, Vorgabe `pcan` |
| `channel` | nein | Vorgabe `PCAN_USBBUS1` |
| `bitrate` | nein | Vorgabe `1000000` |
| `temperature_offset` | nein | Byte-Offset der Temperatur, Vorgabe `6` (siehe Protokollannahmen unten) |
| `max_age` | nein | Höchstalter eines Werts in Sekunden, Vorgabe `1.0` |
| `startup_timeout` | nein | Wartezeit auf den ersten Wert, Vorgabe `5.0` |
| `limit_celsius` | nein | Temperaturgrenze für das Messskript |

`limit_celsius` wird von der Bibliothek **nicht** durchgesetzt — sie trägt den
Wert nur mit, damit Grenzwert und CAN-Parameter in derselben Datei stehen. Die
Abbruchentscheidung trifft das Messskript.

Unbekannte Schlüssel führen zu einem Fehler. Ein Tippfehler wie `maxage` würde
sonst still auf die Vorgabe zurückfallen und das Sicherheitsfenster verstellen.

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
from can_temperature import TemperatureSensor


with TemperatureSensor(arbitration_id=0x1A000003) as sensor:
    print(f"{sensor.read_temperature(timeout=1.0):.2f} °C")
```

Fertig lauffähig als Konsolenskript: [examples/print_temperature.py](examples/print_temperature.py)
liest eine JSON-Konfiguration und gibt jede empfangene Temperatur mit
Uhrzeit aus, bis es mit Strg+C beendet wird.

```powershell
python examples/print_temperature.py --config config.json
```

Ohne Angabe gelten `pcan`, `PCAN_USBBUS1` und `1_000_000 bit/s`.

## Vorhandenen CAN-Bus verwenden

Monitor und Sensor können einen bereits geöffneten `python-can`-Bus übernehmen;
die Bibliothek schließt einen solchen Bus nicht selbst. Das ist auch der Weg,
mehrere IDs über denselben PCAN-Kanal zu überwachen:

```python
import can

from can_temperature import TemperatureMonitor


with can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=1_000_000) as bus:
    with TemperatureMonitor(0x1A000003, bus=bus) as motor:
        ...
```

Ein fertig geöffneter Bus lässt sich nicht mehr umkonfigurieren. `bus` und die
Parameter `interface`, `channel` und `bitrate` schließen sich deshalb gegenseitig
aus; werden sie kombiniert, meldet der Konstruktor einen `TypeError`.

## Decoder ohne Hardware verwenden

```python
from can_temperature import decode_temperature


payload = bytes.fromhex("64 00 C8 00 2C 01 88 13")
assert decode_temperature(payload) == 50.0
```

## Aktuelle Protokollannahmen

- Payload: mindestens `offset + 2` Byte
- Temperatur: ein Little-Endian `uint16` an einem konfigurierbaren Byte-Offset
- Skalierung: `0.01 °C/Bit`
- CAN-Bitrate der vorhandenen Skripte: `1_000_000 bit/s`

Der Offset unterscheidet sich je nach ID und muss pro ID verifiziert werden —
`decode_temperature` und `TemperatureSensor`/`TemperatureMonitor` nehmen ihn
deshalb als Parameter `offset` bzw. `temperature_offset` (Vorgabe `6`, siehe
`DEFAULT_TEMPERATURE_OFFSET`) statt ihn fest zu verdrahten:

| Extended-ID | Offset | Herkunft |
|---|---:|---|
| `0x1A000001`, `0x1A000003` | `6` | dokumentierte Inverter-Skripte (`Iph_Rms, I_DC_flt, U_DC, Temp_raw` als `<4H`) |
| `0x1A000013` | `0` | am Prüfstand gemessen, gegen die reale Motortemperatur plausibilisiert |

`KNOWN_TEMPERATURE_IDS` nennt alle bisher beobachteten IDs rein informativ —
ohne Offset-Information, da dieselbe ID theoretisch unterschiedliche Layouts
haben könnte. Vorzeichenbehaftung und Skalierungsfaktor sind weiterhin nicht
gegen eine Herstellerdokumentation verifiziert, nur gegen Plausibilität am
Prüfstand. Deshalb bleiben Arbitration-ID und Offset beim Erzeugen des Sensors
immer explizit anzugeben.

`max_age` muss über der tatsächlichen Sendeperiode des Sensors liegen. Für
`0x1A000013` liegen am Prüfstand beobachtete ~100 Frames in 2,5 s vor, also
grob 40 ms Periode; `max_age = 1.0` liegt komfortabel darüber.

## Tests

Die Tests benötigen keine angeschlossene CAN-Hardware, aber ein installiertes
Package (siehe Installation):

```powershell
python -m unittest discover -s tests -v
```

Die technischen Erkenntnisse aus den Ursprungsskripten sind unter
`docs/CAN_Temperaturauswertung.md` zusammengefasst.
