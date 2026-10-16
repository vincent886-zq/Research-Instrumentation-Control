"""
串口工作线程 — 解析 STM32 AD7606 二进制帧协议。

v3.3 — 批量发射：将数据帧攒成 batch 再发射，避免 10kHz 高频信号压垮 Qt 事件队列。

二进制 18 字节/帧:
  [0xAA] [8×int16 LE 原始 ADC 码] [帧序号 0~255 循环]  → 数据帧 (10kHz)
  [0xBB] [8×int16 LE 编码 Vzero]   [0x00]              → 校准结果帧
"""
import time
import struct

from PyQt5.QtCore import QThread, pyqtSignal

import serial
import serial.tools.list_ports

NUM_CHANNELS = 8
BINARY_FRAME_SIZE = 18
FRAME_SYNC_DATA = 0xAA
FRAME_SYNC_CAL  = 0xBB
MAX_BATCH_SIZE = 200    # 最多攒 200 帧发射一次
MAX_EMIT_MS = 0.020     # 最多等 20ms 发射一次


class SerialWorker(QThread):
    data_received = pyqtSignal(float, object)
    data_batch = pyqtSignal(list)  # 批量数据 [(ts, values), ...]
    cal_result = pyqtSignal(object)
    log_message = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    connection_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self._ser: serial.Serial | None = None
        self._running = False
        self._port = ""
        self._baud = 921600

        # 帧解析
        self._buf = bytearray()
        self._prev_seq = -1
        # 丢帧统计
        self._total_frames = 0
        self._dropped_frames = 0

        # 批量发射缓冲区
        self._batch_buffer = []
        self._last_emit = 0.0

    # ── 公共接口 ──────────────────────────────────────────

    def open(self, port: str, baud: int = 921600):
        if self._ser and self._ser.is_open:
            self._ser.close()
        self._port = port
        self._baud = baud
        self._ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.005,  # 5ms 足够，MCU 10kHz = 180KB/s, 5ms = 900字节
        )
        self._running = True
        self._buf = bytearray()
        self._prev_seq = -1
        self._total_frames = 0
        self._dropped_frames = 0
        self._batch_buffer = []
        self._last_emit = 0.0
        self.start()
        self.connection_changed.emit(True)

    def close(self):
        self._running = False
        self.wait(1000)
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self.connection_changed.emit(False)

    def send(self, text: str):
        if self._ser and self._ser.is_open:
            try:
                self._ser.write(text.encode("utf-8"))
            except serial.SerialException as e:
                self.error_occurred.emit(f"发送失败: {e}")

    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def reset_input_buffer(self):
        if self._ser and self._ser.is_open:
            try:
                self._ser.reset_input_buffer()
                self._buf = bytearray()
                self._prev_seq = -1
                self._total_frames = 0
                self._dropped_frames = 0
                self._batch_buffer = []
                self._last_emit = 0.0
            except serial.SerialException:
                pass

    # ── 线程主循环 ─────────────────────────────────────

    def run(self):
        while self._running:
            try:
                if not self._ser or not self._ser.is_open:
                    time.sleep(0.01)
                    continue

                waiting = self._ser.in_waiting
                if waiting > 0:
                    chunk = self._ser.read(waiting)
                else:
                    chunk = self._ser.read(1024)

                if chunk:
                    self._buf.extend(chunk)
                    self._parse_frames()
                    self._flush_batch_if_needed()
                else:
                    self._flush_batch()  # 空闲时把剩余的发射出去
                    time.sleep(0.001)

            except serial.SerialException as e:
                self.error_occurred.emit(f"串口异常: {e}")
                self.log_message.emit(f"--- 串口断开: {e} ---")
                self._running = False
                break
            except Exception as e:
                self.log_message.emit(f"--- 线程异常: {e} ---")
                self._flush_batch()
        self.connection_changed.emit(False)

    # ── 二进制帧解析 ──────────────────────────────────

    def _parse_frames(self):
        """只检查字节 0 是否为同步字节，不搜索整个缓冲区。

        原理: 正常同步状态下每 18 字节的第一个字节就是 0xAA。
        AD7606 数据字节里碰巧的 0xAA 永远不会被扫描到 (它们在第 2~18 字节内)。
        """
        while len(self._buf) >= BINARY_FRAME_SIZE:
            # 字节 0 必须是同步字节，否则说明失同步了
            if self._buf[0] not in (FRAME_SYNC_DATA, FRAME_SYNC_CAL):
                # 失同步 → 跳 1 字节重试，直到找到对齐的 SYNC
                self._buf = self._buf[1:]
                continue

            # 对齐了，解析一整帧
            frame = bytes(self._buf[:BINARY_FRAME_SIZE])

            if frame[0] == FRAME_SYNC_CAL:
                raw_codes = struct.unpack_from('<8h', frame, 1)
                self._process_cal_frame(raw_codes)
                self._buf = self._buf[BINARY_FRAME_SIZE:]
                continue

            # === 数据帧 ===
            raw_codes = struct.unpack_from('<8h', frame, 1)
            seq = frame[BINARY_FRAME_SIZE - 1]

            # 丢帧统计 (不影响帧是否被接受)
            if self._prev_seq >= 0:
                diff = (seq - self._prev_seq) & 0xFF
                if diff > 1:
                    self._dropped_frames += (diff - 1)
            self._prev_seq = seq
            self._total_frames += 1

            self._process_data_frame(raw_codes)
            self._buf = self._buf[BINARY_FRAME_SIZE:]

    # ── 帧数据处理 ─────────────────────────────────────

    def _process_data_frame(self, raw_codes: tuple):
        """处理数据帧: 原始 int16 → V/I (16 个浮点数) → 加入 batch 缓冲区。"""
        values = [0.0] * 16
        for ch in range(NUM_CHANNELS):
            code = raw_codes[ch]
            voltage = (code / 32768.0) * 5.0
            # HSTS016L: I = (V - 2.5) / 0.625 * 10 = (V - 2.5) * 16
            current = (voltage - 2.5) * 16.0
            values[ch * 2] = voltage
            values[ch * 2 + 1] = current

        ts = time.time()
        self._batch_buffer.append((ts, values))

    def _flush_batch(self):
        """立即发射 batch 缓冲区中的所有数据帧。"""
        if not self._batch_buffer:
            return
        batch = self._batch_buffer
        self._batch_buffer = []
        self._last_emit = time.perf_counter()
        self.data_batch.emit(batch)

    def _flush_batch_if_needed(self):
        """如果 batch 已满或时间已到，就发射。"""
        if not self._batch_buffer:
            return
        if len(self._batch_buffer) >= MAX_BATCH_SIZE:
            self._flush_batch()
        elif self._last_emit > 0 and (time.perf_counter() - self._last_emit) >= MAX_EMIT_MS:
            self._flush_batch()

    def _process_cal_frame(self, raw_codes: tuple):
        """处理校准结果帧: 编码 int16 ×10000 → Vzero[8] float。"""
        vzero = [0.0] * NUM_CHANNELS
        for ch in range(NUM_CHANNELS):
            vzero[ch] = raw_codes[ch] / 10000.0
        self.cal_result.emit(vzero)
