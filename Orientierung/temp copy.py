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
    channel='PCAN_USBBUS1',    # ggf. 'PCAN_USBBUS2' anpassen
    interface='pcan',
    bitrate=1000000            # muss zu deinem µC (1 Mbit/s) passen
)

WEIGHT_ID = 0x003  # deine CAN-ID aus dem C-Code (Standard 11-bit)

# --- Kommunikationsthread ---
def can_reader(bus, can_id, out_q):
    """Liest kontinuierlich vom CAN-Bus und schreibt Gewicht in die Queue."""
    try:
        while True:
            msg = bus.recv(0.1)
            if msg is None:
                continue

            # Nur Frames mit unserer ID auswerten
            if msg.arbitration_id == can_id:
                # Erste 4 Bytes: signed 32-bit, Big-Endian -> Gewicht in Gramm
                (weight_g,) = struct.unpack('>i', msg.data[0:4])
                out_q.put(weight_g)
    except Exception as e:
        print(f"[Fehler im CAN-Thread] {e}")
    finally:
        out_q.put(None)  # Signal: Ende

# --- Plot Setup ---
WINDOW = 500  # sichtbare Punkte
weights = deque(maxlen=WINDOW)
samples = deque(maxlen=WINDOW)
data_q = queue.Queue()
sample_idx = 0

fig, ax = plt.subplots()
line, = ax.plot([], [], '-', linewidth=1.5, label="Gewicht [g]")
ax.set_title("Gewichtsverlauf vom HX711 (über CAN)")
ax.set_xlabel("Sample")
ax.set_ylabel("Gewicht [g]")
ax.grid(True)
ax.legend()

# --- Update-Funktion für Animation ---
def update(_):
    global sample_idx
    processed = 0

    # Mehrere Werte pro Frame aus der Queue holen (bessere Performance)
    while processed < 100:
        try:
            v = data_q.get_nowait()
        except queue.Empty:
            break

        if v is None:
            plt.close(fig)
            return line,

        sample_idx += 1
        weights.append(v)
        samples.append(sample_idx)
        processed += 1

    if not weights:
        return line,

    line.set_data(samples, weights)
    # X-Achse: rollendes Fenster
    ax.set_xlim(samples[0], samples[-1] if samples[-1] > samples[0] else samples[0] + 1)
    # Y-Achse automatisch an Daten anpassen
    ax.relim()
    ax.autoscale_view(scalex=False, scaley=True)

    return line,

# --- Thread starten ---
t = threading.Thread(target=can_reader, args=(bus, WEIGHT_ID, data_q), daemon=True)
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
