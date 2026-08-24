#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import can
import struct
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from collections import deque
import threading
import queue
import sys

# --- CAN Setup ---
bus = can.interface.Bus(
    channel='PCAN_USBBUS1',    # oder 'PCAN_USBBUS2'
    interface='pcan',
    bitrate=1000000            # an deine Inverter-Einstellung anpassen
)

INVERTER_ID = 0x1A000001  # Beispiel: Inverter A

# --- Kommunikationsthread ---
def can_reader(bus, inv_id, out_q):
    """Liest kontinuierlich vom CAN-Bus und schreibt Temperaturen in Queue."""
    try:
        while True:
            msg = bus.recv(0.1)
            if msg is None:
                continue
            if msg.arbitration_id == inv_id:
                Iph_Rms, I_DC_flt, U_DC, Temp_raw = struct.unpack('<4H', msg.data[:8])
                temp = Temp_raw / 100.0
                out_q.put(temp)
    except Exception as e:
        print(f"[Fehler im CAN-Thread] {e}")
    finally:
        out_q.put(None)  # Signal: Ende

# --- Plot Setup ---
WINDOW = 500  # sichtbare Punkte
temps = deque(maxlen=WINDOW)
samples = deque(maxlen=WINDOW)
data_q = queue.Queue()
sample_idx = 0

fig, ax = plt.subplots()
line, = ax.plot([], [], 'r-', linewidth=1.5, label="Temp MOSFET [°C]")
ax.set_title("Inverter MOSFET Temperatur")
ax.set_xlabel("Sample")
ax.set_ylabel("Temperatur [°C]")
ax.grid(True)
ax.legend()

# --- Update-Funktion für Animation ---
def update(_):
    global sample_idx
    processed = 0
    while processed < 100:
        try:
            v = data_q.get_nowait()
        except queue.Empty:
            break
        if v is None:
            plt.close(fig)
            return line,
        sample_idx += 1
        temps.append(v)
        samples.append(sample_idx)
        processed += 1

    if not temps:
        return line,

    line.set_data(samples, temps)
    ax.set_xlim(samples[0], samples[-1] if samples[-1] > samples[0] else samples[0] + 1)
    ax.relim()               # Achsenlimits neu berechnen
    ax.autoscale_view(scalex=False, scaley=True)  # Y automatisch anpassen
    return line,

# --- Thread starten ---
t = threading.Thread(target=can_reader, args=(bus, INVERTER_ID, data_q), daemon=True)
t.start()

# --- Animation starten ---
ani = FuncAnimation(fig, update, interval=100, blit=False)

def on_close(_event):
    """Sauber beenden, wenn Fenster geschlossen wird."""
    print("\nBeende...")
    bus.shutdown()
    sys.exit(0)

fig.canvas.mpl_connect('close_event', on_close)

print("Starte CAN-Listener... (Fenster schließen zum Beenden)")
plt.show()
