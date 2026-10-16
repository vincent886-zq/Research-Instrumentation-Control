# -*- coding: utf-8 -*-
"""No-hardware acceptance for the manual spindle-start confirmation gate."""

import importlib.util
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "main_ui2.py"


def load_target():
    spec = importlib.util.spec_from_file_location("safe_ui_spindle_confirm", ENTRY)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(module)
    return module


class FakeSpindle:
    def __init__(self):
        self.spindle_state = "STOPPED"
        self.spindle_stop_confirmed = True
        self.estop_latched = False
        self.is_connected = True
        self.Dzz_zs = 0.0
        self.start_calls = []
        self.stop_calls = []
        self.feed_calls = []
        self.motion_calls = []
        self.SetCmd = -1
        self.MoveCmd = -1
        self.Move_type = 0
        self.Fd_Type = 0

    @staticmethod
    def _normalize_spindle_rpm(value, allow_zero=True):
        rpm = float(value)
        if not allow_zero and rpm <= 0:
            raise ValueError("RPM must be positive")
        return rpm

    def start_spindle(self, rpm, caller=""):
        self.start_calls.append((rpm, caller))
        self.spindle_state = "RUNNING"
        self.spindle_stop_confirmed = False
        return True

    def Set_Dzz_zs(self, rpm):
        self.Dzz_zs = float(rpm)
        return True

    def stop_spindle(self, caller=""):
        self.stop_calls.append(caller)
        self.spindle_state = "STOPPED"
        self.spindle_stop_confirmed = True
        return True

    def Set_AxSpd(self, speed):
        self.feed_calls.append(float(speed))

    def Dpos_Det(self):
        return [0.0, 0.0, 0.0, 0.0]

    def ZMC_MMove_all(self, axes, values):
        self.motion_calls.append((list(axes), list(values)))


def main():
    module = load_target()
    app = QApplication.instance() or QApplication([])
    window = module.MainWindow()
    fake = FakeSpindle()
    window.ZMC = fake
    window._zmc_inited = True
    window.ui.DZZ_r_data.setText("12000")

    source = ENTRY.read_text(encoding="utf-8")
    assert "请确认：\\n磁悬浮已开启\\n水冷已开启" in source
    assert not window.SpindleRunningCheckBox.isEnabled()
    assert not window.SpindleRunningCheckBox.isChecked()

    # Cancelling must be a true no-op: no controller START and no status tick.
    window._confirm_spindle_start = lambda: False
    window.Start_Dzz()
    assert fake.start_calls == []
    assert not window.SpindleRunningCheckBox.isChecked()

    # A confirmed manual start marks the read-only state only after START wins.
    window._confirm_spindle_start = lambda: True
    window.Start_Dzz()
    assert fake.start_calls == [(12000.0, "UI_Start")]
    assert window.SpindleRunningCheckBox.isChecked()

    # The same button's confirmed STOP path clears the display state.
    window.Start_Dzz()
    assert fake.stop_calls == ["UI_Stop"]
    assert not window.SpindleRunningCheckBox.isChecked()

    # G-code M3/M5 use their existing non-interactive path.  The manual
    # confirmation must neither appear nor interrupt M3 -> G1 -> M5.
    confirmation_calls = []
    window._confirm_spindle_start = lambda: confirmation_calls.append(True)
    window._spindle_backend_mode = "TEST"
    window.Mat_Gcode = [
        {"cmd": ["M", "S"], "value": [3, 8000]},
        {"cmd": ["G", "X", "F"], "value": [1, 5, 120]},
        {"cmd": ["M"], "value": [5]},
    ]
    window.Gcode2ZMC(0)
    assert fake.start_calls[-1] == (8000, "GCode_M3_line_0")
    assert window.SpindleRunningCheckBox.isChecked()
    window.Gcode2ZMC(1)
    assert fake.feed_calls[-1] == 120.0
    assert fake.motion_calls[-1] == (["X"], [5.0])
    window.Gcode2ZMC(2)
    assert fake.stop_calls[-1] == "Gcode_M5"
    assert not window.SpindleRunningCheckBox.isChecked()
    assert confirmation_calls == []

    window.close()
    print("PASS: manual confirmation, G-code M3/G1/M5, indicator reset")


if __name__ == "__main__":
    main()
