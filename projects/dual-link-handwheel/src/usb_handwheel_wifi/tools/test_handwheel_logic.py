#!/usr/bin/env python3
"""Host-side deterministic checks for the firmware's pure control logic."""

from __future__ import annotations

import random
import re
import binascii
from pathlib import Path


VALID = (0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0)


def decode(states: list[int], initial: int = 3) -> list[int]:
    rotary_state = initial
    history = 0
    events: list[int] = []
    for current in states:
        if current == (rotary_state & 3):
            continue
        rotary_state = ((rotary_state << 2) | current) & 15
        if not VALID[rotary_state]:
            continue
        history = ((history << 4) | rotary_state) & 0xFFFF
        if (history & 0xFF) == 0x2B:
            events.append(1)
        elif (history & 0xFF) == 0x17:
            events.append(-1)
    return events


class Button:
    def __init__(self, initial: int = 1) -> None:
        self.raw = initial
        self.stable = initial
        self.changed_at = 0

    def sample(self, value: int, now: int) -> bool:
        if value != self.raw:
            self.raw = value
            self.changed_at = now
        if self.stable != self.raw and now - self.changed_at >= 25:
            self.stable = self.raw
            return self.stable == 0
        return False


class ArmGate:
    """Executable v4 contract model: unarmed inputs must have no backlog."""

    def __init__(self) -> None:
        self.online = False
        self.armed = False
        self.sequence = 0
        self.pending: list[tuple[int, int]] = []

    def hello(self) -> None:
        self.online = True
        self.disarm()

    def arm(self, token: str) -> tuple[str, str, int]:
        assert self.online
        assert 1 <= len(token) <= 20
        assert all(c.isascii() and (c.isalnum() or c in "-_") for c in token)
        self.disarm()
        self.armed = True
        return ("ARMED", token, self.sequence)

    def input(self, direction: int) -> None:
        if self.online and self.armed:
            self.sequence += 1
            self.pending.append((self.sequence, direction))

    def disarm(self) -> None:
        self.armed = False
        self.pending.clear()

    def timeout(self) -> None:
        self.online = False
        self.disarm()

    def transport_fault(self) -> None:
        """Wi-Fi UART loss/send timeout is a session boundary, not a retry."""
        self.online = False
        self.disarm()

    def reset_health_and_session(self) -> None:
        """Model the unarmed local/host HEALTH recovery command."""
        assert not self.armed
        self.pending.clear()
        self.online = True


def crc16_ccitt(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


def cobs_encode(data: bytes) -> bytes:
    out = bytearray([0])
    code_index = 0
    code = 1
    for value in data:
        if value == 0:
            out[code_index] = code
            code_index = len(out)
            out.append(0)
            code = 1
        else:
            out.append(value)
            code += 1
            if code == 0xFF:
                out[code_index] = code
                code_index = len(out)
                out.append(0)
                code = 1
    out[code_index] = code
    out.append(0)
    return bytes(out)


def cobs_decode(encoded: bytes) -> bytes:
    assert encoded and encoded[-1] == 0
    data = encoded[:-1]
    out = bytearray()
    index = 0
    while index < len(data):
        code = data[index]
        assert code != 0
        index += 1
        end = index + code - 1
        assert end <= len(data)
        out.extend(data[index:end])
        index = end
        if code != 0xFF and index < len(data):
            out.append(0)
    return bytes(out)


def make_bridge_frame(frame_type: int, session: int, payload: bytes = b"") -> bytes:
    assert 0 <= session <= 0xFFFF and len(payload) <= 160
    raw = bytearray((1, frame_type, session & 0xFF, session >> 8,
                     len(payload) & 0xFF, len(payload) >> 8))
    raw.extend(payload)
    raw.extend(crc16_ccitt(raw).to_bytes(2, "little"))
    return cobs_encode(bytes(raw))


def parse_bridge_frame(encoded: bytes) -> tuple[int, int, bytes]:
    raw = cobs_decode(encoded)
    assert len(raw) >= 8 and raw[0] == 1
    size = int.from_bytes(raw[4:6], "little")
    assert size <= 160 and len(raw) == size + 8
    assert int.from_bytes(raw[-2:], "little") == crc16_ccitt(raw[:-2])
    return raw[1], int.from_bytes(raw[2:4], "little"), raw[6:-2]


class BridgeSession:
    """Protocol model: stale data cannot regain authority after any boundary."""

    def __init__(self) -> None:
        self.session = 0
        self.online = False
        self.payloads: list[bytes] = []

    def receive(self, frame_type: int, session: int, payload: bytes) -> None:
        if frame_type == 0x20:  # TCP_CONNECTED
            assert session != 0 and not payload
            self.session = session
            self.online = False
            self.payloads.clear()
        elif frame_type == 0x22:  # TCP_DATA
            if session == self.session and session != 0:
                self.payloads.append(payload)
        elif frame_type == 0x21:  # TCP_DISCONNECTED
            if session == self.session:
                self.session = 0
                self.online = False
                self.payloads.clear()
        elif frame_type == 0x23:  # LINK_FAULT is unconditional fail-closed
            assert len(payload) == 1
            self.session = 0
            self.online = False
            self.payloads.clear()
        else:
            self.session = 0
            self.online = False
            self.payloads.clear()


def noisy_cycle(states: list[int], rng: random.Random) -> list[int]:
    result: list[int] = []
    previous = 3
    for current in states:
        result.extend([previous] * rng.randrange(0, 4))
        if rng.random() < 0.7:
            result.extend([current, previous] * rng.randrange(0, 3))
        result.extend([current] * rng.randrange(1, 5))
        previous = current
    return result


def test_decoder() -> None:
    assert decode([1, 0, 2, 3]) == [1]
    assert decode([2, 0, 1, 3]) == [-1]
    assert decode([1, 0, 2, 3] * 100) == [1] * 100
    assert decode([2, 0, 1, 3] * 100) == [-1] * 100
    assert decode([1, 0, 2, 3, 2, 0, 1, 3]) == [1, -1]
    assert decode([3] * 100) == []
    assert decode([0, 3, 0, 3, 0, 3]) == []  # invalid two-bit jumps

    rng = random.Random(0xEC11)
    for _ in range(2000):
        assert decode(noisy_cycle([1, 0, 2, 3], rng)) == [1]
        assert decode(noisy_cycle([2, 0, 1, 3], rng)) == [-1]


def test_buttons_and_steps() -> None:
    button = Button()
    presses = 0
    samples = [(0, 1), (1, 3), (0, 5), (1, 8), (0, 10),
               (0, 20), (0, 34), (0, 35), (0, 60),
               (1, 70), (0, 72), (1, 74), (1, 100)]
    for value, now in samples:
        presses += int(button.sample(value, now))
    assert presses == 1
    assert button.sample(0, 110) is False
    assert button.sample(0, 140) is True

    steps = [10, 50, 100, 500, 1000]
    index = 2
    for _ in range(20):
        index = min(index + 1, len(steps) - 1)
    assert steps[index] == 1000
    for _ in range(20):
        index = max(index - 1, 0)
    assert steps[index] == 10

    axis = 0
    observed = []
    for _ in range(7):
        axis = (axis + 1) % 3
        observed.append("XYZ"[axis])
    assert observed == ["Y", "Z", "X", "Y", "Z", "X", "Y"]


def test_arm_gate() -> None:
    gate = ArmGate()

    # Offline and pre-ARM rotations are deliberately forgotten, never replayed.
    gate.input(1)
    gate.hello()
    gate.input(-1)
    assert gate.sequence == 0 and gate.pending == []

    assert gate.arm("run_20260911") == ("ARMED", "run_20260911", 0)
    gate.input(1)
    assert gate.pending == [(1, 1)]

    # A new HELLO, explicit DISARM, or heartbeat timeout clears all authority.
    gate.hello()
    assert not gate.armed and gate.pending == []
    gate.input(-1)
    assert gate.sequence == 1 and gate.pending == []
    assert gate.arm("second_session") == ("ARMED", "second_session", 1)
    gate.input(-1)
    gate.timeout()
    assert not gate.online and not gate.armed and gate.pending == []

    gate.hello()
    gate.arm("wifi_session")
    gate.input(1)
    gate.transport_fault()
    assert not gate.online and not gate.armed and gate.pending == []

    gate.reset_health_and_session()
    assert gate.online and not gate.armed and gate.pending == []

    gate.hello()
    for invalid in ("", "too-long-token-1234567", "contains space", "中文"):
        try:
            gate.arm(invalid)
        except AssertionError:
            pass
        else:
            raise AssertionError(f"invalid ARM token accepted: {invalid!r}")


def test_bridge_frames_and_sessions() -> None:
    assert crc16_ccitt(b"123456789") == 0x29B1
    rng = random.Random(0xC0B5)
    for length in (0, 1, 2, 63, 64, 160):
        for _ in range(50):
            payload = bytes(rng.randrange(256) for _ in range(length))
            encoded = make_bridge_frame(0x22, 0xA531, payload)
            assert parse_bridge_frame(encoded) == (0x22, 0xA531, payload)

    bridge = BridgeSession()
    bridge.receive(*parse_bridge_frame(make_bridge_frame(0x20, 7)))
    bridge.receive(*parse_bridge_frame(make_bridge_frame(0x22, 7, b"PING\n")))
    assert bridge.session == 7 and bridge.payloads == [b"PING\n"]
    bridge.receive(*parse_bridge_frame(make_bridge_frame(0x21, 7)))
    assert bridge.session == 0 and bridge.payloads == []
    bridge.receive(*parse_bridge_frame(make_bridge_frame(0x20, 8)))
    bridge.receive(*parse_bridge_frame(make_bridge_frame(0x22, 7, b"ARM,old\n")))
    assert bridge.payloads == []
    bridge.receive(*parse_bridge_frame(make_bridge_frame(0x23, 0, b"\x01")))
    assert bridge.session == 0 and bridge.payloads == []


def test_source_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "Core" / "Src" / "handwheel.c").read_text(encoding="utf-8")
    config = (root / "Core" / "Inc" / "hw_config.h").read_text(encoding="utf-8")
    project = (root / "usb_handwheel.uvprojx").read_text(encoding="utf-8")
    esp_source = (root / "Core" / "Src" / "esp01s.c").read_text(encoding="utf-8")
    main_source = (root / "Core" / "Src" / "main.c").read_text(encoding="utf-8")
    irq_source = (root / "Core" / "Src" / "stm32f1xx_it.c").read_text(encoding="utf-8")
    bridge_protocol = (root.parent / "esp01s_handwheel_bridge" /
                       "bridge_protocol.h").read_text(encoding="utf-8")

    assert "{10U, 50U, 100U, 500U, 1000U}" in source
    assert "0x002BU" in source and "0x0017U" in source
    assert "GPIO_PIN_0" in config and "GPIO_PIN_1" in config and "GPIO_PIN_2" in config
    assert "GPIO_PIN_12" in config and "GPIO_PIN_13" in config
    assert "<CreateHexFile>1</CreateHexFile>" in project
    assert "ec11_verify.c" not in project
    assert re.search(r"EVENT,%lu,%c,%u,%d", source)
    assert '"HELLO,HWHEEL,4\\r\\n"' in source
    assert '"ARMED,%s,%lu\\r\\n"' in source
    assert '"DISARMED\\r\\n"' in source
    assert '"HEALTH,RESET,OK\\r\\n"' in source
    assert '"HEALTH,RESET"' in source
    assert "HWHEEL_RECOVERY_HOLD_MS    3000U" in config
    assert '"RECOVERY,REARM,YES\\r\\n"' in source
    assert '"RECOVERY,REARM,NO\\r\\n"' in source
    assert "REARM? DOWN=YES" in source
    assert '"%c?",' in source
    assert "last_direction = 0;" in source
    assert re.search(r"ARM_TOKEN_MAX_LENGTH\s+20U", source)
    assert "if (pc_online && handwheel_armed)" in source
    assert "if (!pc_online || !handwheel_armed) return;" in source
    assert "Handwheel_Disarm();" in source
    assert "ClearEncoderQueue();" in source
    assert "Esp01s_Send" in source
    assert "CDC_Transmit_FS" not in source
    assert "MX_USB_DEVICE_Init" not in main_source
    assert "HAL_NVIC_DisableIRQ(USB_LP_CAN1_RX0_IRQn);" in main_source
    assert "HAL_NVIC_DisableIRQ(USBWakeUp_IRQn);" in main_source
    assert "Esp01s_Task();" in main_source
    assert "esp01s.c" in project
    assert "usb_device.c" not in project
    assert "usbd_cdc_if.c" not in project
    assert "stm32f1xx_hal_pcd.c" not in project
    assert "USB_LP_CAN1_RX0_IRQHandler" not in irq_source
    assert "ESP_FRAME_TCP_CONNECTED" in esp_source
    assert "ESP_FRAME_CONTROL_ACTIVITY" in esp_source
    assert "ESP_FRAME_TCP_DISCONNECTED" in esp_source
    assert "ESP_FRAME_TCP_DATA" in esp_source
    assert "ESP_FRAME_LINK_FAULT" in esp_source
    assert "CobsDecode" in esp_source and "Crc16Ccitt" in esp_source
    assert "active_session" in esp_source
    assert "ESP_PARTIAL_FRAME_TIMEOUT_MS        50U" in esp_source
    assert "AT+CIP" not in esp_source and "+IPD" not in esp_source
    assert "Handwheel_TransportDisconnected();" in esp_source
    assert "FailBridge();" in esp_source
    assert "Esp01s_NotifyControlActivity();" in source
    assert "ResetSessionHealth();" in source
    assert "kStmToTcp = 0x10" in bridge_protocol
    assert "kControlActivity = 0x11" in bridge_protocol
    assert "kTcpConnected = 0x20" in bridge_protocol
    assert "kTcpDisconnected = 0x21" in bridge_protocol
    assert "kTcpData = 0x22" in bridge_protocol
    assert "kLinkFault = 0x23" in bridge_protocol
    assert "kMaxPayload = 160" in bridge_protocol

    forbidden = ("zmotion", "zaux", "setop", "setda", "rapidstop", "motor")
    lowered = source.lower()
    assert all(term not in lowered for term in forbidden)


def main() -> None:
    test_decoder()
    test_buttons_and_steps()
    test_arm_gate()
    test_bridge_frames_and_sessions()
    test_source_contract()
    print("PASS: decoder 4000 noisy cycles; v4 ARM; COBS/CRC/session bridge; source contract")


if __name__ == "__main__":
    main()
