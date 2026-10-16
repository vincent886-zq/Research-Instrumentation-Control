"""
绘图画布 — 8 通道电流实时波形（去掉电压子图，只保留电流）。
"""
import sys

import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

import numpy as np

if sys.platform == "win32":
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft YaHei", "SimHei", "SimSun", "Arial Unicode MS", "DejaVu Sans"
    ]
else:
    matplotlib.rcParams["font.sans-serif"] = [
        "Arial Unicode MS", "DejaVu Sans", "WenQuanYi Micro Hei"
    ]
matplotlib.rcParams["axes.unicode_minus"] = False

CHANNEL_COLORS = [
    "#E74C3C", "#3498DB", "#2ECC71", "#F39C12",
    "#9B59B6", "#1ABC9C", "#E67E22", "#95A5A6",
]


class PlotCanvas(FigureCanvas):
    """电流单子图实时波形画布（电压不再显示）。"""

    def __init__(self):
        self.fig = Figure(figsize=(7, 4.5), dpi=100)
        self.fig.set_tight_layout({"pad": 1.5})
        super().__init__(self.fig)

        # 仅电流子图（占用整个 figure）
        self.ax_i = self.fig.add_subplot(111)
        self.ax_i.set_ylabel("电流 Current (A)", fontsize=10, color="#333")
        self.ax_i.set_xlabel("时间 Time (s)", fontsize=10, color="#333")

        self._channel_index = 0
        self._ch_display = list(range(8))

        # 仅电流线（8 条）
        self.lines_i = []
        for ch in range(8):
            color = CHANNEL_COLORS[ch]
            li, = self.ax_i.plot([], [], color=color, lw=0.8, alpha=0.9,
                                 visible=(ch == 0), label=f"CH{ch+1} I")
            self.lines_i.append(li)

        self.ax_i.grid(True, alpha=0.25, lw=0.5)
        self.ax_i.tick_params(labelsize=8)

        self.view_range: float = 10.0
        self.i_range: float = 10.0
        self.i_auto_range: bool = False

        self.scroll_enabled: bool = True
        self._scroll_paused: bool = False
        self._scroll_fixed_end: float = 0.0

        self._drag_start_x: float = 0.0
        self._drag_base_end: float = 0.0
        self._is_dragging: bool = False
        self.on_view_changed = None

        self.fig.canvas.mpl_connect('button_press_event', self._on_mouse_press)
        self.fig.canvas.mpl_connect('button_release_event', self._on_mouse_release)
        self.fig.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)

        self._data_store = None
        # 自定义 legend handler (初始为空, 由 set_display_channels 更新)
        self._legend = None

    def bind_store(self, store):
        self._data_store = store

    def set_channel(self, index: int):
        self.set_display_channels([index])

    def set_display_channels(self, indices):
        visible_set = set(int(i) for i in indices if 0 <= int(i) < 8)
        if not visible_set:
            visible_set = {0}
            self._channel_index = 0
        else:
            self._channel_index = min(visible_set)
        for ch in range(8):
            self.lines_i[ch].set_visible(ch in visible_set)
        self._refresh_legend(visible_set)
        self.update_plot()

    def _refresh_legend(self, visible_set):
        """只将当前可见通道加入图例。"""
        # 移除旧 legend
        if self._legend is not None:
            self._legend.remove()
            self._legend = None
        # 收集可见通道的 (handle, label)
        handles_labels = [
            (self.lines_i[ch], self.lines_i[ch].get_label())
            for ch in range(8) if ch in visible_set
        ]
        if not handles_labels:
            return
        # 一行能放下的通道数 (按图大小自动适应)
        ncol = min(len(handles_labels), max(2, len(handles_labels) // 2))
        self._legend = self.ax_i.legend(
            loc="upper right", fontsize=8, ncol=ncol,
            framealpha=0.85, frameon=True,
        )

    def get_channel(self) -> int:
        return self._channel_index

    def update_plot(self):
        if self._data_store is None:
            return

        n = self._data_store.count()
        if n == 0:
            return

        visible_set = set(int(i) for i in self._ch_display
                          if 0 <= int(i) < 8)
        if not visible_set:
            visible_set = {self._channel_index}

        # x 轴范围
        # 用 count 后的最新时间去算范围，避免 to_numpy 全量复制
        t_last = float(self._data_store._t[n - 1])
        if self.scroll_enabled and not self._scroll_paused:
            if t_last > self.view_range:
                xmin = t_last - self.view_range
                xmax = t_last + self.view_range * 0.02
            else:
                xmin = 0.0
                xmax = max(self.view_range, t_last * 1.05)
        else:
            end = self._scroll_fixed_end if self._scroll_fixed_end > 0 else t_last
            xmin = max(0.0, end - self.view_range)
            xmax = end + self.view_range * 0.02

        # ── 性能优化：只取可见窗口数据（带偏移），不复制全量数据 ──
        tw, ch_w = self._data_store.get_window_with_offsets(xmin, xmax)
        n_visible = len(tw)
        if n_visible == 0:
            # 没有可见数据，清空通道线
            for ch_idx in visible_set:
                self.lines_i[ch_idx].set_data([], [])
            self.ax_i.set_xlim(xmin, xmax)
            self.fig.canvas.draw_idle()
            return

        MAX_DRAW_POINTS = 2000
        step = max(1, n_visible // MAX_DRAW_POINTS) if n_visible > MAX_DRAW_POINTS else 1
        tw_d = tw[::step]

        all_i = []
        for ch_idx in visible_set:
            i_d = ch_w[ch_idx * 2 + 1][::step]
            self.lines_i[ch_idx].set_data(tw_d, i_d)
            if len(i_d) > 0:
                all_i.extend(i_d.tolist())

        self.ax_i.set_xlim(xmin, xmax)

        # 电流 Y 轴（自动量程 / 固定）
        if self.i_auto_range and all_i:
            imin, imax = min(all_i), max(all_i)
            ipad = max((imax - imin) * 0.1, 0.02)
            self.ax_i.set_ylim(imin - ipad, imax + ipad)
        else:
            self.ax_i.set_ylim(-self.i_range, self.i_range)

        self.fig.canvas.draw_idle()

    def clear_view(self):
        for ch in range(8):
            self.lines_i[ch].set_data([], [])
        self.ax_i.set_xlim(0, self.view_range)
        self.ax_i.set_ylim(-self.i_range, self.i_range)
        self._scroll_paused = False
        self._scroll_fixed_end = 0.0
        self.fig.canvas.draw_idle()

    # ── 鼠标拖拽平移 ──

    def _on_mouse_press(self, event):
        if event.button != 1 or event.inaxes != self.ax_i:
            return
        self._drag_start_x = event.xdata
        if self._scroll_paused:
            self._drag_base_end = self._scroll_fixed_end
        elif self._data_store and self._data_store.count() > 0:
            n = self._data_store.count()
            self._drag_base_end = float(self._data_store._t[n - 1])
        else:
            self._drag_base_end = self.view_range
        self._is_dragging = True

    def _on_mouse_move(self, event):
        if not self._is_dragging or event.xdata is None or event.inaxes != self.ax_i:
            return
        delta = self._drag_start_x - event.xdata
        new_end = self._drag_base_end + delta
        if self._data_store and self._data_store.count() > 0:
            n = self._data_store.count()
            t_max = float(self._data_store._t[n - 1])
            new_end = min(t_max, new_end)
        new_end = max(self.view_range, new_end)
        self.set_scroll_fixed(new_end)

    def _on_mouse_release(self, event):
        if event.button == 1:
            self._is_dragging = False

    def set_scroll_fixed(self, t_end: float):
        self._scroll_paused = True
        if self._data_store and self._data_store.count() > 0:
            n = self._data_store.count()
            t_max = float(self._data_store._t[n - 1])
            t_end = min(t_max, t_end)
        t_end = max(self.view_range, t_end)
        self._scroll_fixed_end = max(0.0, t_end)
        self.update_plot()
        if self.on_view_changed:
            self.on_view_changed(self._scroll_fixed_end)

    def zoom_in(self):
        self.view_range = max(self.view_range / 1.5, 0.5)
        self.update_plot()

    def zoom_out(self):
        self.view_range = min(self.view_range * 1.5, 3600.0)
        self.update_plot()

    def reset_view(self):
        self.view_range = 10.0
        self.update_plot()

    def zoom_i_in(self):
        self.i_range = max(self.i_range / 1.5, 0.1)
        self.i_auto_range = False
        self.update_plot()

    def zoom_i_out(self):
        self.i_range = min(self.i_range * 1.5, 100.0)
        self.i_auto_range = False
        self.update_plot()

    def reset_i_range(self):
        self.i_range = 10.0
        self.i_auto_range = False
        self.update_plot()

    def toggle_i_auto_range(self):
        self.i_auto_range = not self.i_auto_range
        self.update_plot()

    def resume_autoscroll(self):
        self._scroll_paused = False
        self._scroll_fixed_end = 0.0
        self.update_plot()

    def is_scroll_paused(self) -> bool:
        return self._scroll_paused

    def get_data_time_range(self):
        if self._data_store is None or self._data_store.count() == 0:
            return 0.0, 10.0
        n = self._data_store.count()
        return (float(self._data_store._t[0]),
                float(self._data_store._t[n - 1]))

    def has_data(self) -> bool:
        return self._data_store is not None and self._data_store.count() > 0

    def show_range(self, t0: float, t1: float):
        if t0 >= t1:
            return
        self.ax_i.set_xlim(t0, t1)
        self.fig.canvas.draw_idle()
