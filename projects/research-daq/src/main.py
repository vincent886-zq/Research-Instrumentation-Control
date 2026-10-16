"""Reliable upper-computer application for the F407 + AD7606 displacement firmware."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

# A migrated .py file may be associated with a Python lacking GUI packages.
# Use the registered Python only after checking that it has all dependencies.
if __name__ == "__main__":
    import importlib.util
    import shutil
    import subprocess

    if any(importlib.util.find_spec(name) is None
           for name in ("PyQt5", "serial", "numpy", "matplotlib")):
        launcher = shutil.which("py")
        if launcher:
            probe = subprocess.run(
                [launcher, "-3", "-c",
                 "import PyQt5, serial, numpy, matplotlib; import sys; print(sys.executable)"],
                capture_output=True, text=True,
            )
            if probe.returncode == 0:
                python = probe.stdout.strip()
                if os.path.normcase(os.path.abspath(python)) != os.path.normcase(os.path.abspath(sys.executable)):
                    sys.exit(subprocess.call(
                        [python, "-B", os.path.abspath(__file__), *sys.argv[1:]]
                    ))

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QStatusBar,
    QProgressDialog, QTextEdit, QVBoxLayout, QWidget, QSplitter, QShortcut,
)
import serial.tools.list_ports

from 配置.config import load as load_config, update_export_path, update_serial
from 核心.data_store import CHANNEL_NAMES, DataStore, NUM_CHANNELS
from 核心.f407_protocol import FRAME_SIZE, SAMPLE_RATE_HZ
from 界面.plot_canvas import PlotCanvas
from 驱动.serial_worker import SerialWorker

APP_TITLE = "F407 AD7606 六路位移采集（协议 v1）"
PLOT_INTERVAL_MS = 100
START_TIMEOUT_S = 2.0


class PortScanner(QThread):
    ports_found = pyqtSignal(list)

    def run(self) -> None:
        self.ports_found.emit(list(serial.tools.list_ports.comports()))


class CsvExportWorker(QThread):
    """Write a stopped in-memory capture without blocking the GUI thread."""

    progress_changed = pyqtSignal(int, int)
    completed = pyqtSignal(int, str)
    failed = pyqtSignal(str)

    def __init__(self, store: DataStore, path: str) -> None:
        super().__init__()
        self._store = store
        self._path = path

    def run(self) -> None:
        try:
            rows = self._store.export_csv(self._path, self.progress_changed.emit)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(rows, self._path)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_config()
        self.store = DataStore()
        self.worker = SerialWorker()
        self.canvas = PlotCanvas()
        self.canvas.bind_store(self.store)
        self._running = False
        self._finalizing = False
        self._session_invalid = False
        self._session_started_monotonic = 0.0
        self._data_since_tick = 0
        self._session_path = ""
        self._scanner: PortScanner | None = None
        self._display_channels: list[int] = []
        self._export_worker: CsvExportWorker | None = None
        self._export_dialog: QProgressDialog | None = None
        self._export_started_monotonic = 0.0

        self._setup_ui()
        self._connect_signals()
        self._restore_config()
        self._refresh_ports()

        self.plot_timer = QTimer(self)
        self.plot_timer.timeout.connect(self._on_tick)
        self.plot_timer.start(PLOT_INTERVAL_MS)

    def _setup_ui(self) -> None:
        self.setWindowTitle(APP_TITLE)
        self.resize(1360, 820)
        status = QStatusBar()
        self.setStatusBar(status)
        self.lbl_connection = QLabel("未连接")
        self.lbl_device = QLabel("设备停止")
        self.lbl_integrity = QLabel("未采集")
        status.addWidget(self.lbl_connection)
        status.addPermanentWidget(self.lbl_device)
        status.addPermanentWidget(self.lbl_integrity)

        central = QWidget()
        self.setCentralWidget(central)
        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left.setMaximumWidth(430)
        layout = QVBoxLayout(left)

        serial_group = QGroupBox("USB CDC 连接")
        grid = QGridLayout(serial_group)
        grid.addWidget(QLabel("端口"), 0, 0)
        self.cmb_port = QComboBox()
        grid.addWidget(self.cmb_port, 0, 1, 1, 3)
        grid.addWidget(QLabel("虚拟波特率"), 1, 0)
        self.cmb_baud = QComboBox()
        self.cmb_baud.addItems(["115200", "921600", "1500000", "2500000"])
        self.cmb_baud.setCurrentText("921600")
        grid.addWidget(self.cmb_baud, 1, 1)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self._refresh_ports)
        grid.addWidget(self.btn_refresh, 1, 2)
        self.btn_open = QPushButton("打开")
        self.btn_open.clicked.connect(self._toggle_connection)
        grid.addWidget(self.btn_open, 1, 3)
        layout.addWidget(serial_group)

        control_group = QGroupBox("会话控制")
        controls = QVBoxLayout(control_group)
        row = QHBoxLayout()
        self.btn_start = QPushButton("开始采集  F7")
        self.btn_start.setEnabled(False)
        self.btn_start.clicked.connect(self.start_capture)
        self.btn_stop = QPushButton("停止采集  F8")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(lambda: self.stop_capture("HOST"))
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        controls.addLayout(row)
        self.chk_record = QCheckBox("可靠录制：CSV + 原始字节 + 元数据")
        self.chk_record.setChecked(True)
        self.chk_record.setEnabled(False)
        controls.addWidget(self.chk_record)
        row = QHBoxLayout()
        self.btn_folder = QPushButton("保存目录")
        self.btn_folder.setEnabled(False)
        self.btn_folder.clicked.connect(self._choose_folder)
        self.btn_export = QPushButton("导出内存 CSV")
        self.btn_export.clicked.connect(self._export_csv)
        self.btn_export.setEnabled(False)
        row.addWidget(self.btn_folder)
        row.addWidget(self.btn_export)
        controls.addLayout(row)
        self.lbl_protocol = QLabel("固件固定：6 通道 / ±5 V / 10,000 Hz / CRC-16")
        self.lbl_protocol.setStyleSheet("color:#555")
        controls.addWidget(self.lbl_protocol)
        layout.addWidget(control_group)

        channels = QGroupBox("显示通道（采集端始终完整保存 CH1–CH6）")
        channel_layout = QGridLayout(channels)
        self.chk_channels: list[QCheckBox] = []
        for index, name in enumerate(CHANNEL_NAMES):
            box = QCheckBox(name)
            box.setChecked(index != 0)  # historical displacement subset: CH2–CH6
            box.toggled.connect(self._update_display_channels)
            self.chk_channels.append(box)
            channel_layout.addWidget(box, index // 3, index % 3)
        layout.addWidget(channels)

        stats_group = QGroupBox("完整性状态")
        stats_layout = QVBoxLayout(stats_group)
        self.lbl_count = QLabel("已接收：0")
        self.lbl_rate = QLabel("到达速率：--")
        self.lbl_frame = QLabel("CRC：0；序号缺口：0；缺失样本：0；设备丢失：0")
        self.lbl_writer = QLabel("录制：未开始")
        for label in (self.lbl_count, self.lbl_rate, self.lbl_frame, self.lbl_writer):
            stats_layout.addWidget(label)
        layout.addWidget(stats_group)

        log_group = QGroupBox("事件日志")
        log_layout = QVBoxLayout(log_group)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        self.log.document().setMaximumBlockCount(3000)
        log_layout.addWidget(self.log)
        layout.addWidget(log_group, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
        right_layout.addWidget(NavigationToolbar(self.canvas, self))
        self.canvas.ax.set_ylabel("AD7606 input voltage (V)")
        self.canvas.ax.set_xlabel("sample-clock time (s)")
        right_layout.addWidget(self.canvas, 1)
        note = QLabel("时间轴由 sample_index / 10,000 Hz 给出；PC 接收时间只用于诊断，不参与 FRF 时间轴。")
        note.setStyleSheet("color:#555")
        right_layout.addWidget(note)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        root = QHBoxLayout(central)
        root.addWidget(splitter)

        self.shortcut_start = QShortcut(QKeySequence("F7"), self)
        self.shortcut_start.activated.connect(self.start_capture)
        self.shortcut_stop = QShortcut(QKeySequence("F8"), self)
        self.shortcut_stop.activated.connect(lambda: self.stop_capture("HOST"))

    def _connect_signals(self) -> None:
        self.worker.data_batch.connect(self._on_data_batch)
        self.worker.log_message.connect(self._log)
        self.worker.error_occurred.connect(self._on_worker_error)
        self.worker.connection_changed.connect(self._on_connection_changed)

    def _restore_config(self) -> None:
        serial_cfg = self.cfg.get("serial", {})
        baud = str(serial_cfg.get("baudrate", "921600"))
        if self.cmb_baud.findText(baud) >= 0:
            self.cmb_baud.setCurrentText(baud)
        self._update_display_channels()

    def _refresh_ports(self) -> None:
        if self._scanner and self._scanner.isRunning():
            return
        self._scanner = PortScanner()
        self._scanner.ports_found.connect(self._ports_found)
        self._scanner.start()

    def _ports_found(self, ports: list) -> None:
        previous = self.cmb_port.currentData() or self.cfg.get("serial", {}).get("port", "")
        self.cmb_port.clear()
        for port in ports:
            label = f"{port.device} — {port.description or 'USB serial'}"
            self.cmb_port.addItem(label, port.device)
        index = self.cmb_port.findData(previous)
        if index >= 0:
            self.cmb_port.setCurrentIndex(index)

    def _toggle_connection(self) -> None:
        if self.worker.is_connected():
            if self._running:
                self.stop_capture("DISCONNECT")
                self._finalize_capture("DISCONNECT")
            self.worker.close()
            return
        port = self.cmb_port.currentData()
        if not port:
            QMessageBox.warning(self, "未选择端口", "请选择 F407 的 USB CDC 虚拟串口。")
            return
        try:
            baud = int(self.cmb_baud.currentText())
            self.worker.open(port, baud)
            update_serial(port, baud)
            self.worker.send_command("info")
            self._log(f"已打开 {port}（USB CDC；虚拟波特率 {baud}）")
        except Exception as exc:
            QMessageBox.critical(self, "打开失败", str(exc))

    def _on_connection_changed(self, connected: bool) -> None:
        self.lbl_connection.setText("已连接 F407" if connected else "未连接")
        self.btn_open.setText("关闭" if connected else "打开")
        self.btn_start.setEnabled(connected and not self._running and self._export_worker is None)
        self.btn_stop.setEnabled(connected and self._running)
        self.chk_record.setEnabled(connected and not self._running)
        self.btn_folder.setEnabled(connected and not self._running)
        self._update_export_button()

    def _update_export_button(self) -> None:
        self.btn_export.setEnabled(bool(self.store.count()) and not self._running and
                                   not self._finalizing and self._export_worker is None)

    def _choose_folder(self) -> None:
        current = self.cfg.get("export_path", "") or os.path.join(os.path.dirname(__file__), "csv")
        folder = QFileDialog.getExistingDirectory(self, "选择采集保存目录", current)
        if folder:
            update_export_path(folder)
            self.cfg["export_path"] = folder
            self._log(f"保存目录：{folder}")

    def _new_capture_path(self) -> str:
        folder = self.cfg.get("export_path", "") or os.path.join(os.path.dirname(__file__), "csv")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, datetime.now().strftime("F407_displacement_%Y%m%d_%H%M%S.csv"))

    def start_capture(self) -> None:
        if not self.worker.is_connected() or self._running or self._export_worker is not None:
            return
        self.store.clear()
        self.canvas.clear_view()
        self._data_since_tick = 0
        self._session_invalid = False
        self._finalizing = False
        self._session_path = ""
        if self.chk_record.isChecked():
            self._session_path = self._new_capture_path()
            metadata = {
                "schema": "f407-ad7606-displacement-capture/v1",
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "sample_rate_hz": SAMPLE_RATE_HZ,
                "channels": CHANNEL_NAMES,
                "adc_range": "+/-5V",
                "protocol": "A55A/v1/32B/CRC16-CCITT-FALSE",
                "channel_interpretation_candidate": {"CH2": "bx", "CH3": "by", "CH4": "fy", "CH5": "zd", "CH6": "fx"},
            }
            self.store.start_streaming(self._session_path, metadata)
            self.lbl_writer.setText(f"录制：{os.path.basename(self._session_path)}.part")
        else:
            self.lbl_writer.setText("录制：预览模式（不形成正式数据）")
        # The worker installs the raw sink and sends start atomically between
        # serial reads, so the first binary frames cannot bypass raw.bin.
        raw_sink = self.store.enqueue_raw if self.chk_record.isChecked() else None
        if not self.worker.begin_capture(raw_sink):
            self._session_invalid = True
            self._finalize_capture("START_SEND_FAILED")
            return
        self._session_started_monotonic = time.monotonic()
        self._running = True
        self.lbl_device.setText("采集中（等待有效帧）")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.chk_record.setEnabled(False)
        self.btn_folder.setEnabled(False)
        self._update_export_button()
        self._log("已发送 start；等待 CRC 正确的 F407 数据帧。")

    def stop_capture(self, reason: str) -> None:
        if not self._running or self._finalizing:
            return
        self._finalizing = True
        self.worker.send_command("stop")
        self.lbl_device.setText("停止中，正在排空 USB 数据")
        self._log(f"已发送 stop（原因：{reason}）")
        QTimer.singleShot(600, lambda: self._finalize_capture(reason))

    def _finalize_capture(self, reason: str) -> None:
        if not self._running and not self.store.is_streaming():
            return
        self._running = False
        self._finalizing = False
        self.worker.set_raw_sink(None)
        stats = self.worker.snapshot_stats()
        valid = bool(self.chk_record.isChecked() and not self._session_invalid and self.worker.integrity_ok())
        final = {
            "stop_reason": reason,
            "integrity": stats,
            "host_integrity_ok": valid,
            # Every accepted protocol-v1 frame is 32 bytes.  A formal capture
            # must preserve at least that many original bytes, plus any text.
            "raw_minimum_frame_bytes": int(stats["valid_frames"]) * FRAME_SIZE,
        }
        result = self.store.stop_streaming(final, valid)
        if result:
            if result["capture_complete"]:
                self.lbl_writer.setText(f"录制完成：{result['rows_written']} 行")
                self._log(f"会话完成：{result['csv_path']}")
            else:
                self.lbl_writer.setText("录制无效：保留 .part 与元数据供排查")
                self._log(f"!!! 会话无效：{result}")
        self.lbl_device.setText("设备停止")
        self.btn_start.setEnabled(self.worker.is_connected())
        self.btn_stop.setEnabled(False)
        self.chk_record.setEnabled(self.worker.is_connected())
        self.btn_folder.setEnabled(self.worker.is_connected())
        self._update_export_button()

    def _on_data_batch(self, batch: list) -> None:
        if not self._running:
            return
        if not self.store.add_batch(batch):
            self._session_invalid = True
            self._log("!!! 写盘队列溢出：本会话将判为无效")
        self._data_since_tick += len(batch)

    def _on_worker_error(self, message: str) -> None:
        self._session_invalid = True
        self._log("!!! " + message)
        if self._running:
            self.stop_capture("SERIAL_ERROR")

    def _update_display_channels(self) -> None:
        self._display_channels = [i for i, checkbox in enumerate(self.chk_channels) if checkbox.isChecked()]
        self.canvas.set_display_channels(self._display_channels)

    def _on_tick(self) -> None:
        stats = self.worker.snapshot_stats()
        received = self.store.count()
        rate = self._data_since_tick * (1000.0 / PLOT_INTERVAL_MS)
        self._data_since_tick = 0
        self.lbl_count.setText(f"已接收：{received} 帧")
        self.lbl_rate.setText(f"到达速率：{rate:.0f} 帧/s（目标 {SAMPLE_RATE_HZ}）")
        self.lbl_frame.setText(
            f"CRC：{stats['crc_errors']}；序号缺口：{stats['sequence_gaps']}；"
            f"缺失样本：{stats['missing_samples']}；设备丢失：{stats['device_drop_total']}"
        )
        clean = self.worker.integrity_ok()
        self.lbl_integrity.setText("完整性：通过" if clean else "完整性：异常/待验证")
        self.lbl_integrity.setStyleSheet("color:#198754" if clean else "color:#b02a37; font-weight:bold")

        if self._running and not stats["valid_frames"] and time.monotonic() - self._session_started_monotonic > START_TIMEOUT_S:
            self._session_invalid = True
            self._log("!!! 启动后 2 秒未收到有效 CRC 帧，自动停止并标记无效")
            self.stop_capture("NO_VALID_FRAME")
        if self._running and stats["last_frame_monotonic"] is not None and time.monotonic() - float(stats["last_frame_monotonic"]) > 1.0:
            self._session_invalid = True
            self._log("!!! 已超过 1 秒无有效帧，自动停止并标记无效")
            self.stop_capture("FRAME_TIMEOUT")

        if received:
            end = float(self.store.t_data[-1])
            start = max(0.0, end - 10.0)
            times, channels = self.store.get_window(start, end + 0.01)
            if len(times):
                for index in range(NUM_CHANNELS):
                    if index in self._display_channels:
                        self.canvas.lines[index].set_data(times[::10], channels[index][::10])
                        self.canvas.lines[index].set_visible(True)
                    else:
                        self.canvas.lines[index].set_visible(False)
                self.canvas.ax.set_xlim(start, max(10.0, end))
                visible = [(index, channels[index]) for index in self._display_channels]
                self.canvas._calc_auto_range(times, visible)
                self.canvas.fig.canvas.draw_idle()

    def _export_csv(self) -> None:
        if self._export_worker is not None:
            return
        if self._running:
            QMessageBox.information(self, "正在采集", "请停止采集后，再另存当前内存数据。")
            return
        if not self.store.count():
            QMessageBox.information(self, "无数据", "当前内存中没有可另存的样本。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "另存内存缓存", self._new_capture_path(), "CSV (*.csv)")
        if not path:
            return
        self._export_started_monotonic = time.monotonic()
        self._export_dialog = QProgressDialog("正在准备 CSV 数据…", None, 0, 1000, self)
        self._export_dialog.setWindowTitle("正在另存 CSV")
        self._export_dialog.setWindowModality(Qt.WindowModal)
        self._export_dialog.setAutoClose(False)
        self._export_dialog.setAutoReset(False)
        self._export_dialog.setMinimumDuration(0)
        self._export_dialog.setValue(0)
        self._export_dialog.show()
        self._export_worker = CsvExportWorker(self.store, path)
        self._export_worker.progress_changed.connect(self._on_export_progress)
        self._export_worker.completed.connect(self._on_export_completed)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.finished.connect(self._on_export_finished)
        self._export_worker.start()
        self._update_export_button()
        self.btn_start.setEnabled(False)
        self._log(f"开始另存内存缓存：{path}")

    @staticmethod
    def _format_eta(seconds: float) -> str:
        seconds = max(0, round(seconds))
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes} 分 {seconds:02d} 秒" if minutes else f"{seconds} 秒"

    def _on_export_progress(self, completed: int, total: int) -> None:
        if not self._export_dialog or total <= 0:
            return
        elapsed = max(0.001, time.monotonic() - self._export_started_monotonic)
        ratio = completed / total
        if completed:
            remaining = (total - completed) * elapsed / completed
            eta_text = f"，预计还需 {self._format_eta(remaining)}"
        else:
            eta_text = ""
        self._export_dialog.setLabelText(
            f"正在另存 CSV：{completed:,} / {total:,} 行（{ratio:.1%}）{eta_text}"
        )
        self._export_dialog.setValue(round(ratio * 1000))

    def _close_export_dialog(self) -> None:
        if self._export_dialog:
            self._export_dialog.close()
            self._export_dialog.deleteLater()
            self._export_dialog = None

    def _on_export_completed(self, count: int, path: str) -> None:
        self._close_export_dialog()
        self._log(f"内存缓存已另存：{path}（{count:,} 行）")
        QMessageBox.information(self, "另存完成", f"已保存 {count:,} 行到：\n{path}")

    def _on_export_failed(self, message: str) -> None:
        self._close_export_dialog()
        self._log(f"!!! 另存失败：{message}")
        QMessageBox.critical(self, "另存失败", f"CSV 另存失败：{message}\n\n已写入的部分会保留为 .part 文件。")

    def _on_export_finished(self) -> None:
        worker = self._export_worker
        self._export_worker = None
        if worker:
            worker.deleteLater()
        self._on_connection_changed(self.worker.is_connected())

    def _export_csv_sync_legacy(self) -> None:
        if not self.store.count():
            QMessageBox.information(self, "无数据", "当前内存中没有可导出的样本。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出内存缓存", self._new_capture_path(), "CSV (*.csv)")
        if path:
            count = self.store.export_csv(path)
            self._log(f"导出内存缓存：{path}（{count} 行）")

    def _log(self, text: str) -> None:
        self.log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")

    def closeEvent(self, event) -> None:
        if self._export_worker is not None:
            QMessageBox.information(self, "正在另存", "CSV 正在另存，请等待保存完成后再关闭程序。")
            event.ignore()
            return
        if self._running:
            self._session_invalid = True
            self.worker.send_command("stop")
            self._finalize_capture("APP_CLOSE")
        self.worker.close()
        event.accept()


def main() -> int:
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setApplicationName("F407 AD7606 Displacement Collector")
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
