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
        self.manual_speed_calls = []
        self.direct_move_calls = []
        self.SetCmd = -1
        self.MoveCmd = -1
        self.Move_type = 0
        self.Fd_Type = 0
        self.Now_GCode_RST = False
        self.Move_status = False
        self.current_gcode_feed_mm_min = None
        self.GCode_expected_dpos = None
        self.GCode_motion_deadline = 0.0
        self.GCode_motion_description = ""
        self.last_gcode_fault = ""
        self.G04_dwell_ms = 0
        self.T1sigl = 0
        self.AxSpd_G0_D = 30
        self.AxSpd_G0_Z_D = 5
        self.safety_inputs_verified = True
        self.rapidstop_calls = 0
        self.enable_calls = []
        self.fail_stop = False
        self.fail_rapidstop = False
        self.fail_manual_speed = False
        self.dpos = [0.0, 0.0, 0.0, 0.0]

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
        if self.fail_stop:
            raise RuntimeError("injected spindle-stop failure")
        self.spindle_state = "STOPPED"
        self.spindle_stop_confirmed = True
        return True

    def Set_AxSpd(self, speed):
        self.feed_calls.append(float(speed))
        self.current_gcode_feed_mm_min = float(speed)
        return float(speed)

    def Set_Manual_AxSpd(self, speeds):
        if self.fail_manual_speed:
            raise RuntimeError("injected manual-speed failure")
        values = {axis: float(speeds[axis]) for axis in ("X", "Y", "Z")}
        self.manual_speed_calls.append(values)
        return values

    def ZMC_1Move(self, axis, distance):
        value = float(distance)
        self.direct_move_calls.append((axis, value))
        index = {"X": 0, "Y": 1, "Z": 3}[axis]
        self.dpos[index] += value
        return True

    def ZMC_1MoveAbs(self, axis, target):
        value = float(target)
        self.direct_move_calls.append((axis, value))
        index = {"X": 0, "Y": 1, "Z": 3}[axis]
        self.dpos[index] = value
        return True

    def Dpos_Det(self):
        return list(self.dpos)

    Mpos_Det = Dpos_Det

    def Move_Det(self):
        return [-1, -1, -1]

    def Btn_Det(self):
        return [0, 0, 0]

    def Safe_Det(self, trigger_stop=True):
        return [1] * 8

    def Dzz_zs_Det(self):
        return int(self.Dzz_zs)

    def ZMC_MMove_all(self, axes, values):
        self.motion_calls.append((list(axes), list(values)))

    def AxSpd_G0(self):
        return True

    def Rapidstop(self):
        self.rapidstop_calls += 1
        if self.fail_rapidstop:
            raise RuntimeError("injected axis-rapidstop failure")
        return True

    def Enable_Axs(self, status):
        self.enable_calls.append(int(status))
        return True

    def latch_estop(self, caller=""):
        try:
            self.stop_spindle(caller)
            self.spindle_state = "FAULT"
            return True
        except Exception as exc:
            self.last_safety_error = str(exc)
            self.spindle_state = "FAULT"
            return False


def main():
    module = load_target()
    app = QApplication.instance() or QApplication([])

    class InitialReadFailure:
        def Btn_Det(self):
            raise RuntimeError("injected initial button read failure")

        def Safe_Det(self):
            raise RuntimeError("injected initial safety read failure")

        def Dpos_Det(self):
            raise RuntimeError("injected initial DPOS read failure")

        def Dzz_zs_Det(self):
            raise RuntimeError("injected initial spindle read failure")

    # A transient first DPOS failure must preserve the normal four-slot shape
    # (X, Y, unused axis 2, Z on controller axis 3).
    initial_failure_worker = module.Worker(InitialReadFailure())
    assert initial_failure_worker.Dpos_hold == [0.0, 0.0, 0.0, 0.0]

    window = module.MainWindow()
    fake = FakeSpindle()
    window.ZMC = fake
    window._zmc_inited = True
    window.ui.DZZ_r_data.setText("12000")

    # Endpoint verification uses application DPOS (Z on slot 3) and refuses
    # to advance when an idle axis did not actually reach its recorded target.
    endpoint_worker = module.Worker(fake)
    fake.GCode_expected_dpos = {"X": 1.0, "Z": -0.05}
    fake.dpos = [1.005, 0.0, 0.0, -0.045]
    assert endpoint_worker._verify_gcode_endpoint()
    fake.dpos = [1.02, 0.0, 0.0, -0.05]
    try:
        endpoint_worker._verify_gcode_endpoint()
    except module.SpindleSafetyError as exc:
        assert "未到达目标" in str(exc)
    else:
        raise AssertionError("endpoint mismatch was accepted")
    fake.dpos = [0.0, 0.0, 0.0, 0.0]
    fake.GCode_expected_dpos = None

    # Worker-side limit/timeout/endpoint faults use independent stop channels,
    # clear progression/tracking, and preserve a user-visible fault reason.
    worker_fault_fake = FakeSpindle()
    worker_fault = module.Worker(worker_fault_fake)
    worker_fault.GCode_St_all = True
    worker_fault_fake.Move_status = True
    worker_fault_fake.GCode_expected_dpos = {"X": 10.0}
    worker_fault_fake.GCode_motion_deadline = module.time.monotonic() - 1.0
    assert worker_fault._gcode_motion_timeout_due()
    worker_fault._latch_gcode_fault("injected limit transition", 2)
    assert not worker_fault.GCode_St_all
    assert not worker_fault_fake.Move_status
    assert worker_fault_fake.GCode_expected_dpos is None
    assert worker_fault_fake.rapidstop_calls == 1
    assert worker_fault_fake.enable_calls == [0]
    assert "injected limit transition" in worker_fault_fake.last_gcode_fault

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

    # Manual soft-limit recovery is intentionally narrower than normal jog.
    # Cancellation must have no side effects; confirmation clamps both speed
    # and distance, locks AUTO/handwheel sources, and returns to normal only
    # after a fresh DPOS report confirms the coordinate is back in range.
    window._axes_enabled = True
    window._spindle_backend_mode = "REAL"
    window._require_motion_interlocks = lambda **kwargs: True
    window.ui.ManualSpeedX.setText("240")
    window.ui.ManualSpeedY.setText("240")
    window.ui.ManualSpeedZ.setText("180")
    window.ui.DMove_dist.setText("10")
    window.ui.lineEdit_2.setText("10")
    fake.dpos[3] = 21.5
    window.Z_data[1] = 21.5
    window.F5_data("ALL")
    move_count = len(fake.direct_move_calls)
    speed_count = len(fake.manual_speed_calls)
    window._confirm_soft_limit_recovery = lambda *args: False
    assert not window.M_P("Z", source="panel")
    assert len(fake.direct_move_calls) == move_count
    assert len(fake.manual_speed_calls) == speed_count
    assert not window._soft_limit_recovery_axes

    window._confirm_soft_limit_recovery = lambda *args: True
    assert window.M_P("Z", source="panel")
    assert fake.direct_move_calls[-1] == ("Z", 1.0)
    assert fake.manual_speed_calls[-1] == {"X": 100 / 60, "Y": 100 / 60, "Z": 100 / 60}
    assert window._soft_limit_recovery_axes == {"Z"}
    assert "#d12f2f" in window.ui.Z_Current.styleSheet()
    assert not window.ui.Gcode_run.isEnabled()
    assert not window.ui.pushButton_23.isEnabled()
    assert not window.ui.pushButton_31.isEnabled()

    # Even if a direct controller DPOS read has come back in range before the
    # GUI consumes its DPOS signal, the pending recovery authorization keeps
    # every panel move bounded.  It must never fall back to the editable
    # normal profile in that short asynchronous gap.
    assert window.M_P("X", source="panel")
    assert fake.direct_move_calls[-1] == ("X", 1.0)
    assert fake.manual_speed_calls[-1] == {"X": 100 / 60, "Y": 100 / 60, "Z": 100 / 60}
    assert window._soft_limit_recovery_axes == {"X", "Z"}
    window.Deal_in_signel("Dpos0", 1.0)
    assert window._soft_limit_recovery_axes == {"Z"}

    move_count = len(fake.direct_move_calls)
    assert not window.M_P("Z", source="wifi_handwheel")
    assert not window.M_P("Z", source="keyboard_handwheel")
    assert not window.M_P("Z", source="continuous")
    assert not window.M_P("Z", source="unclassified")
    assert len(fake.direct_move_calls) == move_count
    start_count = len(fake.start_calls)
    window.Start_Dzz()
    assert len(fake.start_calls) == start_count
    window.ui.GCode_text.setPlainText("G91\nG1 Z-0.1 F10")
    window.Run_Gcode()
    assert not window._gcode_running
    assert "存在软限位越界恢复状态" in window.ui.Log_text.toPlainText()

    # One bounded inward manual move remains available.  Only the subsequent
    # position notification ends recovery and restores the normal profile.
    assert window.M_N("Z", source="panel")
    assert fake.direct_move_calls[-1] == ("Z", -1.0)
    window.Deal_in_signel("Dpos3", 21.5)
    assert not window._soft_limit_recovery_axes
    assert "#d12f2f" not in window.ui.Z_Current.styleSheet()
    assert window._manual_speed_profile_dirty
    assert window.ui.Gcode_run.isEnabled()
    assert window.ui.pushButton_23.isEnabled()

    # Leaving recovery does not leave the controller at 100 mm/min.  The
    # following ordinary jog must explicitly restore the operator profile.
    window.ui.DMove_dist.setText("0.1")
    assert window.M_N("Z", source="panel")
    assert fake.direct_move_calls[-1] == ("Z", -0.1)
    assert fake.manual_speed_calls[-1] == {"X": 4.0, "Y": 4.0, "Z": 3.0}

    # A controller-side rejection of the forced 100 mm/min profile must be a
    # hard no-op: no recovery authorization and, critically, no move command.
    fake.dpos[3] = 21.5
    window.Z_data[1] = 21.5
    window.F5_data("ALL")
    window.ui.DMove_dist.setText("10")
    move_count = len(fake.direct_move_calls)
    fake.fail_manual_speed = True
    assert not window.M_P("Z", source="panel")
    assert len(fake.direct_move_calls) == move_count
    assert not window._soft_limit_recovery_axes
    fake.fail_manual_speed = False

    # Absolute recovery and recovery while a spindle is running are always
    # refused even when the caller claims to be an on-screen panel button.
    window.Move_Type = 1
    fake.dpos[3] = 21.5
    window.Z_data[1] = 21.5
    try:
        window._manual_move_checked("Z", 30.0, source="panel")
    except module.SpindleSafetyError as exc:
        assert "相对点动" in str(exc)
    else:
        raise AssertionError("absolute soft-limit recovery was accepted")
    window.Move_Type = 0
    fake.spindle_state = "RUNNING"
    fake.spindle_stop_confirmed = False
    try:
        window._manual_move_checked("Z", 10.0, source="panel")
    except module.SpindleSafetyError as exc:
        assert "主轴已确认停止" in str(exc)
    else:
        raise AssertionError("recovery with running spindle was accepted")
    fake.spindle_state = "STOPPED"
    fake.spindle_stop_confirmed = True
    fake.dpos = [0.0, 0.0, 0.0, 0.0]
    window.X_data[1] = 0.0
    window.Y_data[1] = 0.0
    window.Z_data[1] = 0.0
    window.F5_data("ALL")

    # The first valid REAL G-code run after a fresh connection/enable must use
    # the existing magnetic-bearing/water-cooling confirmation.  Cancel keeps
    # the one-shot pending; confirmation is consumed only by a real run.
    class RunWorker:
        GCode_St_all = False

    window.Worker = RunWorker()
    window._spindle_backend_mode = "REAL"
    window._axes_enabled = True
    window._gcode_first_run_confirmed = False
    window._require_motion_interlocks = lambda **kwargs: True
    window.ui.GCode_text.setPlainText("G91\nG94\nM3 S8000\nM5")
    first_run_prompts = []
    window._confirm_spindle_start = lambda: first_run_prompts.append(False) or False
    window.Run_Gcode()
    assert not window._gcode_running
    assert not window._gcode_first_run_confirmed
    assert len(first_run_prompts) == 1

    window._confirm_spindle_start = lambda: first_run_prompts.append(True) or True
    window.Run_Gcode()
    assert window._gcode_running
    assert window._gcode_first_run_confirmed
    assert len(first_run_prompts) == 2
    window._stop_gcode_safely("test first-run cleanup", rapidstop=False)

    window._confirm_spindle_start = lambda: (_ for _ in ()).throw(
        AssertionError("first-run confirmation repeated in same connection"))
    window.Run_Gcode()
    assert window._gcode_running
    window._stop_gcode_safely("test later-run cleanup", rapidstop=False)

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

    # Blank lines, comments and a trailing newline belong to the editor but
    # not to the executable list.  They must not be used as an execution
    # boundary, while their source positions remain available for highlighting.
    window.ui.GCode_text.setPlainText(
        "G91\n\n; comment\nG94\nM03 S8000\nG04 X10\n"
        "G01 Z-0.05 F10\nG01 Y15 F50\nG04 X5\n"
        "G01 Z1 F50\nM05\n\n")
    window.deal_Gcode()
    assert len(window.Mat_Gcode) == 9
    assert [item["source_line"] for item in window.Mat_Gcode] == [
        0, 3, 4, 5, 6, 7, 8, 9, 10]

    # Ambiguous spindle/dwell syntax is rejected before any command is sent.
    original_warning = module.QMessageBox.warning
    module.QMessageBox.warning = lambda *args, **kwargs: None
    try:
        for invalid_program in (
                "M3", "S8000", "G04 X-1", "G04 X1 P2",
                "M3 S45001", "G04 X601",
                "G1 X1 F601", "G1 Z1 F301", "G95 F0.1\nG1 X1"):
            window.ui.GCode_text.setPlainText(invalid_program)
            window.deal_Gcode()
            assert window.Mat_Gcode == []
    finally:
        module.QMessageBox.warning = original_warning

    window.ui.GCode_text.setPlainText(
        "G91\n\n; comment\nG94\nM03 S8000\nG04 X10\n"
        "G01 Z-0.05 F10\nG01 Y15 F50\nG04 X5\n"
        "G01 Z1 F50\nM05\n\n")
    window.deal_Gcode()
    assert len(window.Mat_Gcode) == 9

    # Execute the user's exact sample in TEST mode without waiting out the
    # dwell timers; every parsed line must remain valid through dispatch.
    motion_count = len(fake.motion_calls)
    for index in range(len(window.Mat_Gcode)):
        window.Gcode2ZMC(index)
        if window._gcode_dwell_timer.isActive():
            window._cancel_gcode_dwell()
    assert fake.start_calls[-1] == (8000, "GCode_M3_line_2")
    expected_sample_motion = [
        (["Z"], [-0.05]), (["Y"], [15.0]), (["Z"], [1.0])]
    assert fake.motion_calls[motion_count:] == expected_sample_motion, (
        fake.motion_calls[motion_count:])
    assert fake.stop_calls[-1] == "Gcode_M5"

    # Feed caps are applied to effective mm/min, including G95 conversion.
    test_backend = module.TcpSpindleBackend()
    assert test_backend.Set_AxSpd(600) == 600
    try:
        test_backend.Set_AxSpd(601)
    except module.SpindleSafetyError:
        pass
    else:
        raise AssertionError("unsafe XY feed was accepted")
    test_backend.Fd_Type = 1
    test_backend.Dzz_zs = 0
    try:
        test_backend.Set_AxSpd(0.1)
    except module.SpindleSafetyError:
        pass
    else:
        raise AssertionError("G95 with zero spindle speed was accepted")

    # Model the worker asking for one more command after M05.  Natural end is
    # idempotent, never indexes Mat_Gcode[9], and does not issue an axis rapid
    # stop (the spindle stop remains independently confirmed).
    window._gcode_running = True
    window.Now_Run_GCode = len(window.Mat_Gcode) - 1
    stop_count = len(fake.stop_calls)
    rapidstop_count = fake.rapidstop_calls
    window.Run_Next_Gcode()
    assert not window._gcode_running
    assert window.Now_Run_GCode == len(window.Mat_Gcode) - 1
    assert len(fake.stop_calls) == stop_count + 1
    assert fake.stop_calls[-1] == "GCode:G代码自然结束"
    assert fake.rapidstop_calls == rapidstop_count
    assert "G代码执行异常" not in window.ui.Log_text.toPlainText()
    window.Run_Next_Gcode()
    assert len(fake.stop_calls) == stop_count + 1

    # Defence in depth: a direct invalid caller gets a controlled safety error,
    # never a raw Python list-index exception.
    try:
        window.Gcode2ZMC(len(window.Mat_Gcode))
    except module.SpindleSafetyError as exc:
        assert "内部索引越界" in str(exc)
    else:
        raise AssertionError("out-of-range G-code index was accepted")

    # An execution exception (M4 is deliberately unsupported) must stop the
    # program, request an axis rapid stop, confirm spindle stop, and log why.
    fake.estop_latched = False
    window.Mat_Gcode = [
        {"cmd": ["M"], "value": [4], "source_line": 0},
    ]
    window.ui.GCode_text.setPlainText("M4")
    window._gcode_running = True
    window.Now_Run_GCode = -1
    rapidstop_count = fake.rapidstop_calls
    stop_count = len(fake.stop_calls)
    window.Run_Next_Gcode()
    assert not window._gcode_running
    assert fake.rapidstop_calls == rapidstop_count + 1
    assert len(fake.stop_calls) == stop_count + 1
    assert fake.stop_calls[-1] == "GCode:G代码异常中止"
    assert "M4反转未实现" in window.ui.Log_text.toPlainText()

    # Stop-channel failures must never be swallowed: the other stop channel is
    # still attempted, the result is false, and the UI fault is latched/logged.
    fake.estop_latched = False
    fake.fail_rapidstop = True
    stop_count = len(fake.stop_calls)
    enable_count = len(fake.enable_calls)
    assert not window._stop_gcode_safely("注入轴停失败", rapidstop=True)
    assert len(fake.stop_calls) == stop_count + 1
    assert len(fake.enable_calls) == enable_count + 1
    assert fake.enable_calls[-1] == 0
    assert fake.estop_latched
    assert "Rapidstop失败" in window.ui.Log_text.toPlainText()
    fake.fail_rapidstop = False

    fake.estop_latched = False
    fake.fail_stop = True
    rapidstop_count = fake.rapidstop_calls
    assert not window._stop_gcode_safely("注入主轴停失败", rapidstop=True)
    assert fake.rapidstop_calls == rapidstop_count + 1
    assert fake.estop_latched
    assert "主轴停机未确认" in window.ui.Log_text.toPlainText()
    fake.fail_stop = False
    fake.is_connected = False

    window.close()
    print("PASS: spindle confirmation, G-code EOF, soft-limit recovery and injected stop-fault reporting")


if __name__ == "__main__":
    main()
