## Kurzfazit

Die Bibliothek ist softwareseitig solide aufgebaut: Empfang, Filterung, Dekodierung, Hintergrundüberwachung, Aktualitätsprüfung und ein abgesicherter generischer Sendeweg funktionieren. Alle 158 Tests laufen erfolgreich, darunter Tests über einen virtuellen `python-can`-Bus.

Aus Sicht des realen Prüfstands ist der Stand jedoch:

- Sicher plausibilisiert ist derzeit nur die Motortemperatur auf `0x1A000013`.
- Weitere 13 eingebaute Signale können technisch dekodiert werden, ihre physikalische Bedeutung, Skalierung oder Vorzeichenbehandlung ist teilweise unbestätigt.
- Der eingebaute Standardkatalog enthält keinen einzigen schreibbaren CAN-Befehl.
- Zwei Kommandotelegramme existieren im optionalen `catalog.example.json`. Ihre Payloads werden korrekt erzeugt, sind aber nicht am Prüfstand validiert.
- Einen uneingeschränkt „sicher steuerbaren“ realen CAN-Befehl gibt es damit aktuell noch nicht.

## Eingebaute CAN-Nachrichten

Der Standardkatalog ist in [catalog.py](C:/Users/Persystems/PycharmProjects/CAN_integration/src/can_integration/catalog.py:87) definiert.

| CAN-ID | Nachricht | Signale | Bewertung |
|---|---|---|---|
| `0x1A000001` ext. | `inverter_status_1` | `iph_rms`, `i_dc_flt`, `u_dc`, `temperature` | Lesbar; Strom/Spannung nur Rohwerte, Temperaturzuordnung und Vorzeichen unbestätigt |
| `0x1A000003` ext. | `inverter_status_3` | `iph_rms`, `i_dc_flt`, `u_dc`, `temperature` | Lesbar; wahrscheinlich MOSFET-/Invertertemperatur, aber nicht eindeutig bestätigt |
| `0x1A000013` ext. | `motor_temperature` | `temperature` | Am Prüfstand mit realer Motortemperatur plausibilisiert; derzeit belastbarstes Signal |
| `0x1A00000C` ext. | `inverter_speed` | `rpm_actual`, `rpm_target`, `rpm_max`, `torque_actual` | Lesbar, aber Vorzeichen und Drehmomentskalierung nicht sicher |
| `0x003` standard | `thrust` | `weight` | Signed 32-Bit Big-Endian in Gramm; aus Mikrocontroller-Code übernommen, kein dokumentierter Hardware-Abgleich |

Insgesamt sind das 5 Telegramme mit 14 Signalen.

Wichtiger Widerspruch: Die README bezeichnet drei IDs als „am Prüfstand geklärt“. Die präzisere Dokumentation direkt im Katalog sagt dagegen, dass nur `motor_temperature` physisch gegengeprüft wurde. Für diese Bestandsaufnahme ist daher nur dieses Signal als praktisch plausibilisiert eingestuft.

### Auffälligkeit bei Drehzahl und Drehmoment

`inverter_speed` verwendet für alle vier Werte `<H`, also unsigned 16 Bit. Die Protokollübersicht beschreibt RPM dagegen als `int16` und für das Drehmoment einen Faktor von 1000. Damit können insbesondere negative Drehzahlen derzeit falsch interpretiert werden. `torque_actual` wird bewusst nur als Rohwert ausgegeben.

## Optionaler erweiterter Katalog

[catalog.example.json](C:/Users/Persystems/PycharmProjects/CAN_integration/catalog.example.json:1) erweitert den Standardkatalog um 13 Telegramme mit 34 Signalen. Zusammen wären damit 18 Telegramme und 48 Signale verfügbar.

### Vollständig modellierte, aber nicht am Prüfstand bestätigte Telemetrie

| CAN-ID | Nachricht | Signale |
|---|---|---|
| `0x1A000004` | `ppm_signal_counters` | Roh-/Gültig-Zähler, Puls- und Pausenzeit |
| `0x1A000005` | `ppwm_status` | `armed`, `inverted`, `state`, `valid`, PPWM-Roh-/Signalwert |
| `0x1A000006` | `current_control_dq` | `id_flt`, `iq_flt`, `id_trgt`, `iq_trgt` |
| `0x1A000007` | `voltage_control_dq` | `ud`, `uq`, beide Fehlerintegrale |
| `0x1A000008` | `motion_control_state` | Motion-State, Error-State, Zykluszeit, Softwareversion |
| `0x1A00000D` | `discovery_token_response` | drei Token-Wörter und Status |

Diese Layouts stammen aus der Protokollübersicht, wurden aber laut Katalog nicht am Prüfstand gegengemessen. Bei den d/q-Werten ist außerdem die Signed-Interpretation lediglich angenommen.

### Nur teilweise modelliert

| CAN-ID | Nachricht | Fehlender Teil |
|---|---|---|
| `0x01000001` | `discovery_request` | Nur erstes Payload-Byte deklariert; vollständiges `"discover"` nicht sendbar |
| `0x01100000` | `unknown_01100000` | Ein Big-Endian-Wert lesbar, Bedeutung und Skalierung unbekannt |
| `0x0A00000A` | `update_process_rx` | Nur Platzhalterbyte; Payload unbekannt |
| `0x1A00000B` | `update_process_request` | Nur Platzhalterbyte; Payload unbekannt |
| `0x1A00000F` | `error_log` | Nur Platzhalterbyte; Fehlerlog kann nicht inhaltlich dekodiert werden |

## Technisch sendbare Befehle

Nur im optionalen Beispielkatalog sind zwei Nachrichten als `writable` markiert.

### Broadcast `0x01000000`

Technisch kodierbar sind:

| Wert | Aktion | erzeugter Payload |
|---:|---|---|
| `0` | PPM disarm | `00 00 00 00 00 00 00 00` |
| `1` | PPM arm | `01 00 00 00 00 00 00 00` |
| `2` | Errorlog auslösen | `02 00 00 00 00 00 00 00` |

### Gerätekommando `0x0A000000`

Das generische Telegramm besitzt `command_id` und `value`:

| Funktion | Parameter | erzeugter Payload |
|---|---|---|
| RPM-Sollwert 2000 | `command_id=0x0110`, `value=2000` | `10 01 D0 07 00 00 00 00` |
| `AutoArmOnInput` aktivieren | `command_id=0x0D13`, `value=1` | `13 0D 01 00 00 00 00 00` |

Diese Payloads habe ich lokal mit dem aktuellen Encoder erzeugt; sie entsprechen exakt der [Protokollübersicht](C:/Users/Persystems/PycharmProjects/CAN_integration/docs/CAN_Protocol_Uebersicht.md:84).

Sie sind trotzdem noch nicht als betriebssicher einzustufen:

- Kein Test mit echtem Inverter bzw. PCAN-Adapter ist dokumentiert.
- `command` akzeptiert technisch jeden Wert von 0 bis 255, nicht nur 0–2.
- `command_id` und `value` akzeptieren jeden `uint16`-Wert.
- Zulässige Drehzahlbereiche und Betriebszustände werden nicht geprüft.
- Nach dem Senden wird keine Antwort oder tatsächliche Übernahme kontrolliert.
- `limits` aus der Konfiguration werden nicht durch die Bibliothek durchgesetzt.

## Noch nicht steuerbar

Folgende Funktionen fehlen oder sind nicht ausreichend modelliert:

1. `set_rpm()` funktioniert mit dem ausgelieferten Katalog nicht.

   Der eingebaute `rpm_target` gehört zu einer Statusmeldung. Das Beispielkommando heißt lediglich `value`. Deshalb findet `set_rpm()` auch mit dem Beispielkatalog kein schreibbares Signal `rpm_target`.

2. Discovery-Heartbeat ist nicht sendbar.

   `0x01000001` müsste zyklisch alle 500 ms mit `"discover"` gesendet werden. Der Eintrag ist read-only und definiert nur das erste Byte. Ein zyklischer Sender existiert ebenfalls nicht.

3. Node Allocation fehlt.

   Nicht implementiert sind:

   - Verwaltung von Token und RX-ID,
   - Assignment-Confirmation,
   - Node-IDs `0x0B000000` bis `0x0F000000`,
   - Wiederherstellung einer bekannten Node-ID,
   - persistente Gerätezuordnung.

4. Restore-Sequenz fehlt.

   Das direkt aufeinanderfolgende Senden von Restore-Frame und Token-Frame aus [Abschnitt 9](C:/Users/Persystems/PycharmProjects/CAN_integration/docs/CAN_Protocol_Uebersicht.md:598) ist nicht modelliert.

5. Update-Prozess ist nicht steuerbar.

   Für `0x0n00000A` und `0x1n00000B` fehlen die Payloaddefinitionen und die Ablaufsteuerung.

6. Errorlog ist nicht auswertbar.

   Das Auslösen über Broadcast-Kommando `2` ist technisch möglich, der Inhalt von `0x1A00000F` kann aber nicht dekodiert werden.

7. Telemetriesignale sind grundsätzlich read-only.

   Insbesondere `id_trgt`, `iq_trgt`, `rpm_target`, `armed` oder `state` sind derzeit lediglich vom Inverter gemeldete Zustände/Zielwerte. Daraus folgt nicht, dass sie über dieselbe ID steuerbar wären.

## Funktionsumfang des Packages

Die öffentliche API umfasst 40 Exporte, definiert in [__init__.py](C:/Users/Persystems/PycharmProjects/CAN_integration/src/can_integration/__init__.py:68).

- Einfache Bedienung: `connect`, `disconnect`, `get`, `values`, `age`, `set_signal`, `send`
- Benannte Kurzformen: `get_temperature`, `get_rpm`, `get_rpm_target`, `get_torque`, `get_thrust`, `set_rpm`
- Mehrere Geräte/Busse: `Device`
- Blockierender Einzelabruf: `SignalReader`
- Kontinuierliche Überwachung: `SignalMonitor`
- Buszugriff: `BusConnection`
- Kodierung/Dekodierung: `Signal`, `Message`
- Katalogverwaltung: `Catalog`, `DEFAULT_CATALOG`, `load_json`
- JSON-Konfiguration: `Config`
- Zeitstempel und Messwerte: `Reading`
- CLI: `can-integration --list`, `--config`, `--messages`, `--set`

Technisch unterstützt werden außerdem:

- Standard- und Extended-CAN-IDs,
- Little- und Big-Endian,
- signed/unsigned Integer und Float-Formate,
- Skalierung und Bias,
- feste DLC/Payload-Längen,
- Hardwarefilter,
- Fremd-, Remote- und Error-Frame-Abweisung,
- Timeout-, Startup- und Stale-Value-Erkennung,
- geliehene oder selbst verwaltete `python-can`-Busse,
- eindeutige bzw. qualifizierte Signalnamen,
- Schutz vor Schreiben auf read-only IDs,
- Wertebereichsprüfung auf Ebene des Binärformats.

## Gesamtbewertung

Die Bibliotheksarchitektur und Byteverarbeitung sind gut testbar und robust. Für Messungen ist `motor_temperature` der derzeit verlässlichste produktive Pfad. Für die übrige Telemetrie fehlen teilweise Bestätigung, Signedness, Skalierung oder genaue Sensorzuordnung.

Der Sendeweg ist technisch vorhanden und erzeugt die dokumentierten Bytes korrekt. Für reale Steuerung fehlen aber noch Hardwarevalidierung, semantische Wertebereiche, Rückmeldung/Verifikation und mehrere zentrale Protokollabläufe. Daher sollte derzeit kein Kommando als uneingeschränkt sicher steuerbar betrachtet werden.
