"""In-memory preview store plus asynchronous durable F407 session writer."""
from __future__ import annotations

import csv
import os
from collections import deque
from itertools import islice
from typing import Any, Callable

import numpy as np

from 核心.capture_writer import CaptureWriter
from 核心.f407_protocol import CHANNELS, SAMPLE_RATE_HZ

MAX_POINTS = 3_000_000
NUM_CHANNELS = CHANNELS
CHANNEL_NAMES = [f"CH{i + 1}" for i in range(NUM_CHANNELS)]
CHANNEL_COLORS = ["#E74C3C", "#3498DB", "#2ECC71", "#F39C12", "#9B59B6", "#1ABC9C"]


class DataStore:
    def __init__(self) -> None:
        self.t_data = deque(maxlen=MAX_POINTS)
        self.ch_data = [deque(maxlen=MAX_POINTS) for _ in range(NUM_CHANNELS)]
        self.code_data = [deque(maxlen=MAX_POINTS) for _ in range(NUM_CHANNELS)]
        self.idx_data = deque(maxlen=MAX_POINTS)
        self.timestamp_us_data = deque(maxlen=MAX_POINTS)
        self._first_index: int | None = None
        self._sample_count = 0
        self._writer: CaptureWriter | None = None

    def clear(self) -> None:
        self.t_data.clear()
        self.idx_data.clear()
        self.timestamp_us_data.clear()
        for values in self.ch_data + self.code_data:
            values.clear()
        self._first_index = None
        self._sample_count = 0

    def add_batch(self, batch: list[dict[str, Any]]) -> bool:
        prepared: list[dict[str, Any]] = []
        for sample in batch:
            sample_index = int(sample["sample_index"])
            if self._first_index is None:
                self._first_index = sample_index
            time_s = (sample_index - self._first_index) / SAMPLE_RATE_HZ
            sample["time_s"] = time_s
            self.t_data.append(time_s)
            self.idx_data.append(sample_index)
            self.timestamp_us_data.append(int(sample["timestamp_us"]))
            for channel in range(NUM_CHANNELS):
                self.ch_data[channel].append(float(sample["volts"][channel]))
                self.code_data[channel].append(int(sample["codes"][channel]))
            self._sample_count += 1
            prepared.append(sample)
        return self._writer.enqueue_samples(prepared) if self._writer and prepared else True

    def start_streaming(self, csv_path: str, metadata: dict[str, Any]) -> None:
        if self._writer:
            raise RuntimeError("a capture writer is already active")
        self._writer = CaptureWriter(csv_path, metadata)

    def enqueue_raw(self, raw_bytes: bytes) -> bool:
        return self._writer.enqueue_raw(raw_bytes) if self._writer else True

    def stop_streaming(self, final_metadata: dict[str, Any], valid: bool) -> dict[str, Any] | None:
        if not self._writer:
            return None
        writer = self._writer
        self._writer = None
        return writer.close(final_metadata, valid)

    def is_streaming(self) -> bool:
        return self._writer is not None

    def count(self) -> int:
        return len(self.t_data)

    def latest(self):
        if not self.t_data:
            return None
        return self.t_data[-1], [values[-1] for values in self.ch_data]

    def get_window(self, t0: float, t1: float):
        if not self.t_data:
            return np.array([], dtype=np.float64), np.zeros((NUM_CHANNELS, 0), dtype=np.float64)
        times = np.fromiter(self.t_data, dtype=np.float64)
        start = int(np.searchsorted(times, t0, side="left"))
        end = int(np.searchsorted(times, t1, side="right"))
        count = max(0, end - start)
        return times[start:end], np.array([
            np.fromiter(islice(values, start, end), dtype=np.float64, count=count)
            for values in self.ch_data
        ])

    def to_numpy(self):
        return np.fromiter(self.t_data, dtype=np.float64), np.array([
            np.fromiter(values, dtype=np.float64) for values in self.ch_data
        ])

    def export_csv(self, path: str, progress_callback: Callable[[int, int], None] | None = None) -> int:
        """Export a stopped in-memory capture with periodic progress updates."""
        count = self.count()
        if not count:
            return 0
        temporary_path = f"{path}.part"
        progress_step = max(1_000, count // 200)
        if progress_callback:
            progress_callback(0, count)
        with open(temporary_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time_s", "sample_index", "timestamp_us", *[f"CH{i + 1}_raw" for i in range(NUM_CHANNELS)], *[f"CH{i + 1}_V" for i in range(NUM_CHANNELS)]])
            columns = zip(
                self.t_data, self.idx_data, self.timestamp_us_data,
                *self.code_data, *self.ch_data, strict=True,
            )
            for completed, values in enumerate(columns, start=1):
                time_s, sample_index, timestamp_us, *channel_values = values
                raw_values = channel_values[:NUM_CHANNELS]
                voltage_values = channel_values[NUM_CHANNELS:]
                writer.writerow([
                    f"{time_s:.7f}", sample_index, timestamp_us,
                    *raw_values, *(f"{voltage:.8f}" for voltage in voltage_values),
                ])
                if progress_callback and (completed == count or completed % progress_step == 0):
                    progress_callback(completed, count)
        os.replace(temporary_path, path)
        return count
