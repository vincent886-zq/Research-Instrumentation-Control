#!/usr/bin/env python3
"""Host-side deterministic checks for the firmware's pure control logic."""

from __future__ import annotations

import random
import re
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

    def reset_health_and_session(self) -> None:
        """Model the unarmed local/host HEALTH recovery command."""
        assert not self.armed
        self.pending.clear()
        self.online = True


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


def test_source_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "Core" / "Src" / "handwheel.c").read_text(encoding="utf-8")
    config = (root / "Core" / "Inc" / "hw_config.h").read_text(encoding="utf-8")
    project = (root / "usb_handwheel.uvprojx").read_text(encoding="utf-8")

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

    forbidden = ("zmotion", "zaux", "setop", "setda", "rapidstop", "motor")
    lowered = source.lower()
    assert all(term not in lowered for term in forbidden)


def main() -> None:
    test_decoder()
    test_buttons_and_steps()
    test_arm_gate()
    test_source_contract()
    print("PASS: decoder 4000 noisy cycles; buttons/debounce; v4 ARM gate; source contract")


if __name__ == "__main__":
    main()
