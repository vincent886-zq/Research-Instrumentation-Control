"""
HSTS016L 电流采集示波器 v2.0
==============================
AMB 磁悬浮轴承铣削电主轴 — 无测力计切削力辨识研究
霍尔电流传感器数据采集 + 实时双通道波形

入口: python main.py
依赖: pip install pyserial PyQt5 matplotlib numpy
"""
import os
import sys
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QTextEdit, QGroupBox,
    QGridLayout, QLineEdit, QSplitter, QMessageBox, QCheckBox,
    QStatusBar, QAction, QFileDialog, QDoubleSpinBox, QScrollBar,
    QFrame,
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QEvent
from PyQt5.QtGui import QFont

from config import load as load_config
from config import update_serial, update_view, update_window, update_export_path, update_channels
from config import update_calibration_settings
from data_store import DataStore
from serial_worker import SerialWorker
from plot_canvas import PlotCanvas
from calibration_manager import (
    save_calibration, load_calibration, list_calibrations,
    delete_calibration, get_last_calibration_file,
    save_last_calibration_file, update_calibration_label,
)

# ── 常量 ─────────────────────────────────────────────────
APP_TITLE = "HSTS016L 电流采集示波器 v2.0"
TEXT_MAX_LINES = 5000   # 日志区最大行数
PLOT_INTERVAL_MS = 100  # 绘图刷新间隔 (ms)。100ms 足够流畅且显著降低 CPU。
BAUD_RATES = ["921600", "115200", "230400", "460800", "1000000", "2000000"]


class PortScanner(QThread):
    """在后台线程枚举串口，避免 comports() 阻塞 GUI。

    某些设备（蓝牙串口、断开的虚拟串口等）会导致
    serial.tools.list_ports.comports() 超时数秒，
    独立线程执行可保证 GUI 始终响应。
    """
    ports_found = pyqtSignal(list)

    def run(self):
        import serial.tools.list_ports
        try:
            ports = list(serial.tools.list_ports.comports())
            self.ports_found.emit(ports)
        except Exception:
            self.ports_found.emit([])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # ── 核心组件 ──
        self.store = DataStore()
        self.canvas = PlotCanvas()
        self.canvas.bind_store(self.store)
        self.canvas.on_view_changed = self._sync_scrollbar_from_canvas
        self.serial = SerialWorker()

        # ── 配置 ──
        self.cfg = load_config()

        # ── 校准配置状态 ──
        self._cal_auto_save = self.cfg.get("calibration", {}).get("auto_save", True)
        self._cal_auto_load_last = self.cfg.get("calibration", {}).get("auto_load_last", False)

        # ── 状态 ──
        self._collecting = False
        self._presample_active = False  # 预采样中（数据流入但不画图）
        self._data_counter = 0
        self._last_draw_count = 0
        self._port_scanner = None
        self._scanning_ports = False
        self._countdown_remaining = 0.0
        self._updating_slider = False  # 程序化更新滚动条时抑制信号回调

        # 通道状态
        self._ch_enabled = [True] * 8      # 启用通道
        self._ch_display = list(range(8))  # 绘图通道
        self._syncing_channels = False      # 防止信号递归

        # 采集状态闪烁定时器（必须在 _setup_ui 之前创建，因为 _apply_status_tag_style 会用到）
        self._status_anim_timer = QTimer()
        self._status_anim_timer.timeout.connect(self._on_status_anim_tick)
        self._status_anim_timer.setInterval(500)
        self._status_anim_state = False

        # 初始化 UI
        self._setup_ui()
        self._setup_menu()
        self._connect_signals()
        self._restore_state()

        # 绘图定时器（100ms）
        self._plot_timer = QTimer()
        self._plot_timer.timeout.connect(self._on_plot_tick)
        self._plot_timer.start(PLOT_INTERVAL_MS)

        # 倒计时定时器（100ms）
        self._countdown_timer = QTimer()
        self._countdown_timer.timeout.connect(self._on_countdown_tick)
        self._countdown_timer.setInterval(100)

        # 如果配置了自动加载上次校准，延迟加载（等 UI 就绪）
        if self._cal_auto_load_last:
            QTimer.singleShot(500, self._auto_load_last_calibration)

        # ── 全局快捷键（F7: 开始采集, F8: 停止采集）──
        # 用 eventFilter 确保不管焦点在哪个控件（matplotlib 画布、输入框等）都能触发
        QApplication.instance().installEventFilter(self)

    # ============================================================
    #  UI 搭建
    # ============================================================

    def _setup_ui(self):
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1000, 600)

        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._lbl_status_conn = QLabel("未连接")
        self._lbl_status_coll = QLabel("已停止")
        self.status_bar.addWidget(self._lbl_status_conn)
        self.status_bar.addPermanentWidget(self._lbl_status_coll)

        # 中央区域
        central = QWidget()
        self.setCentralWidget(central)
        splitter = QSplitter(Qt.Horizontal)

        # ── 左面板 ──
        left = QWidget()
        left.setMaximumWidth(420)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(6, 6, 6, 6)

        # 串口配置
        grp_cfg = QGroupBox("串口配置")
        grid = QGridLayout(grp_cfg)

        grid.addWidget(QLabel("串口:"), 0, 0)
        self.cmb_port = QComboBox()
        self.cmb_port.setMinimumWidth(140)
        grid.addWidget(self.cmb_port, 0, 1)

        grid.addWidget(QLabel("波特率:"), 0, 2)
        self.cmb_baud = QComboBox()
        self.cmb_baud.addItems(BAUD_RATES)
        self.cmb_baud.setCurrentText("921600")
        grid.addWidget(self.cmb_baud, 0, 3)

        btn_refresh = QPushButton("刷新串口")
        btn_refresh.clicked.connect(self._refresh_ports)
        grid.addWidget(btn_refresh, 1, 0, 1, 4)

        left_layout.addWidget(grp_cfg)

        # 控制
        grp_ctrl = QGroupBox("控制")
        ctrl_layout = QVBoxLayout(grp_ctrl)

        row1 = QHBoxLayout()
        self.btn_open = QPushButton("打开串口")
        self.btn_open.clicked.connect(self._toggle_serial)
        row1.addWidget(self.btn_open)

        self.btn_start = QPushButton("开始采集 [F7]")
        self.btn_start.setEnabled(False)
        self.btn_start.setToolTip("开始数据采集 (快捷键: F7)")
        self.btn_start.clicked.connect(self._start_collect)
        row1.addWidget(self.btn_start)

        self.btn_stop = QPushButton("停止采集 [F8]")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setToolTip("停止数据采集 (快捷键: F8)")
        self.btn_stop.clicked.connect(self._stop_collect)
        row1.addWidget(self.btn_stop)

        ctrl_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_zero = QPushButton("零点校准")
        self.btn_zero.setEnabled(False)
        self.btn_zero.setToolTip("向固件发送 zero 指令，并仅对勾选的采集通道记录零点偏移")
        self.btn_zero.clicked.connect(self._on_zero_calibrate)
        row2.addWidget(self.btn_zero)

        self.btn_clear_plot = QPushButton("清空绘图")
        self.btn_clear_plot.clicked.connect(self._clear_data)
        row2.addWidget(self.btn_clear_plot)

        self.btn_export = QPushButton("导出 CSV")
        self.btn_export.clicked.connect(self._export_csv)
        row2.addWidget(self.btn_export)

        self.chk_autoscroll = QCheckBox("自动滚屏")
        self.chk_autoscroll.setChecked(True)
        row2.addWidget(self.chk_autoscroll)

        self.chk_stream = QCheckBox("实时存盘")
        self.chk_stream.setToolTip("采集时每个点实时写入 CSV 文件，不限时长完整记录")
        row2.addWidget(self.chk_stream)

        row2.addStretch()

        ctrl_layout.addLayout(row2)

        # 第三行：预采样 / 定时 / [填充] / 状态指示
        row3 = QHBoxLayout()
        self.chk_presample = QCheckBox("预采样")
        self.chk_presample.toggled.connect(self._on_presample_toggled)
        row3.addWidget(self.chk_presample)

        self.spin_duration = QDoubleSpinBox()
        self.spin_duration.setRange(0.1, 3600.0)
        self.spin_duration.setValue(3.0)
        self.spin_duration.setSingleStep(0.1)
        self.spin_duration.setDecimals(1)
        self.spin_duration.setSuffix(" s")
        self.spin_duration.setMinimumWidth(80)
        row3.addWidget(self.spin_duration)

        self.lbl_countdown = QLabel("")
        self.lbl_countdown.setFont(QFont("Consolas", 11))
        self.lbl_countdown.setStyleSheet("color: #E74C3C; font-weight: bold;")
        row3.addWidget(self.lbl_countdown)

        # 采集状态指示徽标（紧贴倒计时右侧，避免悬浮在最右；仿按钮风格）
        self._status_tag = QFrame()
        self._status_tag.setObjectName("status_tag")
        self._status_tag.setFrameShape(QFrame.NoFrame)
        self._status_tag.setFixedSize(110, 22)   # 加大到 110 容纳 ● RECORDING
        status_layout = QHBoxLayout(self._status_tag)
        status_layout.setContentsMargins(8, 0, 8, 0)
        status_layout.setSpacing(6)
        status_layout.setAlignment(Qt.AlignVCenter)

        # 真正的圆形色块
        self.lbl_status_dot = QLabel()
        self.lbl_status_dot.setFixedSize(8, 8)
        status_layout.addWidget(self.lbl_status_dot, 0, Qt.AlignVCenter)

        self.lbl_status_text = QLabel("PAUSED")
        self.lbl_status_text.setAlignment(Qt.AlignVCenter | Qt.AlignCenter)
        status_layout.addWidget(self.lbl_status_text, 0, Qt.AlignVCenter)

        self._apply_status_tag_style("paused")
        self._status_tag.setToolTip("● 采集状态指示")
        row3.addWidget(self._status_tag)

        # 弹性间隔推到最右（让状态框紧贴其他控件）
        row3.addStretch()
        ctrl_layout.addLayout(row3)

        left_layout.addWidget(grp_ctrl)

        # ── 通道选择（mark3 风格：采集通道 + 绘图通道） ──
        grp_ch = QGroupBox("通道选择")
        ch_layout = QVBoxLayout(grp_ch)

        # 采集通道：勾选后该通道的数据才会被存储/处理
        collect_lbl = QLabel("采集通道 (哪些通道参与采集/校准):")
        collect_lbl.setFont(QFont("", 8))
        ch_layout.addWidget(collect_lbl)
        ch_grid_c = QGridLayout()
        self.chk_collect = []
        for i in range(8):
            cb = QCheckBox(f"CH{i+1}")
            cb.setChecked(False)  # 默认都不选
            cb.stateChanged.connect(lambda s, idx=i: self._on_collect_changed(idx, s))
            self.chk_collect.append(cb)
            ch_grid_c.addWidget(cb, i // 4, i % 4)
        ch_layout.addLayout(ch_grid_c)

        # 全选/全不选 + 校准按钮
        ch_btn_row = QHBoxLayout()
        btn_all_collect = QPushButton("全选")
        btn_all_collect.clicked.connect(lambda: self._set_all_collect(True))
        ch_btn_row.addWidget(btn_all_collect)
        btn_none_collect = QPushButton("全不选")
        btn_none_collect.clicked.connect(lambda: self._set_all_collect(False))
        ch_btn_row.addWidget(btn_none_collect)
        ch_btn_row.addStretch()
        ch_layout.addLayout(ch_btn_row)

        # 绘图通道：勾选后该通道会在波形图上显示（必须是采集通道的子集）
        display_lbl = QLabel("绘图通道 (哪些通道画在图上):")
        display_lbl.setFont(QFont("", 8))
        ch_layout.addWidget(display_lbl)
        ch_grid_d = QGridLayout()
        self.chk_display = []
        for i in range(8):
            cb = QCheckBox(f"CH{i+1}")
            cb.setChecked(False)  # 默认都不选
            cb.stateChanged.connect(lambda s, idx=i: self._on_display_changed(idx, s))
            self.chk_display.append(cb)
            ch_grid_d.addWidget(cb, i // 4, i % 4)
        ch_layout.addLayout(ch_grid_d)

        left_layout.addWidget(grp_ch)

        # 1 秒平均统计（按通道，电流）
        grp_avg = QGroupBox("1 秒平均 (按通道电流)")
        avg_layout = QGridLayout(grp_avg)

        self.lbl_avg_i = []
        for i in range(8):
            row = i // 2
            col = (i % 2) * 2
            lbl_n = QLabel(f"CH{i+1} I:")
            lbl_n.setFont(QFont("Consolas", 9))
            avg_layout.addWidget(lbl_n, row, col)
            i_val = QLabel("--")
            i_val.setFont(QFont("Consolas", 9))
            i_val.setStyleSheet("color: #3498DB;")
            i_val.setMinimumWidth(70)
            avg_layout.addWidget(i_val, row, col + 1)
            self.lbl_avg_i.append(i_val)

        left_layout.addWidget(grp_avg)

        # 接收数据
        grp_rx = QGroupBox("接收数据")
        rx_layout = QVBoxLayout(grp_rx)
        self.txt_rx = QTextEdit()
        self.txt_rx.setReadOnly(True)
        self.txt_rx.setFont(QFont("Consolas", 10))
        # PyQt5 5.15.11 的 QTextEdit 没有 setMaximumBlockCount 方法，
        # 通过 document() 设置硬限制，防止日志无限增长导致内存泄漏 / 卡死。
        self.txt_rx.document().setMaximumBlockCount(TEXT_MAX_LINES)
        rx_layout.addWidget(self.txt_rx)
        left_layout.addWidget(grp_rx)

        # ── 右面板 ──
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 6, 6, 6)

        # matplotlib 工具栏
        from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavToolbar
        self.toolbar = NavToolbar(self.canvas, self)
        right_layout.addWidget(self.toolbar)

        # 绘图区
        right_layout.addWidget(self.canvas, 1)

        # 视图控制（时间轴 + 电流 Y 轴两组独立）
        grp_tool = QGroupBox("视图控制")
        tool_vlayout = QVBoxLayout(grp_tool)

        # 第一行：时间轴 X
        row_t = QHBoxLayout()
        row_t.addWidget(QLabel("时间轴:"))

        btn_zoom_in = QPushButton("放大 +")
        btn_zoom_in.clicked.connect(self.canvas.zoom_in)
        row_t.addWidget(btn_zoom_in)

        btn_zoom_out = QPushButton("缩小 -")
        btn_zoom_out.clicked.connect(self.canvas.zoom_out)
        row_t.addWidget(btn_zoom_out)

        btn_reset = QPushButton("重置")
        btn_reset.clicked.connect(self.canvas.reset_view)
        row_t.addWidget(btn_reset)

        row_t.addSpacing(20)

        row_t.addWidget(QLabel("时段:"))
        self.edit_t0 = QLineEdit("0")
        self.edit_t0.setMaximumWidth(50)
        self.edit_t0.setPlaceholderText("t0")
        row_t.addWidget(self.edit_t0)

        row_t.addWidget(QLabel("~"))
        self.edit_t1 = QLineEdit("5")
        self.edit_t1.setMaximumWidth(50)
        self.edit_t1.setPlaceholderText("t1")
        row_t.addWidget(self.edit_t1)

        row_t.addWidget(QLabel("s"))
        btn_range = QPushButton("应用")
        btn_range.clicked.connect(self._apply_range)
        row_t.addWidget(btn_range)

        row_t.addStretch()
        tool_vlayout.addLayout(row_t)

        # 滚动条：时间轴滑动
        row_scroll = QHBoxLayout()
        row_scroll.addWidget(QLabel("滑动:"))
        self.scroll_slider = QScrollBar(Qt.Horizontal)
        self.scroll_slider.setRange(0, 10000)
        self.scroll_slider.setValue(10000)
        self.scroll_slider.setToolTip("← 拖拽回看历史数据 | 最右端 = 自动滚屏")
        row_scroll.addWidget(self.scroll_slider)
        tool_vlayout.addLayout(row_scroll)

        # 第二行：电流 Y 轴
        row_i = QHBoxLayout()
        row_i.addWidget(QLabel("电流轴:"))

        btn_i_zoom_in = QPushButton("放大 +")
        btn_i_zoom_in.clicked.connect(self.canvas.zoom_i_in)
        row_i.addWidget(btn_i_zoom_in)

        btn_i_zoom_out = QPushButton("缩小 -")
        btn_i_zoom_out.clicked.connect(self.canvas.zoom_i_out)
        row_i.addWidget(btn_i_zoom_out)

        btn_i_reset = QPushButton("重置 ±10A")
        btn_i_reset.clicked.connect(self.canvas.reset_i_range)
        row_i.addWidget(btn_i_reset)

        self.btn_i_auto = QPushButton("自动量程")
        self.btn_i_auto.setCheckable(True)
        self.btn_i_auto.setChecked(True)
        self.btn_i_auto.clicked.connect(self._toggle_i_auto)
        row_i.addWidget(self.btn_i_auto)

        row_i.addWidget(QLabel("±"))
        self.edit_i_span = QLineEdit("")
        self.edit_i_span.setMaximumWidth(50)
        self.edit_i_span.setPlaceholderText("A")
        self.edit_i_span.setEnabled(False)  # 自动量程默认开, 手动输入禁用
        row_i.addWidget(self.edit_i_span)

        btn_i_apply = QPushButton("应用")
        btn_i_apply.clicked.connect(self._apply_i_range)
        row_i.addWidget(btn_i_apply)

        self.lbl_i_range = QLabel("当前: 自动")
        row_i.addWidget(self.lbl_i_range)

        row_i.addStretch()
        tool_vlayout.addLayout(row_i)

        right_layout.addWidget(grp_tool)

        # 统计（显示全部通道实时电流）
        grp_stat = QGroupBox("统计")
        stat_layout = QGridLayout(grp_stat)
        stat_layout.setSpacing(2)

        # 第一行：采样数 + 速率
        self.lbl_count = QLabel("采样数: 0")
        self.lbl_count.setStyleSheet("font-weight: bold;")
        stat_layout.addWidget(self.lbl_count, 0, 0, 1, 4)

        self.lbl_rate = QLabel("速率: -- Hz")
        self.lbl_rate.setStyleSheet("font-weight: bold;")
        stat_layout.addWidget(self.lbl_rate, 0, 4, 1, 4)

        # 第二、三行：8 通道实时电流（4 列×2 行）
        self.lbl_ri = []  # real-time current labels
        for i in range(8):
            lbl = QLabel(f"CH{i+1}: -- A")
            lbl.setStyleSheet("color: #3498DB; font-family: Consolas; font-size: 9px;")
            lbl.setMinimumWidth(120)
            row_i = 1 + i // 4
            col_i = (i % 4) * 2
            stat_layout.addWidget(QLabel(f"CH{i+1}:"), row_i, col_i)
            self.lbl_ri.append(lbl)
            stat_layout.addWidget(lbl, row_i, col_i + 1)

        right_layout.addWidget(grp_stat)

        # ── 组装 ──
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.addWidget(splitter)

    def _setup_menu(self):
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件")
        act_save = QAction("导出 CSV...", self)
        act_save.setShortcut("Ctrl+S")
        act_save.triggered.connect(self._export_csv)
        file_menu.addAction(act_save)

        act_clear = QAction("清空数据", self)
        act_clear.setShortcut("Ctrl+D")
        act_clear.triggered.connect(self._clear_data)
        file_menu.addAction(act_clear)

        file_menu.addSeparator()
        act_exit = QAction("退出", self)
        act_exit.setShortcut("Ctrl+Q")
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        # 视图菜单
        view_menu = menubar.addMenu("视图")
        act_zin = QAction("时间轴放大", self)
        act_zin.setShortcut("Ctrl++")
        act_zin.triggered.connect(self.canvas.zoom_in)
        view_menu.addAction(act_zin)

        act_zout = QAction("时间轴缩小", self)
        act_zout.setShortcut("Ctrl+-")
        act_zout.triggered.connect(self.canvas.zoom_out)
        view_menu.addAction(act_zout)

        act_zrst = QAction("重置时间轴", self)
        act_zrst.setShortcut("Ctrl+0")
        act_zrst.triggered.connect(self.canvas.reset_view)
        view_menu.addAction(act_zrst)

        view_menu.addSeparator()

        act_izin = QAction("电流轴放大", self)
        act_izin.setShortcut("Ctrl+Shift++")
        act_izin.triggered.connect(self.canvas.zoom_i_in)
        view_menu.addAction(act_izin)

        act_izout = QAction("电流轴缩小", self)
        act_izout.setShortcut("Ctrl+Shift+-")
        act_izout.triggered.connect(self.canvas.zoom_i_out)
        view_menu.addAction(act_izout)

        act_irst = QAction("重置电流轴", self)
        act_irst.setShortcut("Ctrl+Shift+0")
        act_irst.triggered.connect(self.canvas.reset_i_range)
        view_menu.addAction(act_irst)

        # 帮助菜单
        help_menu = menubar.addMenu("帮助")
        act_about = QAction("关于", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

        # ── 校准菜单 ──
        cal_menu = menubar.addMenu("校准")

        act_load_cal = QAction("加载校准文件...", self)
        act_load_cal.setShortcut("Ctrl+L")
        act_load_cal.triggered.connect(self._load_calibration_file)
        cal_menu.addAction(act_load_cal)

        act_save_cal = QAction("保存当前校准为...", self)
        act_save_cal.setShortcut("Ctrl+Shift+S")
        act_save_cal.triggered.connect(self._save_calibration_file_as)
        cal_menu.addAction(act_save_cal)

        cal_menu.addSeparator()

        self.act_auto_save_cal = QAction("校准后自动保存文件", self)
        self.act_auto_save_cal.setCheckable(True)
        self.act_auto_save_cal.setChecked(self._cal_auto_save)
        self.act_auto_save_cal.triggered.connect(self._toggle_auto_save_cal)
        cal_menu.addAction(self.act_auto_save_cal)

        self.act_auto_load_cal = QAction("启动时自动加载上次校准", self)
        self.act_auto_load_cal.setCheckable(True)
        self.act_auto_load_cal.setChecked(self._cal_auto_load_last)
        self.act_auto_load_cal.triggered.connect(self._toggle_auto_load_cal)
        cal_menu.addAction(self.act_auto_load_cal)

        cal_menu.addSeparator()

        act_manage_cal = QAction("管理校准配置...", self)
        act_manage_cal.triggered.connect(self._show_calibration_manager)
        cal_menu.addAction(act_manage_cal)

    # ── 信号连接 ────────────────────────────────────────

    def _connect_signals(self):
        self.serial.data_batch.connect(self._on_data_batch)
        self.serial.data_received.connect(self._on_data)
        self.serial.cal_result.connect(self._on_cal_result)
        self.serial.log_message.connect(self._on_log)
        self.serial.error_occurred.connect(self._on_error)
        self.serial.connection_changed.connect(self._on_connection_changed)

        # 时间轴滑动滚动条
        self.scroll_slider.valueChanged.connect(self._on_scroll_changed)

        # 自动滚屏 checkbox 同时控制波形图
        self.chk_autoscroll.toggled.connect(self._on_autoscroll_toggled)

    # ── 状态恢复 ────────────────────────────────────────

    def _restore_state(self):
        # 窗口位置/大小
        wcfg = self.cfg.get("window", {})
        self.resize(wcfg.get("width", 1400), wcfg.get("height", 800))
        self.move(wcfg.get("x", 100), wcfg.get("y", 100))

        # 视图范围
        view_cfg = self.cfg.get("view", {})
        self.canvas.view_range = view_cfg.get("view_range", 10.0)
        self.canvas.i_range = view_cfg.get("i_range", 10.0)
        self.canvas.i_auto_range = view_cfg.get("i_auto_range", False)
        self.btn_i_auto.setChecked(self.canvas.i_auto_range)

        # 恢复上次通道选择（默认全部不选）
        ch_cfg = self.cfg.get("channels", {})
        enabled_mask = ch_cfg.get("enabled_mask", 0x00)
        display_list = ch_cfg.get("display_list", [])
        for i in range(8):
            self.chk_collect[i].setChecked(bool(enabled_mask & (1 << i)))
            self.chk_display[i].setChecked(i in display_list)
        self._sync_channel_state_from_ui()

        # 串口列表 + 恢复上次选择（延迟 100ms 让窗口先显示）
        last_port = self.cfg.get("serial", {}).get("port", "")
        if last_port:
            # 预设上次的串口号（即使还没扫描到，也先显示）
            self.cmb_port.addItem(last_port, last_port)
        QTimer.singleShot(100, self._refresh_ports)

        last_baud = str(self.cfg.get("serial", {}).get("baudrate", 921600))
        idx_b = self.cmb_baud.findText(last_baud)
        if idx_b >= 0:
            self.cmb_baud.setCurrentIndex(idx_b)

    # ============================================================
    #  串口控制
    # ============================================================

    def _refresh_ports(self):
        """枚举可用串口（后台线程，不阻塞 GUI）。"""
        if self._scanning_ports:
            return
        self._scanning_ports = True
        self._lbl_status_conn.setText("扫描串口中...")
        # 每次创建新的 PortScanner（QThread 不可复用）
        self._port_scanner = PortScanner()
        self._port_scanner.ports_found.connect(self._on_ports_found)
        self._port_scanner.start()

    def _on_ports_found(self, ports):
        """后台线程返回串口列表后更新 UI。"""
        self._scanning_ports = False
        current = self.cmb_port.currentData()
        self.cmb_port.clear()
        for p in ports:
            label = f"{p.device}"
            if p.description:
                label += f" — {p.description[:40]}"
            self.cmb_port.addItem(label, p.device)
        if current:
            idx = self.cmb_port.findData(current)
            if idx >= 0:
                self.cmb_port.setCurrentIndex(idx)
        self._lbl_status_conn.setText("未连接")

    def _toggle_serial(self):
        if self.btn_open.text() == "打开串口":
            port = self.cmb_port.currentData()
            if not port:
                QMessageBox.warning(self, "错误", "请先选择串口")
                return
            baud = int(self.cmb_baud.currentText())
            try:
                self.serial.open(port, baud)
                self.btn_open.setText("关闭串口")
                self.btn_start.setEnabled(True)
                self.btn_start.setText("开始采集 [F7]" if not self.chk_presample.isChecked() else "预采样 [F7]")
                self.btn_zero.setEnabled(True)
                self._log(f"串口 {port} {baud}bps 已打开")
                update_serial(port, baud)
            except Exception as e:
                QMessageBox.critical(self, "串口错误", str(e))
        else:
            self._stop_collect()
            self.serial.close()
            self.btn_open.setText("打开串口")
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.btn_zero.setEnabled(False)
            self._log("串口已关闭")

    def _send_cmd(self, cmd: str):
        line = cmd + "\r\n"
        self.serial.send(line)
        self._log(f">>> {cmd}")

    def _sync_channel_state_from_ui(self):
        """把复选框状态同步到内部状态。"""
        self._ch_enabled = [self.chk_collect[i].isChecked() for i in range(8)]
        self._ch_display = [i for i in range(8) if self.chk_display[i].isChecked()]
        self.store.set_all_channels_enabled(False)
        for i in range(8):
            self.store.set_channel_enabled(i, self._ch_enabled[i])
        # 更新绘图可见通道
        self.canvas.set_display_channels(self._ch_display)

    def _on_collect_changed(self, index: int, state: int):
        """采集通道勾选变化：同步取消/勾上绘图通道。"""
        if self._syncing_channels:
            return
        self._syncing_channels = True
        try:
            checked = bool(state)
            self._ch_enabled[index] = checked
            self.store.set_channel_enabled(index, checked)
            # 取消采集→取消绘图；勾上采集→勾上绘图
            if self.chk_display[index].isChecked() != checked:
                self.chk_display[index].setChecked(checked)
            self._ch_display = [i for i in range(8) if self.chk_display[i].isChecked()]
            self.canvas.set_display_channels(self._ch_display)
        finally:
            self._syncing_channels = False

    def _on_display_changed(self, index: int, state: int):
        """绘图通道勾选变化。"""
        if self._syncing_channels:
            return
        self._syncing_channels = True
        try:
            checked = bool(state)
            # 勾上绘图通道 → 要求对应采集通道已勾选
            if checked and not self.chk_collect[index].isChecked():
                self.chk_collect[index].setChecked(True)
                self._ch_enabled[index] = True
                self.store.set_channel_enabled(index, True)
            self._ch_display = [i for i in range(8) if self.chk_display[i].isChecked()]
            self.canvas.set_display_channels(self._ch_display)
        finally:
            self._syncing_channels = False

    def _set_all_collect(self, checked: bool):
        self._syncing_channels = True
        try:
            for i in range(8):
                self.chk_collect[i].setChecked(checked)
                self._ch_enabled[i] = checked
                self.store.set_channel_enabled(i, checked)
                if self.chk_display[i].isChecked() != checked:
                    self.chk_display[i].setChecked(checked)
            self._ch_display = [i for i in range(8) if self.chk_display[i].isChecked()]
            self.canvas.set_display_channels(self._ch_display)
        finally:
            self._syncing_channels = False

    # ============================================================
    #  采集控制
    # ============================================================

    def _on_presample_toggled(self, checked: bool):
        """预采样模式切换：更新按钮文字。"""
        if checked:
            self.btn_start.setText("预采样 [F7]")
            self.btn_start.setToolTip("点击后开始预采样（不画图），就绪后触发定时采集 (快捷键: F7)")
        else:
            self.btn_start.setText("开始采集 [F7]")
            self.btn_start.setToolTip("开始数据采集 (快捷键: F7)")

    def _start_collect(self):
        """开始采集入口。

        预采样模式:
          第一步: 点击"预采样" → 数据流入但不画图。
          第二步: 点击"触发采集" → 开始定时采集 + 画图，到时自动停止。
        实时模式:
          直接开画图。
        """
        # 同步当前勾选状态到 DataStore
        self._sync_channel_state_from_ui()

        # 检查是否有启用的通道
        if not any(self._ch_enabled):
            QMessageBox.warning(self, "无启用通道", "请至少勾选一个采集通道")
            return
        # ── 预采样第二步：触发定时采集 ──
        if self._presample_active:
            self._trigger_timed()
            return

        # ── 预采样第一步：开始预采样 ──
        if self.chk_presample.isChecked():
            self._presample_active = True
            self.store.clear()
            self._data_counter = 0
            self._last_draw_count = 0

            self._send_cmd("s")
            self._collecting = True
            self.btn_start.setText("触发采集")
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(True)
            self._lbl_status_coll.setText("预采样中")
            self._update_status_led()
            self._log("--- 预采样开始（不画图），就绪后点击[触发采集] ---")
            return

        # ── 实时采集 ──
        self.store.clear()
        self._data_counter = 0
        self._last_draw_count = 0

        self._send_cmd("s")
        self._collecting = True
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._lbl_status_coll.setText("采集中")
        self._update_status_led()
        self._log("--- 开始采集，时间归零 ---")
        self.lbl_countdown.setText("")

        self._start_streaming()

    def _trigger_timed(self):
        """在预采样中触发定时采集：开始画图，倒计时到自动停止。"""
        # 清空预采样数据，重新开始计时的数据
        self.store.clear()
        self._data_counter = 0
        self._last_draw_count = 0

        self._countdown_remaining = self.spin_duration.value()
        self._countdown_timer.start()
        self._update_countdown_label()

        self.btn_start.setEnabled(False)
        self._lbl_status_coll.setText("采集中")
        self._log(f"--- 触发定时采集 {self.spin_duration.value():.1f}s ---")

        self._start_streaming()

    def _stop_collect(self):
        self._send_cmd("x")
        self._collecting = False
        self._presample_active = False
        self.btn_start.setEnabled(True)
        self.btn_start.setText("开始采集 [F7]" if not self.chk_presample.isChecked() else "预采样 [F7]")
        self.btn_stop.setEnabled(False)
        self._lbl_status_coll.setText("已停止")
        self._update_status_led()
        self._countdown_timer.stop()
        self._countdown_remaining = 0
        self.lbl_countdown.setText("")
        self._stop_streaming()

    def _on_zero_calibrate(self):
        """零点校准：向固件发送 "z" 命令。

        固件完成校准后会自动发 0xBB 帧，_on_cal_result 处理偏移设置。
        总共约 5.5 秒后生效。
        """
        enabled = [i for i in range(8) if self._ch_enabled[i]]
        if not enabled:
            QMessageBox.warning(self, "无启用通道", "请至少勾选一个采集通道后再校准")
            return

        self._send_cmd("z")
        ch_names = ", ".join(f"CH{i+1}" for i in enabled)
        self._log(f"--- 零点校准已启动（等待固件完成...）：{ch_names} ---")

    # ============================================================
    #  信号槽
    # ============================================================

    def _on_data_batch(self, batch: list):
        """接收一批采样点，批量加入存储。

        仅在 _collecting 状态下才存储数据，避免打开串口时
        MCU 遗留的旧数据被存入。
        """
        if not self._collecting:
            return
        for ts, values in batch:
            self.store.add(ts, values)
        self._data_counter += len(batch)

    def _on_data(self, ts: float, values: list):
        """接收一个采样点（16 个值），只做最轻量的存储。

        仅在 _collecting 状态下才存储数据。
        """
        if not self._collecting:
            return
        self.store.add(ts, values)
        self._data_counter += 1

    def _on_cal_result(self, vzero_values: list):
        """固件发送了校准结果 0xBB 帧 → 自动设置零点偏移。"""
        # 只对勾选的通道生效
        enabled = [i for i in range(8) if self._ch_enabled[i]]
        if not enabled:
            return
        self.store.apply_cal_result(vzero_values)
        # 强制刷新绘图和统计（让偏移立即在界面上可见）
        self.canvas.update_plot()
        self._on_plot_tick()
        ch_names = ", ".join(f"CH{i+1}" for i in enabled)
        offsets_info = ", ".join(f"CH{i+1} Vzero={vzero_values[i]:.3f}V"
                                 for i in enabled)
        self._log(f"--- 零点校准完成（来自固件）：{ch_names} ---")
        self._log(f"    {offsets_info}")

        # ── 自动保存校准文件 ──
        if self._cal_auto_save:
            vzero, i_off, mask = self.store.get_calibration_state()
            cal_path = save_calibration(
                vzero_values=vzero,
                i_offsets=i_off,
                enabled_mask=mask,
                label=f"自动校准 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            )
            if cal_path:
                save_last_calibration_file(cal_path)
                self._log(f"    校准已自动保存 → {os.path.basename(cal_path)}")

    def _on_log(self, text: str):
        """追加日志行。"""
        self.txt_rx.append(text)
        if self.chk_autoscroll.isChecked():
            sb = self.txt_rx.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _on_scroll_changed(self, value: int):
        """滚动条值改变：用户拖拽或点击时调整时间轴显示范围。"""
        if self._updating_slider:
            return  # 程序化更新，忽略
        n = self.store.count()
        if n < 2:
            return
        # 将滑块值 (0.01s 分辨率) 转换为时间
        t_end = value / 100.0
        t_min, t_max = self.canvas.get_data_time_range()
        # 如果滑到最右端（或接近），恢复自动滚屏
        if value >= self.scroll_slider.maximum() - 5:
            self.canvas.resume_autoscroll()
        else:
            self.canvas.set_scroll_fixed(t_end)

    def _sync_scrollbar_from_canvas(self, t_end: float):
        """鼠标拖拽画布后同步滚动条位置。"""
        self._updating_slider = True
        n = self.store.count()
        if n > 1:
            t_min, t_max = self.canvas.get_data_time_range()
            new_max = max(100, int(t_max * 100))
            self.scroll_slider.setMaximum(new_max)
        value = int(t_end * 100)
        self.scroll_slider.setValue(value)
        self._updating_slider = False

    def _on_autoscroll_toggled(self, checked: bool):
        """自动滚屏开关：控制波形图是否跟随最新数据。"""
        self.canvas.scroll_enabled = checked
        if checked:
            self.canvas.resume_autoscroll()
            # 滚动条回到最右
            t_min, t_max = self.canvas.get_data_time_range()
            new_max = max(100, int(t_max * 100))
            self._updating_slider = True
            self.scroll_slider.setMaximum(new_max)
            self.scroll_slider.setValue(new_max)
            self._updating_slider = False

    def _on_error(self, msg: str):
        """串口错误。"""
        self._log(f"!!! {msg}")
        # 自动关闭串口
        self.btn_open.setText("打开串口")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_zero.setEnabled(False)

    def _on_connection_changed(self, connected: bool):
        if connected:
            port = self.cmb_port.currentData() or "?"
            self._lbl_status_conn.setText(f"已连接 {port}")
        else:
            self._lbl_status_conn.setText("未连接")

    # ============================================================
    #  绘图刷新
    # ============================================================

    def _on_plot_tick(self):
        """100ms 定时刷新波形 + 统计标签。

        预采样模式时不画图。
        """
        n = self.store.count()

        # 预采样时不画图（数据照常存储，等待触发采集后再显示）
        if self._presample_active:
            self.lbl_count.setText(f"采样数: {n}")
            return

        # 只有当有新数据或数据被清空/重置时才重绘
        if self._data_counter > 0 or n != self._last_draw_count:
            self.canvas.update_plot()
            self._last_draw_count = n
            self._data_counter = 0

        # 更新统计标签（轻量操作）
        self.lbl_count.setText(f"采样数: {n}")
        latest = self.store.latest()
        if latest:
            _, vals = latest
            # 显示全部 8 通道实时电流
            for ch in range(8):
                if self._ch_enabled[ch]:
                    i = vals[ch * 2 + 1]
                    self.lbl_ri[ch].setText(f"{i:+.3f} A")
                else:
                    self.lbl_ri[ch].setText("--")
            rate = self.store.sample_rate()
            self.lbl_rate.setText(f"速率: {rate:.0f} Hz")

        # 1 秒平均（每个启用通道独立显示电流）
        for ch in range(8):
            if self._ch_enabled[ch]:
                _, i_avg = self.store.avg_last_seconds(1.0, ch)
                if i_avg is not None:
                    self.lbl_avg_i[ch].setText(f"{i_avg:.3f} A")
                else:
                    self.lbl_avg_i[ch].setText("--")
            else:
                # 未启用通道
                self.lbl_avg_i[ch].setText("--")

        # 电流 Y 轴量程显示
        if self.canvas.i_auto_range:
            self.lbl_i_range.setText("当前: 自动")
        else:
            self.lbl_i_range.setText(f"当前: ±{self.canvas.i_range:.1f} A")

        # 更新滚动条：自动滚屏时跟随最新数据
        if self.chk_autoscroll.isChecked() and self.canvas.has_data():
            t_min, t_max = self.canvas.get_data_time_range()
            span = t_max - t_min
            if span > 0:
                new_max = max(100, int(t_max * 100))
                self.scroll_slider.setMaximum(new_max)
                if not self.canvas.is_scroll_paused():
                    # 自动滚屏 → 滚动条保持在最右
                    self._updating_slider = True
                    self.scroll_slider.setValue(new_max)
                    self._updating_slider = False

    # ============================================================
    #  数据操作
    # ============================================================

    def _clear_data(self):
        """清空数据。"""
        self.store.clear()
        self._data_counter = 0
        self._last_draw_count = 0
        self.canvas.clear_view()
        self.lbl_count.setText("采样数: 0")
        for ch in range(8):
            self.lbl_ri[ch].setText("--")
        self.lbl_rate.setText("速率: -- Hz")
        for ch in range(8):
            self.lbl_avg_i[ch].setText("--")
        self._log("--- 数据已清空 ---")
        # 重置滚动条
        t_min, t_max = self.canvas.get_data_time_range()
        self._updating_slider = True
        self.scroll_slider.setMaximum(max(100, int(t_max * 100)))
        self.scroll_slider.setValue(self.scroll_slider.maximum())
        self._updating_slider = False

    def _export_csv(self):
        """导出 CSV 文件。"""
        n = self.store.count()
        if n == 0:
            QMessageBox.information(self, "提示", "暂无数据可导出")
            return

        default_dir = self.cfg.get("export_path", "")
        if not default_dir or not os.path.isdir(default_dir):
            default_dir = os.path.expanduser("~\\Desktop")
        default_name = datetime.now().strftime("HSTS016L_%Y%m%d_%H%M%S.csv")

        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", os.path.join(default_dir, default_name),
            "CSV 文件 (*.csv)"
        )
        if not filepath:
            return

        try:
            written = self.store.export_csv(filepath)
            update_export_path(os.path.dirname(filepath))
            QMessageBox.information(
                self, "导出完成",
                f"已导出 {written} 行数据到:\n{filepath}"
            )
            self._log(f"--- CSV 已导出: {filepath} ({written} 行) ---")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _apply_range(self):
        """应用时间段筛选。"""
        try:
            t0 = float(self.edit_t0.text() or 0)
            t_last = float(self.store._t[self.store.count() - 1]) if self.store.count() > 0 else 10.0
            t1 = float(self.edit_t1.text() or t_last)
            self.canvas.show_range(t0, t1)
        except (ValueError, IndexError):
            pass

    # ============================================================
    #  实时存盘
    # ============================================================

    def _start_streaming(self):
        """如果勾选了实时存盘，创建 CSV 文件并开始流式写入。"""
        if not self.chk_stream.isChecked():
            return
        default_dir = self.cfg.get("export_path", "") or os.path.expanduser("~\\Desktop")
        filename = datetime.now().strftime("HSTS016L_stream_%Y%m%d_%H%M%S.csv")
        path = os.path.join(default_dir, filename)
        try:
            self.store.start_streaming(path)
            self._log(f"--- 实时存盘: {path} ---")
        except Exception as e:
            self._log(f"!!! 实时存盘启动失败: {e}")
            self.chk_stream.setChecked(False)

    def _stop_streaming(self):
        """停止流式存盘。"""
        if not self.store.is_streaming():
            return
        n = self.store.stop_streaming()
        self._log(f"--- 实时存盘已保存 {n} 行 ---")

    def _toggle_i_auto(self):
        """切换电流 Y 轴自动量程。"""
        self.canvas.toggle_i_auto_range()
        self.btn_i_auto.setChecked(self.canvas.i_auto_range)
        auto = self.canvas.i_auto_range
        # 自动量程开启时, 隐藏手动输入; 关闭时显示
        self.edit_i_span.setEnabled(not auto)
        self.lbl_i_range.setText("当前: 自动" if auto
                                 else f"当前: ±{self.canvas.i_range:.2f} A")

    def _apply_i_range(self):
        """应用手动输入的电流 Y 轴量程。"""
        try:
            text = self.edit_i_span.text().strip()
            if not text:
                return
            span = float(text)
            if span <= 0:
                return
            self.canvas.i_range = span
            self.canvas.i_auto_range = False
            self.btn_i_auto.setChecked(False)
            self.edit_i_span.setEnabled(True)
            self.canvas.ax_i.set_ylim(-span, span)
            self.canvas.fig.canvas.draw_idle()
            self.lbl_i_range.setText(f"当前: ±{span:.2f} A")
        except (ValueError, AttributeError):
            pass

    # ============================================================
    #  定时采集倒计时
    # ============================================================

    def _on_countdown_tick(self):
        """倒计时 100ms 回调。"""
        self._countdown_remaining -= 0.1
        if self._countdown_remaining <= 0:
            self._countdown_remaining = 0
            self._countdown_timer.stop()
            self._update_countdown_label()
            self._log(f"--- 定时 {self.spin_duration.value():.1f}s 到达，自动停止 ---")
            self._stop_collect()
        else:
            self._update_countdown_label()

    def _update_countdown_label(self):
        """更新倒计时标签。"""
        r = self._countdown_remaining
        if r >= 60:
            self.lbl_countdown.setText(f"剩余 {r / 60:.1f} min")
        else:
            self.lbl_countdown.setText(f"剩余 {r:.1f} s")

    # ============================================================
    #  采集状态标签指示（方框，与 Qt Fusion 按钮风格一致）
    # ============================================================

    # 共用字体栈
    _FONT_STACK = "-apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', Tahoma, sans-serif"

    # 统一的样式模板（只换颜色变量即可切换状态）
    _STYLES = {
        "paused": {
            "dot": "#9CA3AF",
            "text": "#000000",
            "bg": "#FFFFFF",
            "border": "#B0B0B0",
            "tooltip": "● 待采样（按 F7 开始）",
        },
        "recording_red": {
            "dot": "#E74C3C",
            "text": "#C0392B",
            "bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFEAEA, stop:1 #FFD4D4)",
            "border": "#E74C3C",
            "tooltip": "● 采集中...",
        },
        "recording_blue": {
            "dot": "#3498DB",
            "text": "#2874A6",
            "bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #E8F2FB, stop:1 #D4E5F4)",
            "border": "#3498DB",
            "tooltip": "● 采集中...",
        },
    }

    def _apply_status_style(self, kind: str, text: str = ""):
        """按 kind 设置样式 + 文字。
        调用者负责停止/启动 _status_anim_timer。
        """
        st = self._STYLES[kind]
        self._set_status_dot(st["dot"])
        if text:
            self.lbl_status_text.setText(text)
        self.lbl_status_text.setStyleSheet(
            f"color: {st['text']}; font-family: {self._FONT_STACK}; "
            f"font-size: 10px; font-weight: 600; "
            f"background: transparent; border: none;"
        )
        self._status_tag.setStyleSheet(
            "QFrame#status_tag { "
            f"background: {st['bg']}; "
            f"border: 1px solid {st['border']}; "
            "border-radius: 3px; "   # 与按钮一致的小圆角，避免直角过硬
            "}"
        )
        self._status_tag.setToolTip(st["tooltip"])

    def _apply_status_tag_style(self, state: str):
        """应用状态标签视觉风格（兼容旧调用）。"""
        if state == "recording":
            # 由动画控制，不在这里切换
            return
        else:  # paused / stopped 统一回到 PAUSED
            self._status_anim_timer.stop()
            self._apply_status_style("paused", "PAUSED")

    def _set_status_dot(self, color: str):
        """画圆形点。"""
        self.lbl_status_dot.setStyleSheet(
            f"background-color: {color}; border: none; border-radius: 4px;"
        )

    def _update_status_led(self):
        """根据采集状态更新指示标签。"""
        if self._collecting:
            if not self._status_anim_timer.isActive():
                self._status_anim_timer.start()
                self._status_anim_state = False
        else:
            self._status_anim_timer.stop()
            self._apply_status_style("paused", "PAUSED")

    def _on_status_anim_tick(self):
        """500ms 定时器：红蓝交替闪烁 RECORDING。"""
        self._status_anim_state = not self._status_anim_state
        kind = "recording_red" if self._status_anim_state else "recording_blue"
        self._apply_status_style(kind, "RECORDING")

    # ============================================================
    #  辅助
    # ============================================================

    def _log(self, msg: str):
        """追加日志行（无时间戳，仅程序事件）。"""
        ts = datetime.now().strftime("[%H:%M:%S] ")
        self.txt_rx.append(ts + msg)

    def _show_about(self):
        QMessageBox.about(
            self, "关于",
            f"<b>{APP_TITLE}</b><br><br>"
            "AMB 磁悬浮轴承铣削电主轴<br>"
            "无测力计切削力辨识研究<br><br>"
            "硬件: HSTS016L 霍尔电流传感器 + STM32F407 USB CDC<br>"
            "8 通道同步采集 | 二进制 18B/帧 @ 10 kHz/通道<br>"
            "Python 3.13.12 | PyQt5 + matplotlib + pyserial<br><br>"
            "湖南科技大学 · 机械工程"
        )

    # ============================================================
    #  校准文件管理
    # ============================================================

    def _load_calibration_file(self, filepath: str = ""):
        """加载校准文件，将 Vzero 偏移应用到所有通道。

        Args:
            filepath: .cal 文件路径，为空则弹出文件选择对话框。
        """
        if not filepath:
            # 弹出文件选择对话框
            cal_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_profiles")
            if os.path.exists(cal_dir):
                start_dir = cal_dir
            else:
                start_dir = os.path.dirname(os.path.abspath(__file__))
            filepath, _ = QFileDialog.getOpenFileName(
                self, "加载校准配置文件", start_dir,
                "校准文件 (*.cal);;所有文件 (*)"
            )
            if not filepath:
                return

        data = load_calibration(filepath)
        if data is None:
            QMessageBox.warning(self, "加载失败",
                                f"无法读取校准文件:\n{filepath}")
            return

        vzero = data["vzero"]
        i_offsets = data["i_offsets"]

        # 应用校准到 DataStore
        self.store.load_calibration(vzero, force_all=True)

        # 记录本次加载的校准文件
        save_last_calibration_file(filepath)

        # 更新显示
        self.canvas.update_plot()
        self._on_plot_tick()

        label = data.get("label", os.path.basename(filepath))
        self._log(f"--- 已加载校准: {label} ---")
        info = ", ".join(f"CH{i+1} Vzero={vzero[i]:.3f}V" for i in range(8))
        self._log(f"    {info}")
        self._log(f"    来源: {os.path.basename(filepath)}")

    def _save_calibration_file_as(self):
        """将当前校准状态保存为命名文件。"""
        # 检查是否有校准数据
        has_any_offset = any(abs(v) > 0.0001 for v in self.store.v_offsets)
        if not has_any_offset:
            reply = QMessageBox.question(
                self, "确认保存",
                "当前没有有效的零点偏移数据（所有通道偏移量为 0）。\n"
                "是否仍要保存？建议先进行一次零点校准再保存。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        cal_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_profiles")
        os.makedirs(cal_dir, exist_ok=True)

        default_name = f"cal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.cal"
        filepath, _ = QFileDialog.getSaveFileName(
            self, "保存校准配置", os.path.join(cal_dir, default_name),
            "校准文件 (*.cal);;所有文件 (*)"
        )
        if not filepath:
            return

        # 询问标签
        from PyQt5.QtWidgets import QInputDialog
        label, ok = QInputDialog.getText(
            self, "校准标签", "请输入此校准配置的标签/描述:",
            text=f"手动保存 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        if not ok:
            return

        vzero, i_off, mask = self.store.get_calibration_state()
        filename = os.path.basename(filepath)
        saved = save_calibration(
            vzero_values=vzero,
            i_offsets=i_off,
            enabled_mask=mask,
            label=label,
            filename=filename,
        )
        if saved:
            save_last_calibration_file(saved)
            self._log(f"--- 校准已保存: {os.path.basename(saved)} ({label}) ---")
            QMessageBox.information(self, "保存成功",
                                    f"校准配置已保存到:\n{saved}")
        else:
            QMessageBox.warning(self, "保存失败", "写入校准文件失败，请检查磁盘权限。")

    def _toggle_auto_save_cal(self, checked: bool):
        """切换「校准后自动保存」选项。"""
        self._cal_auto_save = checked
        update_calibration_settings(auto_save=checked)
        self._log(f"{'启用' if checked else '禁用'}校准后自动保存")

    def _toggle_auto_load_cal(self, checked: bool):
        """切换「启动时自动加载上次校准」选项。"""
        self._cal_auto_load_last = checked
        update_calibration_settings(auto_load_last=checked)
        self._log(f"{'启用' if checked else '禁用'}启动时自动加载上次校准")

    def _auto_load_last_calibration(self):
        """启动时自动加载上次使用的校准文件。"""
        last = get_last_calibration_file()
        if last:
            self._log(f"--- 正在自动加载上次校准文件... ---")
            self._load_calibration_file(last)

    def _show_calibration_manager(self):
        """打开校准配置管理对话框。"""
        from PyQt5.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
            QTableWidgetItem, QPushButton, QHeaderView, QLabel,
            QAbstractItemView, QMessageBox, QInputDialog,
        )

        profiles = list_calibrations()

        dialog = QDialog(self)
        dialog.setWindowTitle("校准配置文件管理")
        dialog.resize(700, 400)

        layout = QVBoxLayout(dialog)

        # 说明
        layout.addWidget(QLabel("以下为已保存的校准配置文件："))

        # 表格
        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["标签 / 描述", "校准时间", "文件名", "备注"])
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

        table.setRowCount(len(profiles))
        for row, p in enumerate(profiles):
            table.setItem(row, 0, QTableWidgetItem(p.get("label", p["filename"])))
            ts_raw = p.get("timestamp", "")
            if ts_raw:
                try:
                    from datetime import datetime as dt2
                    ts_display = dt2.fromisoformat(ts_raw).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    ts_display = ts_raw
            else:
                ts_display = ""
            table.setItem(row, 1, QTableWidgetItem(ts_display))
            table.setItem(row, 2, QTableWidgetItem(p["filename"]))

            # 读取备注
            notes = ""
            try:
                import json
                with open(p["filepath"], "r", encoding="utf-8") as f:
                    d = json.load(f)
                    notes = d.get("notes", "")
            except Exception:
                pass
            table.setItem(row, 3, QTableWidgetItem(notes))

            # 存储 filepath 到 item 的 data 中
            table.item(row, 0).setData(Qt.UserRole, p["filepath"])

        layout.addWidget(table)

        # 操作按钮
        btn_layout = QHBoxLayout()

        def _load_selected():
            row = table.currentRow()
            if row < 0:
                QMessageBox.information(dialog, "提示", "请先选择一个校准文件")
                return
            filepath = table.item(row, 0).data(Qt.UserRole)
            dialog.accept()
            self._load_calibration_file(filepath)

        def _delete_selected():
            row = table.currentRow()
            if row < 0:
                QMessageBox.information(dialog, "提示", "请先选择一个校准文件")
                return
            filepath = table.item(row, 0).data(Qt.UserRole)
            label = table.item(row, 0).text()
            reply = QMessageBox.question(
                dialog, "确认删除",
                f"确定要删除校准配置「{label}」吗？\n此操作不可恢复。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
            if delete_calibration(filepath):
                table.removeRow(row)
                QMessageBox.information(dialog, "已删除", f"已删除「{label}」")
            else:
                QMessageBox.warning(dialog, "删除失败", "无法删除该文件")

        def _edit_label():
            row = table.currentRow()
            if row < 0:
                QMessageBox.information(dialog, "提示", "请先选择一个校准文件")
                return
            filepath = table.item(row, 0).data(Qt.UserRole)
            old_label = table.item(row, 0).text()
            new_label, ok = QInputDialog.getText(
                dialog, "编辑标签", "请输入新的标签:",
                text=old_label
            )
            if ok and new_label:
                if update_calibration_label(filepath, new_label):
                    table.item(row, 0).setText(new_label)

        def _edit_notes():
            row = table.currentRow()
            if row < 0:
                QMessageBox.information(dialog, "提示", "请先选择一个校准文件")
                return
            filepath = table.item(row, 0).data(Qt.UserRole)
            old_notes = table.item(row, 3).text() if table.item(row, 3) else ""
            new_notes, ok = QInputDialog.getMultiLineText(
                dialog, "编辑备注", "请输入备注信息:", old_notes
            ) if hasattr(QInputDialog, "getMultiLineText") else \
                QInputDialog.getText(dialog, "编辑备注", "请输入备注信息:",
                                     text=old_notes)
            if ok and new_notes is not None:
                from calibration_manager import update_calibration_notes
                if update_calibration_notes(filepath, str(new_notes)):
                    if table.item(row, 3):
                        table.item(row, 3).setText(str(new_notes))
                    else:
                        table.setItem(row, 3, QTableWidgetItem(str(new_notes)))

        btn_load = QPushButton("加载选中")
        btn_load.clicked.connect(_load_selected)
        btn_layout.addWidget(btn_load)

        btn_delete = QPushButton("删除")
        btn_delete.clicked.connect(_delete_selected)
        btn_layout.addWidget(btn_delete)

        btn_edit = QPushButton("编辑标签")
        btn_edit.clicked.connect(_edit_label)
        btn_layout.addWidget(btn_edit)

        btn_notes = QPushButton("编辑备注")
        btn_notes.clicked.connect(_edit_notes)
        btn_layout.addWidget(btn_notes)

        btn_layout.addStretch()

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dialog.close)
        btn_layout.addWidget(btn_close)

        layout.addLayout(btn_layout)
        layout.addWidget(QLabel(
            "提示: 双击表格行可快速加载所选校准配置",
            styleSheet="color: #888; font-size: 11px;"
        ))

        # 双击加载
        table.cellDoubleClicked.connect(lambda r, c: (
            _load_selected() if r >= 0 else None
        ))

        dialog.exec_()

    # ============================================================
    #  全局事件过滤（F7 / F8 快捷键）
    # ============================================================

    def eventFilter(self, obj, event):
        """监听全局按键事件，捕获 F7（开始采集）/ F8（停止采集）。
        安装位置：__init__ 中 QApplication.instance().installEventFilter(self)"""
        if event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_F7:
                self._start_collect()
                return True
            if key == Qt.Key_F8:
                self._stop_collect()
                return True
        return super().eventFilter(obj, event)

    # ============================================================
    #  窗口关闭
    # ============================================================

    def closeEvent(self, event):
        """关闭窗口时保存配置、停止线程。"""
        # 停止采集
        self._stop_collect()

        # 停止绘图定时器
        self._plot_timer.stop()

        # 关闭串口线程
        self.serial.close()

        # 确保流式文件关闭
        self._stop_streaming()

        # 保存配置
        g = self.geometry()
        update_window(g.x(), g.y(), g.width(), g.height())
        enabled_mask = 0
        display_list = []
        for i in range(8):
            if self.chk_collect[i].isChecked():
                enabled_mask |= (1 << i)
            if self.chk_display[i].isChecked():
                display_list.append(i)
        update_view(
            self.canvas.view_range,
            i_range=self.canvas.i_range,
            i_auto_range=self.canvas.i_auto_range,
        )
        update_channels(enabled_mask, display_list)

        event.accept()


# ============================================================
#  入口
# ============================================================

# 全局串口保护：崩溃/关闭/控制台关闭时自动释放串口
_win_ref = None


def _safe_close_serial():
    w = _win_ref
    if w is None:
        return
    try:
        if hasattr(w, 'serial') and w.serial is not None:
            w.serial.close()
    except Exception:
        pass


if __name__ == "__main__":
    import atexit
    atexit.register(_safe_close_serial)

    # Windows 控制台关闭事件
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        def _console_handler(ctrl_type):
            if ctrl_type <= 2:
                _safe_close_serial()
                return True
            return False

        _kernel32.SetConsoleCtrlHandler(_console_handler, True)

    try:
        # 高 DPI 支持必须在 QApplication 创建前设置
        if hasattr(Qt, "AA_EnableHighDpiScaling"):
            QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        if hasattr(Qt, "AA_UseHighDpiPixmaps"):
            QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        app.setApplicationName("HSTS016L_Oscilloscope")

        win = MainWindow()
        _win_ref = win
        win.show()
        sys.exit(app.exec_())
    except KeyboardInterrupt:
        print("用户中断 (Ctrl+C)")
    except SystemExit:
        pass
    finally:
        _safe_close_serial()
