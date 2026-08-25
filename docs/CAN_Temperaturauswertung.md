# CAN-Temperaturauswertung

> Interne technische Referenz für die spätere Weiterentwicklung der CAN-Integration.  
> Stand: 24.08.2026
>
> **Hinweis:** Dieses Dokument hält den Stand der Ursprungsskripte fest und ist
> als Herleitung weiterhin gültig. Verbindlich für die Implementierung ist
> inzwischen der Katalog in `src/can_integration/catalog.py`; Abschnitt 13
> beschreibt die dort umgesetzte Struktur.

## 1. Zweck und Kurzfassung

Die vorhandenen Skripte `temp.py` und `temp_block.py` lesen jeweils ein CAN-Telegramm eines Inverters über einen PEAK-PCAN-USB-Adapter ein. Aus den acht Nutzdatenbytes wird der vierte 16-Bit-Wert als Temperatur-Rohwert interpretiert und durch `100.0` geteilt. Das Ergebnis wird als MOSFET-Temperatur in Grad Celsius live geplottet.

Der wesentliche Unterschied zwischen den beiden Skripten ist die CAN-ID:

| Quelldatei | CAN-ID | ID-Typ | Ausgewerteter Wert |
|---|---:|---|---|
| `temp.py` | `0x1A000003` | Extended, 29 Bit | `Temp_raw / 100.0` |
| `temp_block.py` | `0x1A000001` | Extended, 29 Bit | `Temp_raw / 100.0` |

Beide Dateien liegen derzeit außerhalb dieses Repositorys unter:

```text
C:\Users\Persystems\PycharmProjects\sonstiges\PythonWorkspace\temp.py
C:\Users\Persystems\PycharmProjects\sonstiges\PythonWorkspace\temp_block.py
```

## 2. Status der Informationen

### Direkt aus dem vorhandenen Code bestätigt

- CAN-Backend: `pcan`
- PCAN-Kanal: `PCAN_USBBUS1`
- Bitrate: `1_000_000 bit/s`
- Telegrammlänge, die der Decoder erwartet: mindestens 8 Byte
- Byte-Reihenfolge: Little-Endian
- Datenformat: vier vorzeichenlose 16-Bit-Werte
- Temperaturposition: Bytes 6 und 7
- Temperaturskalierung: Rohwert geteilt durch 100
- Anzeigeeinheit: Grad Celsius
- Empfang erfolgt in einem Hintergrundthread
- Zwischen Empfang und Plot liegt eine thread-sichere Queue
- Der Plot zeigt maximal 500 Samples

### Noch extern zu verifizieren

- Ob `0x1A000003` und `0x1A000001` tatsächlich dieselbe Payload-Struktur besitzen.
- Welche physische Temperatur jede ID genau repräsentiert, beispielsweise MOSFET, Leistungsblock, Motor oder Kühlkörper.
- Ob der Temperaturwert wirklich unsigned ist. Negative Temperaturen können mit dem aktuellen Format nicht dargestellt werden.
- Ob der Faktor `0.01 °C/Bit` laut Herstellerprotokoll korrekt ist.
- Skalierungen und Einheiten der drei zusätzlich übertragenen Werte `Iph_Rms`, `I_DC_flt` und `U_DC`.
- Erwartete Sendefrequenz der Telegramme.

Diese Punkte dürfen bei einer späteren Implementierung nicht stillschweigend als gesichert behandelt werden.

## 3. Benötigte Hardware und Software

### Hardware

- PEAK-PCAN-USB-Adapter beziehungsweise ein kompatibler PCAN-Kanal
- korrekt terminierter CAN-Bus, normalerweise mit insgesamt zwei 120-Ohm-Abschlusswiderständen
- Inverter oder Steuergerät, das die erwarteten Telegramme sendet
- identische Bitrate bei allen CAN-Teilnehmern

### Python-Abhängigkeiten

```text
python-can
matplotlib
```

Typische Installation:

```powershell
python -m pip install python-can matplotlib
```

Zusätzlich muss unter Windows der passende PEAK-PCAN-Treiber installiert sein. `python-can` ersetzt den Hardwaretreiber nicht.

## 4. Imports und ihre Aufgaben

| Import | Aufgabe im vorhandenen Code |
|---|---|
| `import can` | Zugriff auf den CAN-Bus über `python-can` und das PCAN-Backend |
| `import struct` | Umwandlung der acht Rohbytes in vier 16-Bit-Zahlen |
| `import matplotlib.pyplot as plt` | Erzeugung und Steuerung des Diagrammfensters |
| `from matplotlib.animation import FuncAnimation` | Periodisches Aktualisieren des Diagramms |
| `from collections import deque` | Rollender Puffer mit fester Länge von 500 Samples |
| `import threading` | CAN-Empfang in einem Hintergrundthread |
| `import queue` | Thread-sichere Übergabe der Temperaturwerte an die GUI |
| `import sys` | Beenden des Programms nach dem Schließen des Fensters |

## 5. CAN-Konfiguration

Die vorhandenen Skripte öffnen den Bus unmittelbar beim Programmstart:

```python
bus = can.interface.Bus(
    channel="PCAN_USBBUS1",
    interface="pcan",
    bitrate=1_000_000,
)
```

### Parameter

| Parameter | Aktueller Wert | Bedeutung |
|---|---|---|
| `channel` | `PCAN_USBBUS1` | Erster PCAN-USB-Kanal; bei einem zweiten Adapter/Kanal eventuell `PCAN_USBBUS2` |
| `interface` | `pcan` | Auswahl des PEAK-PCAN-Backends von `python-can` |
| `bitrate` | `1_000_000` | Nominale CAN-Bitrate von 1 Mbit/s |

Die Bitrate muss exakt mit dem Sender übereinstimmen. Bei abweichender Bitrate werden keine gültigen Frames empfangen und der Adapter kann CAN-Fehler melden.

Die verwendeten IDs sind größer als `0x7FF` und damit Extended-CAN-IDs. Bei einer robusten Auswertung sollte deshalb zusätzlich zur ID geprüft werden:

```python
msg.is_extended_id is True
```

## 6. Telegrammformat

Der vorhandene Decoder lautet:

```python
Iph_Rms, I_DC_flt, U_DC, Temp_raw = struct.unpack("<4H", msg.data[:8])
temp_c = Temp_raw / 100.0
```

### Bedeutung von `<4H`

- `<` bedeutet Little-Endian: Das niederwertige Byte eines 16-Bit-Werts kommt zuerst.
- `4` bedeutet vier aufeinanderfolgende Werte.
- `H` bedeutet unsigned short, also eine vorzeichenlose 16-Bit-Zahl.
- Vier Werte zu je zwei Byte ergeben genau acht Byte.

### Bytebelegung

| Bytes | Dekodierte Variable | Aktuelle Verwendung |
|---:|---|---|
| 0–1 | `Iph_Rms` | Wird dekodiert, aber nicht weiterverarbeitet |
| 2–3 | `I_DC_flt` | Wird dekodiert, aber nicht weiterverarbeitet |
| 4–5 | `U_DC` | Wird dekodiert, aber nicht weiterverarbeitet |
| 6–7 | `Temp_raw` | Wird durch 100 geteilt und als °C ausgegeben |

### Temperaturumrechnung

```text
Temperatur [°C] = Temp_raw / 100
```

Beispiele:

| Bytes 6–7 | Little-Endian-Rohwert | Temperatur |
|---|---:|---:|
| `00 00` | 0 | 0,00 °C |
| `D0 07` | 2000 | 20,00 °C |
| `88 13` | 5000 | 50,00 °C |
| `66 20` | 8294 | 82,94 °C |

Mit einem unsigned 16-Bit-Wert beträgt der theoretisch darstellbare Bereich bei dieser Skalierung `0,00 bis 655,35 °C`. Das ist nur der numerische Bereich des Decoders, nicht zwingend der gültige Sensorbereich.

### Kleines Decoder-Beispiel

```python
import struct

payload = bytes.fromhex("64 00 C8 00 2C 01 88 13")
iph_rms, i_dc_flt, u_dc, temp_raw = struct.unpack("<4H", payload)
temp_c = temp_raw / 100.0

assert (iph_rms, i_dc_flt, u_dc, temp_raw) == (100, 200, 300, 5000)
assert temp_c == 50.0
```

## 7. Funktionen und Datenfluss

### `can_reader(bus, inv_id, out_q)`

Diese Funktion läuft in einem Daemon-Thread und ist für den kontinuierlichen Empfang zuständig.

Ablauf:

1. `bus.recv(0.1)` wartet bis zu 100 ms auf eine CAN-Nachricht.
2. Bei einem Timeout liefert `recv()` den Wert `None`; die Schleife läuft weiter.
3. Die Arbitration-ID wird mit der konfigurierten Inverter-ID verglichen.
4. Bei passender ID werden die ersten acht Bytes dekodiert.
5. `Temp_raw` wird durch 100 geteilt.
6. Die berechnete Temperatur wird mit `out_q.put(temp)` in die Queue geschrieben.
7. Bei einer Ausnahme wird eine Fehlermeldung ausgegeben.
8. Beim Ende des Threads wird `None` in die Queue gelegt. Dieses Sentinel signalisiert der Oberfläche, dass der Empfänger beendet wurde.

### `update(_)`

Diese Funktion wird von `FuncAnimation` ungefähr alle 100 ms aufgerufen.

- Sie holt pro Aufruf maximal 100 Werte ohne Blockierung aus der Queue.
- Jeder Wert bekommt eine fortlaufende Sample-Nummer.
- Temperatur und Sample-Nummer werden in `deque`-Puffern gespeichert.
- Jeder Puffer behält nur die letzten 500 Einträge.
- Die X-Achse wird auf das aktuelle rollende Fenster gesetzt.
- Die Y-Achse wird anhand der sichtbaren Temperaturwerte automatisch skaliert.
- Ein `None`-Sentinel schließt das Plotfenster.

Wichtig: Die X-Achse ist eine Sample-Achse, keine Zeitachse. Für zeitbezogene Messungen müssen Empfangszeitstempel gespeichert werden.

### `on_close(_event)`

Diese Callback-Funktion wird beim Schließen des Matplotlib-Fensters aufgerufen:

```python
bus.shutdown()
sys.exit(0)
```

Damit wird der PCAN-Bus freigegeben und der Prozess beendet. Der Empfangsthread hat allerdings kein eigenes Stop-Ereignis; `bus.shutdown()` kann deshalb im Thread eine Ausnahme auslösen, bevor der Prozess endet.

## 8. Plot-Konfiguration

| Einstellung | Wert |
|---|---|
| Sichtbare Historie | 500 Samples |
| Aktualisierungsintervall | 100 ms |
| Maximal abgeholte Queue-Werte je Update | 100 |
| Linienfarbe | Rot |
| Diagrammtitel | `Inverter MOSFET Temperatur` |
| Y-Achse | `Temperatur [°C]` |
| Skalierung | Automatisch anhand sichtbarer Werte |

Die `deque(maxlen=500)` begrenzt nur die dargestellte Historie. Die vorgeschaltete `queue.Queue()` ist dagegen unbegrenzt. Falls CAN-Daten dauerhaft schneller eintreffen, als die GUI sie verarbeitet, kann die Queue anwachsen.

## 9. Start und Bedienung

Beispiel für die bisherige Einzeldatei:

```powershell
python "C:\Users\Persystems\PycharmProjects\sonstiges\PythonWorkspace\temp.py"
```

Vor dem Start prüfen:

1. PCAN-USB ist verbunden und der Treiber erkennt den Adapter.
2. `PCAN_USBBUS1` bezeichnet den richtigen Kanal.
3. Der CAN-Bus ist korrekt terminiert.
4. Sender und Empfänger verwenden 1 Mbit/s.
5. Der Inverter sendet die erwartete Extended-ID.
6. Kein inkompatibles Programm blockiert den PCAN-Kanal.

Das Plotfenster zeigt die Werte live. Durch Schließen des Fensters wird das Programm beendet.

## 10. Robuste Referenzauswertung ohne Plot

Die zentrale Dekodierung sollte später in eine eigene, testbare Funktion ausgelagert werden. Ein geeigneter Ausgangspunkt ist:

```python
import struct


def decode_temperature_message(msg, expected_id: int) -> float | None:
    """Gibt die Temperatur in °C zurück oder None für ein fremdes Frame."""
    if msg.arbitration_id != expected_id:
        return None
    if not msg.is_extended_id:
        return None
    if len(msg.data) < 8:
        raise ValueError(f"CAN-Payload zu kurz: {len(msg.data)} statt 8 Byte")

    _iph_rms, _i_dc_flt, _u_dc, temp_raw = struct.unpack("<4H", msg.data[:8])
    return temp_raw / 100.0
```

Diese Variante verhindert, dass ein gleich nummeriertes Frame mit falschem ID-Typ oder ein zu kurzes Telegramm unbemerkt falsch verarbeitet wird.

## 11. Bekannte Schwachstellen des vorhandenen Codes

- Die Datenlänge wird vor `struct.unpack()` nicht geprüft. Ein passendes Frame mit weniger als acht Byte beendet den Empfangsthread durch eine Ausnahme.
- `is_extended_id` wird nicht geprüft.
- Alle Ausnahmen werden gemeinsam behandelt; ein einzelnes fehlerhaftes Telegramm beendet somit die Messung.
- Die genaue Sensorzuordnung der IDs ist nicht im Code hinterlegt.
- Kanal, Bitrate, ID und Skalierung sind fest im Quellcode eingetragen.
- Die Busverbindung und GUI werden bereits beim Import der Datei gestartet; ein `main()`-Block fehlt.
- Es gibt kein Stop-Event für den Empfangsthread.
- Die Queue besitzt keine Obergrenze.
- Es werden keine Empfangszeitstempel gespeichert.
- Es gibt keine Plausibilitätsprüfung für physikalisch unmögliche Temperaturen.
- Messwerte werden weder protokolliert noch als CSV gespeichert.
- Der Fehlerfall wird nur auf der Konsole ausgegeben und ist im Plot nicht sichtbar.

## 12. Typische Fehlerbilder

### Es kommen keine Werte an

- Falscher PCAN-Kanal ausgewählt.
- Bitrate stimmt nicht mit dem Sender überein.
- Falsche CAN-ID konfiguriert.
- Das Steuergerät sendet nur nach vorheriger Aktivierung oder Anfrage.
- CAN-H/CAN-L vertauscht, fehlende Masseverbindung oder falsche Terminierung.
- PEAK-Treiber fehlt oder Adapter wird bereits exklusiv verwendet.

### Werte sind offensichtlich falsch

- Little-Endian und Big-Endian wurden verwechselt.
- Die verwendete ID besitzt ein anderes Payload-Layout.
- Temperatur ist signed, wird aber mit `H` als unsigned dekodiert.
- Skalierungsfaktor ist nicht `0.01 °C/Bit`.
- Bytes 6–7 enthalten einen anderen Sensorkanal.

### Plot friert ein oder Speicherverbrauch steigt

- Senderate ist höher als die effektive Verarbeitungsrate der GUI.
- Die unbegrenzte Queue wächst.
- Zu viele GUI-Aktualisierungen oder CAN-Frames treffen gleichzeitig ein.

### `struct.error: unpack requires a buffer of 8 bytes`

Das empfangene Frame besitzt weniger als acht Datenbytes. Vor dem Dekodieren `len(msg.data) >= 8` prüfen.

## 13. Umgesetzte Struktur im Repository

Die Aufteilung liegt inzwischen vor:

```text
src/can_integration/
├── catalog.py    # Nachschlagetabelle: CAN-ID -> Bedeutung, plus JSON-Erweiterung
├── signals.py    # Signal/Message, reine Dekodierung ohne Hardwarezugriff
├── bus.py        # Öffnen, Filtern, Schließen des python-can-Busses
├── monitor.py    # Hintergrundempfang für die laufende Messung
├── reader.py     # blockierender Einzelabruf für die Inbetriebnahme
├── config.py     # Messkonfiguration aus JSON
└── __init__.py
```

Verantwortlichkeiten:

- `catalog.py`: die einzige Stelle, an der eine neue CAN-Funktion eingetragen
  wird. Jeder Eintrag nennt ID, Signale und die Herkunft seines Layouts.
- `signals.py`: Datenklassen und reine Decoder-Funktionen. Byte-Reihenfolge und
  Vorzeichen stehen je Signal als `struct`-Format fest, nicht global.
- `bus.py`, `monitor.py`, `reader.py`: Hardwarezugriff, Filter, Thread-Lebenszyklus.
- Separate Anwendung: Plot, CSV-Protokollierung oder Yokogawa-Automatisierung.

Hardwarezugriff und Binärdekodierung bleiben getrennt. Die Byteauswertung wird
mit künstlichen Payloads getestet, ohne dass ein PCAN-Adapter angeschlossen ist.

## 14. Checkliste für spätere Änderungen

- [ ] Richtige CAN-ID anhand der Inverter-Dokumentation bestätigen.
- [ ] Bedeutung von `0x1A000001` und `0x1A000003` dokumentieren.
- [x] Extended-ID-Flag prüfen (`Message.matches`).
- [x] DLC beziehungsweise Payload-Länge prüfen (`Signal.decode`).
- [ ] Endianness bestätigen (je Signal über `format` einstellbar).
- [ ] Signed/unsigned bestätigen.
- [ ] Temperaturfaktor und Einheit bestätigen.
- [ ] Physikalisch gültigen Temperaturbereich festlegen.
- [x] Zeitstempel statt reiner Sample-Nummern ergänzen (`Reading`).
- [x] Kontrolliertes Stoppen des Empfangsthreads implementieren (`SignalMonitor.stop`).
- [x] Decoder-Unit-Tests mit bekannten Telegrammen anlegen (`tests/`).
- [x] CAN-Hardwarefilter für die benötigten IDs konfigurieren (`Message.can_filter`).
- [ ] Optional Messwerte mit Zeitstempel in CSV protokollieren.

## 15. Merksatz

Für die aktuelle Implementierung gilt:

```text
PCAN_USBBUS1, 1 Mbit/s, Extended-ID 0x1A000003 oder 0x1A000001,
8-Byte-Payload, Little-Endian <4H, Temperatur in Bytes 6–7,
Temperatur [°C] = unsigned 16-Bit-Rohwert / 100.
```
