# -*- coding: utf-8 -*-
"""No-hardware acceptance for ESP-01S TCP transport and motion gateway."""

import importlib.util
import os
import socket
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "main_ui2.py"


def load_target():
    spec = importlib.util.spec_from_file_location("safe_ui_serial_stage1", ENTRY)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(module)
    return module


def prepare_pending_arm(window, token="hw_session_01"):
    """Model the UI state after Enter_Mouse_Mode sent ARM, before its reply."""
    window._mouse_mode = True
    window._handwheel_input_source = "wifi_pending"
    window._handwheel_arm_token = token
    window._handwheel_arm_pending = True


class RecordingSerial:
    """Minimal transport fake that records local state visible at DISARM TX."""

    def __init__(self, window):
        self.window = window
        self.writes = []
        self.state_when_written = []

    def isOpen(self):
        return True

    def bytesToWrite(self):
        return 0

    def write(self, data):
        self.writes.append(bytes(data))
        self.state_when_written.append((
            self.window._handwheel_input_source,
            self.window._handwheel_arm_pending,
            self.window._handwheel_arm_acknowledged,
            self.window._handwheel_status_armed,
        ))
        return len(data)


def test_wifi_transport(module):
    """Exercise a real local TCP round trip without an ESP or motion UI."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(1)
    host, port = listener.getsockname()
    transport = module.WifiHandwheelTransport()
    try:
        transport.begin_connect(host, port)
        deadline = time.monotonic() + 1.0
        events = []
        while not transport.isOpen() and time.monotonic() < deadline:
            events.extend(transport.poll())
            time.sleep(0.005)
        assert transport.isOpen()
        assert ("connected", None) in events

        peer, _ = listener.accept()
        peer.settimeout(1)
        assert transport.write(b"HELLO\n") == 6
        deadline = time.monotonic() + 1.0
        received = b""
        while received != b"HELLO\n" and time.monotonic() < deadline:
            transport.poll()
            try:
                received += peer.recv(64)
            except BlockingIOError:
                pass
            time.sleep(0.005)
        assert received == b"HELLO\n"

        peer.sendall(b"HELLO,HWHEEL,4\r\n")
        deadline = time.monotonic() + 1.0
        incoming = b""
        while not incoming and time.monotonic() < deadline:
            for event, payload in transport.poll():
                if event == "data":
                    incoming += payload
            time.sleep(0.005)
        assert incoming == b"HELLO,HWHEEL,4\r\n"

        peer.close()
        deadline = time.monotonic() + 1.0
        saw_error = False
        while time.monotonic() < deadline:
            saw_error = any(event == "error" for event, _ in transport.poll())
            if saw_error:
                break
            time.sleep(0.005)
        assert saw_error and not transport.isOpen()
    finally:
        transport.close()
        listener.close()


def main():
    module = load_target()
    app = QApplication.instance() or QApplication([])
    window = module.MainWindow()
    source = ENTRY.read_text(encoding="utf-8")

    assert window.HandwheelSerialLabel.text() == "WiFi手轮"
    assert window.HandwheelPortConnect.text() == "连接"
    assert window.ui.Log_text.geometry().y() == 82
    assert window.ui.Log_text.geometry().height() == 489
    assert "fields == ['HELLO', 'HWHEEL', '4']" in source
    assert "class WifiHandwheelTransport" in source
    assert "QSerialPort" not in source
    assert 'self._handwheel_serial.write(b"DISARM\\n")' in source
    assert 'self._handwheel_input_source = \'wifi_pending\'' in source
    assert 'secrets.compare_digest' in source

    forbidden_calls = []
    window.M_P = lambda *args, **kwargs: forbidden_calls.append(("M_P", args))
    window.M_N = lambda *args, **kwargs: forbidden_calls.append(("M_N", args))
    window.Start_Dzz = lambda *args, **kwargs: forbidden_calls.append(("Start", args))
    window.Emergency_Stop = lambda *args, **kwargs: forbidden_calls.append(("EStop", args))

    # v3 and incomplete serial frames can never reach a movement entry point.
    window._consume_handwheel_serial_bytes(b"HELLO,HW")
    window._consume_handwheel_serial_bytes(b"HEEL,3\r\nEVENT,1,X,100,1\r\n")
    assert window._handwheel_protocol_faulted
    assert forbidden_calls == []

    window._consume_handwheel_serial_bytes(b"A" * 4097)
    assert "接收缓存溢出" in window.ui.Log_text.toPlainText()
    assert forbidden_calls == []
    assert not window._handwheel_serial.isOpen()

    # v4 transport readiness alone is insufficient: an explicit matching ARM
    # acknowledgement plus armed STATUS are both required.
    window._reset_handwheel_protocol_state()
    motion_calls = []
    window.M_P = lambda axis, **kwargs: motion_calls.append(("M_P", axis)) or True
    window.M_N = lambda axis, **kwargs: motion_calls.append(("M_N", axis)) or True
    window._consume_handwheel_serial_bytes(
        b"HELLO,HWHEEL,4\r\n"
        b"STATUS,X,100,1,0\r\n"
        b"HEALTH,0,0,0\r\n")
    assert window._handwheel_transport_ready()
    assert not window._handwheel_protocol_ready()

    # HEALTH is a valid current-session diagnostic reply, not a permanent
    # historical lock. Actual transport/sequence faults still revoke ARM.
    window._reset_handwheel_protocol_state()
    window._consume_handwheel_serial_bytes(
        b"HELLO,HWHEEL,4\r\n"
        b"STATUS,X,100,1,0\r\n"
        b"HEALTH,0,1,0\r\n")
    assert window._handwheel_transport_ready()
    assert window._handwheel_health_counts == (0, 1, 0)
    assert not window._handwheel_protocol_faulted

    # A claimed lower-side authorization without the matching PC ARM reply
    # must be treated as a protocol fault, never as authority to move.
    window._consume_handwheel_serial_bytes(b"STATUS,X,100,1,1\r\n")
    assert window._handwheel_protocol_faulted
    assert window._handwheel_input_source is None
    assert motion_calls == []

    window._reset_handwheel_protocol_state()
    window._consume_handwheel_serial_bytes(
        b"HELLO,HWHEEL,4\r\n"
        b"STATUS,X,100,1,0\r\n"
        b"HEALTH,0,0,0\r\n")

    prepare_pending_arm(window)
    window._consume_handwheel_serial_bytes(b"ARMED,hw_session_01,40\r\n")
    assert not window._handwheel_protocol_ready()
    assert window._handwheel_last_sequence == 40
    window._consume_handwheel_serial_bytes(b"STATUS,X,100,1,1\r\n")
    assert window._handwheel_protocol_ready()
    assert window._handwheel_input_source == "wifi"

    window._consume_handwheel_serial_bytes(
        b"CONFIG,41,AXIS,Y\r\n"
        b"CONFIG,42,STEP,50\r\n"
        b"EVENT,43,Y,50,1\r\n"
        b"EVENT,44,Y,50,-1\r\n")
    assert window._mouse_axis == "Y"
    assert window.ui.DMove_dist.text() == "0.05"
    assert motion_calls == [("M_P", "Y"), ("M_N", "Y")]

    # Any replay immediately revokes the PC side before a second movement.
    window._consume_handwheel_serial_bytes(b"EVENT,44,Y,50,-1\r\n")
    assert motion_calls == [("M_P", "Y"), ("M_N", "Y")]
    assert window._handwheel_protocol_faulted
    assert window._handwheel_input_source is None
    assert not window._mouse_mode

    # A mismatched ARM reply cannot attach a pendant authorized by another
    # session, even before it has emitted a single EVENT.
    window._reset_handwheel_protocol_state()
    window._consume_handwheel_serial_bytes(
        b"HELLO,HWHEEL,4\r\nSTATUS,Y,500,1,0\r\nHEALTH,0,0,0\r\n")
    prepare_pending_arm(window, "hw_expected_02")
    window._consume_handwheel_serial_bytes(b"ARMED,hw_other_02,100\r\n")
    assert window._handwheel_protocol_faulted
    assert window._handwheel_input_source is None
    assert not window._mouse_mode

    # Fault/exit paths clear the PC-side gate before placing DISARM on the
    # serial queue; a delayed lower reply can therefore not retain authority.
    real_serial = window._handwheel_serial
    fake_serial = RecordingSerial(window)
    window._handwheel_serial = fake_serial
    window._mouse_mode = True
    window._handwheel_input_source = "wifi"
    window._handwheel_arm_pending = False
    window._handwheel_arm_acknowledged = True
    window._handwheel_status_armed = True
    assert window._disarm_usb_handwheel("test explicit revoke")
    assert fake_serial.writes == [b"DISARM\n"]
    assert fake_serial.state_when_written == [(None, False, False, False)]
    window._handwheel_serial = real_serial

    test_wifi_transport(module)
    window.close()
    print("PASS: Wi-Fi TCP round trip, v4 ARM/status gate, existing M_P/M_N reuse, fault revoke, close")


if __name__ == "__main__":
    main()
