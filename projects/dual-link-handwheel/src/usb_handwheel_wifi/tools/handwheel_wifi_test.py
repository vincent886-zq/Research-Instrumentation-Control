#!/usr/bin/env python3
"""Observe-only ESP-01S handwheel TCP smoke test; it never sends ARM."""

from __future__ import annotations

import argparse
import socket
import time


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.4.1")
    parser.add_argument("--port", type=int, default=8266)
    parser.add_argument("--timeout", type=float, default=4.0)
    args = parser.parse_args()

    with socket.create_connection((args.host, args.port), timeout=args.timeout) as sock:
        sock.settimeout(0.25)
        # HELLO creates a new unarmed session; this script deliberately never
        # transmits ARM, EVENT, CONFIG, or a motion-related command.
        sock.sendall(b"HELLO\nSTATUS?\nHEALTH?\nPING\n")
        deadline = time.monotonic() + args.timeout
        received = bytearray()
        while time.monotonic() < deadline:
            try:
                block = sock.recv(1024)
            except TimeoutError:
                continue
            if not block:
                break
            received.extend(block)
            while b"\n" in received:
                line, _, received = received.partition(b"\n")
                print(line.rstrip(b"\r").decode("ascii", errors="replace"))


if __name__ == "__main__":
    main()
