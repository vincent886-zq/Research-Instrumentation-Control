#!/usr/bin/env python3
"""Read-only EC11 USB handwheel protocol tester; it cannot command a machine."""

from __future__ import annotations

import argparse
import threading
import time

import serial


def heartbeat(port: serial.Serial, stop: threading.Event) -> None:
    port.write(b"HELLO\n")
    while not stop.wait(0.5):
        port.write(b"PING\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="USB CDC port, e.g. COM21")
    parser.add_argument("--baud", type=int, default=115200,
                        help="CDC compatibility setting; USB transport ignores it")
    parser.add_argument("--debug", action="store_true",
                        help="request one raw GPIO snapshot after opening")
    args = parser.parse_args()

    stop = threading.Event()
    with serial.Serial(args.port, args.baud, timeout=0.2, write_timeout=1.0) as port:
        print(f"Opened {args.port}. Input-only handwheel test; no CNC motion is possible.")
        worker = threading.Thread(target=heartbeat, args=(port, stop), daemon=True)
        worker.start()
        if args.debug:
            port.write(b"DEBUG?\n")
        port.write(b"STATUS?\n")
        try:
            while True:
                raw = port.readline()
                if raw:
                    text = raw.decode("ascii", errors="replace").rstrip("\r\n")
                    print(time.strftime("%H:%M:%S"), text)
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            stop.set()
            worker.join(timeout=1.0)
            try:
                port.write(b"STATE,CONNECTED,0\n")
            except serial.SerialException:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
