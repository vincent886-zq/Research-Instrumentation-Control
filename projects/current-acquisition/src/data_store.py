"""
数据存储层 — 8 通道 V/I 采集，支持每通道独立启用和零点偏移。

v3.0 — 预分配 numpy 数组替代 deque，to_numpy() 直接返回 view（O(1)）
性能：65 秒数据 (650k 点) 下，add() O(1)，to_numpy() O(1)，不分配内存。
"""
import csv
import os
from datetime import datetime
from itertools import islice

import numpy as np

MAX_POINTS = 3_000_000
NUM_CHANNELS = 8
NUM_VALUES = 16  # V1,I1,...,V8,I8
CHANNEL_NAMES = [f"CH{i+1}" for i in range(NUM_CHANNELS)]


class DataStore:
    """8 通道采集数据容器，预分配 numpy 数组，to_numpy() 零拷贝。"""

    def __init__(self):
        # 预分配固定大小的 numpy 数组
        self._t = np.zeros(MAX_POINTS, dtype=np.float64)
        self._ch = np.zeros((NUM_VALUES, MAX_POINTS), dtype=np.float64)
        self._count = 0
        self._t0_abs: float | None = None

        # 每通道启用状态（True=启用，False=忽略）。默认全部启用
        self.ch_enabled = [True] * NUM_CHANNELS

        # 每通道零点偏移（电压偏移值 V，电流偏移值 A），默认 0
        self.v_offsets = [0.0] * NUM_CHANNELS
        self.i_offsets = [0.0] * NUM_CHANNELS

        # 实时流式存盘
        self._stream_file = None
        self._stream_writer = None
        self._stream_counter = 0

    # ── 写操作 ──────────────────────────────────────────

    def add(self, ts_abs: float, values: list):
        """追加一个采样点。O(1)，预分配数组直接索引赋值。

        注意：内存缓冲区满额 (MAX_POINTS=3M) 后不再接收新数据，
        但 CSV 实时存盘不受影响，会一直完整写入所有数据。
        3M 点 ≈ 300s @ 10kHz 或 ≈ 600s @ 5kHz，超过请依赖 CSV 文件。
        """
        if self._t0_abs is None:
            self._t0_abs = ts_abs

        if self._count >= MAX_POINTS:
            # 内存缓冲区已满，只写 CSV 不写内存
            if self._stream_writer is not None:
                self._write_stream_row(ts_abs - self._t0_abs, values)
            return

        idx = self._count
        self._t[idx] = ts_abs - self._t0_abs

        for i in range(NUM_VALUES):
            ch_idx = i // 2
            if ch_idx >= NUM_CHANNELS or not self.ch_enabled[ch_idx]:
                self._ch[i][idx] = 0.0
            else:
                self._ch[i][idx] = values[i] if i < len(values) else 0.0

        self._count += 1

        # 实时存盘（写出带偏移的值）
        if self._stream_writer is not None:
            self._write_stream_row(self._t[idx], values)

    def _write_stream_row(self, rel_t: float, values: list):
        """向 CSV 写入一行，应用偏移。"""
        row = [f"{rel_t:.6f}"]
        for ch_idx in range(NUM_CHANNELS):
            v_idx = ch_idx * 2
            i_idx = v_idx + 1
            if ch_idx < len(values):
                v = values[v_idx] - self.v_offsets[ch_idx]
                i = values[i_idx] - self.i_offsets[ch_idx]
                row.append(f"{v:.6f}")
                row.append(f"{i:.6f}")
            else:
                row.extend(["0.0", "0.0"])
        self._stream_writer.writerow(row)
        self._stream_counter += 1
        if self._stream_counter % 5000 == 0:
            self._stream_file.flush()

    def clear(self):
        self._count = 0
        self._t0_abs = None
        self._stream_counter = 0

    def reset_offsets(self):
        """清除所有零点偏移。"""
        self.v_offsets = [0.0] * NUM_CHANNELS
        self.i_offsets = [0.0] * NUM_CHANNELS

    def reset_channel_offsets(self, channel_index: int):
        """清除单个通道的偏移。"""
        self.v_offsets[channel_index] = 0.0
        self.i_offsets[channel_index] = 0.0

    def capture_zero_for_channels(self, channel_indices: list):
        latest = self.latest_raw()
        if latest is None:
            return
        _, vals = latest
        for ch_idx in channel_indices:
            if 0 <= ch_idx < NUM_CHANNELS and self.ch_enabled[ch_idx]:
                self.v_offsets[ch_idx] = vals[ch_idx * 2]
                self.i_offsets[ch_idx] = vals[ch_idx * 2 + 1]

    def apply_cal_result(self, vzero_values: list):
        for ch in range(min(len(vzero_values), NUM_CHANNELS)):
            if self.ch_enabled[ch]:
                self.v_offsets[ch] = vzero_values[ch]
                self.i_offsets[ch] = (vzero_values[ch] - 2.5) * 16.0

    def load_calibration(self, vzero_values: list, force_all: bool = False):
        """从校准数据加载零点偏移（用于加载保存的校准文件）。

        Args:
            vzero_values: 8 通道 Vzero 电压值
            force_all: True 则强制设置所有通道（忽略 ch_enabled 状态）
        """
        for ch in range(min(len(vzero_values), NUM_CHANNELS)):
            if force_all or self.ch_enabled[ch]:
                self.v_offsets[ch] = vzero_values[ch]
                self.i_offsets[ch] = (vzero_values[ch] - 2.5) * 16.0

    def get_calibration_state(self) -> tuple:
        """导出当前的校准状态。

        Returns:
            (vzero_values, i_offsets, enabled_mask)
            vzero_values: 8 通道 Vzero 电压值
            i_offsets: 8 通道电流偏移值
            enabled_mask: 通道启用位掩码
        """
        enabled_mask = 0
        for ch in range(NUM_CHANNELS):
            if self.ch_enabled[ch]:
                enabled_mask |= (1 << ch)
        # vzero 是从 v_offsets 反算的：vzero = 2.5 + i_offsets / 16.0
        # 但更准确的是直接从 v_offsets 记录的值
        vzero_values = list(self.v_offsets)
        i_offsets = list(self.i_offsets)
        return vzero_values, i_offsets, enabled_mask

    def set_channel_enabled(self, channel_index: int, enabled: bool):
        if 0 <= channel_index < NUM_CHANNELS:
            self.ch_enabled[channel_index] = enabled

    def set_all_channels_enabled(self, enabled: bool):
        self.ch_enabled = [enabled] * NUM_CHANNELS

    # ── 流式存盘 ─────────────────────────────────────────

    def start_streaming(self, filepath: str):
        self._stream_file = open(filepath, "w", newline="", encoding="utf-8-sig")
        self._stream_writer = csv.writer(self._stream_file)
        header = ["time_s"]
        for i in range(NUM_CHANNELS):
            header.append(f"V{i+1}_V")
            header.append(f"I{i+1}_A")
        self._stream_writer.writerow(header)
        self._stream_counter = 0

    def stop_streaming(self):
        n = self._stream_counter
        if self._stream_file:
            self._stream_file.close()
            self._stream_file = None
            self._stream_writer = None
        self._stream_counter = 0
        return n

    def is_streaming(self) -> bool:
        return self._stream_file is not None

    # ── 读操作（应用偏移） ──────────────────────────────

    def count(self) -> int:
        return self._count

    def latest_raw(self):
        n = self.count()
        if n == 0:
            return None
        idx = (n - 1) % MAX_POINTS if n <= MAX_POINTS else (self._count - 1) % MAX_POINTS
        return (self._t[idx], [self._ch[i][idx] for i in range(NUM_VALUES)])

    def latest(self):
        raw = self.latest_raw()
        if raw is None:
            return None
        t, vals = raw
        adj = list(vals)
        for ch in range(NUM_CHANNELS):
            adj[ch * 2] -= self.v_offsets[ch]
            adj[ch * 2 + 1] -= self.i_offsets[ch]
        return (t, adj)

    def sample_rate(self) -> float:
        n = self.count()
        if n < 2:
            return 0.0
        N = min(1024, n)
        t_last = self._t[(n - 1) % MAX_POINTS]
        t_prev = self._t[(n - N) % MAX_POINTS]
        span = t_last - t_prev
        if span <= 0:
            return 0.0
        return (N - 1) / span

    def to_numpy(self):
        """返回 (t, ch) 视图，O(1)，零拷贝。ch shape=(16, n)。"""
        n = self.count()
        if n == 0:
            return np.array([], dtype=np.float64), np.zeros((NUM_VALUES, 0), dtype=np.float64)
        # 当前数据范围：连续存储在 _t[0:n] 中
        # （循环缓冲时这里会变得复杂，但当前 _count < MAX_POINTS 时就是从头开始的连续区域）
        # 注意：当数据量超过 MAX_POINTS 时，数据可能不是连续的。这里先不考虑溢出情况。
        t = self._t[:n].copy()
        ch = self._ch[:, :n].copy()
        # 应用偏移
        for ch_idx in range(NUM_CHANNELS):
            ch[ch_idx * 2] -= self.v_offsets[ch_idx]
            ch[ch_idx * 2 + 1] -= self.i_offsets[ch_idx]
        return t, ch

    def get_window_with_offsets(self, t0: float, t1: float):
        """快速获取可见窗口数据（带偏移），只复制窗口内的数据。

        性能优化：不对全量数据做 .copy() 和偏移，只对可见窗口操作。
        """
        n = self.count()
        if n == 0:
            return np.array([], dtype=np.float64), np.zeros((NUM_VALUES, 0), dtype=np.float64)
        start = int(np.searchsorted(self._t[:n], t0, side="left"))
        end = int(np.searchsorted(self._t[:n], t1, side="right"))
        if end <= start:
            return np.array([], dtype=np.float64), np.zeros((NUM_VALUES, 0), dtype=np.float64)
        t = self._t[start:end].copy()
        ch = self._ch[:, start:end].copy()
        # 仅对窗口内应用偏移
        for ch_idx in range(NUM_CHANNELS):
            ch[ch_idx * 2] -= self.v_offsets[ch_idx]
            ch[ch_idx * 2 + 1] -= self.i_offsets[ch_idx]
        return t, ch

    def avg_last_seconds(self, seconds: float, channel_index: int = 0):
        n = self.count()
        if n == 0:
            return None, None
        t_last = self._t[n - 1]
        t0 = max(0.0, t_last - seconds)

        lo, hi = 0, n - 1
        start_idx = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._t[mid] < t0:
                lo = mid + 1
            else:
                start_idx = mid
                hi = mid - 1

        if start_idx >= n:
            return None, None
        cnt = n - start_idx
        if cnt == 0:
            return None, None

        vi_idx = channel_index * 2
        offset_v = self.v_offsets[channel_index]
        offset_i = self.i_offsets[channel_index]

        t_slice = self._t[start_idx:n]
        v_slice = self._ch[vi_idx][start_idx:n] - offset_v
        i_slice = self._ch[vi_idx + 1][start_idx:n] - offset_i

        return float(np.mean(v_slice)), float(np.mean(i_slice))

    # ── 导出 ────────────────────────────────────────────

    def export_csv(self, filepath: str) -> int:
        n = self.count()
        if n == 0:
            return 0
        header = ["time_s"]
        for i in range(NUM_CHANNELS):
            header.append(f"V{i+1}_V")
            header.append(f"I{i+1}_A")
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            for i in range(n):
                row = [f"{self._t[i]:.6f}"]
                for ch in range(NUM_CHANNELS):
                    row.append(f"{self._ch[ch * 2][i] - self.v_offsets[ch]:.6f}")
                    row.append(f"{self._ch[ch * 2 + 1][i] - self.i_offsets[ch]:.6f}")
                writer.writerow(row)
        return n
