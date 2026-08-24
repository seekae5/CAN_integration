#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import re
import sys
import threading
import time
from collections import deque
from queue import Queue, Empty

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import serial

# Regex: erste Zahl (int oder float, optional mit Minus) NACH einem Doppelpunkt
PATTERN = re.compile(r":\s*(-?\d+(?:\.\d+)?)")

def parse_args():
    p = argparse.ArgumentParser(
        description="Öffnet eine serielle Verbindung (115200 Baud), liest Textzeilen, "
                    "extrahiert die Zahl nach ':' und plottet sie live."
    )
    p.add_argument("--port", required=True, help="Serieller Port (z. B. COM3, /dev/ttyUSB0, /dev/ttyACM0, /dev/tty.usbserial-XXXX)")
    p.add_argument("--baud", type=int, default=115200, help="Baudrate (Default: 115200)")
    p.add_argument("--timeout", type=float, default=0.2, help="Serial-Timeout in s (Default: 0.2)")
    p.add_argument("--window", type=int, default=500, help="Anzahl Punkte im Plotfenster (Default: 500)")
    p.add_argument("--title", default="Gewicht in gramm", help="Plot-Titel")
    p.add_argument("--show-lines", action="store_true", help="Eingehende Zeilen zusätzlich auf der Konsole anzeigen")
    return p.parse_args()

def serial_reader(port, baud, timeout, out_queue, show_lines):
    """
    Liest zeilenweise vom COM-Port, extrahiert Zahlen nach ':'
    und legt sie als float in die Queue. None signalisiert Fehler/Ende.
    """
    try:
        with serial.Serial(port=port, baudrate=baud, timeout=timeout) as ser:
            # Manche Boards resetten beim Öffnen → kurzen Moment warten
            time.sleep(0.2)
            buffer = bytearray()
            while True:
                chunk = ser.read(256)
                if not chunk:
                    continue
                buffer.extend(chunk)

                # Zeilenweise verarbeiten
                while True:
                    nl = buffer.find(b"\n")
                    if nl == -1:
                        break
                    line = buffer[:nl+1]
                    del buffer[:nl+1]

                    try:
                        text = line.decode("utf-8", errors="replace").strip()
                    except Exception:
                        continue

                    if show_lines:
                        print(text)

                    m = PATTERN.search(text)
                    if m:
                        try:
                            value = float(m.group(1))
                            out_queue.put(value)
                        except ValueError:
                            pass
    except serial.SerialException as e:
        print(f"[Fehler] Konnte Port nicht öffnen/lesen: {e}", file=sys.stderr)
        out_queue.put(None)  # Beenden signalisieren

def main():
    args = parse_args()

    data_q = Queue()
    t = threading.Thread(
        target=serial_reader,
        args=(args.port, args.baud, args.timeout, data_q, args.show_lines),
        daemon=True,
    )
    t.start()

    # Ringpuffer (rollendes Fenster)
    xs = deque(maxlen=args.window)
    ys = deque(maxlen=args.window)
    idx = 0

    fig, ax = plt.subplots()
    line, = ax.plot([], [], linewidth=1.5)  # EIN Artist (wichtig für relim/autoscale)
    ax.set_title(args.title)
    ax.set_xlabel("Sample")
    ax.set_ylabel("Wert")
    ax.grid(True)

    def update(_frame):
        nonlocal idx
        
        # Mehrere Messwerte pro Frame verarbeiten
        processed = 0
        while processed < 200:
            try:
                v = data_q.get_nowait()
            except Empty:
                break

            # None = Reader hat beendet/Fehler
            if v is None:
                plt.close(fig)
                return line,

            xs.append(idx)
            ys.append(v)
            idx += 1
            processed += 1

        if not xs:
            return line,

        # Daten in den Line-Artist schreiben
        line.set_data(list(xs), list(ys))

        # X als rollendes Fenster auf den sichtbaren Bereich setzen
        ax.set_xlim(xs[0], xs[-1] if xs[-1] > xs[0] else xs[0] + 1)

        # Y **automatisch**: aktuelle Datenlimits neu berechnen und skalieren
        ax.relim()                         # Datenlimits aus Artists neu bestimmen
        ax.autoscale_view(scalex=False,    # X lassen wir wie oben gesetzt
                          scaley=True)     # Y automatisch anpassen

        # Falls alle Y identisch sind: etwas Padding hinzufügen, um flaches Bild zu vermeiden
        y_min = 0
        y_max = 20000
        if y_min == y_max:
            pad = 1.0 if y_min == 0 else abs(y_min) * 0.1
            ax.set_ylim(0, 60000)

        return line,

    # WICHTIG: blit=False, damit Achsen vollständig neu gezeichnet werden → Autoskalierung greift
    ani = FuncAnimation(fig, update, interval=50, blit=False)

    try:
        plt.show()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
