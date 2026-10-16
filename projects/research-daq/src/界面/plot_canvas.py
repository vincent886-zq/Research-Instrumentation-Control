"""
绘图画布 — 8 通道位移-时间实时波形。
X 轴由主线程 _on_plot_tick 控制，画布只负责渲染。
"""
import sys

import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from 核心.data_store import CHANNEL_COLORS, CHANNEL_NAMES, NUM_CHANNELS


if sys.platform == "win32":
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft YaHei", "SimHei", "SimSun", "Arial Unicode MS", "DejaVu Sans"
    ]
else:
    matplotlib.rcParams["font.sans-serif"] = [
        "Arial Unicode MS", "DejaVu Sans", "WenQuanYi Micro Hei"
    ]
matplotlib.rcParams["axes.unicode_minus"] = False


class PlotCanvas(FigureCanvas):
    def __init__(self):
        self.fig = Figure(figsize=(7, 4.5), dpi=100)
        self.fig.set_tight_layout({"pad": 1.5})
        super().__init__(self.fig)

        self.ax = self.fig.add_subplot(111)
        self.ax.set_ylabel("ADC input voltage (V)", fontsize=9, color="#333")
        self.ax.set_xlabel("时间 Time (s)", fontsize=9, color="#333")

        # 8 条通道线
        self.lines = []
        for i in range(NUM_CHANNELS):
            line, = self.ax.plot([], [], color=CHANNEL_COLORS[i],
                                 lw=1.0, alpha=0.9, label=CHANNEL_NAMES[i])
            self.lines.append(line)

        # 保存原始 label（用于 legend 过滤后还原）
        self._orig_labels = list(CHANNEL_NAMES)

        self.ax.grid(True, alpha=0.25, lw=0.5)
        self.ax.tick_params(labelsize=8)
        self.ax.legend(fontsize=7, loc="upper right", ncol=4)
        self.ax.set_autoscale_on(False)

        # 状态
        self.view_range: float = 10.0
        self.y_range: float = 5.0
        self.y_auto_range: bool = True
        self.x_end: float = 0.0
        self.scroll_enabled: bool = True           # 自动滚屏开关（由 chk_autoscroll 控制）
        self._scroll_paused: bool = False          # 用户拖滚动条时暂停
        self._scroll_fixed_end: float = 0.0        # 暂停时固定的右边界
        self._display_channels: list[int] = list(range(NUM_CHANNELS))
        self._data_store = None
        self.on_view_changed = None  # 鼠标拖拽后同步滚动条的回调

        # 鼠标拖拽平移（照搬电流采集软件）
        self._drag_start_x: float = 0.0
        self._drag_base_end: float = 0.0
        self._is_dragging: bool = False

        self.fig.canvas.mpl_connect('button_press_event', self._on_mouse_press)
        self.fig.canvas.mpl_connect('button_release_event', self._on_mouse_release)
        self.fig.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)

    def bind_store(self, store):
        self._data_store = store

    # ── 滚动条暂停/恢复（照搬电流采集软件） ──

    def set_scroll_fixed(self, t_end: float):
        """用户拖滚动条到非最右位置时，固定视图。"""
        self._scroll_paused = True
        if self._data_store and self._data_store.count() > 0:
            n = self._data_store.count()
            t_max = float(self._data_store.t_data[-1])
            t_end = min(t_max, t_end)
        t_end = max(self.view_range, t_end)
        self._scroll_fixed_end = max(0.0, t_end)
        self._update_scroll_view()
        if self.on_view_changed:
            self.on_view_changed(self._scroll_fixed_end)

    def resume_autoscroll(self):
        """用户把滑块拖到最右时，恢复自动滚屏。"""
        self._scroll_paused = False
        self._scroll_fixed_end = 0.0
        # 跳到数据最右端（反映最新采集时间）
        if self._data_store and self._data_store.count() > 0:
            n = self._data_store.count()
            self.x_end = float(self._data_store.t_data[-1])
        self._update_scroll_view()

    def _update_scroll_view(self):
        """按当前 scroll 状态更新 x 轴。"""
        end = self._scroll_fixed_end if self._scroll_fixed_end > 0 else self.x_end
        xmin = max(0.0, end - self.view_range)
        xmax = end + self.view_range * 0.02
        self.ax.set_xlim(xmin, xmax)
        self.fig.canvas.draw_idle()

    # ── 鼠标拖拽平移（照搬电流采集软件） ──

    def _on_mouse_press(self, event):
        if event.button != 1 or event.inaxes != self.ax:
            return
        self._drag_start_x = event.xdata
        if self._scroll_paused:
            self._drag_base_end = self._scroll_fixed_end
        elif self._data_store and self._data_store.count() > 0:
            n = self._data_store.count()
            self._drag_base_end = float(self._data_store.t_data[-1])
        else:
            self._drag_base_end = self.view_range
        self._is_dragging = True

    def _on_mouse_move(self, event):
        if not self._is_dragging or event.xdata is None or event.inaxes != self.ax:
            return
        delta = self._drag_start_x - event.xdata
        new_end = self._drag_base_end + delta
        if self._data_store and self._data_store.count() > 0:
            n = self._data_store.count()
            t_max = float(self._data_store.t_data[-1])
            new_end = min(t_max, new_end)
        new_end = max(self.view_range, new_end)
        self.set_scroll_fixed(new_end)

    def _on_mouse_release(self, event):
        if event.button == 1:
            self._is_dragging = False

    def is_scroll_paused(self) -> bool:
        return self._scroll_paused

    def has_data(self) -> bool:
        return self._data_store is not None and self._data_store.count() > 0

    def get_data_time_range(self):
        if self._data_store is None or self._data_store.count() == 0:
            return 0.0, 10.0
        n = self._data_store.count()
        return (float(self._data_store.t_data[0]),
                float(self._data_store.t_data[n - 1]))

    def set_display_channels(self, ch_list: list[int]):
        """更新显示通道列表，并立即按当前可见性重画（不依赖 _on_plot_tick）。"""
        self._display_channels = list(ch_list)
        # 同步 line 可见性：勾选上的显示，否则清空 + 隐藏
        for i in range(NUM_CHANNELS):
            if i in self._display_channels:
                self.lines[i].set_visible(True)
            else:
                self.lines[i].set_data([], [])
                self.lines[i].set_visible(False)
        # 同步图例
        self.refresh_legend()
        self.fig.canvas.draw_idle()

    def refresh_legend(self):
        """按当前 line 的 visible 重建图例：隐藏的 line 不出现在图例里。"""
        for i, line in enumerate(self.lines):
            line.set_label(self._orig_labels[i] if line.get_visible() else "_nolegend_")
        handles_all, labels_all = self.ax.get_legend_handles_labels()
        # 过滤掉隐藏通道的占位 label
        handles = [h for h, l in zip(handles_all, labels_all) if l != "_nolegend_"]
        if handles:
            self.ax.legend(handles, [h.get_label() for h in handles],
                           fontsize=7, loc="upper right", ncol=4)
        else:
            leg = self.ax.get_legend()
            if leg is not None:
                leg.remove()

    def clear_view(self):
        for i in range(NUM_CHANNELS):
            self.lines[i].set_data([], [])
        self.ax.set_xlim(0, self.view_range)
        self.ax.set_ylim(-self.y_range, self.y_range)
        self.x_end = 0.0
        self._scroll_paused = False
        self._scroll_fixed_end = 0.0
        self.fig.canvas.draw_idle()

    # ── Y 轴缩放 ──

    def zoom_y_in(self):
        self.y_range = max(self.y_range / 2.0, 0.001)
        self.y_auto_range = False

    def zoom_y_out(self):
        self.y_range = min(self.y_range * 2.0, 50.0)
        self.y_auto_range = False

    def reset_y_range(self):
        self.y_range = 5.0
        self.y_auto_range = False

    def toggle_y_auto_range(self):
        self.y_auto_range = not self.y_auto_range

    # ── X 轴：时间轴由 _on_plot_tick 100ms 定时器自动扩展 ──

    def zoom_in(self):
        x0, x1 = self.ax.get_xlim()
        center = (x0 + x1) / 2.0
        span = max((x1 - x0) / 2.0, 0.05)
        self.view_range = span
        self.ax.set_xlim(max(0.0, center - span / 2.0), center + span / 2.0)
        self.fig.canvas.draw_idle()

    def zoom_out(self):
        x0, x1 = self.ax.get_xlim()
        center = (x0 + x1) / 2.0
        span = min(max((x1 - x0) * 2.0, 0.1), 3600.0)
        self.view_range = span
        self.ax.set_xlim(max(0.0, center - span / 2.0), center + span / 2.0)
        self.fig.canvas.draw_idle()

    def reset_view(self):
        self.view_range = 10.0
        end = max(self.x_end, self.view_range)
        self.ax.set_xlim(max(0.0, end - self.view_range), end)
        self.fig.canvas.draw_idle()
        if self.on_view_changed:
            self.on_view_changed(end)  # 同步滚动条并刷新数据

    # ── 时段显示 ──

    def show_range(self, t0: float, t1: float):
        if t0 >= t1:
            return
        self.ax.set_xlim(t0, t1)
        self.fig.canvas.draw_idle()

    # ── 自动量程（参考电流采集软件实现） ──

    def _calc_auto_range(self, t_arr, ch_data_list):
        """计算可见窗口内的数据范围并自动设置 Y 轴。

        参考电流采集软件 plot_canvas.py 的思路：
        只对当前 X 轴可见窗口内的数据进行 min/max 计算。
        
        Args:
            t_arr: 时间数组 (numpy array)
            ch_data_list: 通道数据列表 [(ch_index, data_array), ...]
        """
        # 获取当前可见的 X 轴范围
        x0, x1 = self.ax.get_xlim()
        # 只取可见窗口内的数据
        mask = (t_arr >= x0) & (t_arr <= x1)
        
        all_vals = []
        for ch_idx, vals in ch_data_list:
            if len(vals) == 0:
                continue
            windowed = vals[mask]
            if len(windowed) > 0:
                all_vals.extend(windowed.tolist())
        
        if all_vals:
            ymin = min(all_vals)
            ymax = max(all_vals)
            if ymax - ymin < 0.001:
                self.ax.set_ylim(-5.0, 5.0)
            else:
                ypad = max((ymax - ymin) * 0.1, 0.02)
                self.ax.set_ylim(ymin - ypad, ymax + ypad)
        else:
            self.ax.set_ylim(-5.0, 5.0)

    def apply_y_range(self, all_vals=None, t_arr=None, ch_data_list=None):
        """强制应用 Y 轴量程并立刻重绘（不依赖 _on_plot_tick）。

        若提供 t_arr 和 ch_data_list，则使用窗口感知的自动量程；
        否则使用外部传人的 all_vals（兼容旧调用方）。
        """
        if self.y_auto_range:
            if t_arr is not None and ch_data_list is not None:
                self._calc_auto_range(t_arr, ch_data_list)
            elif all_vals and len(all_vals) > 0:
                ymin = min(all_vals)
                ymax = max(all_vals)
                if ymax - ymin < 0.001:
                    self.ax.set_ylim(-5.0, 5.0)
                else:
                    ypad = max((ymax - ymin) * 0.1, 0.02)
                    self.ax.set_ylim(ymin - ypad, ymax + ypad)
            else:
                self.ax.set_ylim(-5.0, 5.0)
        else:
            self.ax.set_ylim(-self.y_range, self.y_range)
        self.fig.canvas.draw()  # 强制立即重绘
