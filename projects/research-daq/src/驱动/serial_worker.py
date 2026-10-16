"""USB CDC worker for the F407/AD7606 protocol v1."""
from __future__ import annotations

import threading
import time
from typing import Callable

from PyQt5.QtCore import QThread, pyqtSignal
import serial

from 核心.f407_protocol import FrameParser, SAMPLE_RATE_HZ


MAX_EMIT_BATCH = 200
MAX_EMIT_INTERVAL_S = 0.02


class SerialWorker(QThread):
    data_batch = pyqtSignal(list)
    log_message = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    connection_changed = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._ser: serial.Serial | None = None
        self._running = False
        self._parser = FrameParser()
        self._raw_sink: Callable[[bytes], bool] | None = None
        self._lock = threading.Lock()
        self._reset_requested = False
        self._capture_active = False
        self._begin_capture_request: tuple[Callable[[bytes], bool] | None, threading.Event, dict] | None = None
        self._stats: dict[str, int | float | None] = {}
        self._reset_stats_locked()

    def _reset_stats_locked(self) -> None:
        self._stats = {
            "valid_frames": 0,
            "crc_errors": 0,
            "protocol_errors": 0,
            "discarded_bytes": 0,
            "sequence_gaps": 0,
            "missing_samples": 0,
            "duplicates": 0,
            "reordered_frames": 0,
            "device_drop_total": 0,
            "timestamp_delta_min_us": None,
            "timestamp_delta_max_us": None,
            "last_sample_index": None,
            "last_timestamp_us": None,
            "last_frame_monotonic": None,
        }

    def open(self, port: str, baud: int = 921600) -> None:
        if self._ser and self._ser.is_open:
            self.close()
        self._ser = serial.Serial(
            port=port, baudrate=baud, bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
            timeout=0.05, write_timeout=1.0,
        )
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()
        self.reset_protocol()
        self._running = True
        self.start()
        self.connection_changed.emit(True)

    def close(self) -> None:
        self._running = False
        self.wait(1500)
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
            except serial.SerialException:
                pass
        self._ser = None
        self.connection_changed.emit(False)

    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def send_command(self, command: str) -> bool:
        if not self.is_connected():
            return False
        try:
            assert self._ser is not None
            self._ser.write((command.strip() + "\r\n").encode("ascii"))
            self._ser.flush()
            return True
        except serial.SerialException as exc:
            self.error_occurred.emit(f"USB CDC 发送失败: {exc}")
            return False

    def begin_capture(self, raw_sink: Callable[[bytes], bool] | None, timeout_s: float = 1.0) -> bool:
        """Atomically arm recording and issue ``start`` from the serial thread.

        A GUI-thread ``set_raw_sink(); send_command('start')`` sequence can race
        an already-blocked USB read: its returned bytes would reach the CSV parser
        with the old (None) raw sink.  Queueing this request makes the reset, raw
        sink installation, and command write occur between two reads.
        """
        if not self.is_connected():
            return False
        done = threading.Event()
        result: dict[str, bool] = {"ok": False}
        with self._lock:
            if self._begin_capture_request is not None:
                return False
            self._begin_capture_request = (raw_sink, done, result)
        return bool(done.wait(timeout_s) and result["ok"])

    def reset_protocol(self) -> None:
        with self._lock:
            self._reset_requested = True
            self._reset_stats_locked()
        if self._ser and self._ser.is_open:
            try:
                self._ser.reset_input_buffer()
            except serial.SerialException:
                pass

    def set_raw_sink(self, sink: Callable[[bytes], bool] | None) -> None:
        with self._lock:
            self._raw_sink = sink

    def _service_begin_capture_request(self) -> None:
        """Run only in the serial thread, before it starts its next read."""
        with self._lock:
            request = self._begin_capture_request
            self._begin_capture_request = None
        if request is None:
            return

        raw_sink, done, result = request
        try:
            if not self._ser or not self._ser.is_open:
                raise serial.SerialException("USB CDC is not open")
            # Remove all pre-session bytes before the lower device is told to run.
            self._ser.reset_input_buffer()
            self._parser.reset()
            with self._lock:
                self._reset_requested = False
                self._reset_stats_locked()
                self._raw_sink = raw_sink
                self._capture_active = True
            self._ser.write(b"start\r\n")
            self._ser.flush()
            result["ok"] = True
        except serial.SerialException as exc:
            with self._lock:
                self._capture_active = False
                self._raw_sink = None
            self.error_occurred.emit(f"USB CDC 启动采集失败: {exc}")
        finally:
            done.set()

    def snapshot_stats(self) -> dict[str, int | float | None]:
        with self._lock:
            result = dict(self._stats)
            result["crc_errors"] = self._parser.crc_errors
            result["protocol_errors"] = self._parser.protocol_errors
            result["discarded_bytes"] = self._parser.discarded_bytes
            return result

    def integrity_ok(self) -> bool:
        stats = self.snapshot_stats()
        return bool(stats["valid_frames"] and not stats["crc_errors"] and
                    not stats["protocol_errors"] and not stats["sequence_gaps"] and
                    not stats["duplicates"] and not stats["reordered_frames"] and
                    not stats["device_drop_total"])

    def _apply_frame_stats(self, sample_index: int, timestamp_us: int, device_drop_total: int) -> None:
        with self._lock:
            previous_index = self._stats["last_sample_index"]
            previous_timestamp = self._stats["last_timestamp_us"]
            if previous_index is not None:
                delta = (sample_index - int(previous_index)) & 0xFFFFFFFF
                if delta == 0:
                    self._stats["duplicates"] = int(self._stats["duplicates"]) + 1
                elif delta == 1:
                    timestamp_delta = (timestamp_us - int(previous_timestamp)) & 0xFFFFFFFF
                    old_min = self._stats["timestamp_delta_min_us"]
                    old_max = self._stats["timestamp_delta_max_us"]
                    self._stats["timestamp_delta_min_us"] = timestamp_delta if old_min is None else min(int(old_min), timestamp_delta)
                    self._stats["timestamp_delta_max_us"] = timestamp_delta if old_max is None else max(int(old_max), timestamp_delta)
                elif delta < 0x80000000:
                    self._stats["sequence_gaps"] = int(self._stats["sequence_gaps"]) + 1
                    self._stats["missing_samples"] = int(self._stats["missing_samples"]) + delta - 1
                else:
                    self._stats["reordered_frames"] = int(self._stats["reordered_frames"]) + 1
            self._stats["valid_frames"] = int(self._stats["valid_frames"]) + 1
            self._stats["device_drop_total"] = max(int(self._stats["device_drop_total"]), device_drop_total)
            self._stats["last_sample_index"] = sample_index
            self._stats["last_timestamp_us"] = timestamp_us
            self._stats["last_frame_monotonic"] = time.monotonic()

    def run(self) -> None:
        pending: list[dict] = []
        last_emit = time.monotonic()
        self._parser.reset()
        while self._running:
            try:
                if not self._ser or not self._ser.is_open:
                    time.sleep(0.01)
                    continue
                # A begin request is handled between reads, never while a chunk
                # still carries a snapshot of the old raw sink.
                self._service_begin_capture_request()
                with self._lock:
                    if self._reset_requested:
                        self._parser.reset()
                        self._reset_requested = False
                    capture_active = self._capture_active
                    raw_sink = self._raw_sink if capture_active else None
                waiting = self._ser.in_waiting
                chunk = self._ser.read(waiting if waiting else 4096)
                if not chunk:
                    if pending:
                        self.data_batch.emit(pending)
                        pending = []
                        last_emit = time.monotonic()
                    continue
                # Bytes from an old/in-flight read are intentionally discarded
                # until begin_capture has armed the session in this same thread.
                if not capture_active:
                    continue
                if raw_sink is not None and not raw_sink(chunk):
                    self.log_message.emit("!!! 原始字节落盘队列已满，本次会话将标记为无效")
                for frame in self._parser.feed(chunk):
                    self._apply_frame_stats(frame.sample_index, frame.timestamp_us, frame.device_drop_total)
                    pending.append({
                        "sample_index": frame.sample_index,
                        "timestamp_us": frame.timestamp_us,
                        "codes": frame.codes,
                        "volts": frame.volts,
                        "device_drop_total": frame.device_drop_total,
                        "pc_received_monotonic": time.monotonic(),
                        "sample_rate_hz": SAMPLE_RATE_HZ,
                    })
                now = time.monotonic()
                if pending and (len(pending) >= MAX_EMIT_BATCH or now - last_emit >= MAX_EMIT_INTERVAL_S):
                    self.data_batch.emit(pending)
                    pending = []
                    last_emit = now
            except serial.SerialException as exc:
                self.error_occurred.emit(f"USB CDC 接收异常: {exc}")
                self._running = False
            except Exception as exc:
                self.error_occurred.emit(f"协议解析异常: {exc}")
                self._running = False
        if pending:
            self.data_batch.emit(pending)
        self.connection_changed.emit(False)
