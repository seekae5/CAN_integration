# Was noch fehlt: von der CAN-Bibliothek zur Messautomation

> Ziel: Wirkungsgrad- und Kennfeldmessungen an Motor und Inverter, mit einem
> Yokogawa WT3000 fuer die dreiphasige elektrische Leistung, an zwei
> Pruefstaenden -- Schub (Waegezelle) und Drehmoment (Burster-Sensor).

## 1. Ausgangslage

`can_integration` ist heute eine saubere **CAN-Signalquelle**: Katalog,
`Device.get(name)`, fail-closed bei Veralterung, Simulation. Fuer eine
Messautomation fehlen drei Dinge, die keine CAN-Frage sind:

* eine **zweite und dritte Quelle** (WT3000, Burster) unter demselben Zugriff,
* ein **gemeinsamer Zeitbezug**, ohne den ein Wirkungsgrad nichts aussagt,
* ein **definierter sicherer Zustand**, wenn die Messung abbricht.

### Die gefaehrlichste Luecke zuerst

Der Monitor ist fail-closed, aber nur fuer das *Skript*: `StaleSignalError`
und `can.CanError` beenden das Messprogramm, `Device.stop()` haelt den
Empfangsthread an und schliesst den Bus. **Kommandiert wird dabei nichts.**
Ein Inverter, der vor dem Abbruch auf 6800 min-1 stand, steht danach immer
noch auf 6800 min-1 -- der Rotor auf dem Schubpruefstand dreht weiter, und
niemand liest mehr mit.

Das ist keine Feinheit der Fehlerbehandlung, sondern die erste Funktion, die
gebaut werden muss (siehe 4.1).

### Zweite Luecke: die Werte, aus denen der Wirkungsgrad entstehen soll

| Signal | Katalogstand |
|---|---|
| `torque_actual` | Rohwert, „Skalierung unbestaetigt" |
| `iph_rms`, `i_dc_flt`, `u_dc` | Rohwerte ohne Einheit, „Skalierung unbestaetigt" |
| `rpm_max` | dynamische, vorzeichenbehaftete Groesse -- der Name ist geraten |
| `weight` (Waegezelle) | Gramm, aber ohne Tara und ohne Kalibrierfaktor |

Aus unbestaetigten Rohwerten laesst sich kein Wirkungsgrad rechnen. Das
entscheidet die Architektur (Abschnitt 3).

## 2. Was die beiden Pruefstaende brauchen

### Schubpruefstand

| Groesse | Quelle | Anmerkung |
|---|---|---|
| Schub | Waegezelle (HX711) ueber CAN, `thrust.weight` | braucht Tara vor jedem Lauf |
| Drehzahl | Inverter ueber CAN, `rpm_actual` | einzige Drehzahlquelle; nicht kalibriert |
| Elektrische Leistung | WT3000 | DC-Seite *und* 3-Phasen-Ausgang, wenn Inverter und Motor getrennt bewertet werden sollen |
| Temperaturen | CAN | Abbruchkriterium, nicht Messgroesse |
| Sollwert | CAN-Kommando an den Inverter | **fehlt im Katalog** |

Kennzahl ist Schub je elektrische Leistung (g/W bzw. N/W) und Schub ueber
Drehzahl. Eine *mechanische Wellenleistung* gibt es hier nicht -- ohne
Drehmoment kein Wirkungsgrad im ueblichen Sinn.

### Drehmomentpruefstand

| Groesse | Quelle | Anmerkung |
|---|---|---|
| Drehmoment | Burster-Sensor | kalibriert, in Nm -- die belastbare Quelle |
| Drehzahl | Burster-Sensor | dito |
| Elektrische Leistung | WT3000, 3-phasig | am Pruefling |
| Temperaturen, Fehlerstatus | CAN | Abbruchkriterium |
| Sollwert Pruefling | Drehmoment, ueber CAN | **fehlt im Katalog** |
| Sollwert Lastmaschine | Drehzahl | zweiter Regler, eigener Zugang |

Ergebnis ist das Kennfeld eta(n, M) = P_mech / P_el mit
P_mech = 2*pi*n[1/s]*M[Nm].

**Besonderheit dieses Aufbaus: zwei Aktoren.** Der Pruefling stellt ein
Moment, die Lastmaschine haelt eine Drehzahl. Beim Abbruch zaehlt die
*Reihenfolge*: erst den Pruefling momentfrei, dann die Lastmaschine
herunterfahren. Umgekehrt beschleunigt der Pruefling gegen eine wegfallende
Last.

## 3. Die Entscheidung, die alles andere vereinfacht

**Der Wirkungsgrad wird aus den kalibrierten Instrumenten gerechnet, nicht aus
CAN.** WT3000 liefert P_el, Burster liefert n und M, die Waegezelle liefert den
Schub. CAN bleibt fuer das, was es sicher kann: Sollwerte setzen, Temperatur
und Fehlerstatus ueberwachen, arm/disarm.

Das spart die gesamte Skalierungsarbeit an `iph_rms`, `u_dc` und
`torque_actual` -- die bleibt Diagnose. Umgekehrt heisst es: die CAN-Werte
duerfen nie unbemerkt in ein Ergebnis geraten. Ein Signalname sollte deshalb
seine Quelle tragen (Abschnitt 5.1).

Zu pruefen, weil es die Architektur stark vereinfachen wuerde: die
WT3000-Reihe kennt eine **Motor-Auswerteoption mit Drehmoment- und
Drehzahleingang**, die P_mech und den Wirkungsgrad im Geraet rechnet -- und
zwar synchron zur eigenen Leistungsmessung. Ist diese Option vorhanden und der
Burster-Sensor darauf verdrahtet, entfaellt das Synchronisationsproblem aus
Abschnitt 5.2 vollstaendig, und die Automation muss nur noch einen Wert
abholen statt zwei Quellen zeitlich zu paaren.

## 4. Was in `can_integration` selbst gehoert

Alles hier ist echte CAN-Arbeit und passt in das bestehende Paket.

### 4.1 Sicherer Zustand und Not-Aus-Pfad -- **umgesetzt**

Eine `SafeState`-Beschreibung (welche Telegramme mit welchen Werten), die
**garantiert** gesendet wird -- bei Exception, bei Strg+C, bei Verlust des
Busses, beim Verlassen des `with`-Blocks. Fuer diesen Aufbau heisst das
mindestens `broadcast_command` mit `command = 0` (disarm), fuer den
Drehmomentpruefstand in definierter Reihenfolge ueber beide Maschinen.

Wichtig: Ein Not-Aus, der ueber denselben Bus geht, der gerade ausgefallen
ist, ist keiner. Die Funktion muss melden, ob sie durchkam -- und die
Dokumentation muss sagen, dass sie eine mechanische Abschaltung nicht ersetzt.

### 4.2 Grenzwerte, die tatsaechlich greifen -- **umgesetzt**

`Config.limits` ist heute `Mapping[str, float]`: eine Zahl je Signal, ohne
Richtung, und niemand wertet sie aus. Gebraucht wird:

* Ober- **und** Untergrenze je Signal, mit Einheit,
* eine Aktion je Grenze (`warn` / `abort`),
* eine Pruefung, die im Empfangsthread mitlaeuft, nicht erst beim Auslesen --
  sonst faellt eine Uebertemperatur erst auf, wenn das Skript zufaellig fragt,
* Ausloesung auch bei *Veralterung*: ein Sensor, der schweigt, ist kein
  unkritischer Sensor.

Ein Grenzwertverstoss loest 4.1 aus.

### 4.3 Die fehlenden Kommandotelegramme

Ohne sie laeuft keine Automation: Drehzahl- und Drehmomentsollwert als
`writable`-Katalogeintraege mit der echten Kommando-ID. Bekannt ist bisher
`inverter_command` (0x0A000000) mit `command_id = 0x0110` fuer die Drehzahl
laut Herstellerdoku, im Log unbestaetigt. Das ist Protokollarbeit, kein Code.

### 4.4 Tara und Kalibrierfaktor der Waegezelle -- **umgesetzt**

Die HX711 liefert einen Rohwert, der als Gramm deklariert ist. Vor jedem Lauf
gehoert dazu ein Nullabgleich bei stehendem Rotor, und der Kalibrierfaktor
gehoert in die Metadaten des Laufs. Solange beides fehlt, ist jeder Schubwert
ein Offset unbekannter Groesse.

### 4.5 Quellenqualifizierte Namen

`signal_keys` qualifiziert heute bei Kollisionen mit dem Nachrichtennamen
(`inverter_status_3.temperature`). Sobald WT3000 und Burster dazukommen,
kollidieren `torque`, `rpm`, `frequency`, `temperature` quer ueber die
Quellen. Die vorhandene Mechanik traegt das -- sie braucht nur eine Ebene
mehr: `can.rpm_actual`, `wt3000.p_total`, `burster.torque`.

## 5. Was in eine Schicht darueber gehoert

Ein Paket namens `can_integration` sollte kein VISA/GPIB mitbringen. Der
Vorschlag ist eine zweite Ebene -- Arbeitsname `bench_automation` --, die CAN,
WT3000 und Burster zusammenfuehrt. `can_integration` bleibt, was es ist.

### 5.1 Ein Quellen-Protokoll als Naht

Die Form steht schon: `get(name)`, `values()`, `age(name)`, `start()/stop()`,
Kontextmanager. Als `Protocol` formuliert, erfuellt `Device` es ohne eine
Zeile Aenderung, und ein `Wt3000` sowie ein `BursterSensor` koennen dasselbe
erfuellen. Darueber ein `CompositeSource`, der mehrere Quellen unter
qualifizierten Namen zusammenfasst.

Das ist dieselbe Naht, an der die Simulation haengt: ein simulierter WT3000
kommt dann genauso ins Spiel wie heute `SimulatedDevice`.

### 5.2 Gemeinsamer Zeitbezug und ehrliche Abtastung

Das eigentliche Problem. CAN schiebt alle 11 ms, der WT3000 wird *abgefragt*
und aktualisiert in seinem eigenen Takt (Groessenordnung 100 ms), und seine
Werte sind **Mittelwerte ueber dieses Intervall**, keine Momentanwerte. Eine
momentane CAN-Drehzahl mit einer ueber 100 ms gemittelten Leistung zu paaren,
ist im Einschwingen schlicht falsch.

Gebraucht wird:

* `sample()` -- ein Datensatz ueber alle Quellen mit **je Wert dessen Alter**,
* eine Schiefe-Grenze: ein Datensatz, in dem eine Quelle zu alt ist, wird
  gekennzeichnet oder verworfen, nicht stillschweigend gemittelt,
* Abfragen des WT3000 im Takt des Geraets, nicht schneller -- sonst
  wiederholt man denselben Messwert und taeuscht Aufloesung vor.

`Reading` traegt mit `timestamp` und `monotonic` bereits die richtige
Grundlage; sie muss nur ueber alle Quellen gelten.

### 5.3 Betriebspunkt, Beruhigung, Mittelung

Der reale Ablauf ist immer derselbe: Betriebspunkt anfahren, warten bis er
steht, ueber N Sekunden mitteln, eine Zeile schreiben. Also:

* `wait_until_steady(signale, toleranz, fenster, timeout)` -- z. B. Drehzahl
  +-0,5 %, Moment +-1 % ueber 2 s,
* `average(dauer)` -> Mittelwert, Standardabweichung, min, max und n je Signal.

Die Standardabweichung ist kein Beiwerk: sie ist der Beleg, dass der Punkt
wirklich stand.

### 5.4 Abgeleitete Groessen als Deklaration

P_mech, eta, g/W gehoeren einmal deklariert, nicht in jedes Skript kopiert --
im selben Namensraum wie die gemessenen Signale, damit sie automatisch
mitgeloggt und von Grenzwerten erfasst werden. Das ist die Fortsetzung des
Katalogprinzips: eine Groesse wird an einer Stelle definiert.

Achtung bei den Einheiten: `2*pi*n/60` nur, wenn n in min-1 vorliegt. Der
haeufigste stille Fehler in genau dieser Rechnung.

### 5.5 Aufzeichnung mit Herkunft

`signal_keys` verspricht in seinem Docstring bereits eine stabile
CSV-Kopfzeile -- geschrieben wird bisher nichts. Dazu gehoert ein Kopf mit:
Katalog (Datei und Pruefsumme), Konfiguration, Pruefstand, Pruefling,
Bediener, Datum, WT3000-Einstellungen (Verschaltung, Messbereiche, Filter,
Aktualisierungsrate), Tara- und Kalibrierwerte.

Bei einem Katalog, in dem so viel ausdruecklich „unbestaetigt" ist, ist die
Angabe, *welcher* Katalog die Zahlen erzeugt hat, kein Formalismus: ohne sie
ist eine Messreihe nach der naechsten Katalogkorrektur wertlos.

### 5.6 Messplan als Datei

Eine Liste von Betriebspunkten mit Verweilzeit und Beruhigungskriterium,
deklarativ wie `Config` -- damit ein Versuchsplan Daten sind und kein Code.
Fuer den Drehmomentpruefstand mit zwei Sollwertspalten (Moment des
Prueflings, Drehzahl der Lastmaschine).

### 5.7 WT3000-Treiber: was er koennen muss

Kein Vollausbau, sondern: Verschaltung und Messbereiche setzen und
*zurueckmelden*, Aktualisierungsrate setzen, numerische Werte im Block
abholen (nicht Wert fuer Wert), Bereichsueberlauf erkennen und melden,
Integration fuer Energie starten/stoppen, Identifikation und Einstellungen
fuer den Metadatenkopf ausgeben.

Zwei Fallstricke am Inverterausgang: die Spannungs-Frequenzerkennung braucht
den passenden Filter, sonst wandern die Messwerte; und ein automatischer
Messbereichswechsel mitten in einer Mittelung macht den Punkt unbrauchbar --
im Messbetrieb also feste Bereiche.

## 6. Offene Fragen

1. Hat der WT3000 die Motor-Auswerteoption, und ist der Burster-Sensor
   analog darauf verdrahtet oder wird er getrennt ausgelesen? Das entscheidet
   ueber 5.2.
2. Wie viele Messelemente hat das Geraet, und soll gleichzeitig DC-Eingang
   und 3-Phasen-Ausgang gemessen werden (Inverter- und Motorwirkungsgrad
   getrennt)?
3. Schnittstelle des WT3000: GP-IB, Ethernet oder USB?
4. Wie wird die Lastmaschine am Drehmomentpruefstand angesteuert -- ueber
   denselben CAN-Bus, einen zweiten, oder ganz anders?
5. Welche Kommando-IDs setzen Drehzahl und Drehmoment wirklich? Ohne diese
   Angabe ist keine Automation moeglich.
6. Wie liest der Burster-Sensor aus: analog ueber eine DAQ-Karte, Frequenz,
   oder digital?
7. Gibt es eine mechanische bzw. elektrische Not-Aus-Kette unabhaengig von
   der Software? Falls nicht, ist das vor jeder Automatisierung zu klaeren.

## 6a. Stand

| Punkt | Stand |
|---|---|
| 4.1 Sicherer Zustand | **fertig** -- `safety.py`, `Device(safe_state=...)`, `tests/test_safety.py` |
| 4.2 Grenzwerte | **fertig** -- `Limit`, Pruefung im Empfangsthread, Wachhund auf `max_age` |
| 4.3 Kommandotelegramme | offen -- Protokollarbeit |
| 4.4 Tara und Kalibrierfaktor | **fertig** -- `calibration.py`, `Device.tare/calibrate`, `tests/test_calibration.py` |
| 4.5 Quellenqualifizierte Namen | offen -- erst mit der zweiten Quelle noetig |
| 5.x Ebene darueber | offen |

Umgesetzt wurde dabei etwas mehr als 4.1/4.2 beschrieben haben:

* Der Wachhund ist **nur scharf, wenn Grenzwerte oder ein sicherer Zustand
  deklariert sind**. Sonst bliebe das bisherige Verhalten nicht erhalten: ein
  veralteter Wert faellt dann weiterhin erst beim Lesen auf und die Messung
  kann sich wieder fangen.
* Verletzungen sind **flankengesteuert**. Zwanzig Telegramme ueber der Grenze
  ergeben eine Meldung, nicht zwanzig -- sonst waere der Rueckruf, der den
  sicheren Zustand sendet, selbst die Ursache eines Sendesturms.
* `BusConnection.send` hat eine **Sendesperre** bekommen. Der sichere Zustand
  geht aus dem Empfangsthread raus, ein Sollwert aus dem Messthread; die
  beiden duerfen sich nicht ueberholen.
* `Config.limits` bleibt in der kurzen Schreibweise gueltig; die lange Form
  mit `min`, `max` und `action` kommt daneben. `Config.limit_rules` traegt das
  geparste Ergebnis.

Zu 4.4 kam ebenfalls mehr dazu als beschrieben:

* Die Kalibrierung wirkt **im Empfangsthread, vor den Grenzwerten**. Sonst
  haette eine Schubgrenze einen anderen Nullpunkt als der Messwert daneben.
* `tare()` mittelt ueber ein Zeitfenster und nimmt dabei **nur Telegramme aus
  diesem Fenster**. Das zuletzt empfangene stammt aus der Zeit davor -- beim
  Spannenabgleich also von vor dem Auflegen des Pruefgewichts -- und zog den
  Mittelwert sichtbar (im Test 528 g statt 500 g).
* `tolerance` lehnt einen Abgleich am bewegten Aufbau ab, und `TareResult`
  traegt Streuung und Anzahl als Beleg mit.
* Eine Aenderung der Kalibrierung zieht das zuletzt empfangene Telegramm mit,
  damit unmittelbar nach einer Tara nicht noch der alte Nullpunkt anliegt.

## 7. Reihenfolge

1. **Sicherer Zustand (4.1) und Grenzwerte (4.2)** -- vor jeder Automatisierung.
2. **Kommandotelegramme (4.3)** -- Protokollarbeit, blockiert alles Weitere.
3. **Quellen-Protokoll (5.1)** und ein WT3000-Treiber im Grundumfang (5.7),
   dazu ein simulierter WT3000 an derselben Naht.
4. **Abtastung und Zeitbezug (5.2)**, dann Beruhigung und Mittelung (5.3).
5. **Aufzeichnung (5.5)** und abgeleitete Groessen (5.4).
6. **Messplan (5.6)** -- erst wenn ein einzelner Betriebspunkt sauber sitzt.

Schritte 1 und 2 sind am Schubpruefstand mit einem Motor zu machen; der
Drehmomentpruefstand mit seinen zwei Aktoren kommt danach.
