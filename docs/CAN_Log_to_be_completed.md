# CAN-Log-Auswertung (aus CAN_Log_Auswertung_0000297.ods)

> Automatisch aus der Original-`.ods`-Datei extrahiert, damit der Inhalt ohne Tabellenkalkulation lesbar ist.
> Quelle: `CAN_Log_Auswertung_0000297.ods`

## Blatt: CAN-Auswertung

CAN-Log-Auswertung – Inverter / Motorsteuerung


| Logger | CSS Electronics CL1000, HW 8.1x, FW 5.86, Session 275 |
|---|---|
| Aufzeichnung | 1.281 Frames über 3,341 s; Zeitbereich 00:00:07,072 bis 00:00:10,413 |
| CAN-Format | 1 Mbit/s, 29-Bit-Identifier (Extended CAN), 8 Datenbytes je beobachtetem Frame |
| Zyklische Kommunikation | Sechs Inverter-IDs mit jeweils 212 Frames; typische Wiederholzeit 15–16 ms (ca. 62,5 Hz) |
| Wahrscheinliche Struktur | Bei den IDs 0x1A0000xx sprechen die Werte für vier Little-Endian-16-Bit-Kanäle pro Frame |
| Geräteerkennung | 0x01000001 sendet alle ca. 500 ms den ASCII-Text „discover“; 0x01100000 erscheint als mögliche Antwort |
| Fehlerstatus | Im kurzen Ausschnitt ist kein Fehlercode eindeutig erkennbar |
| Wichtiger Vorbehalt | Signalnamen und Skalierungen sind Hypothesen. Für eine sichere Dekodierung werden DBC/CAN-Handbuch oder Vergleichslogs benötigt |


Erkannte CAN-IDs und vermutete Bedeutung

| CAN-ID | Vermutete Bedeutung | Datenstruktur | Beobachtete Werte / Bereich | Zykluszeit | Sicherheit | Bemerkung |
|---|---|---|---|---|---|---|
| 0x01000001 | Geräteerkennung / Discovery-Anfrage | 8 ASCII-Bytes | „discover“ | ca. 498–506 ms | hoch | Wahrscheinlich Diagnose-/Suchtelegramm; keine eigentliche Motor-Telemetrie. |
| 0x01100000 | Langsames Diagnose-/Erkennungstelegramm | 8 Bytes; überwiegend 0 | 159–161; 355–385; zuvor 404–423 | ca. 1.043 ms | mittel | Der Wert ist nicht konstant. Eine feste Gerätekennung ist daher unwahrscheinlich; direkte Discovery-Antwort bleibt unbestätigt. |
| 0x1A000003 | Langsame Betriebs-/Status- und Analogwerte | 4 × UInt16 LE | 0/31–32; 0/7; 422–423; 3.239–3.301 bzw. 3.692–3.696 | 10–35 ms | mittel | Wort 3 ist sehr stabil; Kandidat für Zwischenkreisspannung oder Referenz. Wort 4 verhält sich wie ein langsamer 12-Bit-ADC-Wert. |
| 0x1A000006 | Schnelle elektrische Mess-/Reglergrößen | 4 × Int/UInt16 LE | inaktiv: 98; 481; 0; 0 · aktiv: stark dynamisch | 10–35 ms | mittel–hoch | Bei inaktivem Regelzustand teilweise eingefroren, im aktiven Fenster dynamisch. Kandidaten: Strom-, Spannungs- oder d/q-Reglergrößen. |
| 0x1A000007 | Aktive Motor-/Reglergrößen | 4 × Int/UInt16 LE | inaktiv: 0; 0; 0; 0 · aktiv: alle vier dynamisch | 10–35 ms | hoch | Die vollständige Aktivierung zusammen mit dem Regelzustand spricht klar für schnelle Motor- oder Regler-Messwerte. |
| 0x1A000008 | Drehzahlbezogene Größe plus Last-/Strom-/Drehmomentwert | 4 × UInt16 LE | Wort 1: 0/243 · Wort 3: 7.796, 7.997 und 11.595 | 10–35 ms | hoch | Wort 3 bildet stabile, drehzahltypische Plateaus. Wort 1 schaltet zwischen 0 und 243 und ist wahrscheinlich ein lastabhängiger Soll- oder Istwert. |
| 0x1A00000C | Aktive Regelgrößen mit schnellem Signed-Signal | 4 × Int/UInt16 LE | inaktiv: 0; 0; −32.768; 0 · aktiv: ≈1.000; 1.000; −11.324…9.551; ≈203 | 10–35 ms | mittel–hoch | −32.768 wirkt wie ein Ungültigwert. Wort 3 ist im Betrieb eine sehr schnelle elektrische oder phasenbezogene Regelgröße. |
| 0x1A000013 | Möglicher externer 12-Bit-Analog-/Sollwerteingang | 1 × UInt16 LE + 6 Nullbytes | 0; 3.671–3.675; zuvor 4.089–4.094 | 10–35 ms | mittel–hoch | Der Bereich 0–4.095 spricht weiterhin für einen 12-Bit-ADC. Temperatur – Sensor |


## Blatt: 16-Bit-Kanäle

Detailstatistik der vermuteten Little-Endian-16-Bit-Kanäle
Statistik dieses Blatts: 0000297.TXT. Kanal n entspricht Byteposition 2n–2n+1; Vergleich und neue Deutung aus gekuerzt.txt siehe Blatt „Vergleich gekuerzt“. Negative Werte wurden bei plausiblen Bitmustern als Int16 interpretiert.


| CAN-ID | Kanal | Byteposition | Interpretation | Mittelwert | Std.-Abw. | Minimum | Maximum | Verschiedene Werte | Einordnung |
|---|---|---|---|---|---|---|---|---|---|
| 0x1A000003 | 1 | Bytes 0–1 | UInt16 LE | 32,00 | 0,00 | 32 | 32 | 1 | Konstanter Status-/Parameterwert |
| 0x1A000003 | 2 | Bytes 2–3 | UInt16 LE | 6,00 | 0,00 | 6 | 6 | 1 | Konstanter Status-/Parameterwert |
| 0x1A000003 | 3 | Bytes 4–5 | UInt16 LE | 423,00 | 0,00 | 423 | 423 | 1 | Konstanter Status-/Parameterwert |
| 0x1A000003 | 4 | Bytes 6–7 | UInt16 LE | 3.693,62 | 0,71 | 3.692 | 3.696 | 5 | Sehr stabiler Analog-/ADC-Kandidat |
| 0x1A000006 | 1 | Bytes 0–1 | Int16 LE | -3,99 | 125,87 | -309 | 284 | 168 | Dynamische, vorzeichenbehaftete Mess-/Reglergröße |
| 0x1A000006 | 2 | Bytes 2–3 | UInt16 LE | 433,79 | 112,35 | 131 | 675 | 172 | Dynamische Mess-/Reglergröße |
| 0x1A000006 | 3 | Bytes 4–5 | Int16 LE | -6,91 | 0,40 | -8 | -6 | 3 | Nahezu konstanter Offset-/Korrekturwert |
| 0x1A000006 | 4 | Bytes 6–7 | UInt16 LE | 432,55 | 20,81 | 375 | 488 | 81 | Dynamische Mess-/Reglergröße |
| 0x1A000007 | 1 | Bytes 0–1 | Int16 LE | -29,45 | 5,43 | -43 | -18 | 23 | Dynamische, vorzeichenbehaftete Mess-/Reglergröße |
| 0x1A000007 | 2 | Bytes 2–3 | UInt16 LE | 351,58 | 6,64 | 338 | 371 | 31 | Dynamische Mess-/Reglergröße |
| 0x1A000007 | 3 | Bytes 4–5 | Int16 LE | -238,58 | 21,95 | -291 | -167 | 79 | Dynamische, vorzeichenbehaftete Mess-/Reglergröße |
| 0x1A000007 | 4 | Bytes 6–7 | UInt16 LE | 152,97 | 25,70 | 88 | 224 | 89 | Dynamische Mess-/Reglergröße |
| 0x1A000008 | 1 | Bytes 0–1 | UInt16 LE | 243,00 | 0,00 | 243 | 243 | 1 | Konstant; Bedeutung und Skalierung offen |
| 0x1A000008 | 2 | Bytes 2–3 | UInt16 LE | 0,00 | 0,00 | 0 | 0 | 1 | Konstant null |
| 0x1A000008 | 3 | Bytes 4–5 | UInt16 LE | 11.595,00 | 0,00 | 11.595 | 11.595 | 1 | Konstant; möglicher Betriebs-/Konfigurationswert |
| 0x1A000008 | 4 | Bytes 6–7 | UInt16 LE | 0,00 | 0,00 | 0 | 0 | 1 | Konstant null |
| 0x1A00000C | 1 | Bytes 0–1 | UInt16 LE | 999,53 | 2,01 | 995 | 1.006 | 12 | Stabil nahe 1.000 |
| 0x1A00000C | 2 | Bytes 2–3 | UInt16 LE | 1.000,00 | 0,00 | 1.000 | 1.000 | 1 | Konstant 1.000 |
| 0x1A00000C | 3 | Bytes 4–5 | UInt16 LE | 13.569,00 | 0,00 | 13.569 | 13.569 | 1 | Konstanter Betriebs-/Konfigurationswert |
| 0x1A00000C | 4 | Bytes 6–7 | UInt16 LE | 203,80 | 9,51 | 173 | 234 | 46 | Dynamischer Mess-/Reglerwert |
| 0x1A000013 | 1 | Bytes 0–1 | UInt16 LE | 4.091,84 | 1,12 | 4.089 | 4.094 | 6 | Möglicher 12-Bit-ADC nahe Vollaussteuerung |
| 0x1A000013 | 2 | Bytes 2–3 | UInt16 LE | 0,00 | 0,00 | 0 | 0 | 1 | Konstant null |
| 0x1A000013 | 3 | Bytes 4–5 | UInt16 LE | 0,00 | 0,00 | 0 | 0 | 1 | Konstant null |
| 0x1A000013 | 4 | Bytes 6–7 | UInt16 LE | 0,00 | 0,00 | 0 | 0 | 1 | Konstant null |


## Blatt: Vergleich gekuerzt

Vergleichsauswertung – gekuerzt.txt
Ergänzung zur ersten Auswertung · Zwei getrennte Messfenster mit deutlich verschiedenen Betriebszuständen


| Quelle | gekuerzt.txt · CL1000, Session 272, 1 Mbit/s, Extended CAN |
|---|---|
| Messfenster | Fenster 1: 00:00:07,071–00:00:11,141 (4,070 s) · Fenster 2: 00:04:02,264–00:04:21,810 (19,546 s) |
| Unterbrechung | Zwischen den Fenstern wurden rund 231,123 s entfernt; der Sprung ist kein realer Signalverlauf |
| Umfang | 5.828 Frames; keine zusätzlichen CAN-IDs gegenüber 0000297.TXT |
| Zyklus | Zyklische 0x1A0000xx-Telegramme: ca. 10 ms im ersten und ca. 35 ms im zweiten Fenster |
| Zentraler Befund | Fenster 1 zeigt einen wahrscheinlich inaktiven/eingefrorenen Regelzustand; Fenster 2 einen aktiven, dynamischen Regelzustand |


Vergleich der CAN-Signale

| CAN-ID | Kanal | Fenster 1 | Fenster 2 | 0000297.TXT | Neue Vermutung | Sicherheit | Begründung |
|---|---|---|---|---|---|---|---|
| CAN 0x01000001 | gesamtes Payload | ASCII „discover“ | ASCII „discover“ | ASCII „discover“ | Discovery-/Suchanfrage | hoch | Unverändert alle ca. 500 ms; keine Motor-Telemetrie. |
| CAN 0x01100000 | letzte 2 Bytes, BE | 159–161 | 355–385 | 404–423 | Langsamer Diagnose-/Erkennungswert | mittel | Nicht konstant; daher eher Mess-/Diagnosewert als feste Gerätekennung. |
| CAN 0x1A000003 | Wörter 1–4 | 0; 0; 422; 3.239–3.244 | 31–32; 7; 422; 3.279–3.301 | 32; 6; 423; 3.692–3.696 | Langsame Betriebs-/Analogwerte | mittel | Wort 3 ist sehr stabil; Wort 4 zeigt langsames ADC-artiges Verhalten. |
| CAN 0x1A000006 | Wörter 1–4 | 98; 481; 0; 0 | −342…252; 156…689; −8…−6; 384…486 | dynamisch | Schnelle elektrische Reglergrößen | mittel–hoch | Im frühen Fenster teilweise eingefroren, im aktiven Fenster stark dynamisch. |
| CAN 0x1A000007 | Wörter 1–4 | 0; 0; 0; 0 | alle vier dynamisch | alle vier dynamisch | Aktive Motor-/Reglergrößen | hoch | Die Werte erscheinen erst im aktiven Regelzustand. |
| CAN 0x1A000008 | Wort 1 | 0 | 243 | 243 | Last-/Strom-/Drehmoment-Soll- oder Istwert | mittel–hoch | Schaltet mit dem aktiven Betriebszustand von 0 auf 243. |
| CAN 0x1A000008 | Wort 3 | 7.997 | 7.796 | 11.595 | Drehzahlbezogene Größe; min⁻¹ plausibel | hoch | Stabile Plateaus mit plausiblen Drehzahlwerten; leichter Abfall bei aktivem Lastwert 243. |
| CAN 0x1A00000C | Wort 3, Int16 LE | −32.768 | −11.324…9.551 | 13.569 | Schnelle elektrische/phasenbezogene Regelgröße | mittel–hoch | −32.768 wirkt wie ein Ungültigwert; im Betrieb sehr schnell und vorzeichenbehaftet. |
| CAN 0x1A00000C | Wort 4 | 0 | 177–227; Mittel 202,5 | 173–234; Mittel 203,8 | Gefilterte aktive Mess-/Reglergröße | mittel | Im Betrieb stabil um 203, bei inaktivem Zustand null. |
| CAN 0x1A000013 | Wort 1 | 3.671–3.675 | 0 | 4.089–4.094 | Externer 12-Bit-Analog-/Sollwerteingang | mittel–hoch | Wertebereich passt zu 0–4.095; kein eindeutiges universelles Freigabesignal. |


Hinweis: Die genaue Einheit und Skalierung bleiben ohne DBC/CAN-Handbuch oder dokumentierte Motorzustände hypothetisch.
