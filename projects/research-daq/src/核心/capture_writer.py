"""Durable background writer for a single F407 acquisition session."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import queue
import threading
import time
from typing import Any


class CaptureWriter:
    def __init__(self, csv_path: str, metadata: dict[str, Any], queue_size: int = 1024) -> None:
        self.csv_path = Path(csv_path)
        self.raw_path = self.csv_path.with_suffix(".raw.bin")
        self.meta_path = self.csv_path.with_suffix(".metadata.json")
        self._metadata = dict(metadata)
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=queue_size)
        self._thread = threading.Thread(target=self._run, name="capture-writer", daemon=True)
        self._closed = False
        self._failed = False
        self._failure_reason = ""
        self.rows_written = 0
        self.raw_bytes_written = 0
        self._thread.start()

    def enqueue_samples(self, samples: list[dict]) -> bool:
        return self._enqueue("samples", samples)

    def enqueue_raw(self, raw_bytes: bytes) -> bool:
        return self._enqueue("raw", bytes(raw_bytes))

    def _enqueue(self, kind: str, value: Any) -> bool:
        if self._closed or self._failed:
            return False
        try:
            self._queue.put_nowait((kind, value))
            return True
        except queue.Full:
            self._failed = True
            self._failure_reason = "writer_queue_overflow"
            return False

    def close(self, final_metadata: dict[str, Any], valid: bool) -> dict[str, Any]:
        if not self._closed:
            self._closed = True
            self._queue.put(("close", (final_metadata, valid)))
            self._thread.join(timeout=30)
            if self._thread.is_alive():
                self._failed = True
                self._failure_reason = "writer_close_timeout"
        return {
            "rows_written": self.rows_written,
            "raw_bytes_written": self.raw_bytes_written,
            "writer_failed": self._failed,
            "writer_failure_reason": self._failure_reason,
            "csv_path": str(self.csv_path),
            "raw_path": str(self.raw_path),
            "metadata_path": str(self.meta_path),
            "raw_evidence_ok": getattr(self, "raw_evidence_ok", False),
            "capture_complete": getattr(self, "capture_complete", False),
        }

    @staticmethod
    def _flush_sync(handle) -> None:
        handle.flush()
        os.fsync(handle.fileno())

    def _run(self) -> None:
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_part = Path(str(self.csv_path) + ".part")
        raw_part = Path(str(self.raw_path) + ".part")
        meta_part = Path(str(self.meta_path) + ".part")
        last_sync = time.monotonic()
        close_request: tuple[dict[str, Any], bool] | None = None
        try:
            # Make a recoverable session declaration before receiving any bytes.
            with open(meta_part, "w", encoding="utf-8") as meta_file:
                initial_metadata = dict(self._metadata)
                initial_metadata.update({
                    "capture_status": "RECORDING",
                    "writer_started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
                json.dump(initial_metadata, meta_file, ensure_ascii=False, indent=2)
                self._flush_sync(meta_file)
            with open(csv_part, "w", newline="", encoding="utf-8") as csv_file, open(raw_part, "wb") as raw_file:
                writer = csv.writer(csv_file)
                writer.writerow([
                    "time_s", "sample_index", "timestamp_us", "device_drop_total",
                    "CH1_raw", "CH2_raw", "CH3_raw", "CH4_raw", "CH5_raw", "CH6_raw",
                    "CH1_V", "CH2_V", "CH3_V", "CH4_V", "CH5_V", "CH6_V",
                ])
                while True:
                    kind, value = self._queue.get()
                    if kind == "samples":
                        for sample in value:
                            writer.writerow([
                                f"{sample['time_s']:.7f}", sample["sample_index"], sample["timestamp_us"], sample["device_drop_total"],
                                *sample["codes"], *(f"{v:.8f}" for v in sample["volts"]),
                            ])
                            self.rows_written += 1
                    elif kind == "raw":
                        raw_file.write(value)
                        self.raw_bytes_written += len(value)
                    elif kind == "close":
                        self._flush_sync(csv_file)
                        self._flush_sync(raw_file)
                        close_request = value
                        break
                    if time.monotonic() - last_sync >= 1.0:
                        self._flush_sync(csv_file)
                        self._flush_sync(raw_file)
                        last_sync = time.monotonic()
            if close_request is None:
                return
            final_metadata, requested_valid = close_request
            metadata = dict(self._metadata)
            metadata.update(final_metadata)
            minimum_raw_bytes = int(final_metadata.get("raw_minimum_frame_bytes", 0))
            self.raw_evidence_ok = self.raw_bytes_written >= minimum_raw_bytes
            metadata.update({
                "rows_written": self.rows_written,
                "raw_bytes_written": self.raw_bytes_written,
                "raw_minimum_frame_bytes": minimum_raw_bytes,
                "raw_evidence_ok": self.raw_evidence_ok,
                "writer_failed": self._failed,
                "writer_failure_reason": self._failure_reason,
                "finalized_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            valid = bool(requested_valid and not self._failed and self.raw_evidence_ok)
            self.capture_complete = valid
            metadata["capture_status"] = "COMPLETE" if valid else "INVALID"
            with open(meta_part, "w", encoding="utf-8") as meta_file:
                json.dump(metadata, meta_file, ensure_ascii=False, indent=2)
                self._flush_sync(meta_file)
            if valid:
                os.replace(csv_part, self.csv_path)
                os.replace(raw_part, self.raw_path)
                os.replace(meta_part, self.meta_path)
        except Exception as exc:
            self._failed = True
            self._failure_reason = f"writer_exception:{exc}"
