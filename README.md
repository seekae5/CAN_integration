# CAN Integration

Schlankes Python-Package, damit ein Messskript CAN-Werte **lesen und senden**
kann, ohne sich mit Bus, Filtern und Byte-Offsets zu befassen — Motortemperatur,
Drehzahl, Drehmoment, Schub und Sollwerte. Die Bibliothek kapselt Buszugriff und
Kodierung. Messablauf, Yokogawa-Steuerung, Aufzeichnung und die Entscheidung,
was beim Überschreiten eines Grenzwerts passiert, bleiben Aufgabe der
aufrufenden Anwendung.

## Installation

Das Package benötigt Python 3.10 oder neuer, `python-can` und unter Windows den
Treiber des verwendeten CAN-Adapters.

```powershell
python -m pip install -e .
```

## Schnellstart

Werte heißen bei ihrem Namen, nicht bei ihrer CAN-ID:

```python
from can_integration import connect, get, set_signal, disconnect

connect(["motor_temperature", "inverter_speed"])

temperatur = get("temperature")     # aktuelle Motortemperatur in °C
drehzahl   = get("rpm_actual")      # aktuelle Drehzahl in min-1
set_signal("rpm_target", 1000)      # Sollwert senden

disconnect()
```

Für die Größen des Prüfstands gibt es benannte Kurzformen — eine Zeile über
`get()` bzw. `set_signal()`, reine Bequemlichkeit:

```python
from can_integration import get_temperature, get_rpm, get_thrust, set_rpm

get_temperature()    # °C
get_rpm()            # min-1
get_thrust()         # g
set_rpm(1000)
```

Wer mehrere Busse oder Geräte gleichzeitig braucht oder den Bus sauber
schließen will, nimmt dasselbe als Objekt:

```python
from can_integration import Device

with Device(["motor_temperature", "inverter_speed"]) as can_bus:
    print(can_bus.get("temperature"))
    can_bus.set("rpm_target", 1000)
```

`connect()`/`get()` sind nichts anderes als ein `Device`, das das Modul für ein
einzelnes Skript mitführt.

> **Wichtig:** Eine neue CAN-ID braucht für all das **keine Zeile Code**. Sobald
> sie im Katalog steht, ist sie über `get("<signalname>")` und
> `set_signal("<signalname>", wert)` erreichbar.

## Der Katalog: welche CAN-ID was bedeutet

Alles, was das Package über den Bus weiß, steht in einer einzigen Datei:
[src/can_integration/catalog.py](src/can_integration/catalog.py). Sie ist die
Nachschlagetabelle des Projekts — jede bekannte Arbitration-ID mit ihrer
Bedeutung, ihren Signalen und der Herkunft dieser Information.

```powershell
can-integration --list
```

| Name | CAN-ID | Richtung | Signale | Herkunft |
|---|---|---|---|---|
| `inverter_status_1` | `0x1A000001` ext | nur lesen | `iph_rms`, `i_dc_flt`, `u_dc`, `temperature` | `Orientierung/temp_block.py` |
| `inverter_status_3` | `0x1A000003` ext | nur lesen | `iph_rms`, `i_dc_flt`, `u_dc`, `temperature` | `Orientierung/temp.py` |
| `motor_temperature` | `0x1A000013` ext | nur lesen | `temperature` | am Prüfstand gemessen |
| `inverter_speed` | `0x1A00000C` ext | nur lesen | `rpm_actual`, `rpm_target`, `rpm_max`, `torque_actual` | `Orientierung/rpm.py` |
| `thrust` | `0x003` **std** | nur lesen | `weight` | `Orientierung/Schub_CAN.py` |

Alle eingebauten Einträge sind **nur lesend**. Es sind Statusmeldungen der
Geräte; keine davon ist ein Kommandotelegramm. Wie ein solches ergänzt wird,
steht unter [Sollwerte senden](#sollwerte-senden).

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
| `default` | nur beim **Senden**: Wert, wenn der Aufrufer das Signal nicht nennt |
| `unit`, `description` | Dokumentation, erscheint in `--list` |

Eine `Message` kennt zusätzlich `writable` (darf gesendet werden, Vorgabe
`False`) und `length` (zu sendende Payload-Länge, wenn das Gerät eine feste
DLC erwartet).

Byte-Reihenfolge und Vorzeichen stehen damit **je Signal** fest statt global.
Genau deshalb passen der Little-Endian-`uint16` des Inverters und der
Big-Endian-`int32` der Wägezelle in dieselbe Bibliothek.

### Definitionen nur für einen Prüfstand

Was nur an einem Aufbau gilt oder noch nicht am Prüfstand plausibilisiert ist,
gehört nicht ins Package, sondern in eine JSON-Datei neben die Messkonfiguration
— siehe [catalog.example.json](catalog.example.json). Sie hält eine
Nachschlagetabelle über die CAN-IDs des Persystems CAN Protocol
([docs/CAN_Protocol_Uebersicht.md](docs/CAN_Protocol_Uebersicht.md)): Broadcast-
und Discovery-Telegramme, den Schreib-Kanal an ein Gerät und die zyklische
Telemetrie. Die Layouts stammen aus der Herstellerdoku und sind **nicht** am
Prüfstand gegengemessen; jeder Eintrag nennt seine `source` mit Abschnitt.
Die IDs verwenden `n = A` als Beispiel-Geräte-Nibble (RX-Basis `0x0n000000`,
TX-Basis `0x1n......`, `n = A…F`). Die drei bereits am Prüfstand geklärten IDs
(`inverter_status_3` = `0x1A000003`, `inverter_speed` = `0x1A00000C`,
`motor_temperature` = `0x1A000013`) stehen stattdessen im eingebauten Katalog
und fehlen hier bewusst, damit die Datei neben dem Package ladbar bleibt.
Zusätzlich enthält sie `unknown_01100000` aus der CAN-Log-Auswertung
([docs/CAN_Log_to_be_completed.md](docs/CAN_Log_to_be_completed.md)), das in der
Protokoll-Übersicht nicht vorkommt.

```json
{
  "messages": [
    {
      "name": "current_control_dq",
      "arbitration_id": "0x1A000006",
      "description": "Feldorientierte Stroeme: Ist- und Zielwert fuer Id und Iq (TX-Basis 0x1n000006)",
      "source": "docs/CAN_Protocol_Uebersicht.md Abschnitt 6.4: 4 x 16-bit, Faktor 100, Zykluszeit 100 ms; Herstellerdoku, nicht am Pruefstand plausibilisiert",
      "signals": [
        {"name": "id_flt", "offset": 0, "format": "<h", "scale": 0.01},
        {"name": "iq_flt", "offset": 2, "format": "<h", "scale": 0.01},
        {"name": "id_trgt", "offset": 4, "format": "<h", "scale": 0.01},
        {"name": "iq_trgt", "offset": 6, "format": "<h", "scale": 0.01}
      ]
    }
  ]
}
```

Namen bleiben neutral (`unknown_<ID>`), solange auch die Herstellerdoku keine
Bedeutung nennt; sobald ein Layout am Prüfstand plausibilisiert ist, wandert der
Eintrag mit einem sprechenden Namen in `catalog.py`.

Die Konfiguration verweist mit `"catalog"` darauf; der Pfad wird relativ zur
Konfigurationsdatei aufgelöst, das Messverzeichnis bleibt also umziehbar.
Namen und IDs, die der eingebaute Katalog schon belegt, werden **abgelehnt**
statt still überschrieben — eine geprüfte Definition darf nicht unbemerkt von
einer Datei verdeckt werden.

## Sollwerte senden

Lesen und Senden benutzen denselben Katalogeintrag. `scale` und `bias` gelten
in beide Richtungen, ein gesendeter Sollwert meint also dasselbe wie der
zurückgelesene Istwert.

Gesendet werden darf nur, was der Katalog als `writable` deklariert:

```python
from can_integration import Message, Signal

MOTOR_COMMAND = Message(
    name="motor_command",
    arbitration_id=0x1A000020,      # aus der Herstellerdokumentation!
    writable=True,                  # ohne dies wird das Senden abgelehnt
    length=8,                       # feste DLC, die das Gerät erwartet
    description="Drehzahlvorgabe an den Inverter",
    source="Herstellerdokument XYZ, Rev. 3, Tabelle 7",
    signals=(
        Signal("rpm_target", offset=0, format="<H", unit="rpm"),
        Signal("enable", offset=2, format="<B", default=1),
    ),
)
```

Danach genügt:

```python
set_signal("rpm_target", 1000)          # ein Signal, Rest aus `default`
send("motor_command", rpm_target=1000, enable=1)   # das ganze Telegramm
```

### Warum `writable` nötig ist

Eine falsch gelesene Status-ID liefert eine falsche Zahl. Ein falsch
**geschriebenes** Telegramm steuert ein reales Gerät. Deshalb muss die Richtung
im Katalog dastehen, statt sich aus dem Aufruf zu ergeben — `send()` auf eine
Statusmeldung wirft `ReadOnlyMessageError`, statt etwas auf den Bus zu legen.

### Warum `default` nötig ist

Ein Kommandotelegramm trägt meist mehrere Felder, ein Aufruf wie
`set_signal("rpm_target", 1000)` nennt aber nur eins. Jedes übrige Signal muss
deshalb im Katalog sagen, was es enthalten soll. Ein Signal **ohne** `default`
muss explizit übergeben werden; sonst nennt `InvalidValueError` die fehlenden
Namen. So wird kein Feld eines Kommandos stillschweigend mit `0` gefüllt — ein
vergessenes `enable` wäre sonst ein Abschaltbefehl.

### Wertebereich

`set_signal` rechnet den physikalischen Wert über `scale` und `bias` in den
Rohwert zurück und rundet bei Ganzzahlformaten auf den nächsten Bit-Schritt.
Passt der Wert nicht in das Format, wird `InvalidValueError` geworfen und
**nichts** gesendet — `set_signal("rpm_target", 70000)` bei `<H` schlägt fehl,
statt auf 4464 überzulaufen.

### Noch offen: die Kommando-IDs dieses Prüfstands

> Das Package bringt **kein** schreibbares Telegramm mit, weil für diesen
> Aufbau bislang keine Kommando-ID dokumentiert ist. `inverter_speed`
> (`0x1A00000C`) enthält zwar ein Signal `rpm_target`, ist aber eine
> **Statusmeldung des Inverters** — dorthin zu senden setzt keine Drehzahl.
>
> `set_rpm()` funktioniert daher erst, wenn die echte Kommando-ID mit ihrem
> Layout aus der Herstellerdokumentation als `writable`-Eintrag ergänzt ist.
> Bis dahin meldet der Aufruf genau das, statt zu raten.

## Verwendung: Überwachung während einer Messung

`Device` liest den Bus in einem Hintergrundthread und hält immer nur die
**neuesten** Telegramme bereit. Der Zugriff blockiert nicht und passt damit in
eine Messschleife, die vom Messgerät getaktet wird:

```python
from can_integration import Config, Device

config = Config.from_json("config.json")

with Device.from_config(config) as can_bus:
    wt3000.start()
    try:
        while not fertig:
            werte = wt3000.read()

            if can_bus.get("temperature") > config.limit("temperature"):
                break

            writer.writerow((*werte, *can_bus.values().values()))
    finally:
        wt3000.stop()
```

`values()` liefert alle überwachten Signale auf einmal und scheitert als
Ganzes, sobald **ein** Telegramm veraltet ist: eine halb frische Messzeile ist
schlechter als keine. `can_bus.signal_names` ist die dazu passende, stabile
CSV-Kopfzeile.

Darunter arbeitet `SignalMonitor`, erreichbar über `device.monitor`. Es bietet
dasselbe mit mehr Details (`reading()`, `readings()`, `max_age`) und lässt sich
direkt benutzen, wenn `Device` zu knapp ist.

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
| Senden auf eine Statusmeldung | `send()`/`set()` wirft `ReadOnlyMessageError`, es geht nichts auf den Bus |
| Sollwert passt nicht ins Format | `InvalidValueError`, es geht nichts auf den Bus |

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
device.get("temperature")           # float, wirft bei veraltetem Wert oder Busfehler
device.values()                     # alle Signale auf einmal, dieselbe Prüfung
device.set("rpm_target", 1000)      # Sollwert senden
device.reading("temperature")       # Reading(message, values, timestamp, monotonic) oder None
device.age("temperature")           # Sekunden seit dem Telegramm, inf wenn keins kam
device.signal("temperature")        # die Signaldefinition: Einheit, Offset, Skalierung
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

### Konsolenwerkzeug

Mit der Installation kommt der Befehl `can-integration`
([cli.py](src/can_integration/cli.py)). Er gibt jedes empfangene Telegramm mit
Uhrzeit, Signalnamen und Einheiten aus, bis er mit Strg+C beendet wird:

```powershell
can-integration --messages motor_temperature inverter_speed
```

```powershell
can-integration --config config.json
```

`--list` zeigt den Katalog, `--set` sendet einen Sollwert und beendet sich —
der schnellste Weg, einen neuen `writable`-Eintrag gegen die Hardware zu
prüfen:

```powershell
can-integration --config config.json --set rpm_target=1000
```

Ohne Angabe gelten `pcan`, `PCAN_USBBUS1` und `1_000_000 bit/s`.

## Vorhandenen CAN-Bus verwenden

`Device`, Monitor und Reader können einen bereits geöffneten
`python-can`-Bus übernehmen; die Bibliothek schließt einen solchen Bus nicht
selbst:

```python
import can

from can_integration import Device


with can.Bus(interface="pcan", channel="PCAN_USBBUS1", bitrate=1_000_000) as bus:
    with Device(["motor_temperature", "thrust"], bus=bus) as can_bus:
        ...
```

Mehrere IDs brauchen dafür keinen gemeinsamen Bus mehr: Ein Gerät überwacht
beliebig viele Nachrichten über einen Kanal und setzt die Hardwarefilter für
alle. Ein fertig geöffneter Bus lässt sich nicht mehr umkonfigurieren; `bus`
und die Parameter `interface`, `channel` und `bitrate` schließen sich deshalb
gegenseitig aus.

## Kodierung ohne Hardware verwenden

Die Ko­dierung in [signals.py](src/can_integration/signals.py) kennt weder Bus
noch Adapter und lässt sich in beide Richtungen mit künstlichen Payloads
prüfen:

```python
from can_integration import DEFAULT_CATALOG


payload = bytes.fromhex("64 00 C8 00 2C 01 88 13")
assert DEFAULT_CATALOG["inverter_status_3"].decode(payload)["temperature"] == 50.0
```

## Simulation ohne Prüfstand

Solange der reale Aufbau nicht zur Verfügung steht, spielt
`can-integration-sim` eine echte Aufzeichnung auf einen Bus, den die Bibliothek
liest, als käme sie vom Gerät. Die Werte stammen aus einer Messung, nicht aus
einem Modell.

```powershell
python -m can_integration.sim.cli inspect CAN-Logs/0000309.TXT --catalog catalog.example.json
```

`inspect` sendet nichts, sondern sagt, was in der Aufzeichnung steckt: Frames,
Zykluszeiten je CAN-ID und welche davon der Katalog beschreibt. Telegramme ohne
Katalogeintrag werden **gemeldet, nicht verworfen** -- die Liste ist genau die
noch offene Arbeit am Katalog.

```powershell
python -m can_integration.sim.cli replay CAN-Logs/0000309.TXT --loop
```

`replay` spielt zeitrichtig ab. Im selben Prozess genügt das eingebaute
Interface `virtual`:

```python
from can_integration import Device, load_json
from can_integration.sim import LogPlayer, Recording

catalog = load_json("catalog.example.json")
player = LogPlayer(
    Recording.from_file("CAN-Logs/0000309.TXT"),
    interface="virtual", channel="sim", loop=True, catalog=catalog,
)
player.start()
with Device(["inverter_speed"], interface="virtual", channel="sim",
            catalog=catalog) as device:
    print(device.get("rpm_actual"))
player.stop()
```

Für zwei Terminals reicht `virtual` nicht -- es verbindet nur Busse innerhalb
eines Prozesses. Unter macOS und Windows ist `udp_multicast` der Weg
(`pip install "can-integration[sim]"`), unter Linux alternativ `vcan`. Das
Messwerkzeug wählt Interface und Kanal über eine Konfigurationsdatei; dafür
liegt [config.sim.example.json](config.sim.example.json) bereit:

```powershell
# Terminal 1
python -m can_integration.sim.cli replay CAN-Logs/0000309.TXT --interface udp_multicast --channel 239.74.163.2 --loop

# Terminal 2
can-integration --config config.sim.example.json
```

### Richtung: wer sendet was

Eine Aufzeichnung enthält beide Seiten des Busses. Telegramme, die im Log der
Host gesendet hat, spielt `replay` deshalb **nicht** zurück -- sonst hörte die
Bibliothek ihre eigene Rolle. Als Host-Telegramm gilt, was der Katalog
`writable` nennt, plus `discovery_request`: dessen Nutzlast ist eine Konstante
und kein Sollwert, gesendet wird es trotzdem von der GUI. `--direction all`
schaltet den Filter ab, wenn es um die Protokollanalyse und nicht um eine
Messung geht.

### Was der Replay nicht kann

Eine Aufzeichnung antwortet nicht. Ein geschriebener Sollwert ändert am
abgespielten Verkehr nichts. Ein Zustandsmodell, das auf Kommandos reagiert,
ist der nächste Schritt -- der Plan dazu steht in
[docs/CAN_Simulation_Plan.md](docs/CAN_Simulation_Plan.md).

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
| [device.py](src/can_integration/device.py) | **Die einfache Schnittstelle**: `Device`, `connect`, `get`, `set_signal` |
| [signals.py](src/can_integration/signals.py) | `Signal`, `Message`, Ko­dierung und Dekodierung ohne Hardwarezugriff |
| [bus.py](src/can_integration/bus.py) | Öffnen, Filtern, Senden und Schließen des python-can-Busses, `Reading` |
| [monitor.py](src/can_integration/monitor.py) | `SignalMonitor` für die laufende Messung |
| [reader.py](src/can_integration/reader.py) | `SignalReader` für Inbetriebnahme und Diagnose |
| [config.py](src/can_integration/config.py) | `Config` aus JSON |
| [cli.py](src/can_integration/cli.py) | der Konsolenbefehl `can-integration` |
| [sim/logfile.py](src/can_integration/sim/logfile.py) | CL1000-Aufzeichnung lesen: `Recording`, Zykluszeiten, Katalogabdeckung |
| [sim/replay.py](src/can_integration/sim/replay.py) | `LogPlayer`: eine Aufzeichnung zeitrichtig auf einen Bus spielen |
| [sim/cli.py](src/can_integration/sim/cli.py) | der Konsolenbefehl `can-integration-sim` |

Ein Messskript braucht davon in der Regel nur `device.py` und den Katalog.

## Tests

Die Tests benötigen keine angeschlossene CAN-Hardware, aber ein installiertes
Package (siehe Installation):

```powershell
python -m unittest discover -s tests -v
```

| Datei | Prüft |
|---|---|
| `test_signals.py` | Dekodierung, Signal- und Nachrichtendefinitionen |
| `test_encoding.py` | Kodierung: Rundung, Wertebereich, `default`, `writable` |
| `test_catalog.py` | Katalogverwaltung und JSON-Erweiterung |
| `test_config.py` | JSON-Konfiguration |
| `test_reader.py` | blockierender Einzelabruf |
| `test_monitor.py` | Hintergrundüberwachung und Fehlerverhalten |
| `test_device.py` | die einfache Schnittstelle: `Device` und Modulfunktionen |
| `test_sim_logfile.py` | Aufzeichnung lesen: Zeitstempel, Trennzeichen, Katalogabdeckung |
| `test_sim_replay.py` | Replay auf einem `virtual`-Bus, gelesen von einem echten `Device` |

Die technischen Erkenntnisse aus den Ursprungsskripten sind unter
[docs/CAN_Temperaturauswertung.md](docs/CAN_Temperaturauswertung.md)
zusammengefasst; die noch offenen IDs unter
[docs/CAN_Log_to_be_completed.md](docs/CAN_Log_to_be_completed.md).
