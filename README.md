# CAN Temperature

Schlankes Python-Package zum Auslesen von Temperaturtelegrammen eines Inverters
ueber CAN. Die Bibliothek kapselt nur Buszugriff und Dekodierung. Plot,
CSV-Aufzeichnung und Yokogawa-Automatisierung bleiben Aufgabe der aufrufenden
Anwendung.

## Installation

Das Package benoetigt Python 3.10 oder neuer, `python-can` und unter Windows den
Treiber des verwendeten CAN-Adapters.

```powershell
python -m pip install -e .
```

## Verwendung mit PCAN

```python
from can_temperature import TemperatureSensor


with TemperatureSensor(
    arbitration_id=0x1A000003,
    interface="pcan",
    channel="PCAN_USBBUS1",
    bitrate=1_000_000,
) as sensor:
    temperature_celsius = sensor.read_temperature(timeout=1.0)
    print(f"{temperature_celsius:.2f} °C")
```

Wird innerhalb des Timeouts kein passendes Extended-CAN-Frame empfangen, wird
`TemperatureTimeoutError` ausgelöst. Ein passendes, aber zu kurzes Telegramm
führt zu `InvalidTemperatureFrameError`.

## Vorhandenen CAN-Bus verwenden

Eine Messanwendung kann einen bereits geöffneten `python-can`-Bus übergeben.
Die Bibliothek schließt einen solchen Bus nicht selbst:

```python
import can

from can_temperature import TemperatureSensor


with can.Bus(
    interface="pcan",
    channel="PCAN_USBBUS1",
    bitrate=1_000_000,
) as bus:
    sensor = TemperatureSensor(arbitration_id=0x1A000003, bus=bus)
    temperature_celsius = sensor.read_temperature()
```

## Decoder ohne Hardware verwenden

```python
from can_temperature import decode_temperature


payload = bytes.fromhex("64 00 C8 00 2C 01 88 13")
assert decode_temperature(payload) == 50.0
```

## Aktuelle Protokollannahmen

- bekannte Extended-IDs: `0x1A000001` und `0x1A000003`
- Payload: mindestens 8 Byte
- Temperatur: Bytes 6 und 7 als Little-Endian `uint16`
- Skalierung: `0.01 °C/Bit`
- CAN-Bitrate der vorhandenen Skripte: `1_000_000 bit/s`

Die physische Zuordnung der beiden IDs, Vorzeichenbehaftung und Skalierung
muessen noch gegen die Herstellerdokumentation verifiziert werden. Deshalb ist
die Arbitration-ID beim Erzeugen des Sensors immer explizit anzugeben.

## Tests

Die Tests benoetigen keine angeschlossene CAN-Hardware:

```powershell
python -m unittest discover -s tests -v
```

Die technischen Erkenntnisse aus den Ursprungsskripten sind unter
`docs/CAN_Temperaturauswertung.md` zusammengefasst.
