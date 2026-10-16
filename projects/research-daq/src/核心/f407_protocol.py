"""Protocol v1 shared with the F407 AD7606 displacement firmware."""
from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Iterable

SYNC = b"\xA5\x5A"
FRAME_SIZE = 32
VERSION = 1
TYPE_SAMPLE = 1
CHANNELS = 6
SAMPLE_RATE_HZ = 10_000


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True)
class SampleFrame:
    sample_index: int
    timestamp_us: int
    codes: tuple[int, int, int, int, int, int]
    device_drop_total: int

    @property
    def volts(self) -> tuple[float, float, float, float, float, float]:
        return tuple(code * 5.0 / 32768.0 for code in self.codes)


class FrameParser:
    """Self-resynchronising parser. A frame is accepted only after CRC passes."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.crc_errors = 0
        self.protocol_errors = 0
        self.discarded_bytes = 0

    def reset(self) -> None:
        self.buffer.clear()
        self.crc_errors = 0
        self.protocol_errors = 0
        self.discarded_bytes = 0

    def feed(self, data: bytes) -> Iterable[SampleFrame]:
        self.buffer.extend(data)
        frames: list[SampleFrame] = []
        while True:
            position = self.buffer.find(SYNC)
            if position < 0:
                # Preserve a possible first byte of the two-byte sync word.
                keep = 1 if self.buffer.endswith(SYNC[:1]) else 0
                self.discarded_bytes += len(self.buffer) - keep
                if keep:
                    self.buffer[:] = self.buffer[-1:]
                else:
                    self.buffer.clear()
                break
            if position:
                self.discarded_bytes += position
                del self.buffer[:position]
            if len(self.buffer) < FRAME_SIZE:
                break

            candidate = bytes(self.buffer[:FRAME_SIZE])
            if (candidate[2] != VERSION or candidate[3] != TYPE_SAMPLE or
                    candidate[4] != CHANNELS or candidate[5] != 0):
                self.protocol_errors += 1
                del self.buffer[0]
                continue
            expected_crc = struct.unpack_from("<H", candidate, 30)[0]
            if crc16_ccitt_false(candidate[:30]) != expected_crc:
                self.crc_errors += 1
                del self.buffer[0]
                continue

            sample_index, timestamp_us, *rest = struct.unpack_from("<II6hI", candidate, 6)
            codes = tuple(rest[:6])
            device_drop_total = rest[6]
            frames.append(SampleFrame(sample_index, timestamp_us, codes, device_drop_total))
            del self.buffer[:FRAME_SIZE]
        return frames
