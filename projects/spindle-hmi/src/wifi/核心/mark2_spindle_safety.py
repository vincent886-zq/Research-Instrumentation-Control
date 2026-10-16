"""Frozen DZZ_MARK2 spindle safety core for the UI2 integration.

The methods in ``Mark2SpindleSafetyCore`` are copied from the accepted
``DZZ_MARK2/main3.py`` implementation.  UI2 supplies the ZMotion adapter,
locks, safety-monitor callback and axis emergency actions; this module owns
the only REAL spindle Start/Stop/E-stop implementation used at runtime.
"""

import ctypes
import math
import time


c_int = ctypes.c_int
c_float = ctypes.c_float
c_p = ctypes.pointer


class SpindleSafetyError(RuntimeError):
    """A spindle command was not confirmed by the controller output readback."""


class Mark2SpindleSafetyCore:
    @staticmethod
    def _normalize_spindle_rpm(value, allow_zero=True):
        try:
            rpm = float(value)
        except (TypeError, ValueError) as exc:
            raise SpindleSafetyError(f'Invalid spindle speed: {value!r}') from exc
        if not math.isfinite(rpm):
            raise SpindleSafetyError('Spindle speed must be finite')
        if rpm < (0 if allow_zero else 0.000001) or rpm > 45000:
            raise SpindleSafetyError(f'Spindle speed {rpm:g} is outside 0..45000 rpm')
        return rpm

    def _read_spindle_outputs(self):
        op = c_int(-1)
        da = c_float(-1)
        self._call('ZAux_Direct_GetOp', self.handle, 7, c_p(op))
        self._call('ZAux_Direct_GetDA', self.handle, 0, c_p(da))
        if not math.isfinite(da.value):
            raise SpindleSafetyError(f'DA0 readback is not finite: {da.value!r}')
        return op.value, da.value

    def _verify_spindle_outputs(self, expected_op, expected_da, samples=3,
                                interval=0.02, abort_on_stop=False):
        """Require consecutive stable controller-command readbacks."""
        samples = max(1, int(samples))
        for sample in range(samples):
            if abort_on_stop and self._stop_requested.is_set():
                raise SpindleSafetyError('Stop pre-empted start')
            op, da = self._read_spindle_outputs()
            if op != int(expected_op) or abs(da - float(expected_da)) > 1.0:
                raise SpindleSafetyError(
                    f'Spindle output mismatch: expected IO7={int(expected_op)}, '
                    f'DA0={float(expected_da):.3f}; got IO7={op}, DA0={da:.3f} '
                    f'(sample {sample + 1}/{samples})'
                )
            if sample + 1 < samples:
                time.sleep(max(0.0, float(interval)))
        return True

    def Set_Dzz_zs(self, value):
        """Stage a speed only while fully stopped; live speed changes are locked."""
        rpm = self._normalize_spindle_rpm(value)
        with self._io_lock:
            if rpm > 0 and self.estop_latched:
                raise SpindleSafetyError('E-stop/fault is latched')
            if self.spindle_state != 'STOPPED':
                raise SpindleSafetyError('Speed setpoint can only be changed while spindle is stopped')
            self.Dzz_zs = rpm
            return True

    def stop_spindle(self, caller='', attempts=3):
        """Stop RUN and speed independently; success requires both output readbacks."""
        self._stop_requested.set()
        with self._io_lock:
            if self.handle.value in (None, 0) or not self.is_connected:
                self.Dzz_zs = 0
                self.spindle_state = 'FAULT'
                self.spindle_stop_confirmed = False
                self.estop_latched = True
                self.last_safety_error = (
                    'Cannot confirm spindle stop because controller communication is unavailable'
                )
                raise SpindleSafetyError(self.last_safety_error)
            self.spindle_state = 'STOPPING'
            errors = []
            for attempt in range(1, max(1, attempts) + 1):
                try:
                    self._call('ZAux_Direct_SetOp', self.handle, 7, c_int(0))
                except Exception as exc:
                    errors.append(f'IO7 stop attempt {attempt}: {exc}')
                try:
                    self._call('ZAux_Direct_SetDA', self.handle, 0, c_float(0))
                except Exception as exc:
                    errors.append(f'DA0 stop attempt {attempt}: {exc}')
                try:
                    self._verify_spindle_outputs(0, 0.0, samples=3, interval=0.02)
                    self.Dzz_zs = 0
                    self.spindle_state = 'STOPPED'
                    self.spindle_stop_confirmed = True
                    self.last_safety_error = ''
                    return True
                except Exception as exc:
                    errors.append(f'stable stop verification attempt {attempt}: {exc}')
            self.estop_latched = True
            self.spindle_state = 'FAULT'
            self.spindle_stop_confirmed = False
            self.last_safety_error = '; '.join(errors[-6:]) or 'Spindle stop was not confirmed'
            raise SpindleSafetyError(f'Spindle stop not confirmed ({caller}): {self.last_safety_error}')

    def start_spindle(self, value, caller=''):
        rpm = self._normalize_spindle_rpm(value, allow_zero=False)
        self._require_safety_monitor_healthy()
        if self._stop_requested.is_set() and (not self.spindle_stop_confirmed or self.spindle_state != 'STOPPED'):
            raise SpindleSafetyError('Start rejected while stop is pending')
        with self._io_lock:
            self._require_handle()
            if self.estop_latched:
                raise SpindleSafetyError('E-stop/fault is latched; reset only after physical inspection')
            try:
                safe = self.Safe_Det(trigger_stop=False)
                # IO6/IO16 are NC emergency circuits: 0 means pressed/open.
                if safe[6] == 0 or safe[7] == 0:
                    raise SpindleSafetyError('Physical emergency stop is active')

                # DI3 is the cabinet physical Stop button and is active high in
                # the existing machine mapping.  A held button must block Start,
                # even when no input transition was observed by the Worker.
                physical_stop = c_int(0)
                self._call('ZAux_Direct_GetIn', self.handle, 3, c_p(physical_stop))
                if physical_stop.value == 1:
                    raise SpindleSafetyError('Physical stop button DI3 is active')

                # Internal state is not sufficient: another client or retained
                # controller logic may have changed the physical command outputs.
                pre_op, pre_da = self._read_spindle_outputs()
                if pre_op != 0 or abs(pre_da) > 1.0:
                    raise SpindleSafetyError(
                        f'Start preflight requires zero outputs, got IO7={pre_op}, DA0={pre_da:.3f}'
                    )

                if self._stop_requested.is_set():
                    if not self.spindle_stop_confirmed or self.spindle_state != 'STOPPED':
                        raise SpindleSafetyError('Start rejected while stop is pending')
                    self._stop_requested.clear()
                self.spindle_state = 'STARTING'
                raw_da = rpm * 4095.0 / 45000.0
                self._call('ZAux_Direct_SetDA', self.handle, 0, c_float(raw_da))
                self._verify_spindle_outputs(0, raw_da, samples=3, interval=0.02,
                                             abort_on_stop=True)
                if self._stop_requested.is_set():
                    raise SpindleSafetyError('Stop pre-empted start')
                self._call('ZAux_Direct_SetOp', self.handle, 7, c_int(1))
                self._verify_spindle_outputs(1, raw_da, samples=3, interval=0.02,
                                             abort_on_stop=True)
                if self._stop_requested.is_set():
                    raise SpindleSafetyError('Stop pre-empted start')
            except Exception as exc:
                self.estop_latched = True
                try:
                    self.stop_spindle(f'Start_Failure:{caller}')
                except Exception:
                    pass
                # A fail-safe zero confirmation does not erase the initiating
                # fault.  Only explicit latch reset may return to STOPPED.
                self.spindle_state = 'FAULT'
                raise SpindleSafetyError(f'Spindle start failed: {exc}') from exc
            self.Dzz_zs = rpm
            self.spindle_state = 'RUNNING'
            self.spindle_stop_confirmed = False
            self.last_safety_error = ''
            return True

    def Ctr_Dzz(self, value):
        if int(value) == 0:
            return self.stop_spindle('Ctr_Dzz')
        if int(value) == 1:
            return self.start_spindle(self.Dzz_zs, 'Ctr_Dzz')
        raise SpindleSafetyError(f'Invalid spindle command: {value!r}')

    def Safe_Det(self, trigger_stop=True):
        Ig_handle=self.handle
        safe_in_list=[c_int(0),c_int(0),c_int(0),c_int(0),c_int(0),c_int(0),c_int(0),c_int(0)]
        for io, dest in zip((8, 9, 12, 11, 14, 15, 6, 16), safe_in_list):
            self._call('ZAux_Direct_GetIn', Ig_handle, io, c_p(dest))
        result = [item.value for item in safe_in_list]
        self.safety_inputs_verified = True
        # This machine uses normally-closed E-stop circuits: either 0 means
        # active/open.  A confirmed stop is one-shot per press.  If stopping is
        # not confirmed, retry at a bounded rate while the E-stop remains held.
        physical_estop_active = result[6] == 0 or result[7] == 0
        if trigger_stop:
            if physical_estop_active:
                first_detection = not self._physical_estop_active
                self._physical_estop_active = True
                self.estop_latched = True
                now = time.monotonic()
                retry_due = (
                    first_detection or
                    (not self.spindle_stop_confirmed and
                     now - self._last_estop_retry >= self._estop_retry_interval)
                )
                if retry_due:
                    self._last_estop_retry = now
                    stop_ok = self.latch_estop('Physical_EStop')
                    if not stop_ok and not self.last_safety_error:
                        self.last_safety_error = 'Physical E-stop spindle stop was not confirmed'
                # Axis emergency actions are issued once per physical press.
                # Failures are latched and reported, but do not kill polling;
                # continued polling is required for bounded spindle-stop retry.
                if first_detection:
                    action_errors = []
                    try:
                        self.Rapidstop()
                    except Exception as exc:
                        action_errors.append(f'axis rapid stop: {exc}')
                    try:
                        self.Enable_Axs(0)
                    except Exception as exc:
                        action_errors.append(f'axis disable: {exc}')
                    if action_errors:
                        detail = '; '.join(action_errors)
                        self.last_safety_error = '; '.join(
                            item for item in (self.last_safety_error, detail) if item
                        )
                        self.spindle_state = 'FAULT'
                        self.estop_latched = True
            else:
                self._physical_estop_active = False
                self._last_estop_retry = 0.0
        return result # X-/X+/Y-/Y+/Z-/Z+/E-stop1/E-stop2

    def latch_estop(self, caller=''):
        self.estop_latched = True
        try:
            result = self.stop_spindle(caller or 'EStop')
            self.spindle_state = 'FAULT'
            return result
        except Exception as exc:
            self.spindle_state = 'FAULT'
            self.last_safety_error = str(exc)
            return False

    def clear_estop_latch(self):
        safe = self.Safe_Det(trigger_stop=False)
        if safe[6] == 0 or safe[7] == 0:
            raise SpindleSafetyError('Physical emergency stop is still active')
        if not self.spindle_stop_confirmed:
            raise SpindleSafetyError('Spindle output zero is not confirmed')
        self.estop_latched = False
        self.spindle_state = 'STOPPED'
        self.last_safety_error = ''
        self._stop_requested.set()
        return True


MARK2_METHODS = (
    '_normalize_spindle_rpm', '_read_spindle_outputs',
    '_verify_spindle_outputs', 'Set_Dzz_zs', 'stop_spindle',
    'start_spindle', 'Ctr_Dzz', 'Safe_Det', 'latch_estop',
    'clear_estop_latch',
)


def install_mark2_spindle_safety(target_class):
    """Install the frozen MARK2 implementation as the REAL backend core."""
    for name in MARK2_METHODS:
        setattr(target_class, name, Mark2SpindleSafetyCore.__dict__[name])
    target_class._mark2_safety_core_installed = True
    return target_class
