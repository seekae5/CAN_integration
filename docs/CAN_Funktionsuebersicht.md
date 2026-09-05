# Funktionsübersicht der CAN-Bibliothek

Stand: 05.09.2026 · Package `can-integration`, Version `0.1.0`.

Diese Bestandsaufnahme beschreibt die im Quellcode vorhandenen Funktionen. Grundlage sind die Module unter `src/can_integration`, die README, die Beispielkonfiguration und der Beispielkatalog sowie die vorhandenen Tests. Es wurde keine CAN-Hardware angesprochen. Protokollbeschreibungen und Beispieldefinitionen sind nicht automatisch vollständig implementierte Geräteabläufe.

## 1. Zweck und Aufbau

- **CAN-Messwerte lesen und Telegramme senden:** Die Bibliothek kapselt Buszugriff, Nachrichtenauswahl und die Umrechnung zwischen Payload und Signalwerten.
- **Zugriff über Namen:** Anwendungen verwenden beispielsweise `temperature` oder `rpm_actual` statt CAN-IDs und Byte-Offsets.
- **Mehrere Telegramme auf einem Bus:** Reader und Monitor können mehrere Nachrichtendefinitionen gleichzeitig verarbeiten. Separate `Device`-Instanzen ermöglichen mehrere Busse.
- **Drei Zugriffsebenen:** Modulfunktionen für einfache Skripte, `Device` als objektorientierter Einstieg sowie `SignalMonitor`, `SignalReader` und `BusConnection` für direkten Zugriff.
- **Voraussetzungen:** Python ab 3.10 und `python-can>=4.4,<5`; das tatsächliche Backend benötigt die passende Hardware-/Treiberumgebung.

Quellen: [öffentliche Exporte](../src/can_integration/__init__.py), [Paketdefinition](../pyproject.toml).

## 2. Einfache Schnittstelle und Geräteverwaltung

- `connect(messages, ...)`: Öffnet ein Gerät, startet den Hintergrundempfang und wartet beim initialen Start auf ein dekodierbares Telegramm jeder ausgewählten Nachricht. Akzeptiert Namen, `Message`-Definitionen oder eine `Config`; gibt das `Device` zurück.
- `disconnect()`: Stoppt das global verbundene Gerät; ohne Verbindung wirkungslos.
- `device()`: Liefert das global verbundene Gerät; ohne vorheriges `connect()` entsteht `NotConnectedError`.
- `get(name)`: Liefert den zuletzt empfangenen Signalwert mit Alters- und Empfangsfehlerprüfung.
- `values()`: Liefert ein Dictionary aller überwachten Signale. Fehlt ein frisches Telegramm, schlägt der gesamte Aufruf fehl.
- `age(name)`: Liefert das Empfangsalter in Sekunden, beziehungsweise `inf`, wenn noch kein Telegramm vorliegt.
- `set_signal(name, value, timeout=...)`: Sucht das Signal in den schreibbaren Katalogeinträgen und sendet das zugehörige Telegramm.
- `send(message, values=None, timeout=..., **signals)`: Sendet ein ganzes Telegramm; Werte sind als Mapping oder Schlüsselwortargumente möglich. Schlüsselwortargumente überschreiben gleichnamige Mapping-Werte.
- `Device(...)` / `Device.from_config(config, bus=...)`: Objektvariante mit `start()`, `stop()` und Kontextmanager (`with`). Lese- und Schreibmethoden heißen `get()`, `values()`, `age()`, `set()` und `send()`.
- Zusätzliche Geräteauskunft: `reading(name)` liefert das letzte `Reading` ohne Frischeprüfung, `signal(name)` die Definition; `signal_names`, `messages` und `monitor` stellen Namen, Telegrammdefinitionen und den zugrunde liegenden Monitor bereit.
- Die Modulfunktionen verwalten genau eine Verbindung. Ein zweites `connect()` ohne `disconnect()` wird abgelehnt.

Quelle: [device.py](../src/can_integration/device.py).

## 3. Benannte Messgrößen und eingebauter Katalog

- `get_temperature()` → `temperature`, Temperatur in °C.
- `get_rpm()` → `rpm_actual`, gemeldete Istdrehzahl.
- `get_rpm_target()` → `rpm_target`, gemeldeter Drehzahl-Sollwert.
- `get_torque()` → `torque_actual`, derzeit Rohwert ohne bestätigte Drehmomentskalierung.
- `get_thrust()` → `weight`, Gewicht/Schubmessung in Gramm; keine Umrechnung in Newton.
- `set_rpm(value)` → `set_signal("rpm_target", value)`; benötigt einen schreibbaren Katalogeintrag mit genau diesem Signalnamen.

Die fünf eingebauten Nachrichten sind sämtlich **nur lesbar**:

| Nachricht | CAN-ID / Format | Verfügbare Signale und Dekodierung |
|---|---|---|
| `inverter_status_1` | `0x1A000001`, Extended | `iph_rms`, `i_dc_flt`, `u_dc`: jeweils `<H`, Offsets 0/2/4, Rohwerte; `temperature`: `<H`, Offset 6, Faktor 0,01 °C |
| `inverter_status_3` | `0x1A000003`, Extended | Gleiches Signal- und Byte-Layout wie `inverter_status_1` |
| `motor_temperature` | `0x1A000013`, Extended | `temperature`: `<H`, Offset 0, Faktor 0,01 °C; übrige Bytes nicht definiert |
| `inverter_speed` | `0x1A00000C`, Extended | `rpm_actual`, `rpm_target`, `rpm_max`, `torque_actual`: jeweils `<H`, Offsets 0/2/4/6; Drehzahlen in rpm, Drehmoment als Rohwert |
| `thrust` | `0x003`, Standard | `weight`: `>i`, Offset 0, vorzeichenbehafteter 32-Bit-Wert in g |

- `<H` bedeutet Little-Endian, unsigned 16 Bit; `>i` bedeutet Big-Endian, signed 32 Bit.
- Laut Quellcode ist die Motortemperatur am Prüfstand gegen die reale Temperatur plausibilisiert. Die übrigen eingebauten Layouts stammen aus älteren Skripten; insbesondere Strom-/Spannungsskalierung, Drehmomentskalierung und die genaue Zuordnung der Invertertemperaturen bleiben unbestätigt.
- Bei mehrfach vorkommenden Signalnamen ist ein qualifizierter Name erforderlich, beispielsweise `motor_temperature.temperature`. Die Kurzfunktion `get_temperature()` löst Mehrdeutigkeit nicht selbst auf.

Quelle: [catalog.py](../src/can_integration/catalog.py).

## 4. Kontinuierliche Überwachung mit `SignalMonitor`

- `start()` / `stop()` und Kontextmanager: Starten und beenden einen Empfangsthread; `from_config()` übernimmt eine Konfiguration.
- Der Thread liest kontinuierlich und hält nur das neueste dekodierte Telegramm je ausgewählter Nachricht vor. Eine Messwerthistorie wird nicht gespeichert.
- `value(name)` und `values()`: Lesen den Cache ohne auf ein neues Telegramm zu warten und verweigern fehlende oder zu alte Werte.
- `reading(name)` und `readings()`: Geben gespeicherte Telegramme ohne Alters- oder Empfangsfehlerprüfung zurück. `reading(name)` kann bei unbekannten oder mehrdeutigen Namen trotzdem einen Namensfehler auslösen.
- `age(name)`, `signal(name)`, `signal_names`, `messages`, `max_age` und `connection`: Stellen Diagnoseinformationen, Definitionen und die Busverbindung bereit.
- Standardwerte: `max_age=1.0` Sekunden und `startup_timeout=5.0` Sekunden; Empfangspolling mit 0,1 Sekunden Timeout.
- Zu kurze Telegramme aktualisieren den Cache nicht. Der vorherige Wert altert weiter; Timeout-/Altersfehler können den letzten Dekodierfehler nennen.
- Empfangsfehler werden gespeichert und bei geprüften Wertzugriffen erneut ausgelöst.
- `values()` prüft alle Telegramme auf Frische, garantiert aber keine zeitgleiche Abtastung verschiedener CAN-IDs.

Quelle: [monitor.py](../src/can_integration/monitor.py).

## 5. Blockierender Einzelabruf mit `SignalReader`

- `connect()` / `close()` und Kontextmanager: Verwalten die Verbindung; `from_config()` erstellt den Reader aus einer Konfiguration.
- `read(timeout=1.0)`: Wartet auf das nächste passende Telegramm einer ausgewählten Nachricht und liefert ein `Reading`.
- `read_signal(name, timeout=1.0)`: Wartet gezielt auf das Telegramm des gewünschten Signals; andere konfigurierte Nachrichten werden übersprungen.
- `messages` und `signal_names`: Stellen die ausgewählten Definitionen und verwendbaren Namen bereit.
- Ein Timeout erzeugt `SignalTimeoutError`; ein passendes, aber zu kurzes Telegramm erzeugt `InvalidFrameError`.
- Geeignet für Inbetriebnahme und Diagnose. Im Gegensatz zum Monitor wird keine laufende Aktualisierung eines Messwertcaches angeboten.

Quelle: [reader.py](../src/can_integration/reader.py).

## 6. Buszugriff und Empfangsdaten

- `BusConnection.connect()` öffnet den Bus bei Bedarf; `close()` schließt nur einen selbst geöffneten Bus.
- Standardparameter: `interface="pcan"`, `channel="PCAN_USBBUS1"`, `bitrate=1_000_000`. Andere Schnittstellen können über das `python-can`-Backend gewählt werden.
- Ein vorhandener `can.BusABC` kann über `bus=` übergeben werden. Direkte Konstruktoraufrufe erlauben dann keine gleichzeitigen Parameter `interface`, `channel` oder `bitrate`.
- Bei selbst geöffneten Bussen werden CAN-Filter aus den Definitionen übergeben. Ein geliehener Bus wird nicht umgefiltert; zusätzliche Softwareprüfung erfolgt in beiden Fällen.
- `match(frame)` berücksichtigt ID und Standard-/Extended-Format und verwirft Error- und Remote-Frames.
- `message(...)`, `message_names`, `messages` und `catalog` erlauben Zugriff auf Definitionen.
- `read(timeout)` liefert ein dekodiertes `Reading` oder bei ausbleibendem Empfang `None`; die höhere Reader-Schicht wandelt `None` in einen Timeoutfehler um.
- `send(message, values, timeout=...)` und `send_signal(name, value, timeout=...)` kodieren und senden. Das Kommandotelegramm muss nicht zu den überwachten Nachrichten gehören.
- `Reading` enthält `message`, `values`, `timestamp` und `monotonic`. Der Backend-Zeitstempel dient der Protokollierung, mit lokaler Zeit als Rückfall; Altersprüfungen verwenden die lokale monotone Empfangszeit.

Quelle: [bus.py](../src/can_integration/bus.py).

## 7. Signaldefinition, Kodierung und Katalogverwaltung

- `Signal`: Beschreibt Name, Byte-Offset, `struct`-Format, `scale`, `bias`, Einheit, Beschreibung und optionalen Sendestandardwert `default`.
- `Signal.decode(payload)`: Berechnet `Rohwert * scale + bias`.
- `Signal.raw(value)` und `Signal.encode(value, payload)`: Rechnen physikalische Werte zurück; Ganzzahlformate runden auf den darstellbaren Rohwertschritt. Nichtnumerische, nichtendliche und nicht darstellbare Sendewerte werden abgelehnt.
- `Signal.size`, `end` und `is_integer`: Informieren über Breite, benötigte Payload-Länge und Formatart.
- `Message`: Bündelt ID, Standard-/Extended-Kennung, Signale, Herkunft, Schreibfreigabe und optionale Sendelänge.
- `Message.decode()` dekodiert sämtliche definierten Signale; `encode()` erzeugt den Payload nur bei `writable=True`. Nicht übergebene Signale benötigen einen `default`; unbesetzte Bytes bleiben null.
- `Message.signal()`, `matches()` und `describe()` sowie Eigenschaften wie `signal_names`, `signals_by_name`, `key`, `label`, `minimum_length`, `payload_length` und `can_filter` unterstützen Suche, Diagnose und Busanbindung.
- `Catalog`: Nachschlagen per Name (`catalog[name]`, `get()`), per ID (`by_id()`) oder Signal (`find_signal()`); Auflösen von Auswahlen (`resolve()`), Hinzufügen (`add()`), Erweitern als Kopie (`extended_with()`) und lesbare Darstellung (`describe()`).
- Doppelte Namen und doppelte Kombinationen aus ID und Standard-/Extended-Format werden im Katalog abgelehnt.
- `load_json(path, base=...)`: Lädt zusätzliche Nachrichtendefinitionen ohne Überschreiben vorhandener Einträge. `message_from_dict()` und `signal_from_dict()` sind zusätzlich im Katalogmodul verfügbar.
- `signal_keys()` erzeugt eindeutige Namen; `resolve_signal()` löst einfache oder qualifizierte Namen auf. `parse_can_id()` und `format_can_id()` im Signalmodul lesen bzw. formatieren CAN-IDs.
- Neue byteorientierte Telegramme lassen sich über Katalogdefinitionen ergänzen. Es gibt keine eigene Bitfeld-, Multiplexing- oder DBC-Importschnittstelle.

Quellen: [signals.py](../src/can_integration/signals.py), [catalog.py](../src/can_integration/catalog.py).

## 8. JSON-Konfiguration

- `Config.from_json(path)` lädt Nachrichtenauswahl, Busparameter, Alters-/Starttimeout und Grenzwerte.
- Der optionale JSON-Schlüssel `catalog` verweist auf einen zusätzlichen Katalog relativ zur Konfigurationsdatei.
- `Config.from_dict(values, catalog=...)` übernimmt bereits eingelesene Daten; ein Katalogdateipfad innerhalb dieses Dictionaries wird nicht unterstützt.
- `definitions`, `signal_names` und `limit(name)` liefern ausgewählte Definitionen, Signalnamen und einen hinterlegten Grenzwert beziehungsweise `None`.
- Unbekannte Konfigurationsschlüssel, leere/doppelte Nachrichtenauswahlen sowie unbekannte oder mehrdeutige Grenzwertsignale werden abgelehnt.
- `limits` speichert Grenzwerte, führt aber selbst weder einen Vergleich noch eine Abschaltung aus.

Quellen: [config.py](../src/can_integration/config.py), [Beispielkonfiguration](../config.example.json).

## 9. Zusätzliche Funktionen im Beispielkatalog

Die Datei `catalog.example.json` erweitert den eingebauten Katalog bei explizitem Laden um 13 Definitionen. Sie ist nicht Bestandteil von `DEFAULT_CATALOG`.

- **Zwei schreibbare Telegramme:** `broadcast_command` (`0x01000000`) mit `command` für PPM disarm/arm und Errorlog-Anforderung; `inverter_command` (`0x0A000000`) mit `command_id` und `value` für Parameterschreiben.
- **Weitere Telemetrie:** `ppm_signal_counters`, `ppwm_status`, `current_control_dq`, `voltage_control_dq` und `motion_control_state` beschreiben Zähler, Zustände, Reglerwerte und Versions-/Fehlerinformationen.
- **Tokenantwort:** `discovery_token_response` dekodiert drei Tokenwörter und ein Statuswort; daraus folgt keine automatische Gerätezuordnung.
- **Unbekannte Messgröße:** `unknown_01100000` liest einen Big-Endian-Wert aus den letzten zwei Bytes, dessen Bedeutung offen ist.
- **Unvollständige Platzhalter:** `discovery_request`, `update_process_rx`, `update_process_request` und `error_log` definieren lediglich das erste Payloadbyte und sind nicht schreibbar. Vollständige Abläufe bzw. Payloadinterpretationen fehlen.
- **Wichtige Einschränkung:** Auch mit diesem Beispielkatalog funktioniert `set_rpm()` nicht direkt: Der generische Schreibkanal verwendet `command_id` und `value`, kein schreibbares Signal `rpm_target`. Ein entsprechender Parameteraufruf müsste über `send("inverter_command", command_id=..., value=...)` formuliert werden.
- Die Definitionen enthalten Hinweise zur Herkunft und zu unbestätigten Annahmen. Die Beispiel-Geräteadresse ist fest auf Node-Nibble A gesetzt; dynamische Adresszuordnung fehlt.

Quelle: [catalog.example.json](../catalog.example.json).

## 10. Kommandozeilenwerkzeug

- `can-integration --list`: Zeigt den eingebauten Katalog mit IDs, Richtung, Signalen und Herkunft.
- `can-integration --config config.example.json --list`: Zeigt den Katalog einschließlich konfigurierter Erweiterungen.
- `can-integration --messages motor_temperature inverter_speed`: Gibt laufend empfangene Telegramme aus.
- `--config DATEI`: Übernimmt die Auswahl und Parameter aus JSON.
- `--timeout SEKUNDEN`: Wartezeit pro Telegramm bei der Anzeige, standardmäßig 2 Sekunden.
- `--set SIGNAL=WERT`: Sendet einen Wert und beendet sich; mehrfach angegeben werden separate Telegramme gesendet, kein gemeinsames Mehrsignal-Kommando.
- Der CLI-Schreibweg startet zunächst ein `Device` und wartet damit auf die konfigurierten Empfangsnachrichten. Er ist kein reiner Sendebetrieb ohne Empfangsvoraussetzung.
- Strg+C beendet die Anzeige. Empfangstimeouts und zu kurze Telegramme werden angezeigt, anschließend wird weitergelesen.

Quelle: [cli.py](../src/can_integration/cli.py).

## 11. Fehlerbehandlung und Umfangsgrenzen

- `UnknownMessageError`, `UnknownSignalError`, `AmbiguousSignalError`: Unbekannte bzw. nicht eindeutige Namen.
- `NotConnectedError`: Zugriff über Modulfunktionen ohne Verbindung.
- `SignalTimeoutError`: Fehlendes Telegramm beim Start oder Einzelabruf.
- `StaleSignalError`: Fehlender oder veralteter Cachewert beim geprüften Lesen.
- `InvalidFrameError`: Payload zu kurz für die Signaldefinition.
- `InvalidValueError`: Ungeeigneter Sendewert oder fehlender Pflichtwert.
- `ReadOnlyMessageError`: Versuch, ein nicht schreibbares Telegramm zu kodieren/senden. Fehlt im Katalog überhaupt ein schreibbares Telegramm, meldet `send_signal()` stattdessen `ValueError`.
- Weitere Definitions-/Konfigurationsfehler verwenden `ValueError` bzw. `TypeError`; Backendfehler können an die Anwendung weitergegeben werden.
- **Nicht implementiert:** Automatische Grenzwertabschaltung, Messablaufsteuerung, Messdateiexport, automatischer Wiederverbindungsablauf, periodische Discovery-/Heartbeat-Sendeschleife, Node Allocation, Restore-Sequenz und Firmware-Update-Ablauf.
- Ein erfolgreicher Sendeaufruf beinhaltet keine gerätespezifische Bestätigung oder Prüfung der tatsächlichen Wirkung. Werteprüfungen betreffen das Binärformat, nicht zulässige Gerätebetriebsbereiche oder symbolische Kommandoenumerationen.

## 12. Vorhandene Absicherung

- Die vorhandenen Tests behandeln Signaldekodierung und -kodierung, Kataloge, Konfiguration, Reader, Monitor und Geräte-/Modul-API.
- Geprüfte Szenarien umfassen unter anderem Endianness, Signedness, Skalierung, Wertebereiche, Schreibschutz, Mehrdeutigkeit, Timeouts, veraltete Werte, Busfehler und Buslebenszyklus.
- Reader- und Monitor-Tests enthalten auch Tests mit dem virtuellen `python-can`-Bus. Das bestätigt keine physikalische Signalbedeutung am Prüfstand.
- Für diese Dokumentationsaufnahme wurden Quellcode und Testfälle gelesen; die Tests wurden nicht erneut ausgeführt und es wurde keine Hardwarevalidierung vorgenommen.

Quellen: [Tests](../tests), [README](../README.md), [ergänzende Bestandsaufnahme](CAN_Bestandsaufnahme.md).
