# Persystems CAN Protocol – Übersicht

> Quelle: exportierte Wiki-Seite **„Persystems Protocol“**.  
> Diese Datei fasst die CAN-IDs, Payloads, Signalbelegungen, Zustände und die Discovery-/Node-Allocation-Logik der Quelle zusammen.  
> Begriffe, Konstanten, Faktoren und Werte wurden aus der Quelle übernommen; nicht dokumentierte Einheiten oder Interpretationen wurden nicht ergänzt.

## 1. Allgemeines

Die Quelle beschreibt das CAN-Interface des **Persystems CAN Protocol**.

### Elektrischer Anschluss

- Der 15-Pin-Anschluss besitzt laut Quelle ein individuelles Pinout.
- Der 9-Pin-Ausgang des **Persystems Adapter** folgt dem CAN-Standard.
- Das in der Quelle referenzierte Anschlussbild ist im HTML-Export nicht als verwertbare Pin-Tabelle enthalten.

---

# 2. CAN-ID-Schema

In mehreren IDs wird `n` als variabler Bestandteil der Geräte-ID verwendet.

Beispiele aus der Quelle:

- Geräte-RX-Basis: `0x0n000000`
- Geräte-TX: `0x1n......`
- Default-/Beispiel-RX-ID: `0x0A000000`
- Weitere verwendete RX-IDs: `0x0B000000` bis `0x0F000000`
- Beispiel-TX-Antwort für RX `0x0A000000`: `0x1A00000D`

Für die Discovery-Logik wird die aktuelle RX-ID aus einer Antwort-ID so abgeleitet:

```text
rxBase = responseId & 0x0F000000
```

Beispiele:

```text
0x1A00000D -> 0x0A000000
0x1B00000D -> 0x0B000000
...
0x1F00000D -> 0x0F000000
```

---

# 3. Gesamtübersicht der CAN-IDs

| CAN-ID | Richtung / Verwendung | Zyklus / Auslöser | Kurzbeschreibung |
|---|---|---:|---|
| `0x0n000000` | Inverter Rx | bei Bedarf | Schreiben von Parametern, z. B. RPM-Sollwert oder `AutoArmOnInput` |
| `0x0n00000A` | Inverter Rx | bei Bedarf | Update process – receive response request |
| `0x01000000` | Broadcast Rx | bei Bedarf | Broadcast-Kommandos für alle Geräte |
| `0x01000001` | Discovery Broadcast | alle 500 ms | ASCII-Payload `discover` |
| `0x1n000003` | Inverter Tx | 100 ms | Strom, DC-Spannung, MOSFET-Temperatur |
| `0x1n000004` | Inverter Tx | 10 ms | Raw-/Valid-Signalzähler und Puls-/Pausenwerte |
| `0x1n000005` | Inverter Tx | 10 ms | Armed/Inverted, State/Valid und PPWM-Signale |
| `0x1n000006` | Inverter Tx | 100 ms | `Id_Flt`, `Iq_Flt`, `Id_trgt`, `Iq_trgt` |
| `0x1n000007` | Inverter Tx | 100 ms | `Ud`, `Uq` und Fehlerintegrale |
| `0x1n000008` | Inverter Tx | 100 ms | Motion-Control-State, Error-State, Cycle-Time, SW-Version |
| `0x1n00000B` | Inverter Tx | 2 ms | Update process request |
| `0x1n00000C` | Inverter Tx | 100 ms | RPM- und Torque-Werte |
| `0x1n00000F` | Inverter Tx | bei Fehler / Anfrage, 10 ms | Error log |
| `0x1n000013` | Inverter Tx | 100 ms | Motor-Temperatur `NTC1k` |
| `0x1A00000D` ... `0x1F00000D` | Discovery Response | als Antwort auf `discover` | Token-Antwort eines Inverters |

---

# 4. Inverter Rx – Kommandos an ein bestimmtes Gerät

## 4.1 `0x0n000000` – RPM-Sollwert schreiben

**DLC:** `8`

Beispielpayload:

```text
10 01 D0 07 00 00 00 00
```

Byte-Bedeutung laut Quelle:

| Bytes | Wert | Bedeutung |
|---|---|---|
| 0..1 | `10 01` | Write 16 bit on RPM setpoint |
| 2..3 | `D0 07` | 2000 RPM als Sollwert, byte-swapped; `0x07D0 = 2000` |
| 4..7 | `00 00 00 00` | Ignorierte Daten |

---

## 4.2 `0x0n000000` – `AutoArmOnInput` schreiben

**DLC:** `8`

Payload:

```text
13 0D 01 00 00 00 00 00
```

Byte-Bedeutung:

| Bytes | Wert | Bedeutung |
|---|---|---|
| 0..1 | `13 0D` | Write 16 bit on SU Param: `AutoArmOnInput` |
| 2..3 | `01 00` | Wert `1` schreiben, um `AutoArmOnInput` zu aktivieren |
| 4..7 | `00 00 00 00` | Ignorierte Daten |

Hinweis der Quelle:

- `AutoArmOnInput` geht nach einem Powercycle verloren.

---

## 4.3 `0x0n00000A` – Update-Prozess

**DLC:** `8`

**Data:** `xxx`

**Beschreibung:** `Update process - receive response request`

Weitere Payload-Details sind in der Quelle an dieser Stelle nicht angegeben.

---

# 5. Broadcast-ID `0x01000000`

Diese ID gilt laut Quelle für **alle Geräte auf dem Bus**.

**DLC:** jeweils `8`

| Payload | Bedeutung |
|---|---|
| `00 00 00 00 00 00 00 00` | `disarm PPM` |
| `01 00 00 00 00 00 00 00` | `arm PPM` |
| `02 00 00 00 00 00 00 00` | `trigger_Errorlog` |

---

# 6. Inverter Tx – zyklische und ereignisbezogene Daten

> Die Spalten `Faktor` geben die in der Quelle angegebenen Faktoren wieder.  
> Wo die Quelle keine explizite physikalische Einheit nennt, wird hier keine zusätzliche Interpretation ergänzt.

## 6.1 `0x1n000003`

**Cycle time:** `100 ms`

| 16-bit-Wert | Faktor | Beschreibung |
|---|---:|---|
| `Iph_Rms` | `10` | Phase current, root mean square |
| `I_DC_flt` | `10` | Supply current, filtered |
| `U_DC` | `10` | Supply voltage, non-filtered |
| `Temperature_Mosfet` | `100` | MOSFET-Temperatur |

---

## 6.2 `0x1n000004`

**Cycle time:** `10 ms`

| 16-bit-Wert | Faktor / Einheit |
|---|---|
| `raw_signal_count` | `1` |
| `valid_signal_count` | `1` |
| `raw_pulse_value` | `100 ns` |
| `raw_pause_value` | `100 ns` |

---

## 6.3 `0x1n000005`

**Cycle time:** `10 ms`

| 16-bit-Wort | Inhalt | Faktor / Typ |
|---|---|---|
| Val1 | `armed:8, inverted:8` | `1(bool), 1(bool)` |
| Val2 | `state:8, valid:8` | `1 (uint8), 1 (bool)` |
| Val3 | `PPWM_signal_raw` | `1 (uint16)` |
| Val4 | `PPWM_signal` | `1 (uint16)` |

### PPWM-State

```text
PPWM_state_disarmed       = 0
PPWM_state_invalid        = 1
PPWM_state_armed          = 2
PPWM_state_armed_invalid  = 3
PPWM_state_armed_valid    = 4
PPWM_state_enabled_always = 5
```

---

## 6.4 `0x1n000006`

**Cycle time:** `100 ms`

| 16-bit-Wert | Faktor |
|---|---:|
| `Id_Flt` | `100` |
| `Iq_Flt` | `100` |
| `Id_trgt` | `100` |
| `Iq_trgt` | `100` |

---

## 6.5 `0x1n000007`

**Cycle time:** `100 ms`

| 16-bit-Wert | Faktor |
|---|---:|
| `Ud` | `100` |
| `Uq` | `100` |
| `Ud_error_integral` | `1000` |
| `Uq_error_integral` | `1000` |

---

## 6.6 `0x1n000008`

**Cycle time:** `100 ms`

| 16-bit-Wert | Faktor / Typ |
|---|---|
| `MotionCtrlState` | `1 (uint16)` |
| `ErrorState` | `1 (uint16)` |
| `max_cycle_time (internal quality)` | `1 (uint16)` |
| `SW Version, dev:4 patch:4 minor:4 major:4` | `1` |

### MotionCtrlState

Die Quelle erläutert:

- `STO` = Safe Torque Off
- `Freewheeling`
- `ASC` = Active Short Circuit

Definierte Werte:

```text
STO                = 0x0000
waitingForSetpoint = 0x00F2  (STO or ASC by parameter)
ControlActive      = 0x00F3
HardFault          = 0xFF00
```

### ErrorState

```text
ERROR_STATUS_OK                     = 0
ERROR_STATUS_OVERCURRENT_PROTECTION = 30
ERROR_STATUS_PPM_INVALID            = 41
ERROR_STATUS_PPM_TIMEOUT            = 42
ERROR_STATUS_PPM_INVERSION_FAULT    = 43
ERROR_STATUS_OVERTEMPERATURE        = 44
ERROR_STATUS_PPM_SIGNAL_NOISY       = 45
```

---

## 6.7 `0x1n00000B`

**Cycle time:** `2 ms`

**Beschreibung:** `Update process request`

Weitere Signal-/Payload-Details sind in der Tabelle nicht angegeben.

---

## 6.8 `0x1n00000C`

**Cycle time:** `100 ms`

| 16-bit-Wert | Faktor / Typ |
|---|---|
| `RPM_act` | `1 (int16)` |
| `RPM_target` | `1 (int16)` |
| `RPM_max` | `1 (int16)` |
| `TQ_act` | `1000` |

---

## 6.9 `0x1n00000F`

**Cycle time:** `10 ms (at error or requested)`

**Beschreibung:** `Error log at request or at overcurrent`

Weitere Signal-/Payload-Details sind in der Tabelle nicht angegeben.

---

## 6.10 `0x1n000013`

**Cycle time:** `100 ms`

| 16-bit-Wert | Faktor |
|---|---:|
| `Temp motor NTC1k` | `100` |
| `A` | `0` |
| `B` | `0` |
| `C` | `0` |

---

# 7. PersyCAN Discovery Node Allocation

## 7.1 Ziel

Die GUI übernimmt die Rolle des **Node-Allocation-Hosts**.

Laut Quelle können mehrere Inverter zunächst mit derselben Default-ID starten, z. B.:

```text
0x0A000000
```

Die Software trennt die Geräte anschließend automatisch auf:

```text
0x0A000000
0x0B000000
...
0x0F000000
```

Anforderungen laut Quelle:

- Bereits zugeordnete IDs dürfen nach einem Powercycle eines einzelnen Geräts nicht von neu gestarteten Geräten verdrängt werden.
- Bekannte Geräte sollen nach einem Powercycle direkt auf ihre zuletzt bekannte ID zurückgesetzt werden.

---

## 7.2 Discovery-/Heartbeat-ID `0x01000001`

Die GUI sendet alle **500 ms**:

```text
CAN-ID: 0x01000001
DLC:    8
Data:   64 69 73 63 6F 76 65 72
```

Die Daten entsprechen ASCII:

```text
discover
```

Der Heartbeat muss weiterlaufen, solange die Geräte im PersyCAN-Discovery-Modus gehalten werden sollen.

### Firmware-Verhalten

Bei Empfang von `discover`:

- Der Inverter aktiviert temporär das PersyCAN-Protokoll.
- Das dauerhaft gespeicherte CAN-Protokoll wird **nicht** geändert.
- Dadurch bleiben bestehende Kundenkonfigurationen erhalten.
- Wird länger als **2 Sekunden** kein `discover` empfangen, fällt der Inverter auf sein gespeichertes Protokoll zurück, z. B. DroneCAN.

---

## 7.3 Token Response

Ein Inverter, der für den aktuell aktiven Host noch nicht initialisiert ist, antwortet auf `discover`.

### Response-ID

Beispiele:

| Aktuelle RX-ID | Token-Response-ID |
|---|---|
| `0x0A000000` | `0x1A00000D` |
| `0x0B000000` | `0x1B00000D` |
| ... | ... |
| `0x0F000000` | `0x1F00000D` |

### Payload

Die 8 Byte bestehen aus vier 16-bit-Wörtern:

| Word | Inhalt |
|---|---|
| Word 0 | Token word 0, little-endian |
| Word 1 | Token word 1, little-endian |
| Word 2 | Token word 2, little-endian |
| Word 3 | Debug/Status; kann von der GUI ignoriert werden |

Der Token besteht aus:

```text
3 x uint16_t = 48 bit
```

Die GUI muss den Token laut Quelle nicht berechnen. Sie muss ihn lediglich:

- speichern,
- vergleichen,
- zurückschreiben.

---

## 7.4 RX-ID aus der Response-ID ableiten

Beispiel:

```text
Response:      0x1A00000D
Current RX-ID: 0x0A000000
```

Allgemein:

```text
rxBase = responseId & 0x0F000000
```

Zuordnung im vorhandenen PersyCAN-ID-Bereich:

```text
0x1A00000D -> 0x0A000000
0x1B00000D -> 0x0B000000
...
0x1F00000D -> 0x0F000000
```

---

## 7.5 Interner Zustand der GUI

Für jede beobachtete RX-ID:

- offene Candidate-Liste
- zuletzt bestätigter Owner-Token

Global:

- Mapping von Token zu bekannter RX-ID

Empfohlene minimale Datenstrukturen aus der Quelle:

```text
candidatesByRxId[rxId] = list of { token, lastSeenMs }

ownerTokenByRxId[rxId] = token

ownedRxIdByToken[token] = rxId
```

---

## 7.6 Token Key

Aus den drei Token-Wörtern soll ein stabiler Schlüssel aufgebaut werden.

Beispiel:

```text
tokenKey = hex4(token0) + hex4(token1) + hex4(token2)
```

Wichtig:

- Byte-Reihenfolge beim Empfang beachten.
- Die Token-Wörter sind `uint16_t`-Werte im Little-Endian-Format.

---

# 8. Node-Allocation-Fälle

## 8.1 Fall 1 – Neue ID ist frei

Beispiel:

1. Ein Inverter antwortet auf `0x1A00000D`.
2. Die GUI leitet daraus RX-ID `0x0A000000` ab.
3. Für `0x0A000000` existiert noch kein Owner.
4. Der Token wird als Owner dieser RX-ID gespeichert.

```text
ownerTokenByRxId[0x0A000000] = token
ownedRxIdByToken[token] = 0x0A000000
```

Danach bestätigt die Software den Token auf `0x0A000000`.

---

## 8.2 Normale Assignment Confirmation

### CAN-ID

Die **aktuelle RX-ID**, z. B.:

```text
0x0A000000
```

### Register

```text
xA/00..02
```

### PersyCAN Write

```text
structure index A
offset 00
write 3 words
```

### Payload

```text
Byte 0:    0x3A
Byte 1:    0x00
Byte 2..3: token0 little-endian
Byte 4..5: token1 little-endian
Byte 6..7: token2 little-endian
```

Schema:

```text
ID 0x0A000000
DLC 8
3A 00 <token0_lo> <token0_hi> <token1_lo> <token1_hi> <token2_lo> <token2_hi>
```

### Firmware-Reaktion

- Der Inverter mit dem passenden Token bleibt auf dieser ID.
- Andere Inverter auf derselben ID erkennen, dass der Token nicht passt, und wechseln auf die nächste ID.

---

## 8.3 Fall 2 – RX-ID besitzt bereits einen Owner

Beispiel:

```text
0x0A000000 already belongs to Token A.
Another inverter B restarts and comes back on default 0x0A000000.
B responds on 0x1A00000D with Token B.
```

Die Software erkennt:

```text
ownerTokenByRxId[0x0A000000] = Token A
new response token = Token B
```

Wenn Token B nicht bereits einer anderen ID zugeordnet ist, schreibt die Software **Token A erneut** auf `0x0A000000`.

Ergebnis:

- Inverter A bleibt auf `0x0A000000`.
- Inverter B erhält „not my token“ und wechselt auf die nächste ID, z. B. `0x0B000000`.
- B antwortet anschließend erneut auf seiner neuen ID und der Prozess wird fortgesetzt.

---

## 8.4 Fall 3 – Bekannter Token erscheint auf der falschen ID

Beispiel:

```text
Token B was previously on 0x0B000000.
After a power cycle, B starts again with default 0x0A000000.
B responds on 0x1A00000D with Token B.
```

Die Software erkennt:

```text
ownedRxIdByToken[Token B] = 0x0B000000
current RX-ID from response = 0x0A000000
```

Token B soll direkt zurück auf seine bekannte ID verschoben werden.

Ablauf:

1. Restore Command an die aktuelle/falsche RX-ID `0x0A000000`.
2. Unmittelbar danach den Token senden.
3. Nur der Inverter mit Token B führt den Restore aus.
4. Andere Geräte auf `0x0A000000` ignorieren den Restore, da deren Token nicht passt.

---

# 9. Restore Command

## 9.1 Restore vorbereiten

Ziel laut Quelle:

```text
"Token X shall directly switch to node index Y."
```

### CAN-ID

```text
current RX-ID
```

Also die ID, von der der Token gerade geantwortet hat.

### Register

```text
xA/03..04
```

### Payload

```text
Byte 0:    0x2A
Byte 1:    0x03
Byte 2..3: 0xA11D little-endian
Byte 4..5: targetNodeIndex little-endian
Byte 6..7: 00 00
```

### `targetNodeIndex`

```text
0x0A000000 -> 0x000A
0x0B000000 -> 0x000B
...
0x0F000000 -> 0x000F
```

Beispiel für Ziel `0x0B000000`:

```text
0A000000  2A 03 1D A1 0B 00 00 00
```

---

## 9.2 Restore Token Step

Unmittelbar nach dem Restore Command wird der Token geschrieben.

### CAN-ID

Weiterhin die aktuelle/falsche RX-ID.

### Payload

```text
Byte 0:    0x3A
Byte 1:    0x00
Byte 2..3: token0 little-endian
Byte 4..5: token1 little-endian
Byte 6..7: token2 little-endian
```

### Firmware-Verhalten

Wenn der Token passt:

- Inverter setzt seine RX-ID direkt auf den Zielindex.
- CAN wird neu initialisiert.
- Inverter gilt als initialisiert.

Wenn der Token nicht passt:

- Restore Command wird ignoriert.

Wichtig:

- Restore Command und Token Frame sollen direkt hintereinander gesendet werden.
- Die Firmware verwirft einen offenen Restore Command nach kurzer Zeit, falls der Token Frame nicht folgt.

---

# 10. Empfohlene Entscheidungslogik

Die Quelle gibt folgende Logik vor:

```text
onTokenResponse(currentRxId, token):

    tokenKey = makeTokenKey(token)

    add candidate currentRxId/token

    if tokenKey is known in ownedRxIdByToken:
        targetRxId = ownedRxIdByToken[tokenKey]

        if targetRxId != currentRxId:
            sendRestore(currentRxId, targetRxId, token)
            remove candidates for currentRxId
            return

    if currentRxId has ownerToken:
        ownerToken = ownerTokenByRxId[currentRxId]

        if ownerToken != token:
            sendNormalAssignment(currentRxId, ownerToken)
            remove candidates for currentRxId
            return

    if currentRxId has no ownerToken:
        ownerTokenByRxId[currentRxId] = token
        ownedRxIdByToken[tokenKey] = currentRxId
        sendNormalAssignment(currentRxId, token)
        remove candidates for currentRxId
        return

    if currentRxId ownerToken == token:
        sendNormalAssignment(currentRxId, token)
        remove candidates for currentRxId
        return
```

---

# 11. Timing

Vorgaben bzw. Empfehlungen aus der Quelle:

- `discover` alle **500 ms** senden.
- Candidates nach ungefähr **5 Sekunden** ohne erneute Antwort verwerfen.
- Assignments nicht permanent senden.
- Assignment nur senden, wenn:
  - eine Token Response empfangen wurde, oder
  - ein offener Candidate verarbeitet werden soll.
- Optional: Assignments pro RX-ID auf ungefähr **1 Sekunde** drosseln, um CAN-Bus-Spam zu vermeiden.

---

# 12. Verhalten nach erfolgreicher Zuordnung

- Ein bestätigter Inverter antwortet nicht weiter auf `discover`, solange der Heartbeat aktiv bleibt.
- Die GUI soll `discover` trotzdem weiter senden.
- Dadurch bleiben die Geräte temporär im PersyCAN-Service-Modus.

Fehlt `discover` länger als **2 Sekunden**:

- Geräte vergessen ihren Initialisierungszustand.
- Sobald `discover` erneut startet, antworten sie wieder.

---

# 13. Persistenz und Geräteidentität

Hinweise aus der Quelle:

- Der Token ersetzt die UID **nicht** als langfristige Geräteidentität.
- Für die reine Node Allocation reicht der Token aus.
- Für eine persistente Geräteliste über GUI-Neustarts hinweg soll später zusätzlich die vollständige UID gelesen werden.
- Für reine Runtime-Zuordnung reichen:

```text
Token -> RX-ID
RX-ID -> Owner-Token
```

- Diese Mappings sollen bei CAN-Disconnect gelöscht werden, wenn keine persistente Zuordnung gewünscht ist.
- Soll die Zuordnung einen GUI-Neustart überleben, muss mindestens `Token -> RX-ID` persistent gespeichert werden.

---

# 14. Erwarteter CAN-Trace

## Discovery

```text
01000001  64 69 73 63 6F 76 65 72
```

## Token Response eines Geräts auf Node A

```text
1A00000D  <token0> <token1> <token2> <status>
```

## Normale Bestätigung

```text
0A000000  3A 00 <token0_le> <token1_le> <token2_le>
```

## Direkter Restore eines bekannten Tokens von A zurück nach B

```text
0A000000  2A 03 1D A1 0B 00 00 00
0A000000  3A 00 <token0_le> <token1_le> <token2_le>
```

---

# 15. Kurzreferenz

## Broadcast

```text
0x01000000
00 00 00 00 00 00 00 00 -> disarm PPM
01 00 00 00 00 00 00 00 -> arm PPM
02 00 00 00 00 00 00 00 -> trigger_Errorlog
```

## Discovery

```text
0x01000001
64 69 73 63 6F 76 65 72 -> "discover"
```

## Assignment

```text
current RX-ID
3A 00 <token0_le> <token1_le> <token2_le>
```

## Restore

```text
current RX-ID
2A 03 1D A1 <targetNodeIndex_le> 00 00

direkt danach:

3A 00 <token0_le> <token1_le> <token2_le>
```

## Geräte-Telemetrie

```text
0x1n000003 -> Strom / DC-Spannung / MOSFET-Temperatur
0x1n000004 -> Raw-Signal-Zähler / Puls / Pause
0x1n000005 -> PPWM Status und Signal
0x1n000006 -> Id/Iq Ist- und Zielwerte
0x1n000007 -> Ud/Uq und Fehlerintegrale
0x1n000008 -> MotionCtrlState / ErrorState / Cycle Time / SW Version
0x1n00000B -> Update process request
0x1n00000C -> RPM / Torque
0x1n00000F -> Error log
0x1n000013 -> Motor NTC1k Temperatur
```

---

# 16. Vollständige CAN-ID-Referenztabelle

> `n` ist der Geräte-ID-Nibble. `n = 1` ist für Broadcast reserviert, `n = A…F` adressiert die
> einzelnen Inverter (Node A…F). RX-Basis eines Geräts: `0x0n000000`, TX-Basis: `0x1n......`.
> Die RX-Basis lässt sich aus einer Discovery-Antwort ableiten: `rxBase = responseId & 0x0F000000`.
> Alle Frames haben `DLC = 8`. Faktoren in Klammern sind die in der Quelle genannten Skalierungen.

| CAN-ID | Richtung | Zyklus / Auslöser | Bedeutung | Payload / Signale (16-bit-Wörter, sofern nicht anders angegeben) |
|---|---|---|---|---|
| `0x01000000` | Host → alle (Broadcast Rx) | bei Bedarf | Broadcast-Kommandos an alle Geräte am Bus | `00 00 …` = `disarm PPM`; `01 00 …` = `arm PPM`; `02 00 …` = `trigger_Errorlog` |
| `0x01000001` | Host → alle (Discovery Broadcast / Heartbeat) | alle 500 ms | Discovery-/Heartbeat-Trigger; hält die Inverter im temporären PersyCAN-Modus (Rückfall nach > 2 s ohne `discover`) | `64 69 73 63 6F 76 65 72` = ASCII `discover` |
| `0x0n000000` (n = A…F) | Host → einzelnes Gerät (Inverter Rx) | bei Bedarf | Parameter schreiben; während der Discovery zusätzlich Assignment- und Restore-Frames | RPM-Sollwert: `10 01 <rpm_lo> <rpm_hi> 00 00 00 00` (z. B. `D0 07` = 2000); `AutoArmOnInput`: `13 0D 01 00 00 00 00 00` (nach Powercycle verloren); Assignment: `3A 00 <token0_LE> <token1_LE> <token2_LE>`; Restore: `2A 03 1D A1 <targetNodeIndex_LE> 00 00` |
| `0x0n00000A` (n = A…F) | Host → Gerät (Inverter Rx) | bei Bedarf | `Update process – receive response request` | in der Quelle nicht spezifiziert (`xxx`) |
| `0x0A000000` | Gerät Rx (konkrete Node-ID) | – | Default-/Start-RX-ID; Node A nach Zuteilung | siehe `0x0n000000` |
| `0x0B000000` … `0x0F000000` | Gerät Rx (konkrete Node-IDs) | – | Weitere Node-IDs (B…F), von der GUI in der Node Allocation vergeben | siehe `0x0n000000` |
| `0x1A00000D` … `0x1F00000D` (`0x1n00000D`) | Inverter → Host (Discovery / Token Response) | als Antwort auf `discover` (von noch nicht initialisierten Invertern) | Token-Antwort; RX-Basis = `responseId & 0x0F000000` | Word 0–2: Token (`3 × uint16` little-endian, 48 bit) zum Speichern/Vergleichen/Zurückschreiben; Word 3: Debug/Status (GUI kann ignorieren) |
| `0x1n000003` | Inverter → Host (Tx) | 100 ms | Strom / DC-Spannung / MOSFET-Temperatur | `Iph_Rms` (×10), `I_DC_flt` (×10), `U_DC` (×10, ungefiltert), `Temperature_Mosfet` (×100) |
| `0x1n000004` | Inverter → Host (Tx) | 10 ms | PPM-Rohsignal-Zähler und Puls-/Pausenwerte | `raw_signal_count` (×1), `valid_signal_count` (×1), `raw_pulse_value` (100 ns), `raw_pause_value` (100 ns) |
| `0x1n000005` | Inverter → Host (Tx) | 10 ms | PPWM-Status und -Signal | Val1: `armed:8`, `inverted:8` (bool); Val2: `state:8` (uint8), `valid:8` (bool); Val3: `PPWM_signal_raw` (uint16); Val4: `PPWM_signal` (uint16). PPWM-State: 0 disarmed, 1 invalid, 2 armed, 3 armed_invalid, 4 armed_valid, 5 enabled_always |
| `0x1n000006` | Inverter → Host (Tx) | 100 ms | Feldorientierte Ströme (Ist / Ziel) | `Id_Flt` (×100), `Iq_Flt` (×100), `Id_trgt` (×100), `Iq_trgt` (×100) |
| `0x1n000007` | Inverter → Host (Tx) | 100 ms | Reglerspannungen und Fehlerintegrale | `Ud` (×100), `Uq` (×100), `Ud_error_integral` (×1000), `Uq_error_integral` (×1000) |
| `0x1n000008` | Inverter → Host (Tx) | 100 ms | Motion-Control-State, Error-State, Cycle-Time, SW-Version | `MotionCtrlState` (uint16): `STO=0x0000`, `waitingForSetpoint=0x00F2`, `ControlActive=0x00F3`, `HardFault=0xFF00`; `ErrorState` (uint16): `OK=0`, `OVERCURRENT_PROTECTION=30`, `PPM_INVALID=41`, `PPM_TIMEOUT=42`, `PPM_INVERSION_FAULT=43`, `OVERTEMPERATURE=44`, `PPM_SIGNAL_NOISY=45`; `max_cycle_time` (uint16); `SW Version` (dev:4 patch:4 minor:4 major:4) |
| `0x1n00000B` | Inverter → Host (Tx) | 2 ms | `Update process request` | in der Quelle nicht spezifiziert |
| `0x1n00000C` | Inverter → Host (Tx) | 100 ms | Drehzahl- und Moment-Werte | `RPM_act` (int16), `RPM_target` (int16), `RPM_max` (int16), `TQ_act` (×1000) |
| `0x1n00000F` | Inverter → Host (Tx) | 10 ms bei Fehler / auf Anfrage | `Error log at request or at overcurrent` | in der Quelle nicht spezifiziert |
| `0x1n000013` | Inverter → Host (Tx) | 100 ms | Motor-Temperatur | `Temp motor NTC1k` (×100); `A`, `B`, `C` = 0 (ungenutzt) |
