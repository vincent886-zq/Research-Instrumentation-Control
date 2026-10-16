# -*- coding: utf-8 -*-
"""
main_ui2.py
磁悬浮电主轴控制系统 V2.0 - 使用 ui2.ui 生成的布局

从 main_ui1.py 完整迁移：ZAUXWrapper / Worker / MainWindow 全部内嵌，
包含急停锁存、PC侧通信看门狗、双通道停机确认和故障锁定。
当前 DLL 不提供旧代码使用的 ZAux_BasCommand，因此没有控制器侧 RTCSub 看门狗。

ui2.ui 新增功能：
- 三色状态灯（急停/恢复之间）
- 模式切换（普通/手轮，复选框互斥）
- MPG 轴选择（X/Y/Z 复选框互斥）
- 步进选择 7 档（0.01/0.05/0.1/0.5/1/5/10）
- 连续模式（单点/连续切换）
- G54 对刀（X/Y/Z 独立设零，按钮变蓝）
- 机械坐标 + 工件坐标双显（保留 3 位小数）
- Connect/InitZMC/Ax_En 状态颜色变化
- REAL ZMotion / TEST 树莓派虚拟主轴后端（连接或运行时禁止切换）
"""

import sys
import os
import ctypes
import platform
import time
import math
import socket
import errno
import ipaddress
import select
import threading
import secrets
from datetime import datetime
from time import sleep as Tsleep

from PySide6.QtCore import (QRect, Qt, QEvent, QObject, QThread, QTimer, Signal,
                            QSettings)
from PySide6.QtGui import (QColor, QFont, QKeySequence, QShortcut, QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QLabel,
                               QLineEdit, QMainWindow, QMessageBox,
                               QPushButton, QCheckBox, QTextEdit)

# 便携路径：源码运行时使用脚本目录；PyInstaller 运行时把可写配置放在 EXE
# 旁边，把只读资源同时从打包目录查找。目录可包含中文、空格并可整体迁移。
_SOURCE_BASE = os.path.dirname(os.path.abspath(__file__))
_APP_BASE = (os.path.dirname(os.path.abspath(sys.executable))
             if getattr(sys, 'frozen', False) else _SOURCE_BASE)
_RESOURCE_BASE = os.path.abspath(getattr(sys, '_MEIPASS', _SOURCE_BASE))
_BASE = _APP_BASE
# 程序根目录只保留主入口；运行资源按职责分类到以下目录。
_LAYOUT_DIR = os.path.join(_RESOURCE_BASE, "界面")
_CORE_DIR = os.path.join(_RESOURCE_BASE, "核心")
_DRIVER_DIR = os.path.join(_RESOURCE_BASE, "驱动")
_CONFIG_DIR = os.path.join(_APP_BASE, "配置")
os.makedirs(_CONFIG_DIR, exist_ok=True)
_SETTINGS_PATH = os.path.join(_CONFIG_DIR, "portable_settings.ini")
for _p in (_LAYOUT_DIR, _CORE_DIR, _RESOURCE_BASE, _SOURCE_BASE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ui2 import Ui_MainWindow  # 你的 .ui 编译出的 UI 类
from mark2_spindle_safety import (
    SpindleSafetyError,
    install_mark2_spindle_safety,
)

# 全局常量
c_int = ctypes.c_int
c_float = ctypes.c_float
c_p = ctypes.pointer

# The flashed pendant exposes exactly these five discrete increments.  Motion
# still passes through the existing per-axis speed, idle and soft-limit checks.
WIFI_HANDWHEEL_MAX_STEP_UM = 1000
WIFI_HANDWHEEL_DEFAULT_HOST = "192.168.4.1"
WIFI_HANDWHEEL_DEFAULT_PORT = 8266

# Conservative software limits for the current converted engraving machine.
# G0 remains controlled by AxSpd_G0_D (30 mm/s).  G1 uses the limits below;
# values can only be raised after a documented mechanical acceptance test.
GCODE_MAX_FEED_XY_MM_MIN = 600.0
GCODE_MAX_FEED_Z_MM_MIN = 300.0
GCODE_POSITION_TOLERANCE_MM = 0.01
GCODE_MOTION_MIN_TIMEOUT_S = 5.0
GCODE_MOTION_TIMEOUT_FACTOR = 3.0
GCODE_MOTION_TIMEOUT_MARGIN_S = 2.0
GCODE_MAX_DWELL_MS = 600000

# A controller power cycle resets DPOS on this machine, so the legacy static
# software window can occasionally be a false boundary.  It remains a strict
# boundary for AUTO.  Only the explicit, panel-only recovery state below may
# cross it, one deliberately small manual move at a time.
SOFT_LIMIT_RECOVERY_SPEED_MM_MIN = 100.0
SOFT_LIMIT_RECOVERY_MAX_STEP_MM = 1.0


class WifiHandwheelTransport:
    """Non-blocking, bounded TCP transport for the ESP-01S pendant.

    This class deliberately has no motion knowledge.  It only maintains one
    socket and bounded byte buffers; MainWindow still owns every ARM, health
    and motion decision.
    """

    _MAX_PENDING_BYTES = 4096
    _CONNECT_TIMEOUT_SECONDS = 4.0

    def __init__(self):
        self._socket = None
        self._connecting = False
        self._connect_deadline = 0.0
        self._pending_tx = bytearray()
        self._endpoint = ""

    def isOpen(self):
        return self._socket is not None and not self._connecting

    def isConnecting(self):
        return self._socket is not None and self._connecting

    def bytesToWrite(self):
        return len(self._pending_tx)

    @property
    def endpoint(self):
        return self._endpoint

    def begin_connect(self, host, port):
        self.close()
        # ESP AP uses a numeric local address.  Avoid DNS on the UI thread and
        # avoid silently connecting to an unintended host name.
        ipaddress.ip_address(host)
        if not 1 <= port <= 65535:
            raise ValueError("TCP 端口必须在 1 到 65535 之间")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setblocking(False)
        result = sock.connect_ex((host, port))
        pending_codes = {
            errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY,
            getattr(errno, "WSAEWOULDBLOCK", 10035),
            getattr(errno, "WSAEINPROGRESS", 10036),
        }
        if result not in (0, *pending_codes):
            sock.close()
            raise OSError(result, os.strerror(result))

        self._socket = sock
        self._connecting = result != 0
        self._connect_deadline = time.monotonic() + self._CONNECT_TIMEOUT_SECONDS
        self._endpoint = f"{host}:{port}"
        return not self._connecting

    def write(self, data):
        if not self.isOpen():
            return -1
        payload = bytes(data)
        if not payload:
            return 0
        if len(self._pending_tx) + len(payload) > self._MAX_PENDING_BYTES:
            return -1
        self._pending_tx.extend(payload)
        try:
            self._flush()
        except OSError:
            self.close()
            return -1
        return len(payload)

    def close(self):
        sock, self._socket = self._socket, None
        self._connecting = False
        self._connect_deadline = 0.0
        self._pending_tx.clear()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def poll(self):
        """Return ordered ('connected'|'data'|'error', payload) events."""
        if self._socket is None:
            return []
        events = []
        try:
            if self._connecting:
                if time.monotonic() >= self._connect_deadline:
                    raise TimeoutError("连接 ESP-01S 超时")
                _, writable, exceptional = select.select(
                    [], [self._socket], [self._socket], 0)
                if exceptional:
                    error_code = self._socket.getsockopt(socket.SOL_SOCKET,
                                                          socket.SO_ERROR)
                    raise OSError(error_code or errno.ECONNABORTED,
                                  "ESP-01S TCP 连接失败")
                if not writable:
                    return events
                error_code = self._socket.getsockopt(socket.SOL_SOCKET,
                                                      socket.SO_ERROR)
                if error_code:
                    raise OSError(error_code, os.strerror(error_code))
                self._connecting = False
                events.append(("connected", None))

            self._flush()
            for _ in range(8):
                try:
                    data = self._socket.recv(2048)
                except BlockingIOError:
                    break
                if not data:
                    raise ConnectionError("ESP-01S 已关闭 TCP 会话")
                events.append(("data", data))
        except (OSError, TimeoutError, ConnectionError) as exc:
            self.close()
            events.append(("error", str(exc)))
        return events

    def _flush(self):
        if not self.isOpen() or not self._pending_tx:
            return
        try:
            sent = self._socket.send(self._pending_tx)
        except BlockingIOError:
            return
        if sent <= 0:
            raise ConnectionError("ESP-01S TCP 发送失败")
        del self._pending_tx[:sent]


def _scalar_value(value):
    """Accept either ctypes values or the frozen MARK2 integer snapshots."""
    return getattr(value, 'value', value)


# =====================================================================
# ZAUXWrapper - ZMC 控制器驱动封装（自包含，不依赖 main3.py）
# =====================================================================

class ZAUXWrapper:
    def __init__(self):
        self.handle = ctypes.c_void_p()
        self.sys_ip = ""
        self.sys_info = ""
        self.is_connected = False
        # MARK2 parity: every native DLL call shares one serialized gate;
        # spindle command sequences have an additional re-entrant IO lock.
        self._api_lock = threading.RLock()
        self._io_lock = threading.RLock()
        # STOP/ESTOP 在等待 IO 锁时也能抢先通知正在执行的 START。
        # 事件在确认零输出前一直保持，防止排队的 START 越过 STOP。
        self._stop_requested = threading.Event()
        self._stop_requested.set()
        self._physical_estop_active = False
        self._last_estop_retry = 0.0
        self._estop_retry_interval = 0.2
        self._safety_monitor_health_check = None
        self.estop_latched = False
        self.safety_inputs_verified = False
        self.spindle_state = "STOPPED"
        self.spindle_stop_confirmed = True
        self.last_safety_error = ""
        self.controller_watchdog_available = False
        self.Init_platform()
        self.Init_parms()
        self.Temp_cmd=[]
        self.Per_cmd=[]

    def Init_platform(self):
        self.zmcaux = None
        self.driver_error = ""
        self._dll_dir_handles = []
        systype=platform.system()
        if systype == 'Windows':
            # 不再依赖原电脑的 DZZ_py、E: 盘或当前工作目录。
            dll_dirs = []
            env_dir = os.environ.get('ZMOTION_DLL_DIR')
            if env_dir:
                dll_dirs.append(env_dir)
            dll_dirs.extend([
                os.path.join(_APP_BASE, '驱动'),
                _DRIVER_DIR,
                os.path.join(_APP_BASE, '雕刻机', '雕刻机'),
                os.path.join(_RESOURCE_BASE, '雕刻机', '雕刻机'),
            ])
            # 打包版与源码版可能得到相同路径，保持顺序并去重。
            dll_dirs = list(dict.fromkeys(os.path.abspath(p) for p in dll_dirs))
            errors = []
            for dll_dir in dll_dirs:
                for dll_name in ('zauxdll64.dll', 'zauxdll.dll'):
                    dll_path = os.path.abspath(os.path.join(dll_dir, dll_name))
                    if not os.path.isfile(dll_path):
                        continue
                    try:
                        if hasattr(os, 'add_dll_directory'):
                            self._dll_dir_handles.append(os.add_dll_directory(dll_dir))
                        self.zmcaux = ctypes.WinDLL(dll_path)
                        print(f'已加载运动控制驱动: {dll_path}')
                        return
                    except OSError as exc:
                        errors.append(f'{dll_path}: {exc}')
            self.driver_error = (
                '未找到可供当前 64 位 Python 使用的 ZMotion 驱动。'
                '请把 64 位 zauxdll.dll（及 zmotion.dll）放入程序的 dll 文件夹。'
            )
            if errors:
                self.driver_error += ' 已找到的驱动无法加载：' + ' | '.join(errors)
            print(self.driver_error)
        elif systype == 'Darwin':
            self.zmcdll = ctypes.cdll('./dll/zmotion.dylib')
            print("macOS")
        elif systype == 'Linux':
            self.zmcdll=ctypes.cdll('./dll/zmotion.so')
            print("Linux")
        else:
            print('OS Not Supported!!')
    
    def Init_parms(self):
        self.connect_mode=0
        self.X_AID=0
        self.Y_AID=1
        self.Z_AID=2
        self.axis_list=[c_int(0),c_int(1),c_int(3)]
        self.axis_Mlist={'X':c_int(0),'Y':c_int(1),'Z':c_int(3)}
        self.atype=[c_int(1),c_int(1),c_int(1)]
        self.aunits=[c_float(3273),c_float(3273),c_float(6546)]
        self.Move_type=0          # 0=绝对运动，1=相对运动
        self.T1sigl=0
        self.SetCmd=-1
        self.MoveCmd=-1
        self.Fd_Type=0            # 0=units/min, 1=units/r
        self.Dzz_zs=0
        self.AxSpd_G0_D=30
        self.AxSpd_G0_Z_D=5
        self.Cric_Direct=0        # 0=逆时针，1=顺时针
        self.Main_Deal_Gsigl=0
        self.Move_status=False
        self.Now_GCode_RST=False
        self.G04_dwell_ms=0  # G04 暂停标记；由 GUI 单次定时器计时，Worker 继续安全轮询
        self.current_gcode_feed_mm_min = None
        self.GCode_expected_dpos = None
        self.GCode_motion_deadline = 0.0
        self.GCode_motion_description = ""
        self.last_gcode_fault = ""

    def search(self, console=[]):
        if self.zmcaux is None:
            print(self.driver_error)
            return []
        iplist = ctypes.create_string_buffer(b'', 4096+256+1)
        ret = self.zmcaux.ZAux_SearchEthlist(ctypes.byref(iplist), 4096+256+1, 200)
        self._check_ret('ZAux_SearchEthlist', ret)
        s = iplist.value.decode()
        str_iplist = s.split()
        num = len(str_iplist)
        print(num, "Controller(s) Found:")
        print(*str_iplist, sep='\n')
        console.append("Searching..")
        console.append(str(num) + " Controller(s) Found:")
        return str_iplist

    def connect(self, ip, console=None):
        if console is None:
            console = []
        if self.zmcaux is None:
            print(self.driver_error)
            return -2
        if self.handle.value not in (None, 0):
            if self.is_connected:
                print('已连接')
                return 0
            # A non-zero handle with is_connected=False is stale/ambiguous.
            try:
                self._checked_call('ZAux_Close', self.handle)
            except Exception:
                pass
            self.handle = ctypes.c_void_p()
        ip_bytes = ip.encode('utf-8')
        p_ip = ctypes.c_char_p(ip_bytes)
        print("Connecting to", ip, "...")
        ret = self.zmcaux.ZAux_OpenEth(p_ip, ctypes.pointer(self.handle))
        if ret != 0:
            msg = f"连接失败（错误码: {ret}）"
            print(msg)
            return ret
        # 限制所有后续 Direct 调用的网络等待时间，避免 DLL 无响应时无限冻结 UI。
        try:
            self._checked_call('ZAux_SetTimeOut', self.handle, ctypes.c_uint(500))
        except Exception:
            try:
                self.zmcaux.ZAux_Close(self.handle)
            finally:
                self.handle = ctypes.c_void_p(0)
                self.is_connected = False
            raise
        # OpenEth 返回 0，再验证：尝试读一次数据确认控制器真的在线
        test_val = c_float(0)
        try:
            self._checked_call('ZAux_Direct_GetDpos', self.handle, c_int(0), c_p(test_val))
        except Exception:
            try:
                self.zmcaux.ZAux_Close(self.handle)
            finally:
                self.handle = ctypes.c_void_p()
                self.is_connected = False
            return -1
        # 验证通过
        msg = ip + " Connected"
        print(msg)
        self.sys_ip = ip
        self.is_connected = True
        return 0
    
    def Disconnect(self):
        if self.handle.value in (None, 0):
            self.is_connected = False
            return 0
        if self.spindle_state != 'STOPPED' or not self.spindle_stop_confirmed:
            self.stop_spindle("Disconnect")
        self._checked_call('ZAux_Close', self.handle)
        self.handle = ctypes.c_void_p()
        self.is_connected = False
        return 0

    def _check_ret(self, action, ret):
        """ZMotion API 以非零返回码报告失败；统一转换为异常。"""
        if ret != 0:
            raise SpindleSafetyError(f"{action} 失败，错误码 {ret}")
        return ret

    def _checked_call(self, name, *args):
        if self.zmcaux is None:
            raise SpindleSafetyError(self.driver_error or "ZMotion 驱动未加载")
        with self._api_lock:
            func = getattr(self.zmcaux, name)
            ret = func(*args)
            self._check_ret(name, ret)
            return ret

    # The frozen MARK2 safety core calls this name.  Keep one adapter so the
    # safety module and all remaining UI2 motion functions share the same
    # serialized, return-code-checked native API gate.
    def _call(self, name, *args):
        return self._checked_call(name, *args)

    def _require_handle(self):
        if self.zmcaux is None:
            raise SpindleSafetyError("ZMotion 驱动未加载")
        if self.handle.value in (None, 0) or not self.is_connected:
            raise SpindleSafetyError("控制器未连接")

    def set_safety_monitor_health_check(self, callback):
        self._safety_monitor_health_check = callback

    def _require_safety_monitor_healthy(self):
        try:
            callback = self._safety_monitor_health_check
            if callback is None:
                raise SpindleSafetyError("安全轮询线程未配置")
            result = callback()
            if result is False:
                raise SpindleSafetyError("安全轮询线程状态异常")
            return True
        except Exception as exc:
            self.estop_latched = True
            self.spindle_state = "FAULT"
            self.last_safety_error = f"安全轮询联锁：{exc}"
            if isinstance(exc, SpindleSafetyError):
                raise
            raise SpindleSafetyError(self.last_safety_error) from exc
        
    def Init_ZMC404(self):
        self._require_handle()
        Ig_handle = self.handle
        Iaxis_list = self.axis_list
        configs = [
            (self.X_AID, 7, 9, 8),
            (self.Y_AID, 10, 11, 12),
            (self.Z_AID, 13, 15, 14),
        ]
        for idx, datum_in, fwd_in, rev_in in configs:
            axis = Iaxis_list[idx]
            self._checked_call('ZAux_Direct_SetAtype', Ig_handle, axis, self.atype[idx])
            self._checked_call('ZAux_Direct_SetUnits', Ig_handle, axis, self.aunits[idx])
            self._checked_call('ZAux_Direct_SetLspeed', Ig_handle, axis, c_float(10))
            self._checked_call('ZAux_Direct_SetSpeed', Ig_handle, axis, c_float(100))
            self._checked_call('ZAux_Direct_SetAccel', Ig_handle, axis, c_float(1000))
            self._checked_call('ZAux_Direct_SetDecel', Ig_handle, axis, c_float(1000))
            self._checked_call('ZAux_Direct_SetSramp', Ig_handle, axis, c_float(1000))
            self._checked_call('ZAux_Direct_SetCreep', Ig_handle, axis, c_float(5))
            self._checked_call('ZAux_Direct_SetDatumIn', Ig_handle, axis, datum_in)
            self._checked_call('ZAux_Direct_SetFwdIn', Ig_handle, axis, fwd_in)
            self._checked_call('ZAux_Direct_SetRevIn', Ig_handle, axis, rev_in)
        self.Init_AxSpd()
        # 当前 DLL 不导出旧代码使用的 ZAux_BasCommand；不得伪报已安装控制器看门狗。
        self.controller_watchdog_available = False
        print('Safety: controller-side spindle watchdog is NOT installed; PC watchdog only')
        return True

    def Init_AxSpd(self):
        Ig_handle = self.handle
        Iaxis_list = self.axis_list
        IX_AID = self.X_AID
        IY_AID = self.Y_AID
        IZ_AID = self.Z_AID
        self._checked_call('ZAux_Direct_SetSpeed', Ig_handle, Iaxis_list[IX_AID], c_float(10))
        self._checked_call('ZAux_Direct_SetSpeed', Ig_handle, Iaxis_list[IY_AID], c_float(10))
        self._checked_call('ZAux_Direct_SetSpeed', Ig_handle, Iaxis_list[IZ_AID], c_float(5))

    def Set_Manual_AxSpd(self, speeds):
        """写入独立的手动点动速度档，单位为 mm/s。

        G 代码的 F 进给通过 ``Set_AxSpd`` 设置，绝不能隐式复用到
        手动点动。调用方必须先确认三轴处于 Idle，且只有三轴速度均
        成功写入后才可发送点动命令。
        """
        try:
            values = {axis: float(speeds[axis]) for axis in ('X', 'Y', 'Z')}
        except (KeyError, TypeError, ValueError) as e:
            raise SpindleSafetyError(f"手动速度参数不完整: {e}")
        # 绝不允许手动面板把速度提高到旧版 Init_AxSpd 的默认值以上：
        # X/Y=10 mm/s（600 mm/min），Z=5 mm/s（300 mm/min）。
        maximums = {'X': 10.0, 'Y': 10.0, 'Z': 5.0}
        for axis, value in values.items():
            if not math.isfinite(value) or not 0.001 <= value <= maximums[axis]:
                raise SpindleSafetyError(
                    f"{axis}手动速度必须在0.001～{maximums[axis]:g} mm/s")
        Ig_handle = self.handle
        Iaxis_list = self.axis_list
        self._checked_call('ZAux_Direct_SetSpeed', Ig_handle,
                           Iaxis_list[self.X_AID], c_float(values['X']))
        self._checked_call('ZAux_Direct_SetSpeed', Ig_handle,
                           Iaxis_list[self.Y_AID], c_float(values['Y']))
        self._checked_call('ZAux_Direct_SetSpeed', Ig_handle,
                           Iaxis_list[self.Z_AID], c_float(values['Z']))
        return values

    def AxSpd_G0(self):
        Ig_handle = self.handle
        Izmcaux = self.zmcaux
        Iaxis_list = self.axis_list
        IX_AID = self.X_AID
        IY_AID = self.Y_AID
        IZ_AID = self.Z_AID
        xy_speed = self.AxSpd_G0_D
        z_speed = self.AxSpd_G0_Z_D
        self._checked_call('ZAux_Direct_SetSpeed', Ig_handle, Iaxis_list[IX_AID], c_float(xy_speed))
        self._checked_call('ZAux_Direct_SetSpeed', Ig_handle, Iaxis_list[IY_AID], c_float(xy_speed))
        self._checked_call('ZAux_Direct_SetSpeed', Ig_handle, Iaxis_list[IZ_AID], c_float(z_speed))

    def Set_AxSpd(self, value):
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise SpindleSafetyError(f"非法进给速度: {value}")
        if self.Fd_Type == 0:
            value = value / 60
        else:
            value = self.Dzz_zs * value / 60
        effective_mm_min = value * 60.0
        if not math.isfinite(effective_mm_min) or effective_mm_min <= 0:
            raise SpindleSafetyError(
                "G代码有效进给必须大于0；G95要求主轴已有非零设定转速")
        if effective_mm_min > GCODE_MAX_FEED_XY_MM_MIN:
            raise SpindleSafetyError(
                f"G代码有效进给 {effective_mm_min:g} mm/min 超过"
                f"当前安全上限 {GCODE_MAX_FEED_XY_MM_MIN:g} mm/min")
        Ig_handle = self.handle
        Izmcaux = self.zmcaux
        Iaxis_list = self.axis_list
        IX_AID = self.X_AID
        IY_AID = self.Y_AID
        IZ_AID = self.Z_AID
        self._checked_call('ZAux_Direct_SetSpeed', Ig_handle, Iaxis_list[IX_AID], c_float(value))
        self._checked_call('ZAux_Direct_SetSpeed', Ig_handle, Iaxis_list[IY_AID], c_float(value))
        self._checked_call('ZAux_Direct_SetSpeed', Ig_handle, Iaxis_list[IZ_AID], c_float(value))
        self.current_gcode_feed_mm_min = effective_mm_min
        return effective_mm_min

    def ZMC_1Move(self, C_Ax_ID, dist):
        Ig_handle = self.handle
        Izmcaux = self.zmcaux
        Iaxis_list = self.axis_list
        n = 0 if C_Ax_ID == 'X' else (1 if C_Ax_ID == 'Y' else 2)
        if C_Ax_ID == 'X' or C_Ax_ID == 'Z':
            dist = -dist  # 方向取反
        self._checked_call(
            'ZAux_Direct_Single_Move', Ig_handle, Iaxis_list[n], c_float(dist))

    def ZMC_1MoveAbs(self, C_Ax_ID, dist):
        Ig_handle = self.handle
        Izmcaux = self.zmcaux
        Iaxis_list = self.axis_list
        n = 0 if C_Ax_ID == 'X' else (1 if C_Ax_ID == 'Y' else 2)
        if C_Ax_ID == 'X' or C_Ax_ID == 'Z':
            dist = -dist  # 方向取反
        self._checked_call(
            'ZAux_Direct_Single_MoveAbs', Ig_handle, Iaxis_list[n], c_float(dist))

    def ZMC_1Move_all(self, C_Ax_ID, dist):
        if self.Move_type == 0:
            self.ZMC_1MoveAbs(C_Ax_ID, dist)
        else:
            self.ZMC_1Move(C_Ax_ID, dist)
        self.Move_status = True

    def ZMC_MMove(self, C_Ax_IDs, dlist_list):
        Ig_handle = self.handle
        Izmcaux = self.zmcaux
        Iaxis_list = self.axis_list
        if len(C_Ax_IDs) != len(dlist_list) or not C_Ax_IDs:
            raise SpindleSafetyError("多轴相对运动的轴/目标数量不一致")
        b = []
        for i in C_Ax_IDs:
            b.append(Iaxis_list[0 if i == 'X' else (1 if i == 'Y' else 2)])
        p_C_Ax_IDs = (c_int * len(b))(*b)
        # Public movement methods use the same application coordinates shown
        # by Dpos_Det/Mpos_Det.  X/Z are inverted only at the DLL boundary.
        physical_values = [(-float(v) if ax in ('X', 'Z') else float(v))
                           for ax, v in zip(C_Ax_IDs, dlist_list)]
        p_dlist_list = (c_float * len(physical_values))(*physical_values)
        self._checked_call(
            'ZAux_Direct_Move', Ig_handle, c_int(len(b)), p_C_Ax_IDs, p_dlist_list)

    def ZMC_MMoveAbs(self, C_Ax_IDs, dlist_list):
        Ig_handle = self.handle
        Izmcaux = self.zmcaux
        Iaxis_list = self.axis_list
        if len(C_Ax_IDs) != len(dlist_list) or not C_Ax_IDs:
            raise SpindleSafetyError("多轴绝对运动的轴/目标数量不一致")
        b = []
        for i in C_Ax_IDs:
            b.append(Iaxis_list[0 if i == 'X' else (1 if i == 'Y' else 2)])
        p_C_Ax_IDs = (c_int * len(b))(*b)
        physical_values = [(-float(v) if ax in ('X', 'Z') else float(v))
                           for ax, v in zip(C_Ax_IDs, dlist_list)]
        p_dlist_list = (c_float * len(physical_values))(*physical_values)
        self._checked_call(
            'ZAux_Direct_MoveAbs', Ig_handle, c_int(len(b)), p_C_Ax_IDs, p_dlist_list)

    def ZMC_MMove_all(self, C_Ax_IDs, dlist_list):
        if self.Move_type == 0:
            self.ZMC_MMoveAbs(C_Ax_IDs, dlist_list)
        else:
            self.ZMC_MMove(C_Ax_IDs, dlist_list)
        self.Move_status = True

    def ZMC_MMoveCirc(self, C_Ax_IDs, dlist1, dlist2):
        Ig_handle = self.handle
        Izmcaux = self.zmcaux
        Iaxis_list = self.axis_Mlist
        b = [Iaxis_list[i] for i in C_Ax_IDs]
        num = len(b)
        p_C_Ax_IDs = (c_int * num)(*b)
        # 终点坐标：传递给 DLL
        p_dl1 = (c_float * num)(*dlist1)
        # 中心点：dlist2 可能只有 I,J（2 个值），补足到 num
        dl2_adj = list(dlist2)
        while len(dl2_adj) < num:
            dl2_adj.append(0.0)
        p_dl2 = (c_float * len(dl2_adj))(*dl2_adj[:num])
        self._checked_call('ZAux_Direct_MoveCirc', Ig_handle, c_int(num), p_C_Ax_IDs,
                           p_dl1, p_dl2, c_int(self.Cric_Direct))

    def ZMC_MMoveCircAbs(self, C_Ax_IDs, dlist1, dlist2):
        Ig_handle = self.handle
        Izmcaux = self.zmcaux
        Iaxis_list = self.axis_Mlist
        b = [Iaxis_list[i] for i in C_Ax_IDs]
        num = len(b)
        p_C_Ax_IDs = (c_int * num)(*b)
        # 终点坐标
        p_dl1 = (c_float * num)(*dlist1)
        # 中心点：dlist2 可能只有 I,J（2 个值），补足到 num
        dl2_adj = list(dlist2)
        while len(dl2_adj) < num:
            dl2_adj.append(0.0)
        p_dl2 = (c_float * len(dl2_adj))(*dl2_adj[:num])
        self._checked_call('ZAux_Direct_MoveCircAbs', Ig_handle, c_int(num), p_C_Ax_IDs,
                           p_dl1, p_dl2, c_int(self.Cric_Direct))

    def ZMC_MMoveCirc_all(self, Gcmd, C_Ax_IDs, dlist1, dlist2):
        self.Cric_Direct = 1 if Gcmd == 2 else 0
        if self.Move_type == 0:
            self.ZMC_MMoveCircAbs(C_Ax_IDs, dlist1, dlist2)
        else:
            self.ZMC_MMoveCirc(C_Ax_IDs, dlist1, dlist2)
        self.Move_status = True

    def Enable_Axs(self, Status):
        print(f"  [Enable_Axs] Status={Status}")
        Ig_handle = self.handle
        self._checked_call('ZAux_Direct_SetOp', Ig_handle, 2, c_int(Status))
        self._checked_call('ZAux_Direct_SetOp', Ig_handle, 3, c_int(not Status))
        self._checked_call('ZAux_Direct_SetOp', Ig_handle, 12, c_int(Status))
        self._checked_call('ZAux_Direct_SetOp', Ig_handle, 13, c_int(Status))
        self._checked_call('ZAux_Direct_SetOp', Ig_handle, 15, c_int(Status))
        print(f"  [Enable_Axs] 完成 Status={Status}")

    def Btn_Det(self):
        Ig_handle = self.handle
        Btn_in_list = [c_int(0), c_int(0), c_int(0)]
        for io, value in zip((2, 3, 4), Btn_in_list):
            self._checked_call('ZAux_Direct_GetIn', Ig_handle, io, c_p(value))
        # 注释掉自动关使能：由 Deal_in_signel 信号处理
        # if Btn_in_list[1].value == 1:
        #     self.Enable_Axs(0)
        return Btn_in_list

    def GoHome(self):
        Ig_handle = self.handle
        Iaxis_list = self.axis_list
        self.Init_AxSpd()
        self._checked_call('ZAux_Direct_Single_Datum', Ig_handle,
                           Iaxis_list[self.Z_AID], c_int(19))
        self._checked_call('ZAux_Direct_Single_Datum', Ig_handle,
                           Iaxis_list[self.X_AID], c_int(18))
        self._checked_call('ZAux_Direct_Single_Datum', Ig_handle,
                           Iaxis_list[self.Y_AID], c_int(19))

    def GoZero(self):
        self.ZMC_MMoveAbs(['Z'], [0])
        self.ZMC_MMoveAbs(['X', 'Y'], [0, 0])

    def Move_Det(self):
        Ig_handle = self.handle
        Iaxis_list = self.axis_list
        status = [c_int(-1), c_int(-1), c_int(-1)]
        self._checked_call('ZAux_Direct_GetIfIdle', Ig_handle,
                           Iaxis_list[self.X_AID], c_p(status[self.X_AID]))
        self._checked_call('ZAux_Direct_GetIfIdle', Ig_handle,
                           Iaxis_list[self.Y_AID], c_p(status[self.Y_AID]))
        self._checked_call('ZAux_Direct_GetIfIdle', Ig_handle,
                           Iaxis_list[self.Z_AID], c_p(status[self.Z_AID]))
        return [a.value for a in status]

    def Dpos_Det(self):
        Dpos = [c_float(0), c_float(0), c_float(0), c_float(0)]
        Iaxis_list = self.axis_list
        Ig_handle = self.handle
        for i in Iaxis_list:
            self._checked_call('ZAux_Direct_GetDpos', Ig_handle, i, c_p(Dpos[i.value]))
        vals = [i.value for i in Dpos]
        vals[0] = -vals[0]   # X 方向取反
        vals[3] = -vals[3]   # Z 方向取反
        return vals

    def Mpos_Det(self):
        Mpos = [c_float(0), c_float(0), c_float(0), c_float(0)]
        Iaxis_list = self.axis_list
        Ig_handle = self.handle
        for i in Iaxis_list:
            self._checked_call('ZAux_Direct_GetMpos', Ig_handle, i, c_p(Mpos[i.value]))
        vals = [i.value for i in Mpos]
        vals[0] = -vals[0]   # X 方向取反
        vals[3] = -vals[3]   # Z 方向取反
        return vals

    def Dzz_zs_Det(self):
        Val = c_float(0)
        self._checked_call('ZAux_Direct_GetDA', self.handle, c_int(0), c_p(Val))
        zs = int((Val.value / 4095) * 45000)
        return zs

    def R_CSys(self):
        for i in self.axis_list:
            self._checked_call('ZAux_Direct_SetDpos', self.handle, i, c_float(0))

    def Rapidstop(self):
        return self._checked_call(
            'ZAux_Direct_Rapidstop', self.handle, c_int(2))


# Runtime ownership boundary: REAL spindle Start/Stop/E-stop comes from the
# frozen DZZ_MARK2 safety module above.  The older in-class copies are replaced
# here before any ZAUXWrapper instance can be created.
install_mark2_spindle_safety(ZAUXWrapper)


# =====================================================================
# TcpSpindleBackend - 树莓派虚拟主轴（TEST 模式，不访问真实 IO/运动轴）
# =====================================================================

class TcpSpindleBackend:
    """与 ZAUXWrapper 暴露同一安全接口的 TCP 测试后端。"""

    def __init__(self, host="192.168.50.100", port=5000, timeout=0.5):
        self.default_host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.zmcaux = None
        self.driver_error = "TEST 后端不使用 ZMotion DLL"
        self.sock = None
        self._rx_buffer = b""
        self._io_lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._stop_requested.set()
        self.handle = ctypes.c_void_p()
        self.sys_ip = ""
        self.is_connected = False
        self.estop_latched = False
        self.safety_inputs_verified = False
        self.spindle_state = "STOPPED"
        self.spindle_stop_confirmed = True
        self.last_safety_error = ""
        self.controller_watchdog_available = False
        self.Dzz_zs = 0.0
        self.actual_rpm = 0.0
        self.Move_type = 0
        self.T1sigl = 0
        self.SetCmd = -1
        self.MoveCmd = -1
        self.Fd_Type = 0
        self.AxSpd_G0_D = 30
        self.AxSpd_G0_Z_D = 5
        self.Cric_Direct = 0
        self.Move_status = False
        self.Now_GCode_RST = False
        self.G04_dwell_ms = 0
        self.current_gcode_feed_mm_min = None
        self.GCode_expected_dpos = None
        self.GCode_motion_deadline = 0.0
        self.GCode_motion_description = ""
        self.last_gcode_fault = ""
        self._last_status = {}
        self._last_status_at = 0.0
        self.stop_confirm_timeout = 12.0

    @staticmethod
    def _normalize_spindle_rpm(value, allow_zero=True):
        try:
            rpm = float(value)
        except (TypeError, ValueError) as exc:
            raise SpindleSafetyError(f"无效主轴转速: {value!r}") from exc
        if not math.isfinite(rpm) or rpm < 0 or rpm > 45000:
            raise SpindleSafetyError(f"主轴转速 {rpm!r} 超出允许范围 0～45000 rpm")
        if not allow_zero and rpm <= 0:
            raise SpindleSafetyError("启动转速必须大于 0 rpm")
        return rpm

    def search(self, console=[]):
        return [self.default_host]

    def _require_connection(self):
        if self.sock is None or not self.is_connected:
            raise SpindleSafetyError("TEST 虚拟主轴未连接")

    def _recv_line(self):
        while b'\n' not in self._rx_buffer:
            data = self.sock.recv(4096)
            if not data:
                raise ConnectionError("树莓派关闭了 TCP 连接")
            self._rx_buffer += data
            if len(self._rx_buffer) > 65536:
                raise SpindleSafetyError("TEST 响应超过长度限制")
        raw, self._rx_buffer = self._rx_buffer.split(b'\n', 1)
        return raw.decode('utf-8', errors='strict').rstrip('\r')

    def _command(self, command, allow_error=False):
        with self._io_lock:
            self._require_connection()
            try:
                self.sock.sendall((command + '\n').encode('utf-8'))
                response = self._recv_line()
            except (OSError, ConnectionError, UnicodeError) as exc:
                self.estop_latched = True
                self.spindle_stop_confirmed = False
                self.spindle_state = "FAULT"
                self.last_safety_error = f"TEST 通信失败: {exc}"
                raise SpindleSafetyError(self.last_safety_error) from exc
            if response.startswith('ERR ') and not allow_error:
                raise SpindleSafetyError(f"TEST 返回错误: {response}")
            return response

    @staticmethod
    def _parse_status(line):
        if not line.startswith('STATUS '):
            raise SpindleSafetyError(f"TEST 状态格式错误: {line!r}")
        result = {}
        for item in line.split()[1:]:
            if '=' not in item:
                raise SpindleSafetyError(f"TEST 状态字段错误: {item!r}")
            key, value = item.split('=', 1)
            result[key] = value
        required = {'STATE', 'CMD_RPM', 'RPM', 'RUN', 'FAULT'}
        if not required.issubset(result):
            raise SpindleSafetyError(f"TEST 状态缺少字段: {required - set(result)}")
        if result['STATE'] not in {'STOPPED', 'STARTING', 'RUNNING', 'STOPPING', 'FAULT'}:
            raise SpindleSafetyError(f"TEST 状态值非法: {result['STATE']}")
        try:
            result['CMD_RPM'] = float(result['CMD_RPM'])
            result['RPM'] = float(result['RPM'])
            result['RUN'] = int(result['RUN'])
        except ValueError as exc:
            raise SpindleSafetyError(f"TEST 状态数值非法: {line}") from exc
        if (not math.isfinite(result['CMD_RPM']) or result['CMD_RPM'] < 0
                or result['CMD_RPM'] > 45000
                or not math.isfinite(result['RPM']) or result['RPM'] < 0
                or result['RPM'] > 45000 or result['RUN'] not in (0, 1)):
            raise SpindleSafetyError(f"TEST 状态超出范围: {line}")
        return result

    def _get_status(self, force=True):
        if not force and time.monotonic() - self._last_status_at < 0.1:
            return self._last_status
        try:
            status = self._parse_status(self._command('GET_STATUS'))
        except Exception as exc:
            self.estop_latched = True
            self.spindle_stop_confirmed = False
            self.spindle_state = "FAULT"
            self.last_safety_error = str(exc)
            raise
        self._last_status = status
        self._last_status_at = time.monotonic()
        self.actual_rpm = status['RPM']
        self.spindle_state = status['STATE']
        if status['FAULT'] != '0':
            self.estop_latched = True
        self.spindle_stop_confirmed = (
            status['RUN'] == 0 and status['RPM'] == 0)
        return status

    def connect(self, ip, console=[]):
        if self.is_connected:
            return 0
        host = (ip or self.default_host).strip()
        try:
            sock = socket.create_connection((host, self.port), timeout=self.timeout)
            sock.settimeout(self.timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock = sock
            self._rx_buffer = b""
            greeting = self._recv_line()
            if not greeting.startswith('OK SPINDLE_SIM VERSION='):
                raise SpindleSafetyError(f"不是受支持的虚拟主轴服务: {greeting!r}")
            self.is_connected = True
            self.handle = ctypes.c_void_p(1)
            self.sys_ip = host
            self.safety_inputs_verified = True
            self._get_status(force=True)
            return 0
        except Exception as exc:
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock = None
            self.handle = ctypes.c_void_p()
            self.is_connected = False
            self.last_safety_error = str(exc)
            print(f"TEST 连接失败: {exc}")
            return -1

    def Disconnect(self):
        if not self.is_connected:
            return 0
        self.stop_spindle("TEST_Disconnect")
        with self._io_lock:
            try:
                self.sock.close()
            finally:
                self.sock = None
                self.handle = ctypes.c_void_p()
                self.is_connected = False
        return 0

    def Init_ZMC404(self):
        self._require_connection()
        self.safety_inputs_verified = True
        self._get_status(force=True)
        return True

    def Set_Dzz_zs(self, value):
        rpm = self._normalize_spindle_rpm(value)
        if rpm > 0 and self._stop_requested.is_set():
            raise SpindleSafetyError("TEST STOP/ESTOP 已请求，禁止设置非零转速")
        response = self._command(f"SET_SPEED {rpm:g}")
        if not response.startswith('OK SET_SPEED '):
            raise SpindleSafetyError(f"TEST 设置转速未确认: {response}")
        status = self._get_status(force=True)
        if abs(status['CMD_RPM'] - rpm) > 0.5:
            raise SpindleSafetyError(
                f"TEST 转速设定读回不一致: {status['CMD_RPM']} != {rpm}")
        self.Dzz_zs = rpm
        return True

    def start_spindle(self, rpm, caller=""):
        rpm = self._normalize_spindle_rpm(rpm, allow_zero=False)
        if (self._stop_requested.is_set()
                and (not self.spindle_stop_confirmed or self.spindle_state != "STOPPED")):
            raise SpindleSafetyError("TEST STOPPING/ESTOP 期间的 START 已拒绝")
        with self._io_lock:
            if self.estop_latched:
                raise SpindleSafetyError("TEST 急停/故障已锁存，必须先恢复")
            if not self.safety_inputs_verified:
                raise SpindleSafetyError("TEST 安全状态尚未验证")
            if self._stop_requested.is_set():
                if not self.spindle_stop_confirmed or self.spindle_state != "STOPPED":
                    raise SpindleSafetyError("TEST STOP/ESTOP 正在生效，未确认零速前禁止启动")
                self._stop_requested.clear()
            self.spindle_state = "STARTING"
            try:
                if self._stop_requested.is_set():
                    raise SpindleSafetyError("TEST STOP/ESTOP 已抢占启动流程")
                self.Set_Dzz_zs(rpm)
                if self._stop_requested.is_set():
                    raise SpindleSafetyError("TEST STOP/ESTOP 已抢占启动流程")
                response = self._command('START')
                if response != 'OK START':
                    raise SpindleSafetyError(f"TEST START 未确认: {response}")
                status = self._get_status(force=True)
                if status['RUN'] != 1 or status['STATE'] not in {'STARTING', 'RUNNING'}:
                    raise SpindleSafetyError(f"TEST 启动状态不一致: {status}")
                if self._stop_requested.is_set():
                    raise SpindleSafetyError("TEST STOP/ESTOP 在启动确认期间到达")
            except Exception as exc:
                self.estop_latched = True
                stop_failure = None
                try:
                    self.stop_spindle(f"TEST_Start_Failure:{caller}")
                except Exception as stop_exc:
                    stop_failure = stop_exc
                detail = f"TEST 主轴启动失败: {exc}"
                if stop_failure is not None:
                    detail += f"；自动停机也未确认: {stop_failure}"
                raise SpindleSafetyError(detail) from exc
            self.Dzz_zs = rpm
            self.spindle_state = status['STATE']
            self.spindle_stop_confirmed = False
            return True

    def stop_spindle(self, caller="", attempts=3):
        self._stop_requested.set()
        with self._io_lock:
            if not self.is_connected:
                if self.sock is None:
                    self.Dzz_zs = 0
                    self.actual_rpm = 0
                    self.spindle_state = "STOPPED"
                    self.spindle_stop_confirmed = True
                    return True
                raise SpindleSafetyError("TEST 连接状态异常，无法确认停止")
            last_error = None
            for _ in range(max(1, attempts)):
                try:
                    response = self._command('STOP', allow_error=True)
                    if response != 'OK STOP':
                        raise SpindleSafetyError(f"TEST STOP 被拒绝: {response}")
                    deadline = time.monotonic() + self.stop_confirm_timeout
                    while time.monotonic() < deadline:
                        status = self._get_status(force=True)
                        if status['RUN'] == 0 and status['RPM'] == 0:
                            self.Dzz_zs = 0
                            self.actual_rpm = 0
                            self.spindle_stop_confirmed = True
                            # FAULT 可以在物理量已归零后继续锁存，不能伪装成无故障。
                            self.spindle_state = status['STATE']
                            self.last_safety_error = ""
                            return True
                        time.sleep(0.05)
                    raise SpindleSafetyError(f"TEST 停机超时: {status}")
                except Exception as exc:
                    last_error = exc
                    if isinstance(exc, SpindleSafetyError) and '被拒绝' in str(exc):
                        break
            self.estop_latched = True
            self.spindle_state = "FAULT"
            self.spindle_stop_confirmed = False
            self.last_safety_error = str(last_error or "TEST 停机未确认")
            raise SpindleSafetyError(
                f"TEST 主轴停机未确认 [from:{caller}]: {self.last_safety_error}")

    def latch_estop(self, caller=""):
        self.estop_latched = True
        try:
            response = self._command('ESTOP', allow_error=True)
            if response != 'OK ESTOP':
                raise SpindleSafetyError(f"TEST ESTOP 被拒绝: {response}")
            return self.stop_spindle(caller or "TEST_EStop")
        except Exception as exc:
            self.last_safety_error = str(exc)
            self.spindle_stop_confirmed = False
            return False

    def clear_estop_latch(self):
        self.stop_spindle("TEST_Clear_EStop_Precheck")
        response = self._command('CLEAR_FAULT', allow_error=True)
        if response != 'OK CLEAR_FAULT':
            raise SpindleSafetyError(f"TEST 故障解除失败: {response}")
        status = self._get_status(force=True)
        if status['RUN'] != 0 or status['RPM'] != 0 or status['FAULT'] != '0':
            raise SpindleSafetyError(f"TEST 恢复后状态不安全: {status}")
        self.estop_latched = False
        self.spindle_state = "STOPPED"
        self.spindle_stop_confirmed = True
        return True

    def Ctr_Dzz(self, value, caller=""):
        if int(value) == 0:
            return self.stop_spindle(caller or "TEST_Ctr_Dzz")
        if int(value) == 1:
            return self.start_spindle(self.Dzz_zs, caller or "TEST_Ctr_Dzz")
        raise SpindleSafetyError(f"非法主轴启停值: {value}")

    def Dzz_zs_Det(self):
        return int(self._get_status(force=False)['RPM'])

    def Btn_Det(self):
        return [c_int(0), c_int(0), c_int(0)]

    def Safe_Det(self):
        self._require_connection()
        self.safety_inputs_verified = True
        return [c_int(1) for _ in range(8)]

    def Dpos_Det(self):
        return [0.0, 0.0, 0.0, 0.0]

    Mpos_Det = Dpos_Det

    def Move_Det(self):
        self.Move_status = False
        return [-1, -1, -1]

    def Rapidstop(self):
        self.Move_status = False
        return True

    def Enable_Axs(self, status):
        return True

    def Set_AxSpd(self, value):
        raw = float(value)
        effective_mm_min = (raw if self.Fd_Type == 0
                            else self.Dzz_zs * raw)
        if (not math.isfinite(effective_mm_min)
                or effective_mm_min <= 0
                or effective_mm_min > GCODE_MAX_FEED_XY_MM_MIN):
            raise SpindleSafetyError(
                f"TEST G代码有效进给 {effective_mm_min!r} mm/min 无效或超限")
        self.current_gcode_feed_mm_min = effective_mm_min
        return effective_mm_min

    def AxSpd_G0(self):
        return True

    def ZMC_1Move(self, *args):
        self.Move_status = False

    ZMC_1MoveAbs = ZMC_1Move
    ZMC_1Move_all = ZMC_1Move

    def ZMC_MMove_all(self, *args):
        self.Move_status = False

    ZMC_MMoveCirc_all = ZMC_MMove_all

    def GoHome(self):
        self.Move_status = False

    GoZero = GoHome

    def R_CSys(self):
        return True


# =====================================================================
# Worker - 轮询线程（自包含，不依赖 main3.py）
# =====================================================================

class Worker(QThread):
    updateSignal = Signal(str, float)

    def __init__(self, ZMC):
        super().__init__()
        self.ZMC = ZMC
        self.Btn_status = None
        self.Safe_status = None
        self.Wstatus = True
        self.GCode_St_all = False
        self.Dpos_hold = None
        self.zs_hold = 0
        self.EStop_Flag = False
        self.last_poll_ok = 0.0
        self.faulted = False
        self._monitor_ready_reported = False
        try:
            self.Btn_status = self.ZMC.Btn_Det()
        except Exception:
            self.Btn_status = [c_int(0), c_int(0), c_int(0)]
        try:
            self.Safe_status = self.ZMC.Safe_Det()
        except Exception:
            self.Safe_status = [c_int(0) for _ in range(8)]
        try:
            self.Dpos_hold = self.ZMC.Dpos_Det()
        except Exception:
            # REAL DPOS snapshots contain four slots because physical Z is
            # controller axis 3.  A transient first-read failure must retain
            # the same shape or the first successful refresh can index past
            # this fallback and create a false communication fault.
            self.Dpos_hold = [0.0, 0.0, 0.0, 0.0]
        try:
            self.zs_hold = self.ZMC.Dzz_zs_Det()
        except Exception:
            self.zs_hold = 0

    @staticmethod
    def _classify_safety_changes(previous, current):
        """Keep limit indications separate from the two NC E-stop circuits."""
        current = [_scalar_value(item) for item in current]
        if previous is None:
            for index in (6, 7):
                if index < len(current) and current[index] == 0:
                    return [('EStopHandled', index)]
            return []
        previous = [_scalar_value(item) for item in previous]
        events = []
        for index, (old, new) in enumerate(zip(previous, current)):
            if new == old:
                continue
            if index in (6, 7):
                events.append(('EStopHandled' if new == 0 else 'EStopReleased', index))
            else:
                events.append(('LimitInput', index))
        return events

    def _clear_gcode_motion_tracking(self):
        self.ZMC.GCode_expected_dpos = None
        self.ZMC.GCode_motion_deadline = 0.0
        self.ZMC.GCode_motion_description = ""

    def _verify_gcode_endpoint(self):
        expected = self.ZMC.GCode_expected_dpos
        if not expected:
            raise SpindleSafetyError(
                "G代码运动到位追踪缺失，拒绝继续下一行")
        actual = self.ZMC.Dpos_Det()
        if len(actual) < 4:
            raise SpindleSafetyError("G代码到位复核的DPOS长度不足")
        index = {'X': 0, 'Y': 1, 'Z': 3}
        mismatches = []
        for axis, target in expected.items():
            value = float(actual[index[axis]])
            if (not math.isfinite(value)
                    or abs(value - target) > GCODE_POSITION_TOLERANCE_MM):
                mismatches.append(
                    f"{axis}实际{value:.3f}/目标{target:.3f}")
        if mismatches:
            raise SpindleSafetyError(
                "G代码未到达目标：" + "，".join(mismatches))
        return True

    def _gcode_motion_timeout_due(self):
        return bool(
            self.GCode_St_all
            and self.ZMC.Move_status
            and self.ZMC.GCode_motion_deadline > 0
            and time.monotonic() > self.ZMC.GCode_motion_deadline)

    def _latch_gcode_fault(self, reason, code, signal='GCodeFault'):
        """Fail-safe G-code stop with independent spindle/axis attempts."""
        errors = []
        self.ZMC.estop_latched = True
        try:
            stop_ok = self.ZMC.latch_estop(f"GCode_Worker:{reason}")
            if not stop_ok:
                errors.append(
                    self.ZMC.last_safety_error or "主轴停机未确认")
        except Exception as exc:
            errors.append(f"主轴急停调用失败: {exc}")
        try:
            self.ZMC.Rapidstop()
        except Exception as exc:
            errors.append(f"轴Rapidstop失败: {exc}")
        try:
            self.ZMC.Enable_Axs(0)
        except Exception as exc:
            errors.append(f"轴下使能失败: {exc}")
        self.GCode_St_all = False
        self.ZMC.Move_status = False
        self._clear_gcode_motion_tracking()
        detail = reason
        if errors:
            detail += "；" + "；".join(errors)
        self.ZMC.last_gcode_fault = detail
        self.updateSignal.emit(signal, code)

    def run(self):
        n = 0
        poll_fail = 0
        poll_fail_limit = 3
        comm_fault_reported = False
        while self.Wstatus:
            n += 1
            try:
                L_a = self.ZMC.Btn_Det()
                L_b = self.ZMC.Safe_Det()
                if L_a != self.Btn_status:
                    r = [_scalar_value(a) - _scalar_value(b)
                         for a, b in zip(L_a, self.Btn_status)]
                    self.Btn_status = L_a
                    for idx, v in enumerate(r):
                        if v == 1:
                            if idx == 1:
                                # Cabinet Stop DI3 must not wait for the GUI
                                # event queue; stop from the polling thread.
                                self._latch_gcode_fault(
                                    "实体Stop按钮DI3触发", 5,
                                    signal='PhysicalStopHandled')
                            else:
                                self.updateSignal.emit('Btn', idx)
                            print('Btn' + str(idx))
                if L_b != self.Safe_status:
                    for event, idx in self._classify_safety_changes(self.Safe_status, L_b):
                        self.updateSignal.emit(event, idx)
                        # Limit polarity is not yet certified on this converted
                        # machine.  During AUTO, any limit transition is treated
                        # conservatively as a latched program fault; this avoids
                        # continuing after the controller locally stops an axis.
                        if event == 'LimitInput' and self.GCode_St_all:
                            self._latch_gcode_fault(
                                f"G代码运行中限位输入发生变化（索引 {idx}）", 2)
                    self.Safe_status = L_b
                if n >= 5 or n == 0:
                    n = 1
                    dp = self.ZMC.Dpos_Det()
                    if dp != self.Dpos_hold:
                        for idx, v in enumerate(dp):
                            if v != self.Dpos_hold[idx]:
                                self.updateSignal.emit('Dpos' + str(idx), v)
                        self.Dpos_hold = dp
                zs = self.ZMC.Dzz_zs_Det()
                if self.zs_hold != zs:
                    self.zs_hold = zs
                    self.updateSignal.emit('Dzs', self.zs_hold)
                poll_fail = 0
                comm_fault_reported = False
                self.last_poll_ok = time.monotonic()
                if not self._monitor_ready_reported:
                    self._monitor_ready_reported = True
                    self.updateSignal.emit('MonitorReady', 0)
            except Exception as e:
                poll_fail += 1
                if n % 50 == 0:
                    print(f'Worker poll fail ({poll_fail} consec): {e}')
                # PC 侧看门狗：所有 checked DLL 非零返回码也会走到这里。
                if poll_fail >= poll_fail_limit and not comm_fault_reported:
                    comm_fault_reported = True
                    self.faulted = True
                    self.last_poll_ok = 0.0
                    self.ZMC.estop_latched = True
                    try:
                        self.ZMC.latch_estop("Communication_Watchdog")
                    except Exception as stop_error:
                        print(f'Watchdog spindle stop unconfirmed: {stop_error}')
                    try:
                        self.ZMC.Rapidstop()
                    except Exception as rapid_error:
                        print(f'Watchdog Rapidstop failed: {rapid_error}')
                    try:
                        self.ZMC.Enable_Axs(0)
                    except Exception as disable_error:
                        print(f'Watchdog disable axes failed: {disable_error}')
                    self.GCode_St_all = False
                    self.ZMC.Move_status = False
                    self._clear_gcode_motion_tracking()
                    self.updateSignal.emit('CommFault', poll_fail)
                    print('Watchdog: communication fault latched; emergency stop requested')
            try:
                if self.ZMC.Move_status:
                    if self._gcode_motion_timeout_due():
                        self._latch_gcode_fault(
                            "G代码单段运动超时："
                            + (self.ZMC.GCode_motion_description or "未知运动"), 3)
                    else:
                        ms = self.ZMC.Move_Det()
                        if 0 not in ms:
                            if self.GCode_St_all:
                                self._verify_gcode_endpoint()
                                self.ZMC.Move_status = False
                                self._clear_gcode_motion_tracking()
                            else:
                                self.ZMC.Move_status = False
            except Exception as e:
                print(f'Worker motion-status fault: {e}')
                if self.GCode_St_all:
                    self._latch_gcode_fault(
                        f"G代码运动状态/到位复核异常: {e}", 0)
            try:
                if self.GCode_St_all and not self.ZMC.Now_GCode_RST:
                    if self.ZMC.SetCmd == -1 and not self.ZMC.Move_status:
                        # G04 暂停绝不能在 Worker 内 sleep：那会停止安全输入
                        # 轮询并使 last_poll_ok 过期。暂停计时由 GUI 单次定时器
                        # 完成；本线程仍以 20 ms 周期继续轮询急停/通信状态。
                        if self.ZMC.G04_dwell_ms <= 0 and self.ZMC.T1sigl == 0:
                            self.updateSignal.emit('NG', 0)
            except Exception as e:
                print(f'Worker G-code progression fault: {e}')
                self._latch_gcode_fault(
                    f"G代码推进线程异常: {e}", 1)
            Tsleep(0.02)
        self.last_poll_ok = 0.0


class DebugEventFilter(QObject):
    """调试模式事件过滤器：捕获所有按钮点击并打印触发的事件和 ZMotion 函数"""

    # 按钮 → (事件描述, ZMotion DLL 函数)
    BTN_ACTION = {
        "pushButton":     ("搜索控制器 IP", "ZAux_SearchEthlist"),
        "pushButton_5":   ("连接控制器", "ZAux_OpenEth"),
        "pushButton_10":  ("初始化轴参数", "ZAux_Direct_SetAtype/Units/Speed/Accel/Decel/Sramp/Creep + RTCSub"),
        "pushButton_2":   ("X+ 正向移动 " , "ZAux_Direct_Single_Move(handle, axis0, +dist) / ZAux_Direct_Single_MoveAbs(handle, axis0, +pos)"),
        "pushButton_4":   ("X- 负向移动 " , "ZAux_Direct_Single_Move(handle, axis0, -dist) / ZAux_Direct_Single_MoveAbs(handle, axis0, -pos)"),
        "pushButton_3":   ("Y- 负向移动 " , "ZAux_Direct_Single_Move(handle, axis1, -dist) / ZAux_Direct_Single_MoveAbs(handle, axis1, -pos)"),
        "pushButton_8":   ("Y+ 正向移动 " , "ZAux_Direct_Single_Move(handle, axis1, +dist) / ZAux_Direct_Single_MoveAbs(handle, axis1, +pos)"),
	"pushButton_6":   ("Z+ 抬刀远离工件(上)", "ZAux_Direct_Single_Move(handle, axis3, +dist)"),
	"pushButton_7":   ("Z- 下刀靠近工件(下)", "ZAux_Direct_Single_Move(handle, axis3, -dist)"),
        "pushButton_13":  ("切换轴使能/未使能", "ZAux_Direct_SetOp IO2(使能) IO3(停止) IO12/13/15(标志)"),
        "pushButton_15":  ("执行回零程序", "ZAux_Direct_Single_Datum"),
        "pushButton_16":  ("重设坐标系 → 0", "ZAux_Direct_SetDpos"),
        "pushButton_17":  ("回原点 (0,0,0)", "ZAux_Direct_Single_MoveAbs (X,Y → 0; Z → 0)"),
        "pushButton_9":   ("启停电主轴", "ZAux_Direct_SetOp IO7"),
        "pushButton_19":  ("运行/停止 G代码", "parse → ZAux_Direct_Move/ZMoveCirc/ZMoveCircAbs"),
        "pushButton_11":  ("紧急停止", "ZAux_Direct_Rapidstop + ZAux_Direct_SetOp IO2=0(下使能)"),
        "pushButton_18":  ("恢复使能", "ZAux_Direct_SetOp IO2=1(使能) IO3=0 IO12/13/15=1"),
        "pushButton_12":  ("手动运行（标签）", "无作用（仅显示文字）"),
    }

    def __init__(self, main_window):
        super().__init__()
        self._win = main_window

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.MouseButtonPress
                and isinstance(obj, QPushButton)
                and self._win._debug_mode):
            name = obj.objectName()
            txt = obj.text() or name or "?"
            action, zmc_func = self.BTN_ACTION.get(name, ("未知操作", "?"))
            # 获取当前距离值（仅方向键需要）
            dist_info = ""
            if name in ("pushButton_2", "pushButton_3", "pushButton_4",
                        "pushButton_6", "pushButton_7", "pushButton_8"):
                try:
                    d = float(self._win.ui.DMove_dist.text() or "0")
                    dist_info = f" (距离={d}mm)"
                except Exception:
                    dist_info = " (距离=0mm)"
            self._win.Append_Log(
                f"[调试] 按钮 '{txt}' → {action}{dist_info} | 调用 {zmc_func}")
            return False  # 不拦截
        return False  # 默认不拦截任何事件


class MainWindow(QMainWindow):
    def __init__(self):
        QMainWindow.__init__(self)
        self.RealZMC = ZAUXWrapper()
        self.TestZMC = None
        self.ZMC = self.RealZMC
        self._spindle_backend_mode = "REAL"
        # 注：self.ZMC 替代了原先的 ZAUXWrapper 继承

        # 选数（限位值沿用 main3 原版，防止启动时限位不明撞机）
        self.StartPosition = [0, 0, 0]
        self.X_data = [-43, 0, 262]
        self.Y_data = [-33, 0, 250]
        self.Z_data = [-260, 0, 22]
        # Per-axis authorization is transient only.  It is never persisted or
        # inherited by a reconnect, an emergency stop, or a new controller
        # session.  A red coordinate is rendered while this set contains an
        # axis, or while its measured application DPOS is outside the window.
        self._soft_limit_recovery_axes = set()
        self.Dzz_data = [0, 0]
        self.Dzz_zs = 0
        self.Move_Type = 0          # 0=相对, 1=绝对（与 main3 行为一致）
        self.M_dist_data = 0
        self.Mat_Gcode = []
        self._gcode_running = False
        self._gcode_first_run_confirmed = False
        self.Now_Run_GCode = -1
        self.Safe_cnt = 0
        # G 代码的 F 与手动点动使用不同速度档。任何 Init、回零或
        # G 代码运行后，下一次手动运动都要重新确认并写入该速度档。
        self._manual_speed_profile_dirty = True
        self._manual_speed_profile_last = None
        # Authoritative machine state.  Button text is display-only, exactly
        # like the MARK2 spindle controls.
        self._axes_enabled = False
        self.Temp_cmd = []
        self.Per_cmd = []
        self.log_f = []

        # UI 设置
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self._setup_spindle_start_indicator()
        self._setup_handwheel_serial_ui()
        self.RealZMC.set_safety_monitor_health_check(
            self._check_safety_monitor_health)

        # Relative/absolute is a persistent operating mode, not transient
        # decoration: green=relative, red=absolute.
        self.ui.Move_Type.setStyleSheet(
            "background-color: #218838; color: white; font-weight: bold;")

        self._setup_spindle_backend_ui()
        self.bind()

        # 加载历史 IP
        self._load_ip_history()

        # 调试模式：F12 切换
        self._debug_mode = False
        QShortcut(QKeySequence("F12"), self, activated=self.Toggle_Debug_Mode)
        # 调试模式事件过滤器（仅监控用，不拦截任何事件）
        self._DebugFilter = DebugEventFilter(self)
        self.installEventFilter(self._DebugFilter)  # 只安装到主窗口，不影响子控件
        self.Append_Log("提示：按 F12 进入调试模式（仅未连接/未使能时可用）")

        # 手轮快捷键：用 QShortcut + WidgetContext 代替全局事件过滤器
        self._setup_handwheel_shortcuts()

        # 手轮模式（模拟手轮：上下键移动，左右键切换轴，Esc退出）
        self._mouse_mode = False
        self._mouse_axis = 'X'
        self._mouse_step = 0.01

        # 连续模式长按移动定时器（每 100ms 触发一次）
        self._cont_timer = QTimer(self)
        self._cont_timer.setInterval(100)
        self._cont_btn = None  # 当前长按的方向：('M_P', 'X') 或 ('M_N', 'Y') 等
        self._cont_timer.timeout.connect(self._on_cont_timer)

        # G04 必须异步计时：Worker 在等待期间仍需持续轮询急停、限位和
        # 通信状态。single-shot 可避免暂停结束后重复推进下一条 G 代码。
        self._gcode_dwell_timer = QTimer(self)
        self._gcode_dwell_timer.setSingleShot(True)
        self._gcode_dwell_timer.timeout.connect(self._finish_gcode_dwell)

        # ui2.ui 自带手轮模式 UI 控件
        self._step_to_dist = {
            self.ui.pushButton_24: 0.01, self.ui.pushButton_25: 0.05,
            self.ui.pushButton_26: 0.1,  self.ui.pushButton_27: 0.5,
            self.ui.pushButton_28: 1,    self.ui.pushButton_29: 5,
            self.ui.pushButton_30: 10,
        }
        self._continuous_mode = False  # 连续模式开关
        # 默认：点动模式，连续按钮为黑字白底
        self._set_continuous_style(False)
        # 默认：Mpg 轴 X，普通模式
        self._sync_mode_checkboxes()
        self.ui.checkBox_4.setChecked(True)   # 默认勾选 X 轴
        self.ui.checkBox_5.setChecked(False)
        self.ui.checkBox_6.setChecked(False)
        # 绑定：模式切换按钮
        self.ui.pushButton_22.clicked.connect(self.Toggle_Mode_Normal_Mouse)
        # 绑定：模拟手轮触发按钮
        self.ui.pushButton_23.clicked.connect(self.Activate_Mouse_Mode)
        # 绑定：MPG 轴复选框（已设为只读，仅显示当前轴状态，不可点击切换）
        self.ui.checkBox_4.clicked.connect(lambda: self._set_mpg_axis('X'))
        self.ui.checkBox_5.clicked.connect(lambda: self._set_mpg_axis('Y'))
        self.ui.checkBox_6.clicked.connect(lambda: self._set_mpg_axis('Z'))
        self.ui.checkBox_4.setEnabled(False)
        self.ui.checkBox_5.setEnabled(False)
        self.ui.checkBox_6.setEnabled(False)
        # 绑定：步进选择按钮（互斥）
        for btn in self._step_to_dist.keys():
            btn.clicked.connect(lambda checked, b=btn: self._on_step_btn_clicked(b))
        # 绑定：连续模式按钮
        self.ui.pushButton_31.clicked.connect(self.Toggle_Continuous_Mode)
        # 绑定：G54 设零
        self.ui.pushButton_33.clicked.connect(lambda: self.G54_SetZero('X'))
        self.ui.pushButton_34.clicked.connect(lambda: self.G54_SetZero('Y'))
        self.ui.pushButton_35.clicked.connect(lambda: self.G54_SetZero('Z'))

        # 连续模式方向键绑定（默认 click 模式）
        # self._bind_continuous()  # 已注释：bind() 已连接方向键，_bind_continuous 会覆盖导致失效

        # G54 偏置（与 Mpos_Det 一致：4 元素，Z 在索引 3）
        self.G54_offset = [0.0, 0.0, 0.0, 0.0]

        # 使能按钮初始颜色：未使能=黄色，使能=绿色（由 Ax_En 切换）
        self.ui.Ax_enable.setStyleSheet("background-color: #FFD700; color: #222; font-weight: bold;")

        self.Init_par()

        # 初始数据展示
        self.F5_data('ALL')
        self.Update_StatusLight('disable')  # 初始：黄灯-未使能
        # 加载上次保存的 G54 偏置
        self._load_g54()

        # 初始禁用所有需 InitZMC 才能用的按键（与 main3 行为一致）
        for _n in ('InitZMC', 'X_N_B', 'X_P_B', 'Y_N_B', 'Y_P_B',
                   'Z_N_B', 'Z_P_B', 'DZZ_Run_B', 'GoHome', 'Reset_CSys',
                   'Gcode_run', 'GoZero', 'Ax_enable', 'EStop_Btn', 'Resume_Btn',
                   'ManualSpeedBox'):
            w = getattr(self.ui, _n, None)
            if w is not None:
                w.setEnabled(False)

        # 两个距离输入框联动：改一个另一个同步
        self.ui.lineEdit_2.setText(self.ui.DMove_dist.text())
        def _sync_dist(src, dst):
            t = src.text()
            if dst.text() != t:
                dst.blockSignals(True)
                dst.setText(t)
                dst.blockSignals(False)
        self.ui.DMove_dist.editingFinished.connect(
            lambda: _sync_dist(self.ui.DMove_dist, self.ui.lineEdit_2))
        self.ui.lineEdit_2.editingFinished.connect(
            lambda: _sync_dist(self.ui.lineEdit_2, self.ui.DMove_dist))

        for _name in ('ManualSpeedX', 'ManualSpeedY', 'ManualSpeedZ'):
            getattr(self.ui, _name).editingFinished.connect(
                self._mark_manual_speed_profile_dirty)

        if self.ZMC.zmcaux is None:
            self.ui.Search.setEnabled(False)
            self.ui.Connect.setEnabled(False)
            self.Append_Log("界面已启动，但运动控制驱动不可用。")
            self.Append_Log(self.ZMC.driver_error)
        else:
            self.Append_Log("系统就绪。请点击 search 搜索控制器。")

    # ===== 基础 =====
    def Init_par(self):
        self.Dzz_zs = 0

    def _check_safety_monitor_health(self):
        """MARK2 parity: Start is denied without a fresh polling heartbeat."""
        worker = getattr(self, 'Worker', None)
        if worker is None or not worker.isRunning():
            raise SpindleSafetyError("安全轮询线程未运行")
        if getattr(worker, 'faulted', False):
            raise SpindleSafetyError("安全轮询线程已故障")
        last_poll_ok = float(getattr(worker, 'last_poll_ok', 0.0) or 0.0)
        age = time.monotonic() - last_poll_ok if last_poll_ok > 0 else float('inf')
        if age > 0.2:
            raise SpindleSafetyError(
                f"安全输入心跳过期（{age:.3f}s，限制0.200s）")
        return True

    def _setup_spindle_backend_ui(self):
        """在现有 UI 顶部加入显式 REAL/TEST 选择，不修改生成的 ui2.py。"""
        self.SpindleBackendLabel = QLabel("主轴后端", self.ui.centralwidget)
        self.SpindleBackendLabel.setGeometry(QRect(380, 25, 61, 24))
        self.SpindleBackendMode = QComboBox(self.ui.centralwidget)
        self.SpindleBackendMode.setGeometry(QRect(440, 25, 131, 24))
        self.SpindleBackendMode.addItems(["REAL 硬件", "TEST 树莓派"])
        self.SpindleBackendMode.setCurrentIndex(0)
        self.SpindleBackendMode.setStyleSheet(
            "background-color: #fff3cd; color: #7a4f00; font-weight: bold;")
        self.SpindleBackendMode.currentIndexChanged.connect(
            self._on_spindle_backend_changed)

    def _setup_spindle_start_indicator(self):
        """Create a display-only running indicator next to the spindle controls."""
        self.SpindleRunningCheckBox = QCheckBox(self.ui.centralwidget)
        self.SpindleRunningCheckBox.setObjectName("SpindleRunningCheckBox")
        self.SpindleRunningCheckBox.setGeometry(QRect(505, 565, 20, 21))
        self.SpindleRunningCheckBox.setAccessibleName("主轴已启动状态")
        self.SpindleRunningCheckBox.setToolTip("仅在主轴启动命令成功后由程序勾选")
        self.SpindleRunningCheckBox.setChecked(False)
        self.SpindleRunningCheckBox.setEnabled(False)

    def _set_spindle_running_indicator(self, running):
        """The indicator is status-only; operators cannot alter it directly."""
        self.SpindleRunningCheckBox.setChecked(bool(running))

    def _confirm_spindle_start(self):
        """Require an explicit operator acknowledgement before a manual START."""
        message_box = QMessageBox(self)
        message_box.setIcon(QMessageBox.Icon.Warning)
        message_box.setWindowTitle("启动主轴前确认")
        message_box.setText("请确认：\n磁悬浮已开启\n水冷已开启")
        confirm_button = message_box.addButton("确认", QMessageBox.ButtonRole.AcceptRole)
        cancel_button = message_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        message_box.setDefaultButton(cancel_button)
        message_box.exec()
        return message_box.clickedButton() is confirm_button

    def _setup_handwheel_serial_ui(self):
        """Add the ESP-01S Wi-Fi handwheel transport panel above the log.

        Transport starts in observe-only mode.  Motion is possible only after
        the existing handwheel mode has been explicitly entered and this
        transport has completed its own protocol health checks.
        """
        panel = self.ui.groupBox_8
        # Keep the existing log/export header, add one compact network row, and
        # move only the log text down.  No machine-control widget is touched.
        self.ui.Log_text.setGeometry(QRect(10, 82, 221, 489))

        self.HandwheelSerialLabel = QLabel("WiFi手轮", panel)
        self.HandwheelSerialLabel.setGeometry(QRect(10, 49, 52, 25))
        self.HandwheelSerialLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.HandwheelHostInput = QLineEdit(panel)
        self.HandwheelHostInput.setGeometry(QRect(63, 49, 76, 25))
        self.HandwheelHostInput.setPlaceholderText("192.168.4.1")
        self.HandwheelHostInput.setToolTip("ESP-01S AP 地址")
        self.HandwheelPortInput = QLineEdit(panel)
        self.HandwheelPortInput.setGeometry(QRect(141, 49, 39, 25))
        self.HandwheelPortInput.setPlaceholderText("8266")
        self.HandwheelPortInput.setToolTip("ESP-01S TCP 端口")
        self.HandwheelPortConnect = QPushButton("连接", panel)
        self.HandwheelPortConnect.setGeometry(QRect(182, 49, 49, 25))

        settings = self._portable_settings()
        self.HandwheelHostInput.setText(
            str(settings.value("handwheel_wifi/host", WIFI_HANDWHEEL_DEFAULT_HOST)))
        self.HandwheelPortInput.setText(
            str(settings.value("handwheel_wifi/port", WIFI_HANDWHEEL_DEFAULT_PORT)))

        self._handwheel_serial = WifiHandwheelTransport()
        self._handwheel_rx_buffer = bytearray()
        self._handwheel_disconnect_in_progress = False
        self._handwheel_hello_seen = False
        self._handwheel_status_online = False
        self._handwheel_status_armed = False
        self._handwheel_health_ok = False
        self._handwheel_health_counts = None
        self._handwheel_health_reset_pending = False
        self._handwheel_recovery_prompt_expected = False
        self._handwheel_protocol_faulted = False
        self._handwheel_last_sequence = None
        self._handwheel_arm_token = None
        self._handwheel_arm_pending = False
        self._handwheel_arm_acknowledged = False
        # None / "keyboard" / "usb".  It is intentionally separate from
        # _mouse_mode: the latter remains the existing, operator-visible
        # handwheel enable state.
        self._handwheel_input_source = None
        self._handwheel_transport_poll = QTimer(self)
        self._handwheel_transport_poll.setInterval(20)
        self._handwheel_transport_poll.timeout.connect(
            self._poll_handwheel_wifi)

        self._handwheel_heartbeat = QTimer(self)
        self._handwheel_heartbeat.setInterval(500)
        self._handwheel_heartbeat.timeout.connect(
            self._send_handwheel_heartbeat)

        self.HandwheelPortConnect.clicked.connect(
            self.Toggle_Handwheel_Serial)

    def _reset_handwheel_protocol_state(self):
        """Forget every trust decision when a USB handwheel session changes."""
        self._handwheel_hello_seen = False
        self._handwheel_status_online = False
        self._handwheel_status_armed = False
        self._handwheel_health_ok = False
        self._handwheel_health_counts = None
        self._handwheel_health_reset_pending = False
        self._handwheel_recovery_prompt_expected = False
        self._handwheel_protocol_faulted = False
        self._handwheel_last_sequence = None
        self._handwheel_arm_token = None
        self._handwheel_arm_pending = False
        self._handwheel_arm_acknowledged = False

    def _handwheel_transport_ready(self):
        # HEALTH is a current-session diagnostic reply.  A non-zero historical
        # counter must never become a permanent lock after the lower bridge has
        # already established a fresh, unarmed TCP session.
        return (self._handwheel_hello_seen
                and self._handwheel_status_online
                and self._handwheel_health_ok
                and not self._handwheel_protocol_faulted)

    def _handwheel_protocol_ready(self):
        """True only after the v4 ARM acknowledgement and armed STATUS agree."""
        return (self._handwheel_transport_ready()
                and self._handwheel_status_armed
                and self._handwheel_arm_acknowledged
                and getattr(self, '_handwheel_input_source', None) == 'wifi'
                and getattr(self, '_mouse_mode', False))

    def _request_usb_handwheel_health_reset(self):
        """Ask the physical pendant to discard stale data while unarmed."""
        if (not self._handwheel_serial.isOpen()
                or self._handwheel_status_armed
                or getattr(self, '_handwheel_input_source', None) is not None):
            self.Append_Log("[WiFi手轮] HEALTH 重置被拒绝：手轮必须已连接且 ARM=OFF")
            return False
        if self._handwheel_serial.bytesToWrite() >= 256:
            self.Append_Log("[WiFi手轮] HEALTH 重置未发送：网络发送队列繁忙")
            return False
        self._handwheel_health_reset_pending = True
        # HELLO makes the request valid even when the pendant has just timed
        # out to PC=OFF but the serial transport itself is already back.
        written = self._handwheel_serial.write(b"HELLO\nHEALTH,RESET\n")
        if written < 0:
            self._handwheel_health_reset_pending = False
            self.Append_Log("[WiFi手轮] HEALTH 重置命令发送失败")
            return False
        self.Append_Log("[WiFi手轮] 已请求下位机清队列并重置 HEALTH；等待新会话，ARM 保持 OFF")
        return True

    def _disarm_usb_handwheel(self, reason, notify_lower=True):
        """Revoke locally before asking the pendant to discard its motion queue."""
        was_active = (
            getattr(self, '_handwheel_input_source', None) in ('wifi', 'wifi_pending')
            or getattr(self, '_handwheel_arm_pending', False)
            or getattr(self, '_handwheel_arm_acknowledged', False)
            or getattr(self, '_handwheel_status_armed', False))
        self._handwheel_input_source = None
        self._handwheel_arm_pending = False
        self._handwheel_arm_acknowledged = False
        self._handwheel_arm_token = None
        self._handwheel_status_armed = False
        self._handwheel_last_sequence = None
        if (notify_lower and was_active and self._handwheel_serial.isOpen()
                and self._handwheel_serial.bytesToWrite() < 256):
            self._handwheel_serial.write(b"DISARM\n")
        return was_active

    @staticmethod
    def _handwheel_arm_token_valid(token):
        return (1 <= len(token) <= 20 and token.isascii()
                and all(char.isalnum() or char in '-_' for char in token))

    def _begin_usb_handwheel_arm(self):
        """Issue a fresh v4 authorization only after the existing UI gate opened."""
        if not self._handwheel_transport_ready() or self._handwheel_status_armed:
            return False
        token = 'hw_' + secrets.token_hex(8)
        self._handwheel_arm_token = token
        self._handwheel_arm_pending = True
        self._handwheel_arm_acknowledged = False
        self._handwheel_last_sequence = None
        # Block keyboard shortcuts while the physical pendant is awaiting ACK.
        self._handwheel_input_source = 'wifi_pending'
        written = self._handwheel_serial.write(f"ARM,{token}\n".encode('ascii'))
        if written < 0:
            self._disarm_usb_handwheel('ARM command write failed')
            return False
        return True

    def _maybe_grant_usb_handwheel_authority(self):
        if (self._handwheel_arm_pending
                and self._handwheel_arm_acknowledged
                and self._handwheel_status_armed
                and getattr(self, '_mouse_mode', False)):
            self._handwheel_arm_pending = False
            self._handwheel_input_source = 'wifi'
            self.Append_Log('[WiFi手轮] v4 ARM 已确认；已接管现有手轮输入')

    def _cancel_usb_handwheel_authority(self, reason, notify_lower=True):
        """Make any transport fault unable to leave physical handwheel motion armed."""
        was_active = self._disarm_usb_handwheel(reason, notify_lower=notify_lower)
        if was_active and getattr(self, '_mouse_mode', False):
            self.Exit_Mouse_Mode()
        if was_active:
            self.Append_Log(f"[WiFi手轮] 已取消接管：{reason}")

    def _handwheel_protocol_fault(self, reason):
        if self._handwheel_protocol_faulted:
            return
        self._handwheel_protocol_faulted = True
        self._handwheel_health_ok = False
        self.Append_Log(f"[WiFi手轮] 协议异常：{reason}；请断开后重新连接")
        self._cancel_usb_handwheel_authority(reason)

    @staticmethod
    def _parse_handwheel_uint(text, field_name):
        if not text or not text.isascii() or not text.isdecimal():
            raise ValueError(f"{field_name}不是非负十进制整数")
        return int(text)

    def _accept_handwheel_sequence(self, sequence):
        """Every CONFIG/EVENT shares one strictly contiguous input sequence."""
        previous = self._handwheel_last_sequence
        if previous is None:
            self._handwheel_last_sequence = sequence
            return True
        if sequence == previous + 1:
            self._handwheel_last_sequence = sequence
            return True
        if sequence <= previous:
            self._handwheel_protocol_fault(
                f"序号倒退/重复（收到 {sequence}，上次 {previous}）")
        else:
            self._handwheel_protocol_fault(
                f"序号跳变（收到 {sequence}，期望 {previous + 1}）")
        return False

    def _sync_usb_handwheel_axis(self, axis):
        self._mouse_axis = axis
        boxes = {
            'X': self.ui.checkBox_4,
            'Y': self.ui.checkBox_5,
            'Z': self.ui.checkBox_6,
        }
        for name, box in boxes.items():
            box.setChecked(name == axis)

    def _sync_usb_handwheel_step(self, step_um):
        distance = step_um / 1000.0
        self._mouse_step = distance
        self.ui.DMove_dist.setText(f"{distance:g}")
        self.ui.lineEdit_2.setText(f"{distance:g}")
        # Keep the existing handwheel indicator truthful without synthesizing
        # a button click or generating an additional motion event.
        for button, button_distance in self._step_to_dist.items():
            if abs(button_distance - distance) < 1e-12:
                button.setStyleSheet(
                    "background-color: #4a9eff; color: white; font-weight: bold;")
            else:
                button.setStyleSheet("background-color: #e0e0e0; color: #222;")

    def _handle_usb_handwheel_event(self, sequence, axis, step_um, direction):
        """Map one validated physical detent to the established M_P/M_N path."""
        del sequence  # Sequence was checked before this command-entry gate.
        self._sync_usb_handwheel_axis(axis)
        self._sync_usb_handwheel_step(step_um)

        if not self._handwheel_protocol_ready():
            self.Append_Log("[WiFi手轮] EVENT 已忽略：握手/在线状态/健康检查未完成")
            return
        if (getattr(self, '_handwheel_input_source', None) != 'wifi'
                or not getattr(self, '_mouse_mode', False)):
            self.Append_Log("[WiFi手轮] EVENT 已忽略：未在现有手轮模式中接管")
            return
        if self.Move_Type != 0:
            self.Append_Log("[WiFi手轮] EVENT 已忽略：仅允许相对模式")
            return
        if getattr(self, '_gcode_running', False):
            self.Append_Log("[WiFi手轮] EVENT 已忽略：G代码正在运行")
            return
        if step_um > WIFI_HANDWHEEL_MAX_STEP_UM:
            self.Append_Log(
                f"[WiFi手轮] EVENT 已忽略：{step_um / 1000.0:g}mm 超过当前安全上限 "
                f"{WIFI_HANDWHEEL_MAX_STEP_UM / 1000.0:g}mm")
            return

        if direction > 0:
            accepted = self.M_P(axis, source='wifi_handwheel')
            logical_direction = '+'
        else:
            accepted = self.M_N(axis, source='wifi_handwheel')
            logical_direction = '-'
        if accepted:
            self.Append_Log(
                f"[WiFi手轮] 已通过既有 {axis}{logical_direction} 点动路径")

    def _handle_handwheel_protocol_line(self, text):
        """Strict protocol parser.  Malformed serial text never reaches motion."""
        fields = text.split(',')
        try:
            if fields == ['HELLO', 'HWHEEL', '4']:
                # A new HELLO is a lower-side session boundary and therefore
                # must never inherit a prior PC-side authorization.
                if (self._handwheel_hello_seen
                        or getattr(self, '_handwheel_arm_pending', False)
                        or getattr(self, '_handwheel_input_source', None) == 'wifi'):
                    self._cancel_usb_handwheel_authority(
                        'handwheel started a new session', notify_lower=False)
                self._reset_handwheel_protocol_state()
                self._handwheel_hello_seen = True
                return

            if len(fields) == 5 and fields[0] == 'STATUS':
                axis = fields[1]
                step_um = self._parse_handwheel_uint(fields[2], 'STATUS步长')
                online = self._parse_handwheel_uint(fields[3], 'STATUS在线标志')
                armed = self._parse_handwheel_uint(fields[4], 'STATUS授权标志')
                if (axis not in ('X', 'Y', 'Z')
                        or step_um not in (10, 50, 100, 500, 1000)
                        or online not in (0, 1) or armed not in (0, 1)):
                    raise ValueError('STATUS字段超出协议范围')
                self._sync_usb_handwheel_axis(axis)
                self._sync_usb_handwheel_step(step_um)
                self._handwheel_status_online = bool(online)
                self._handwheel_status_armed = bool(armed)
                if armed:
                    if not self._handwheel_arm_acknowledged:
                        raise ValueError('未收到匹配 ARMED 就报告已授权')
                    self._maybe_grant_usb_handwheel_authority()
                elif (self._handwheel_arm_pending
                      or self._handwheel_arm_acknowledged
                      or getattr(self, '_handwheel_input_source', None) == 'wifi'):
                    self._cancel_usb_handwheel_authority(
                        'lower-side authorization was revoked', notify_lower=False)
                return

            if len(fields) == 3 and fields[0] == 'ARMED':
                token = fields[1]
                baseline = self._parse_handwheel_uint(fields[2], 'ARMED序号基线')
                if (not self._handwheel_arm_pending
                        or self._handwheel_arm_acknowledged
                        or not self._handwheel_arm_token_valid(token)
                        or not secrets.compare_digest(token, self._handwheel_arm_token or '')):
                    raise ValueError('ARMED 不匹配当前 ARM 请求')
                self._handwheel_arm_acknowledged = True
                self._handwheel_last_sequence = baseline
                self._maybe_grant_usb_handwheel_authority()
                return

            if fields == ['DISARMED']:
                self._cancel_usb_handwheel_authority(
                    'lower-side DISARM confirmation', notify_lower=False)
                return

            if fields == ['HEALTH', 'RESET', 'OK']:
                if self._handwheel_health_reset_pending:
                    self._reset_handwheel_protocol_state()
                    self.Append_Log("[WiFi手轮] 下位机已确认 HEALTH 重置；等待 HELLO/STATUS/HEALTH")
                else:
                    self.Append_Log("[WiFi手轮] 下位机按键已完成 HEALTH 重置")
                self._handwheel_recovery_prompt_expected = True
                return

            if (fields == ['ERR', 'HEALTH_RESET_REQUIRES_ONLINE']
                    or fields == ['ERR', 'HEALTH_RESET_REQUIRES_DISARM']):
                self._handwheel_health_reset_pending = False
                self.Append_Log("[WiFi手轮] HEALTH 重置被下位机拒绝：必须 PC=ON 且 ARM=OFF")
                return

            if len(fields) == 3 and fields[:2] == ['RECOVERY', 'REARM']:
                choice = fields[2]
                if choice not in ('YES', 'NO'):
                    raise ValueError('RECOVERY 重启手轮选择非法')
                if not self._handwheel_recovery_prompt_expected:
                    self.Append_Log("[WiFi手轮] 已忽略未关联恢复会话的 REARM 请求")
                    return
                self._handwheel_recovery_prompt_expected = False
                if choice == 'NO':
                    self.Append_Log("[WiFi手轮] 操作者在下位机选择不重新 ARM")
                    return
                if not self._handwheel_transport_ready():
                    self.Append_Log("[WiFi手轮] 下位机请求重新 ARM，但新会话尚未就绪，保持 OFF")
                    return
                self.Append_Log("[WiFi手轮] 操作者在下位机确认重新 ARM；执行原有手轮安全检查")
                self.Activate_Mouse_Mode()
                return

            if fields[:2] == ['HELLO', 'HWHEEL'] or fields[0] == 'STATUS':
                raise ValueError('only v4 handwheel replies are accepted')

            if len(fields) == 4 and fields[0] == 'HEALTH':
                health = tuple(
                    self._parse_handwheel_uint(item, 'HEALTH计数')
                    for item in fields[1:])
                self._handwheel_health_counts = health
                # A valid HEALTH reply is required for handshake completeness,
                # but non-zero counts are diagnostics. Real current transport
                # loss is still fail-closed via STATUS=OFF, TCP disconnect,
                # bridge LINK_FAULT, CRC error, or lower PING timeout.
                self._handwheel_health_ok = True
                if health != (0, 0, 0):
                    self.Append_Log(
                        f"[WiFi手轮] HEALTH 告警 {health}；当前会话仍保持 ARM=OFF，"
                        "可继续完成正常握手；若计数持续增长，请检查链路")
                return

            if len(fields) == 4 and fields[0] == 'CONFIG':
                if not self._handwheel_arm_acknowledged:
                    raise ValueError('CONFIG arrived before ARM acknowledgement')
                sequence = self._parse_handwheel_uint(fields[1], 'CONFIG序号')
                if sequence == 0 or not self._accept_handwheel_sequence(sequence):
                    return
                if fields[2] == 'AXIS' and fields[3] in ('X', 'Y', 'Z'):
                    self._sync_usb_handwheel_axis(fields[3])
                    return
                if fields[2] == 'STEP':
                    step_um = self._parse_handwheel_uint(fields[3], 'CONFIG步长')
                    if step_um in (10, 50, 100, 500, 1000):
                        self._sync_usb_handwheel_step(step_um)
                        return
                raise ValueError('CONFIG字段超出协议范围')

            if len(fields) == 5 and fields[0] == 'EVENT':
                if not self._handwheel_arm_acknowledged:
                    raise ValueError('EVENT arrived before ARM acknowledgement')
                sequence = self._parse_handwheel_uint(fields[1], 'EVENT序号')
                axis = fields[2]
                step_um = self._parse_handwheel_uint(fields[3], 'EVENT步长')
                direction = fields[4]
                if (sequence == 0 or axis not in ('X', 'Y', 'Z')
                        or step_um not in (10, 50, 100, 500, 1000)
                        or direction not in ('1', '-1')):
                    raise ValueError('EVENT字段超出协议范围')
                if not self._accept_handwheel_sequence(sequence):
                    return
                self._handle_usb_handwheel_event(
                    sequence, axis, step_um, int(direction))
                return
        except ValueError as exc:
            self._handwheel_protocol_fault(f"{text!r}：{exc}")
            return

        # Other defined firmware replies (for example DEBUG) remain log-only.
        # Unknown input is deliberately not a protocol fault so a future
        # diagnostic reply cannot make a running UI unsafe.

    def Scan_Handwheel_Ports(self, checked=False, log_result=True):
        """Compatibility entry point: Wi-Fi pendant uses a fixed TCP endpoint."""
        del checked
        if self._handwheel_serial.isOpen() or self._handwheel_serial.isConnecting():
            if log_result:
                self.Append_Log("[WiFi手轮] 已连接时不修改地址，请先断开")
            return
        if log_result:
            self.Append_Log(
                f"[WiFi手轮] 目标地址：{self.HandwheelHostInput.text().strip()}:"
                f"{self.HandwheelPortInput.text().strip()}")

    def Toggle_Handwheel_Serial(self, checked=False):
        """Connect/disconnect the ESP-01S TCP transport; it starts unarmed."""
        del checked
        if self._handwheel_serial.isOpen() or self._handwheel_serial.isConnecting():
            self._disconnect_handwheel_serial(log_result=True)
            return

        host = self.HandwheelHostInput.text().strip()
        try:
            port = int(self.HandwheelPortInput.text().strip())
        except ValueError:
            self.Append_Log("[WiFi手轮] 连接失败：TCP 端口必须是整数")
            return
        self._handwheel_rx_buffer.clear()
        self._reset_handwheel_protocol_state()

        try:
            connected_now = self._handwheel_serial.begin_connect(host, port)
        except (OSError, ValueError) as exc:
            self.Append_Log(f"[WiFi手轮] 连接 {host}:{port} 失败：{exc}")
            return

        self.HandwheelHostInput.setEnabled(False)
        self.HandwheelPortInput.setEnabled(False)
        self.HandwheelPortConnect.setText("断开")
        self.HandwheelPortConnect.setStyleSheet(
            "background-color: #b57600; color: white; font-weight: bold;")
        self._handwheel_transport_poll.start()
        if connected_now:
            self._complete_handwheel_wifi_connection()
        else:
            self.Append_Log(f"[WiFi手轮] 正在连接 {host}:{port}；保持 ARM=OFF")

    def _complete_handwheel_wifi_connection(self):
        if not self._handwheel_serial.isOpen():
            return
        self.HandwheelPortConnect.setStyleSheet(
            "background-color: #218838; color: white; font-weight: bold;")
        self._handwheel_heartbeat.start()
        if self._handwheel_serial.write(b"HELLO\nSTATUS?\nHEALTH?\n") < 0:
            self._disconnect_handwheel_serial(log_result=False)
            self.Append_Log("[WiFi手轮] 初始握手发送失败")
            return
        settings = self._portable_settings()
        settings.setValue("handwheel_wifi/host", self.HandwheelHostInput.text().strip())
        settings.setValue("handwheel_wifi/port", self.HandwheelPortInput.text().strip())
        self.Append_Log(
            f"[WiFi手轮] 已连接 {self._handwheel_serial.endpoint}；等待 "
            "HELLO、STATUS=ONLINE、HEALTH=0 后才可接管")

    def _poll_handwheel_wifi(self):
        for event, payload in self._handwheel_serial.poll():
            if event == "connected":
                self._complete_handwheel_wifi_connection()
            elif event == "data":
                self._consume_handwheel_serial_bytes(payload)
            elif event == "error":
                self._disconnect_handwheel_serial(log_result=False)
                self.Append_Log(f"[WiFi手轮] TCP 连接已断开：{payload}")

    def _disconnect_handwheel_serial(self, log_result=True):
        if self._handwheel_disconnect_in_progress:
            return
        self._handwheel_disconnect_in_progress = True
        endpoint = self._handwheel_serial.endpoint
        try:
            self._handwheel_heartbeat.stop()
            self._handwheel_transport_poll.stop()
            self._cancel_usb_handwheel_authority("WiFi 手轮断开", notify_lower=False)
            self._handwheel_serial.close()
            self._handwheel_rx_buffer.clear()
            self._reset_handwheel_protocol_state()
            self.HandwheelHostInput.setEnabled(True)
            self.HandwheelPortInput.setEnabled(True)
            self.HandwheelPortConnect.setText("连接")
            self.HandwheelPortConnect.setStyleSheet("")
            self.HandwheelPortConnect.setEnabled(True)
            if log_result and endpoint:
                self.Append_Log(f"[WiFi手轮] 已断开 {endpoint}")
        finally:
            self._handwheel_disconnect_in_progress = False

    def _send_handwheel_heartbeat(self):
        if not self._handwheel_serial.isOpen():
            self._handwheel_heartbeat.stop()
            return
        # Heartbeat only maintains the pendant's PC:ONLINE display.  It is not
        # and must never become an axis-motion command.
        if self._handwheel_serial.bytesToWrite() < 256:
            if self._handwheel_serial.write(b"PING\n") < 0:
                self._disconnect_handwheel_serial(log_result=False)
                self.Append_Log("[WiFi手轮] 心跳发送失败，已撤销手轮授权")

    def _consume_handwheel_serial_bytes(self, data):
        """Frame bounded ASCII lines, log them, then pass valid lines to the gate."""
        self._handwheel_rx_buffer.extend(bytes(data))
        if len(self._handwheel_rx_buffer) > 4096:
            self._handwheel_rx_buffer.clear()
            self.Append_Log("[WiFi手轮] 接收缓存溢出，已丢弃异常数据")
            self._handwheel_protocol_fault("上位机接收缓存溢出")
            return

        while b"\n" in self._handwheel_rx_buffer:
            raw, _, remaining = self._handwheel_rx_buffer.partition(b"\n")
            self._handwheel_rx_buffer = bytearray(remaining)
            raw = raw.rstrip(b"\r")
            if not raw:
                continue
            if len(raw) > 512:
                self.Append_Log("[WiFi手轮] 收到超长消息，已丢弃")
                continue
            text = raw.decode("ascii", errors="replace")
            self.Append_Log(f"[WiFi手轮 RX] {text}")
            self._handle_handwheel_protocol_line(text)

    def _is_test_mode(self):
        return self._spindle_backend_mode == "TEST"

    def _on_spindle_backend_changed(self, index):
        requested = "TEST" if index == 1 else "REAL"
        if requested == self._spindle_backend_mode:
            return
        current_connected = bool(getattr(self.ZMC, 'is_connected', False))
        current_safe = (
            getattr(self.ZMC, 'spindle_state', 'STOPPED') == 'STOPPED'
            and getattr(self.ZMC, 'spindle_stop_confirmed', True))
        if current_connected or self.ui.Connect.text() == 'Disconnect' or not current_safe:
            self.Append_Log("拒绝切换主轴后端：必须先确认停止并断开当前连接")
            self.SpindleBackendMode.blockSignals(True)
            self.SpindleBackendMode.setCurrentIndex(
                1 if self._spindle_backend_mode == "TEST" else 0)
            self.SpindleBackendMode.blockSignals(False)
            return

        if hasattr(self, 'Worker') and self.Worker is not None and self.Worker.isRunning():
            self.Worker.Wstatus = False
            self.Worker.wait(1000)
        self._zmc_inited = False
        self._axes_enabled = False
        self._gcode_first_run_confirmed = False
        self._halt_manual_motion_ui()
        self._disable_controls_after_init()
        self.ui.InitZMC.setEnabled(False)
        self.ui.EStop_Btn.setEnabled(False)
        self.ui.Resume_Btn.setEnabled(False)
        self.ui.Connect.setText('Connect')
        self.ui.Connect.setStyleSheet("")
        self.ui.iplist.clear()
        self.ui.Ax_enable.setText('未使能')
        self.ui.DZZ_Run_B.setText('Start')

        if requested == "TEST":
            if self.TestZMC is None:
                self.TestZMC = TcpSpindleBackend(host=self._load_test_host())
            self.ZMC = self.TestZMC
            self._spindle_backend_mode = "TEST"
            self.ui.iplist.addItem(self.TestZMC.default_host)
            self.ui.iplist.setEditable(True)
            self.ui.Search.setEnabled(True)
            self.ui.Connect.setEnabled(True)
            self.SpindleBackendMode.setStyleSheet(
                "background-color: #3399ff; color: white; font-weight: bold;")
            self.Update_StatusLight('test')
            self.Append_Log("已切换到 TEST 树莓派虚拟主轴；真实运动轴已锁定")
            self.Append_Log(
                f"TEST 地址：{self.TestZMC.default_host}:{self.TestZMC.port}")
        else:
            self.ZMC = self.RealZMC
            self._spindle_backend_mode = "REAL"
            self.ui.Search.setEnabled(self.RealZMC.zmcaux is not None)
            self.ui.Connect.setEnabled(False)
            self.ui.iplist.setEditable(False)
            self.SpindleBackendMode.setStyleSheet(
                "background-color: #fff3cd; color: #7a4f00; font-weight: bold;")
            self.Update_StatusLight('disable')
            self._load_ip_history()
            self.Append_Log("已切换到 REAL 硬件后端；连接前请确认实体急停/STO")

    def _apply_test_mode_control_policy(self):
        """TEST 模式只允许主轴与 G 代码仿真，禁止任何真实轴按钮。"""
        if not self._is_test_mode():
            return
        self._axes_enabled = False
        self._halt_manual_motion_ui()
        for name in ('X_N_B', 'X_P_B', 'Y_N_B', 'Y_P_B', 'Z_N_B', 'Z_P_B',
                     'GoHome', 'Reset_CSys', 'GoZero', 'Ax_enable', 'pushButton_23'):
            widget = getattr(self.ui, name, None)
            if widget is not None:
                widget.setEnabled(False)
        self.ui.DZZ_Run_B.setEnabled(True)
        self.ui.Gcode_run.setEnabled(True)
        self.ui.EStop_Btn.setEnabled(True)
        self.ui.Resume_Btn.setEnabled(True)
        self._refresh_motion_controls()

    def _load_ip_history(self):
        """从程序目录内的便携 INI 加载历史连接 IP。"""
        try:
            s = self._portable_settings()
            ips = s.value("connection/ip_history", [])
            # 首次升级时兼容导入旧版注册表设置；以后只使用目录内 INI。
            if not ips and not s.contains("connection/ip_history_migrated"):
                legacy = QSettings("DZZ", "main_ui1")
                ips = legacy.value("connection/ip_history", [])
                s.setValue("connection/ip_history_migrated", True)
                if isinstance(ips, list) and ips:
                    s.setValue("connection/ip_history", ips)
            if isinstance(ips, list):
                for ip in ips:
                    found = False
                    for i in range(self.ui.iplist.count()):
                        if self.ui.iplist.itemText(i) == ip:
                            found = True
                            break
                    if not found:
                        self.ui.iplist.addItem(ip)
        except Exception:
            pass

    def _save_ip_history(self, ip):
        """将成功连接的 IP 写入程序目录内的便携 INI。"""
        try:
            s = self._portable_settings()
            ips = s.value("connection/ip_history", [])
            if not isinstance(ips, list):
                ips = []
            if ip and ip not in ips:
                ips.insert(0, ip)   # 最新的放最前面
                ips = ips[:20]      # 最多保留 20 条
                s.setValue("connection/ip_history", ips)
                s.sync()
        except Exception:
            pass

    @staticmethod
    def _portable_settings():
        """配置随程序目录迁移，不依赖原电脑注册表或用户名。"""
        return QSettings(_SETTINGS_PATH, QSettings.Format.IniFormat)

    def _load_test_host(self):
        try:
            host = str(self._portable_settings().value(
                "test/host", "192.168.50.100")).strip()
            return host or "192.168.50.100"
        except Exception:
            return "192.168.50.100"

    def _save_test_host(self, host):
        try:
            host = str(host).strip()
            if host:
                s = self._portable_settings()
                s.setValue("test/host", host)
                s.sync()
        except Exception:
            pass

    # ===== 手轮模式（模拟手轮） =====
    def Enter_Mouse_Mode(self):
        """进入手轮模式：禁用方向键，记录当前步长，Esc 退出"""
        if self._debug_mode:
            self.Append_Log("手轮模式：调试模式中无法进入")
            return False
        if self._soft_limit_recovery_active():
            self.Append_Log("手轮模式拒绝：存在软限位越界恢复状态，请先回到正常软限位范围")
            return False
        if self.Move_Type != 0:
            self.Append_Log("手轮模式拒绝：必须先切回绿色「相对」模式")
            return False
        try:
            self._require_motion_interlocks(require_axes=True, require_idle=True)
        except Exception as e:
            self.Append_Log(f"手轮模式拒绝：{e}")
            return False
        self._mouse_mode = True
        # 禁用方向键（急停/恢复按钮保持可用，确保安全）
        for n in ('X_N_B','X_P_B','Y_N_B','Y_P_B','Z_N_B','Z_P_B'):
            getattr(self.ui, n).setEnabled(False)
        # 模拟手轮按钮变绿
        self.ui.pushButton_23.setStyleSheet(
            "background-color: #2ea84f; color: white; font-weight: bold;")
        self.Append_Log("=" * 50)
        self.Append_Log("    进入手轮模式（模拟对刀）")
        self.Append_Log("    · X+右移，X-左移")
        self.Append_Log("    · Y+靠近操作者，Y-远离操作者")
        self.Append_Log("    · Z+靠近工件，Z-远离工件")
        self.Append_Log("    · Ctrl+↑/↓ = 当前轴正向/负向点动")
        self.Append_Log("    · Ctrl+← → = 切换 X→Y→Z→X")
        self.Append_Log("    · Esc = 退出")
        self.Append_Log(f"    当前轴: {self._mouse_axis}  步长: {self._mouse_step}mm")
        self.Append_Log("    对刀步骤：")
        self.Append_Log("    1. 选中 Z 轴，步长 0.1mm，Ctrl+↓下键点动靠近工件表面")
        self.Append_Log("    2. 步长切到 0.01mm，继续慢速靠近")
        self.Append_Log("    3. 看到碰刀火花 → 记下当前 Z 坐标")
        self.Append_Log("    4. Ctrl+↑上键抬刀 Z+ 离开工件")
        self.Append_Log("    5. 切到 X/Y 轴重复上述步骤")
        self.Append_Log("    ⚠ 装刀后刀尖比滑座长，Z 轴限位数值仅供参考")
        self.Append_Log("    ⚠ 操作 Z 轴时请以工件坐标（G54）为准，不要只看机械坐标")
        self.Append_Log("=" * 50)
        return True

    def Exit_Mouse_Mode(self):
        if not self._mouse_mode:
            return
        # Local rejection comes before the serial DISARM request.  A delayed
        # USB reply can therefore never keep this UI-side motion path active.
        self._disarm_usb_handwheel('operator exited handwheel mode')
        self._mouse_mode = False
        self._handwheel_input_source = None
        self._halt_manual_motion_ui()
        # 模拟手轮按钮恢复默认
        self.ui.pushButton_23.setStyleSheet("")
        self._refresh_motion_controls()
        self._refresh_spindle_controls()
        self.Append_Log("[手轮] 已退出")

    def _set_mouse_axis(self, ax):
        self._mouse_axis = ax

    def _sync_mode_checkboxes(self):
        """勾选状态变化时同步互斥关系"""
        sender = self.sender()
        # 普通/手轮互斥
        if sender in (self.ui.checkBox_7, self.ui.checkBox_8):
            if sender == self.ui.checkBox_7 and self.ui.checkBox_7.isChecked():
                self.ui.checkBox_8.setChecked(False)
            elif sender == self.ui.checkBox_8 and self.ui.checkBox_8.isChecked():
                self.ui.checkBox_7.setChecked(False)
            else:
                # 都不勾选时强制勾选其中一个（默认勾选普通）
                if not self.ui.checkBox_7.isChecked() and not self.ui.checkBox_8.isChecked():
                    self.ui.checkBox_7.setChecked(True)
        # MPG 轴互斥
        elif sender in (self.ui.checkBox_4, self.ui.checkBox_5, self.ui.checkBox_6):
            if sender == self.ui.checkBox_4 and self.ui.checkBox_4.isChecked():
                self.ui.checkBox_5.setChecked(False)
                self.ui.checkBox_6.setChecked(False)
                self._mouse_axis = 'X'
            elif sender == self.ui.checkBox_5 and self.ui.checkBox_5.isChecked():
                self.ui.checkBox_4.setChecked(False)
                self.ui.checkBox_6.setChecked(False)
                self._mouse_axis = 'Y'
            elif sender == self.ui.checkBox_6 and self.ui.checkBox_6.isChecked():
                self.ui.checkBox_4.setChecked(False)
                self.ui.checkBox_5.setChecked(False)
                self._mouse_axis = 'Z'

    def _set_mpg_axis(self, ax):
        """MPG 轴复选框切换"""
        self._mouse_axis = ax
        self._sync_mode_checkboxes()

    def Toggle_Mode_Normal_Mouse(self):
        """pushButton_22: 切换 普通/手轮 模式（按钮文本/颜色、复选框互斥）"""
        if self._mouse_mode:
            # 已在手轮模式，点此按钮退出手轮
            self.Exit_Mouse_Mode()
        if self.ui.checkBox_7.isChecked():
            # 当前普通模式 → 切到手轮模式
            self.ui.checkBox_7.setChecked(False)
            self.ui.checkBox_8.setChecked(True)
            self.ui.pushButton_22.setText("普通\n模式")
            self.ui.pushButton_22.setStyleSheet("background-color: #2ea84f; color: white; font-weight: bold;")
            self.Append_Log("模式切换：手轮模式（待激活）")
        else:
            # 当前手轮模式 → 切回普通模式
            self.ui.checkBox_7.setChecked(True)
            self.ui.checkBox_8.setChecked(False)
            self.ui.pushButton_22.setText("手轮\n模式")
            self.ui.pushButton_22.setStyleSheet("background-color: #e0e0e0; color: #222;")
            self.Append_Log("模式切换：普通模式")

    # ===== 手轮快捷键（QShortcut 方式，不干扰控件事件） =====
    def _setup_handwheel_shortcuts(self):
        """用 QShortcut 注册手轮快捷键，WidgetWithChildrenShortcut 确保在子控件中也能触发"""
        from PySide6.QtCore import Qt as QtConst
        for key_seq, handler in [
            ("Esc",          self._shortcut_handwheel_exit),
            ("Ctrl+Up",      self._shortcut_handwheel_up),
            ("Ctrl+Down",    self._shortcut_handwheel_down),
            ("Ctrl+Left",    self._shortcut_handwheel_left),
            ("Ctrl+Right",   self._shortcut_handwheel_right),
        ]:
            sc = QShortcut(QKeySequence(key_seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(handler)

    def _shortcut_handwheel_exit(self):
        if not self._mouse_mode:
            return
        self.Exit_Mouse_Mode()

    def _shortcut_handwheel_up(self):
        if (not self._mouse_mode
                or getattr(self, '_handwheel_input_source', None) in ('wifi', 'wifi_pending')):
            return
        self.M_P(self._mouse_axis, source='keyboard_handwheel')

    def _shortcut_handwheel_down(self):
        if (not self._mouse_mode
                or getattr(self, '_handwheel_input_source', None) in ('wifi', 'wifi_pending')):
            return
        self.M_N(self._mouse_axis, source='keyboard_handwheel')

    def _shortcut_handwheel_left(self):
        if (not self._mouse_mode
                or getattr(self, '_handwheel_input_source', None) in ('wifi', 'wifi_pending')):
            return
        axes = ['X', 'Z', 'Y']
        idx = axes.index(self._mouse_axis)
        self._mouse_axis = axes[(idx + 1) % 3]
        self.ui.checkBox_4.setChecked(self._mouse_axis == 'X')
        self.ui.checkBox_5.setChecked(self._mouse_axis == 'Y')
        self.ui.checkBox_6.setChecked(self._mouse_axis == 'Z')
        self.Append_Log(f"[手轮] 切换到 {self._mouse_axis} 轴")

    def _shortcut_handwheel_right(self):
        if (not self._mouse_mode
                or getattr(self, '_handwheel_input_source', None) in ('wifi', 'wifi_pending')):
            return
        axes = ['X', 'Y', 'Z']
        idx = axes.index(self._mouse_axis)
        self._mouse_axis = axes[(idx + 1) % 3]
        self.ui.checkBox_4.setChecked(self._mouse_axis == 'X')
        self.ui.checkBox_5.setChecked(self._mouse_axis == 'Y')
        self.ui.checkBox_6.setChecked(self._mouse_axis == 'Z')
        self.Append_Log(f"[手轮] 切换到 {self._mouse_axis} 轴")

    def Activate_Mouse_Mode(self):
        """pushButton_23: 模拟手轮 - 真正进入/退出鼠标控制模式"""
        if self.ui.checkBox_7.isChecked():
            self.Append_Log("当前是普通模式，请先切到手轮模式")
            return
        if self._mouse_mode:
            self.Exit_Mouse_Mode()
        else:
            if self._soft_limit_recovery_active():
                self.Append_Log("手轮模式拒绝：软限位越界恢复期间仅允许界面单步点动")
                return
            serial_open = self._handwheel_serial.isOpen()
            if serial_open and not self._handwheel_transport_ready():
                health = getattr(self, '_handwheel_health_counts', None)
                if health is not None and health != (0, 0, 0):
                    answer = QMessageBox.question(
                        self, "WiFi手轮 HEALTH 异常",
                        "下位机检测到历史丢弃计数：\n"
                        f"编码器={health[0]}，发送={health[1]}，接收={health[2]}\n\n"
                        "是否让下位机清空队列并将 HEALTH 重置为 0？\n"
                        "该操作保持 ARM=OFF，不会自动开始运动。",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No)
                    if answer == QMessageBox.StandardButton.Yes:
                        self._request_usb_handwheel_health_reset()
                    else:
                        self.Append_Log("[WiFi手轮] 操作者取消 HEALTH 重置，保持拒绝接管")
                    return
                self.Append_Log(
                    "WiFi手轮尚未完成 v4 HELLO/ONLINE/HEALTH 校验，拒绝进入手轮模式")
                return
            if not self.Enter_Mouse_Mode():
                return
            if serial_open:
                if not self._begin_usb_handwheel_arm():
                    self.Append_Log("[WiFi手轮] ARM 请求未发出，已退出手轮模式")
                    self.Exit_Mouse_Mode()
                    return
                self.Append_Log("[WiFi手轮] 已请求 v4 ARM；等待 ARMED 与 STATUS=ARMED 后才接管")
            else:
                self._handwheel_input_source = 'keyboard'
                self.Append_Log("[手轮] 当前使用原有键盘模拟输入")

    def _on_step_btn_clicked(self, btn):
        """步进选择按钮：互斥，更新 DMove_dist"""
        dist = self._step_to_dist[btn]
        # 连续模式：只允许步长 < 1
        if self._continuous_mode and dist >= 1:
            self.Append_Log(f"连续模式只支持步长<1，{dist}mm 已禁用")
            return
        # 高亮选中的按钮
        for b in self._step_to_dist.keys():
            if b == btn:
                b.setStyleSheet("background-color: #4a9eff; color: white; font-weight: bold;")
            else:
                b.setStyleSheet("background-color: #e0e0e0; color: #222;")
        # 同步到 DMove_dist
        self.ui.DMove_dist.setText(str(dist))
        self.ui.lineEdit_2.setText(str(dist))
        self._mouse_step = dist
        self.Append_Log(f"[步进] 步长改为 {dist}mm")

    def _make_move_handler(self, direction, ax):
        """连续模式：方向键按下时开始重复移动，松开时停止"""
        def _pressed():
            # 立即执行一次
            ok = (self.M_P(ax, source='continuous') if direction == 'M_P'
                  else self.M_N(ax, source='continuous'))
            if ok:
                self._cont_btn = (direction, ax)
                self._cont_timer.start()
            else:
                self._halt_manual_motion_ui()
        def _released():
            self._cont_btn = None
            self._cont_timer.stop()
        return _pressed, _released

    def _on_cont_timer(self):
        """连续模式定时器触发：按当前长按的按钮方向继续移动"""
        if self._cont_btn is None:
            self._cont_timer.stop()
            return
        direction, ax = self._cont_btn
        ok = (self.M_P(ax, source='continuous') if direction == 'M_P'
              else self.M_N(ax, source='continuous'))
        if not ok:
            self._halt_manual_motion_ui()

    def _bind_continuous(self):
        """连续模式：切换 pressed/released 和 clicked 连接"""
        Iui = self.ui
        # 所有方向按键: (btn, direction, ax)
        _dir_btns = [
            (Iui.X_N_B, 'M_N', 'X'), (Iui.X_P_B, 'M_P', 'X'),
            (Iui.Y_N_B, 'M_N', 'Y'), (Iui.Y_P_B, 'M_P', 'Y'),
            (Iui.Z_N_B, 'M_N', 'Z'), (Iui.Z_P_B, 'M_P', 'Z'),
        ]
        for btn, direction, ax in _dir_btns:
            try:
                btn.pressed.disconnect()
                btn.released.disconnect()
                btn.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            if self._continuous_mode:
                p, r = self._make_move_handler(direction, ax)
                btn.pressed.connect(p)
                btn.released.connect(r)
            else:
                btn.clicked.connect(lambda checked, d=direction, a=ax: (
                    self.M_P(a, source='panel') if d == 'M_P'
                    else self.M_N(a, source='panel')))

    def Toggle_Continuous_Mode(self):
        """连续模式：单点/连续切换；连续时步长<1"""
        if self._soft_limit_recovery_active():
            self.Append_Log("连续模式拒绝：软限位越界恢复期间仅允许界面单步点动")
            return False
        self._continuous_mode = not self._continuous_mode
        self._set_continuous_style(self._continuous_mode)
        self._bind_continuous()  # 重新绑定方向键事件
        if self._continuous_mode:
            self.Append_Log("连续模式：长按方向键可连续移动（步长<1）")
        else:
            self.Append_Log("单点模式：点击方向键单次移动")
        # 如果是单点模式，确保长按定时器停止
        if not self._continuous_mode:
            self._cont_timer.stop()
            self._cont_btn = None

    def _set_continuous_style(self, on):
        if on:
            self.ui.pushButton_31.setStyleSheet(
                "background-color: #d12f2f; color: white; font-weight: bold;")
            self.ui.pushButton_31.setText("连续")
            # 步长 1/5/10 灰显
            for btn, d in self._step_to_dist.items():
                if d >= 1:
                    btn.setEnabled(False)
                    btn.setStyleSheet("background-color: #888; color: white;")
                else:
                    btn.setEnabled(True)
        else:
            self.ui.pushButton_31.setStyleSheet(
                "background-color: #e0e0e0; color: #222;")
            self.ui.pushButton_31.setText("连续")
            for btn in self._step_to_dist.keys():
                btn.setEnabled(True)
                # 高亮当前选中
                if abs(self._step_to_dist[btn] - self._mouse_step) < 1e-9:
                    btn.setStyleSheet("background-color: #4a9eff; color: white; font-weight: bold;")
                else:
                    btn.setStyleSheet("background-color: #e0e0e0; color: #222;")

    # ===== G54 偏置持久化 =====
    def _g54_path(self):
        """G54 偏置文件路径（JSON 格式）"""
        return os.path.join(_CONFIG_DIR, "g54_offset.json")

    def _save_g54(self):
        """原子保存 G54 偏置；写入失败不冒充已保存。"""
        path = self._g54_path()
        tmp_path = path + ".tmp"
        try:
            import json
            data = {
                "format_version": 1,
                "G54_offset": self.G54_offset,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception as e:
            try:
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            raise SpindleSafetyError(f"保存G54偏置失败: {e}") from e

    def _load_g54(self):
        """从文件加载 G54 偏置"""
        try:
            import json
            path = self._g54_path()
            if not os.path.isfile(path):
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = data.get("G54_offset", [])
            if len(loaded) >= 4:
                values = [float(item) for item in loaded[:4]]
                if not all(math.isfinite(item) for item in values):
                    raise ValueError("G54文件包含非有限数值")
                self.G54_offset = values
                ts = data.get("timestamp", "未知时间")
                self.Append_Log(f"已加载 G54 偏置（{ts}）："
                                f"X={self.G54_offset[0]:.3f}  Y={self.G54_offset[1]:.3f}  Z={self.G54_offset[3]:.3f}")
                # 恢复三个按钮的蓝色
                if self.G54_offset[0] != 0:
                    self.ui.pushButton_33.setStyleSheet(
                        "background-color: #4a9eff; color: white; font-weight: bold;")
                if self.G54_offset[1] != 0:
                    self.ui.pushButton_34.setStyleSheet(
                        "background-color: #4a9eff; color: white; font-weight: bold;")
                if self.G54_offset[3] != 0:
                    self.ui.pushButton_35.setStyleSheet(
                        "background-color: #4a9eff; color: white; font-weight: bold;")
        except Exception as e:
            print(f"加载 G54 偏置失败: {e}")

    def G54_SetZero(self, ax):
        """G54 设零：记录当前机械坐标为 G54 偏置，按钮变蓝"""
        try:
            if self._soft_limit_recovery_active():
                raise SpindleSafetyError(
                    "软限位越界恢复期间禁止G54设零；请先回到正常软件范围")
            if ax not in ('X', 'Y', 'Z'):
                raise SpindleSafetyError(f"非法G54轴: {ax}")
            self._require_motion_interlocks(require_axes=True, require_idle=True)
            mpos = self.ZMC.Mpos_Det()  # 用机械坐标（Mpos），不是指令位置（Dpos）
            # Mpos_Det 返回 [X, Y, _, Z]，4 个元素，Z 在索引 3
            idx = {'X': 0, 'Y': 1, 'Z': 3}[ax]
            if len(mpos) < 4 or not math.isfinite(float(mpos[idx])):
                raise SpindleSafetyError("控制器返回的机械坐标无效")
            previous = self.G54_offset[idx]
            self.G54_offset[idx] = float(mpos[idx])
            try:
                self._save_g54()  # 保存到文件
            except Exception:
                self.G54_offset[idx] = previous
                raise
            # 按钮变蓝
            btn = {'X': self.ui.pushButton_33, 'Y': self.ui.pushButton_34, 'Z': self.ui.pushButton_35}[ax]
            btn.setStyleSheet("background-color: #4a9eff; color: white; font-weight: bold;")
            self.Append_Log(f"G54 设 {ax}=0：记录机械坐标 {mpos[idx]:.3f}mm")
            self.Update_MechAndWork_Coords()
            return True
        except Exception as e:
            self.Append_Log(f"G54 设零失败：{e}")
            return False

    def _g54_absolute_dpos_targets(self, axes, work_values):
        """Convert G54 work targets to application-coordinate DPOS targets."""
        if len(axes) != len(work_values):
            raise SpindleSafetyError("G代码轴列表与目标数量不一致")
        dpos = self.ZMC.Dpos_Det()
        mpos = self.ZMC.Mpos_Det()
        if len(dpos) < 4 or len(mpos) < 4:
            raise SpindleSafetyError("控制器DPOS/MPOS返回长度不足")
        index = {'X': 0, 'Y': 1, 'Z': 3}
        converted = []
        for ax, work_target in zip(axes, work_values):
            if ax not in index:
                raise SpindleSafetyError(f"不支持的运动轴: {ax}")
            idx = index[ax]
            values = (float(dpos[idx]), float(mpos[idx]),
                      float(self.G54_offset[idx]), float(work_target))
            if not all(math.isfinite(item) for item in values):
                raise SpindleSafetyError("G54换算遇到非有限坐标")
            # Preserve any existing DPOS-MPOS offset instead of assuming the
            # two controller coordinate systems are identical.
            desired_mpos = values[2] + values[3]
            converted.append(values[0] + desired_mpos - values[1])
        return converted

    def Update_MechAndWork_Coords(self):
        """更新机械坐标和工件坐标（3 位小数）

        lineEdit 实际位置（ui2.py geometry）：
          X行 y=460: lineEdit_20=机械, lineEdit_19=工件
          Y行 y=490: lineEdit_16=机械, lineEdit_21=工件
          Z行 y=520: lineEdit_17=机械, lineEdit_18=工件
        """
        try:
            mpos = self.ZMC.Mpos_Det()
            # mpos: [X, Y, _, Z]，Z 在索引 3
            mx, my, mz = mpos[0], mpos[1], mpos[3]
            # 工件坐标 = 机械 - G54 偏置
            wx = mx - self.G54_offset[0]
            wy = my - self.G54_offset[1]
            wz = mz - self.G54_offset[3]
            # 机械坐标（按 X/Y/Z 实际行号写入）
            self.ui.lineEdit_20.setText(f"{mx:.3f}")  # X机械
            self.ui.lineEdit_16.setText(f"{my:.3f}")  # Y机械
            self.ui.lineEdit_17.setText(f"{mz:.3f}")  # Z机械
            # 工件坐标
            self.ui.lineEdit_19.setText(f"{wx:.3f}")  # X工件
            self.ui.lineEdit_21.setText(f"{wy:.3f}")  # Y工件
            self.ui.lineEdit_18.setText(f"{wz:.3f}")  # Z工件
        except Exception:
            pass

    # ===== 调试模式 =====
    def Toggle_Debug_Mode(self):
        """F12 切换：进入/退出调试模式"""
        Iui = self.ui
        # 已连/已使能：禁止进入
        if Iui.Connect.text() == 'Disconnect':
            self.Append_Log("调试模式：已连接 ZMC，禁止进入")
            return
        if self._axes_enabled:
            self.Append_Log("调试模式：轴已使能，禁止进入")
            return
        if self._debug_mode:
            self._exit_debug()
        else:
            self._enter_debug()

    def _enter_debug(self):
        """进入只读调试：不解锁任何会向控制器发送命令的按钮。"""
        self._debug_mode = True
        Iui = self.ui
        # 状态灯换成蓝色（调试中）
        Iui.StatusLight.setStyleSheet(
            "background-color: #3399ff; border-radius: 10px; border: 1px solid #888;")
        Iui.StatusLabel.setText("调试中")
        self.Append_Log("=" * 40)
        self.Append_Log("进入只读调试模式：不会解锁运动、主轴、急停恢复等控制按钮")
        self.Append_Log("=" * 40)

    def _exit_debug(self):
        """退出调试：还原按钮初始 enable 状态 + 还原状态灯"""
        self._debug_mode = False
        self._zmc_inited = False
        Iui = self.ui
        Iui.InitZMC.setStyleSheet("")
        Iui.X_N_B.setEnabled(False)
        Iui.X_P_B.setEnabled(False)
        Iui.Y_N_B.setEnabled(False)
        Iui.Y_P_B.setEnabled(False)
        Iui.Z_N_B.setEnabled(False)
        Iui.Z_P_B.setEnabled(False)
        Iui.GoHome.setEnabled(False)
        Iui.Reset_CSys.setEnabled(False)
        Iui.GoZero.setEnabled(False)
        Iui.DZZ_Run_B.setEnabled(False)
        self.Update_StatusLight('disable')
        self.Append_Log("退出调试模式")

    def _debug_force_exit(self, reason):
        """InitZMC 或使能时强制退出调试"""
        if self._debug_mode:
            self.Append_Log(f"调试模式强制退出：{reason}")
            self._exit_debug()

    def _debug_log_click(self, btn_name, slot_name, *args):
        """所有调试期按钮的点击都走这里，打印到日志"""
        self.Append_Log(f"[调试] {btn_name} 被点击 → 触发 {slot_name}{args}")

    def MSleep(self, t):
        self.T1sigl = 1
        self.Timer1.start(t)

    def Timesigl(self):
        self.T1sigl = 0
        self.Timer1.stop()

    def bind(self):
        Iui = self.ui
        Iui.Search.clicked.connect(self.search_ip)
        Iui.Connect.clicked.connect(self.connect2ZMC)
        Iui.InitZMC.clicked.connect(self.Init_ZMC)
        Iui.DZZ_r_data.returnPressed.connect(self.Adj_Dzz_zs)
        Iui.DMove_dist.returnPressed.connect(self.Validate_M_dist)
        Iui.Move_Type.clicked.connect(self.Change_Move_Type)
        Iui.DZZ_Run_B.pressed.connect(self.Start_Dzz)
        # 使用 pressed 而非 clicked，避免 Qt 事件循环幽灵信号触发停止
        Iui.X_N_B.clicked.connect(lambda: self.M_N('X', source='panel'))
        Iui.X_P_B.clicked.connect(lambda: self.M_P('X', source='panel'))
        Iui.Y_N_B.clicked.connect(lambda: self.M_N('Y', source='panel'))
        Iui.Y_P_B.clicked.connect(lambda: self.M_P('Y', source='panel'))
        Iui.Z_N_B.clicked.connect(lambda: self.M_N('Z', source='panel'))
        Iui.Z_P_B.clicked.connect(lambda: self.M_P('Z', source='panel'))
        Iui.Ax_enable.clicked.connect(self.Ax_En)
        Iui.GoHome.clicked.connect(self.ZMC_gohome)
        Iui.Reset_CSys.clicked.connect(self.R_CSys)
        Iui.Gcode_run.clicked.connect(self.Run_Gcode)
        Iui.GoZero.clicked.connect(self.GoZero)
        # 新增
        Iui.EStop_Btn.clicked.connect(
            lambda checked=False: self.Emergency_Stop("UI_EStop"))
        Iui.Resume_Btn.clicked.connect(self.Resume_From_Stop)
        Iui.Log_export.clicked.connect(self.Export_Log)

    # ===== 日志辅助 =====
    def Append_Log(self, msg):
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            self.ui.Log_text.append(f"[{ts}] {msg}")
        except Exception:
            pass

    # ===== 显示更新 =====
    def F5_data(self, who):
        if who in ('XN', 'ALL'):
            self.ui.X_N_Limit.setText(str(round(self.X_data[0], 3)))
        if who in ('XC', 'ALL'):
            self.ui.X_Current.setText(str(round(self.X_data[1], 3)))
        if who in ('XP', 'ALL'):
            self.ui.X_P_Limit.setText(str(round(self.X_data[2], 3)))
        if who in ('YN', 'ALL'):
            self.ui.Y_N_Limit.setText(str(round(self.Y_data[0], 3)))
        if who in ('YC', 'ALL'):
            self.ui.Y_Current.setText(str(round(self.Y_data[1], 3)))
        if who in ('YP', 'ALL'):
            self.ui.Y_P_Limit.setText(str(round(self.Y_data[2], 3)))
        if who in ('ZN', 'ALL'):
            self.ui.Z_N_Limit.setText(str(round(self.Z_data[0], 3)))
        if who in ('ZC', 'ALL'):
            self.ui.Z_Current.setText(str(round(self.Z_data[1], 3)))
        if who in ('ZP', 'ALL'):
            self.ui.Z_P_Limit.setText(str(round(self.Z_data[2], 3)))
        if who in ('Dzs', 'ALL'):
            self.ui.Disp_Dzz_zs.setText(str(round(self.Dzz_zs, 3)))
        self._refresh_soft_limit_recovery_visuals()

    @staticmethod
    def _soft_limit_contains(limits, value):
        """Use the same 0.001 mm tolerance as the established motion gate."""
        return float(limits[0]) - 0.001 <= float(value) <= float(limits[2]) + 0.001

    def _axis_is_outside_soft_limit(self, axis, value=None):
        if axis not in ('X', 'Y', 'Z'):
            raise SpindleSafetyError(f"非法软限位轴: {axis!r}")
        data = {'X': self.X_data, 'Y': self.Y_data, 'Z': self.Z_data}[axis]
        if value is None:
            value = data[1]
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise SpindleSafetyError(f"{axis}当前坐标无效，不能判断软限位") from exc
        if not math.isfinite(value):
            raise SpindleSafetyError(f"{axis}当前坐标非有限，不能判断软限位")
        return not self._soft_limit_contains(data, value)

    def _soft_limit_recovery_active(self):
        """Whether AUTO/handwheel must stay locked until manual recovery ends."""
        if bool(getattr(self, '_soft_limit_recovery_axes', set())):
            return True
        try:
            return any(self._axis_is_outside_soft_limit(axis)
                       for axis in ('X', 'Y', 'Z'))
        except SpindleSafetyError:
            # An unreadable position must fail closed just like an outside one.
            return True

    def _refresh_soft_limit_recovery_visuals(self):
        """Render the existing current-coordinate cells; no UI layout changes."""
        state = getattr(self, '_soft_limit_recovery_axes', set())
        mapping = (
            ('X', self.X_data, self.ui.X_Current),
            ('Y', self.Y_data, self.ui.Y_Current),
            ('Z', self.Z_data, self.ui.Z_Current),
        )
        for axis, data, widget in mapping:
            outside = (axis in state) or not self._soft_limit_contains(data, data[1])
            widget.setStyleSheet(
                "background-color: #d12f2f; color: white; font-weight: bold;"
                if outside else "")

    def _record_position_update_for_soft_limit_recovery(self, axis):
        """Only a fresh DPOS update may clear a previously confirmed recovery."""
        if axis in self._soft_limit_recovery_axes:
            if not self._axis_is_outside_soft_limit(axis):
                self._soft_limit_recovery_axes.discard(axis)
                self._manual_speed_profile_dirty = True
                self._manual_speed_profile_last = None
                self.Append_Log(
                    f"[软限位恢复] {axis}已回到正常软件范围，恢复状态自动退出")
                self._refresh_motion_controls()
        self._refresh_soft_limit_recovery_visuals()

    def _clear_soft_limit_recovery_state(self, reason):
        """Recovery authorization is never allowed to survive a state boundary."""
        if not self._soft_limit_recovery_axes:
            return
        self._soft_limit_recovery_axes.clear()
        self._manual_speed_profile_dirty = True
        self._manual_speed_profile_last = None
        self.Append_Log(f"[软限位恢复] 已清除恢复授权：{reason}")
        self._refresh_soft_limit_recovery_visuals()

    def Update_StatusLight(self, state):
        """状态灯：使能=绿、未使能=黄、急停=红、TEST=蓝。"""
        colors = {
            'enable':  ('#2ea84f', '使能'),
            'disable': ('#FFD700', '未使能'),
            'estop':   ('#d12f2f', '急停'),
            'test':    ('#3399ff', 'TEST'),
        }
        color, text = colors.get(state, ('#FFD700', '未使能'))
        # 小圆点只显示颜色
        self.ui.StatusLight.setStyleSheet(
            f"background-color: {color}; border-radius: 10px; border: 1px solid #888;")
        # 旁边的文字标签
        self.ui.StatusLabel.setText(text)
        self.ui.StatusLabel.setStyleSheet(
            f"font: 8pt 'Microsoft YaHei'; color: {color};")

    def _refresh_spindle_controls(self):
        """Render controls from authoritative state; labels never drive logic."""
        state = getattr(self.ZMC, 'spindle_state', 'FAULT')
        confirmed = bool(getattr(self.ZMC, 'spindle_stop_confirmed', False))
        # This is an operator-status indicator, not a command input.  Keep it
        # truthful for every stop/fault path that refreshes spindle controls.
        self._set_spindle_running_indicator(state in ('STARTING', 'RUNNING'))
        labels = {
            'STOPPED': 'Start',
            # While acceleration is being reported asynchronously, STOP must
            # stay visible and clickable.  STOP always outranks START.
            'STARTING': 'Stop',
            'RUNNING': 'Stop',
            'STOPPING': 'Stopping...',
            'FAULT': 'FAULT' if confirmed else 'STOP?',
        }
        self.ui.DZZ_Run_B.setText(labels.get(state, 'STOP?'))
        normal_ready = (
            bool(getattr(self, '_zmc_inited', False))
            and bool(getattr(self.ZMC, 'is_connected', False))
            and not bool(getattr(self.ZMC, 'estop_latched', True))
            and state in ('STOPPED', 'STARTING', 'RUNNING')
            and (state != 'STOPPED' or confirmed)
        )
        self.ui.DZZ_Run_B.setEnabled(normal_ready)
        self.ui.EStop_Btn.setEnabled(bool(getattr(self.ZMC, 'is_connected', False)))
        self.ui.Resume_Btn.setEnabled(
            bool(getattr(self.ZMC, 'is_connected', False))
            and bool(getattr(self.ZMC, 'estop_latched', False))
            and confirmed)

    def _halt_manual_motion_ui(self):
        """Stop every GUI-side source that could enqueue another jog."""
        self._disarm_usb_handwheel('manual motion halted')
        self._cont_btn = None
        if hasattr(self, '_cont_timer'):
            self._cont_timer.stop()
        self._handwheel_input_source = None
        if getattr(self, '_mouse_mode', False):
            self._mouse_mode = False
            self.ui.pushButton_23.setStyleSheet("")

    def _refresh_motion_controls(self):
        """Render all axis controls from authoritative safety state."""
        connected = bool(getattr(self.ZMC, 'is_connected', False))
        initialized = bool(getattr(self, '_zmc_inited', False))
        latched = bool(getattr(self.ZMC, 'estop_latched', True))
        test_mode = self._is_test_mode()
        gcode_active = bool(getattr(self, '_gcode_running', False))
        recovery_active = self._soft_limit_recovery_active()
        axis_ready = (
            connected and initialized and self._axes_enabled
            and not latched and not test_mode)
        direct_jog_ready = (
            axis_ready and not getattr(self, '_mouse_mode', False)
            and not gcode_active)

        self.ui.Ax_enable.setText('使能' if self._axes_enabled else '未使能')
        self.ui.Ax_enable.setStyleSheet(
            "background-color: #2ea84f; color: white; font-weight: bold;"
            if self._axes_enabled else
            "background-color: #FFD700; color: #222; font-weight: bold;")
        # If axes are already enabled, the button must remain available for
        # de-energising them.  Otherwise a latch keeps enable unavailable.
        self.ui.Ax_enable.setEnabled(
            connected and initialized and not test_mode
            and not gcode_active and (self._axes_enabled or not latched))

        # Recovery keeps only the six screen direction buttons available.  It
        # deliberately locks coordinate reset, homing, and all automated paths
        # so a false DPOS window cannot silently turn into a new reference.
        for name in ('X_N_B', 'X_P_B', 'Y_N_B', 'Y_P_B', 'Z_N_B', 'Z_P_B'):
            getattr(self.ui, name).setEnabled(direct_jog_ready)
        for name in ('GoHome', 'GoZero', 'Reset_CSys'):
            getattr(self.ui, name).setEnabled(direct_jog_ready and not recovery_active)
        self.ui.pushButton_23.setEnabled(axis_ready and not recovery_active)
        # 手动速度只在 REAL 轴已使能且未运行 G 代码时可编辑；它不是
        # 程序 F 进给的编辑入口。
        self.ui.ManualSpeedBox.setEnabled(
            axis_ready and not gcode_active and not recovery_active)
        for name in ('pushButton_33', 'pushButton_34', 'pushButton_35'):
            getattr(self.ui, name).setEnabled(direct_jog_ready and not recovery_active)
        self.ui.pushButton_31.setEnabled(direct_jog_ready and not recovery_active)
        # TEST keeps its virtual G-code path; REAL machining needs enabled axes.
        self.ui.Gcode_run.setEnabled(
            connected and initialized and not latched
            and not getattr(self, '_mouse_mode', False)
            and not recovery_active
            and (test_mode or self._axes_enabled))

    def _require_motion_interlocks(self, require_axes=True, require_idle=True):
        """Fresh command-entry guard for every REAL jog/tool-setting action."""
        if self._is_test_mode():
            raise SpindleSafetyError("TEST模式禁止真实轴运动")
        if not bool(getattr(self.ZMC, 'is_connected', False)):
            raise SpindleSafetyError("控制器未连接")
        if not bool(getattr(self, '_zmc_inited', False)):
            raise SpindleSafetyError("控制器尚未InitZMC")
        if require_axes and not self._axes_enabled:
            raise SpindleSafetyError("运动轴尚未使能")
        if bool(getattr(self.ZMC, 'estop_latched', True)):
            raise SpindleSafetyError("急停/故障已锁存")
        self._check_safety_monitor_health()

        safe = self.ZMC.Safe_Det(trigger_stop=False)
        if _scalar_value(safe[6]) == 0 or _scalar_value(safe[7]) == 0:
            raise SpindleSafetyError("实体急停DI6/DI16仍处于按下状态")
        buttons = self.ZMC.Btn_Det()
        if len(buttons) > 1 and _scalar_value(buttons[1]) == 1:
            raise SpindleSafetyError("实体Stop按钮DI3仍处于按下状态")
        if require_idle:
            status = self.ZMC.Move_Det()
            if any(_scalar_value(item) != -1 for item in status):
                raise SpindleSafetyError("运动轴尚未停止，拒绝叠加下一条命令")
        return True

    def _current_command_positions(self):
        values = self.ZMC.Dpos_Det()
        if len(values) < 4:
            raise SpindleSafetyError("控制器坐标返回长度不足")
        result = {'X': float(values[0]), 'Y': float(values[1]), 'Z': float(values[3])}
        if not all(math.isfinite(item) for item in result.values()):
            raise SpindleSafetyError("控制器返回了非有限坐标")
        return result

    def _mark_manual_speed_profile_dirty(self):
        """输入框变更后只标记待应用，绝不在编辑时发送运动/速度命令。"""
        self._manual_speed_profile_dirty = True

    def _read_manual_axis_speeds(self):
        """读取界面 mm/min，转换成 ZMotion SetSpeed 使用的 mm/s。"""
        fields = {
            'X': self.ui.ManualSpeedX,
            'Y': self.ui.ManualSpeedY,
            'Z': self.ui.ManualSpeedZ,
        }
        display_values = {}
        controller_values = {}
        maximums = {'X': 600.0, 'Y': 600.0, 'Z': 300.0}
        for axis, field in fields.items():
            text = field.text().strip()
            if not text:
                raise SpindleSafetyError(f"{axis}手动速度为空")
            try:
                value = float(text)
            except ValueError:
                raise SpindleSafetyError(f"{axis}手动速度不是数字: {text!r}")
            if not math.isfinite(value):
                raise SpindleSafetyError(f"{axis}手动速度必须是有限数值")
            if not 0.06 <= value <= maximums[axis]:
                raise SpindleSafetyError(
                    f"{axis}手动速度必须在0.06～{maximums[axis]:g} mm/min")
            display_values[axis] = value
            controller_values[axis] = value / 60.0
        return display_values, controller_values

    def _apply_manual_speed_profile_if_needed(self):
        """在手动点动前恢复手动速度档；失败时绝不继续下发运动。"""
        display_values, controller_values = self._read_manual_axis_speeds()
        profile = (display_values['X'], display_values['Y'], display_values['Z'])
        if (not self._manual_speed_profile_dirty
                and profile == self._manual_speed_profile_last):
            return controller_values
        self.ZMC.Set_Manual_AxSpd(controller_values)
        self._manual_speed_profile_last = profile
        self._manual_speed_profile_dirty = False
        self.Append_Log(
            "[手动速度] 已确认并应用："
            f"X={display_values['X']:g}、Y={display_values['Y']:g}、"
            f"Z={display_values['Z']:g} mm/min")
        return controller_values

    def _apply_soft_limit_recovery_speed(self):
        """Write the fixed recovery profile immediately before its one move."""
        controller_value = SOFT_LIMIT_RECOVERY_SPEED_MM_MIN / 60.0
        profile = {'X': controller_value, 'Y': controller_value, 'Z': controller_value}
        self.ZMC.Set_Manual_AxSpd(profile)
        # The controller now contains the recovery profile.  The next normal
        # manual command must explicitly restore the operator's selected one.
        self._manual_speed_profile_dirty = True
        self._manual_speed_profile_last = None
        return profile

    def _confirm_soft_limit_recovery(self, axis, current, target, executed_value):
        direction = '+' if executed_value > 0 else '-'
        limits = {'X': self.X_data, 'Y': self.Y_data, 'Z': self.Z_data}[axis]
        text = (
            "当前指令将越过软件软限位。\n\n"
            f"{axis}{direction}：{current:.3f} → {target:.3f} mm\n"
            f"软件范围：[{float(limits[0]):.3f}, {float(limits[2]):.3f}] mm\n\n"
            "当前机械零点未经回零确认。确认后只执行本次受限恢复移动：\n"
            f"• 速度固定 {SOFT_LIMIT_RECOVERY_SPEED_MM_MIN:g} mm/min\n"
            f"• 单次距离 {abs(executed_value):g} mm（最大 "
            f"{SOFT_LIMIT_RECOVERY_MAX_STEP_MM:g} mm）\n"
            "• 主轴必须已确认停止\n"
            "• G代码、连续模式和两类手轮将锁定，直至坐标回到正常范围。\n\n"
            "确认执行本次恢复移动吗？")
        answer = QMessageBox.question(
            self, "软限位越界恢复", text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        return answer == QMessageBox.StandardButton.Yes

    def _enter_soft_limit_recovery_state(self, axis, current, target, executed_value):
        """Stop any GUI source before the bounded override becomes active."""
        self._halt_manual_motion_ui()
        if self._continuous_mode:
            self._continuous_mode = False
            self._set_continuous_style(False)
            self._bind_continuous()
        self._soft_limit_recovery_axes.add(axis)
        self._refresh_soft_limit_recovery_visuals()
        self._refresh_motion_controls()
        self.Append_Log(
            f"[软限位恢复] {axis}已进入恢复状态：{current:.3f} → "
            f"{target:.3f} mm，本次仅允许 {abs(executed_value):g} mm / "
            f"{SOFT_LIMIT_RECOVERY_SPEED_MM_MIN:g} mm/min")

    def _manual_move_checked(self, ax, signed_value, source='unclassified'):
        self._require_motion_interlocks(require_axes=True, require_idle=True)
        if ax not in ('X', 'Y', 'Z'):
            raise SpindleSafetyError(f"非法点动轴: {ax!r}")
        signed_value = float(signed_value)
        if not math.isfinite(signed_value) or signed_value == 0:
            raise SpindleSafetyError("点动距离必须是非零有限数值")
        current = self._current_command_positions()
        target = (current[ax] + signed_value if self.Move_Type == 0 else signed_value)
        limits = {'X': self.X_data, 'Y': self.Y_data, 'Z': self.Z_data}[ax]
        # The authorization is intentionally global while it is pending a
        # fresh UI DPOS signal.  The controller may already report an in-range
        # value here while the GUI has not yet consumed that signal; allowing
        # the normal profile in that gap would defeat the 100 mm/min / 1 mm
        # recovery restriction.
        recovery_needed = (self._soft_limit_recovery_active()
                           or not self._soft_limit_contains(limits, current[ax])
                           or not self._soft_limit_contains(limits, target))
        command_value = signed_value
        if recovery_needed:
            if self.Move_Type != 0:
                raise SpindleSafetyError("软限位越界恢复只允许相对点动")
            if source != 'panel':
                raise SpindleSafetyError(
                    "软限位越界恢复仅允许界面方向按钮；"
                    "G代码、连续模式和手轮输入已锁定")
            state = getattr(self.ZMC, 'spindle_state', 'FAULT')
            if state != 'STOPPED' or not bool(
                    getattr(self.ZMC, 'spindle_stop_confirmed', False)):
                raise SpindleSafetyError("软限位越界恢复要求主轴已确认停止")
            command_value = math.copysign(
                min(abs(signed_value), SOFT_LIMIT_RECOVERY_MAX_STEP_MM), signed_value)
            target = current[ax] + command_value
            if ax not in self._soft_limit_recovery_axes:
                if not self._confirm_soft_limit_recovery(
                        ax, current[ax], target, command_value):
                    raise SpindleSafetyError("操作者取消软限位越界恢复")
                # Profile write precedes authorization.  If the controller
                # rejects the 100 mm/min profile, no motion command and no
                # recovery authorization may survive the failure.
                self._apply_soft_limit_recovery_speed()
                self._enter_soft_limit_recovery_state(
                    ax, current[ax], target, command_value)
            else:
                # A recovery command never inherits the editable normal profile.
                self._apply_soft_limit_recovery_speed()
        else:
            # This is the established normal manual path.  It deliberately
            # remains byte-for-byte equivalent in behaviour after all checks.
            self._apply_manual_speed_profile_if_needed()
        if self.Move_Type == 0:
            self.ZMC.ZMC_1Move(ax, command_value)
        else:
            self.ZMC.ZMC_1MoveAbs(ax, target)
        self.ZMC.Move_status = True
        return current[ax], target

    def _set_safety_fault_ui(self, message):
        """故障锁存界面；未确认停机时绝不显示“已停止”。"""
        self._clear_soft_limit_recovery_state("急停/安全故障")
        self._cancel_gcode_dwell()
        if hasattr(self, 'Worker') and self.Worker is not None:
            self.Worker.GCode_St_all = False
        self.ZMC.Move_status = False
        self._clear_gcode_motion_tracking()
        self.ZMC.estop_latched = True
        self.ZMC.spindle_state = 'FAULT'
        self._axes_enabled = False
        self._gcode_running = False
        self._halt_manual_motion_ui()
        self.Update_StatusLight('estop')
        self.ui.Connect.setStyleSheet(
            "background-color: #d12f2f; color: white; font-weight: bold;")
        self._refresh_spindle_controls()
        self.ui.DZZ_Run_B.setEnabled(False)
        self.ui.Gcode_run.setText('run')
        self.ui.Gcode_run.setEnabled(False)
        for name in ('X_N_B', 'X_P_B', 'Y_N_B', 'Y_P_B',
                     'Z_N_B', 'Z_P_B', 'GoHome', 'GoZero'):
            getattr(self.ui, name).setEnabled(False)
        self._refresh_motion_controls()
        self.Append_Log(f"[安全故障] {message}")

    # ===== 信号处理（联动更新全部轴 + 状态灯） =====
    def Deal_in_signel(self, msg, Dlist):
        if msg == 'Btn':
            if Dlist == 0 and not self._axes_enabled:
                self.Ax_En()
            if Dlist == 1:
                # DZZ_MARK2 parity: the cabinet physical Stop button (DI3)
                # always enters the same latched spindle-and-axis stop path.
                # It must not depend on the axis button label or enable state.
                self.Emergency_Stop('Physical_Stop_Button_DI3')
            if Dlist == 2:
                self.Init_ZMC()
        elif msg == 'EStopHandled':
            # Safe_Det already executed the MARK2 spindle/axis emergency
            # sequence.  This signal updates the UI only, avoiding a duplicate
            # stop command sequence from the GUI thread.
            source = 'DI6' if int(Dlist) == 6 else 'DI16'
            detail = ("IO7/DA0停机读回已确认" if self.ZMC.spindle_stop_confirmed
                      else "停机尚未确认，保持急停锁存并由安全轮询限频重试")
            self._set_safety_fault_ui(f"实体急停 {source} 已触发：{detail}")
            self.Update_StatusLight('estop')
        elif msg == 'PhysicalStopHandled':
            detail = getattr(
                self.ZMC, 'last_gcode_fault', '') or "实体Stop已处理"
            self._set_safety_fault_ui(
                f"实体Stop按钮DI3已在安全轮询线程中处理：{detail}")
            self.Update_StatusLight('estop')
        elif msg == 'EStopReleased':
            source = 'DI6' if int(Dlist) == 6 else 'DI16'
            self.Append_Log(f"实体急停 {source} 已复位；锁存仍保留，必须人工点击恢复")
        elif msg == 'LimitInput':
            self.Append_Log(
                f"限位输入状态变化：索引 {int(Dlist)}"
                "（G代码运行期间将锁存中止）")
        elif msg == 'CommFault':
            self._set_safety_fault_ui(
                f"通信连续失败 {int(Dlist)} 次；急停已锁存，主轴停机状态需人工确认")
        elif msg == 'MonitorReady':
            if bool(getattr(self.ZMC, 'is_connected', False)):
                self.ui.InitZMC.setEnabled(True)
                self._refresh_spindle_controls()
                self.Append_Log("安全轮询首帧已确认，InitZMC 已解锁")
        elif msg == 'GCodeFault':
            detail = getattr(
                self.ZMC, 'last_gcode_fault', '') or "原因未记录"
            self._set_safety_fault_ui(
                f"G代码工作线程安全中止：{detail}；"
                "急停已锁存，主轴/轴状态需人工确认")
        elif msg == 'Dpos0':
            self.X_data[1] = Dlist
            self.F5_data('ALL')  # 联动更新 X/Y/Z
            self._record_position_update_for_soft_limit_recovery('X')
            self.Update_MechAndWork_Coords()
        elif msg == 'Dpos1':
            self.Y_data[1] = Dlist
            self.F5_data('ALL')
            self._record_position_update_for_soft_limit_recovery('Y')
            self.Update_MechAndWork_Coords()
        elif msg == 'Dpos2':
            # 轴2 未使用，忽略（与 main3 一致）
            pass
        elif msg == 'Dpos3':
            self.Z_data[1] = Dlist
            self.F5_data('ALL')
            self._record_position_update_for_soft_limit_recovery('Z')
            self.Update_MechAndWork_Coords()
        elif msg == 'M_St':
            if Dlist == 0:
                pass
        elif msg == 'NG':
            if Dlist == 0:
                self.Run_Next_Gcode()
        elif msg == 'Dzs':
            self.Dzz_zs = Dlist
            self.F5_data('Dzs')

    # ===== 控制方法（与 main3.py 一致） =====
    def search_ip(self):
        try:
            ips = self.ZMC.search()  # self 当 ZMC 用
            self.ui.iplist.clear()
            for ip in ips:
                self.ui.iplist.addItem(ip)
            if ips:
                self.ui.Connect.setEnabled(True)
                target = "虚拟主轴服务" if self._is_test_mode() else "控制器"
                self.Append_Log(f"找到 {len(ips)} 个{target}地址，请选择后点 Connect 连接")
            else:
                self.Append_Log("未搜索到控制器，请检查网线/电源")
        except Exception as e:
            import traceback
            err_msg = f"搜索异常: {type(e).__name__}: {e}"
            print(err_msg)
            traceback.print_exc()
            self.Append_Log(err_msg)

    def connect2ZMC(self):
        print("=== connect2ZMC 被调用了 ===")
        Iui = self.ui
        print(f"Connect.text={repr(Iui.Connect.text())} Connect.enabled={Iui.Connect.isEnabled()}")
        try:
            if Iui.Connect.text() == 'Connect':
                ip = Iui.iplist.currentText()
                if ip == '':
                    print('Connect error!!!')
                    self.Append_Log("连接失败：未选择 IP")
                else:
                    ret = self.ZMC.connect( ip)
                    if ret != 0:
                        self.Append_Log(f"连接 {ip} 失败")
                        return
                    # Every new controller session requires one fresh operator
                    # acknowledgement before the first valid REAL G-code run.
                    self._gcode_first_run_confirmed = False
                    self._clear_soft_limit_recovery_state("新建控制器连接")
                    # 控制器可能保留上一会话的输出；连接后第一动作必须是安全归零。
                    try:
                        self.ZMC.stop_spindle("Connect_Safe_Default")
                        self.ZMC.Rapidstop()
                        self.ZMC.Enable_Axs(0)
                        self._axes_enabled = False
                    except Exception as e:
                        self.ZMC.estop_latched = True
                        Iui.Connect.setText('Disconnect')
                        self.SpindleBackendMode.setEnabled(False)
                        Iui.InitZMC.setEnabled(False)
                        Iui.EStop_Btn.setEnabled(True)
                        Iui.Resume_Btn.setEnabled(True)
                        self._set_safety_fault_ui(
                            f"已建立连接，但初始安全归零失败：{e}")
                        return
                    Iui.Connect.setText('Disconnect')
                    self.SpindleBackendMode.setEnabled(False)
                    # Init remains disabled until Worker confirms its first
                    # complete safety/status polling frame.
                    Iui.InitZMC.setEnabled(False)
                    Iui.EStop_Btn.setEnabled(True)
                    Iui.Resume_Btn.setEnabled(True)
                    # 改动1：connect 变绿色
                    Iui.Connect.setStyleSheet("background-color: #2ea84f; color: white; font-weight: bold;")
                    # 显示三色灯
                    Iui.StatusLight.setVisible(True)
                    # 强制退出调试模式
                    self._debug_force_exit("已连接 ZMC")
                    if not self._is_test_mode():
                        self._save_ip_history(ip)
                        self._load_ip_history()
                    else:
                        self._save_test_host(ip)
                    # 如果已有旧 Worker，先停掉
                    if hasattr(self, 'Timer1') and self.Timer1 is not None:
                        try: self.Timer1.stop()
                        except: pass
                    self.Timer1 = QTimer(self)
                    self.Timer1.timeout.connect(self.Timesigl)
                    if hasattr(self, 'Worker') and self.Worker is not None and self.Worker.isRunning():
                        self.Worker.Wstatus = False
                        try: self.Worker.wait(1000)
                        except: pass
                    self.Worker = Worker(self.ZMC)
                    self.Worker.updateSignal.connect(self.Deal_in_signel)
                    self.Worker.start()
                    self._refresh_motion_controls()
                    self._refresh_spindle_controls()
                    # 强制读取一次位置，刷新显示（main3 靠位置变化触发，但静止时不触发）
                    try:
                        dp = self.ZMC.Dpos_Det()
                        self.X_data[1] = dp[0]
                        self.Y_data[1] = dp[1]
                        self.Z_data[1] = dp[3]
                        self.F5_data('ALL')
                        self.Update_MechAndWork_Coords()
                    except Exception:
                        pass
                    self.Append_Log(f"已连接 {ip}")
                    if self._is_test_mode():
                        self.Append_Log("TEST 后端已连接：所有真实运动轴输出保持锁定")
                    if not self.ZMC.safety_inputs_verified:
                        self.Append_Log("安全输入尚未成功读取：主轴启动保持禁止")
                    if self.ZMC.estop_latched:
                        self._set_safety_fault_ui("连接后检测到急停/停机故障锁存")
                    if self._is_test_mode():
                        self.Append_Log("提示：请按 InitZMC 解锁虚拟主轴测试")
                    else:
                        self.Append_Log("提示：请按 InitZMC 配置轴参数，然后按「使能」开启轴电源")
            elif Iui.Connect.text() == 'Disconnect':
                # MARK2 parity: request and confirm the spindle stop before
                # waiting for polling-thread shutdown.  Disconnect must never
                # delay STOP behind a potentially blocked Worker.
                self._cancel_gcode_dwell()
                self.ZMC.stop_spindle("UI_Disconnect")
                self._halt_manual_motion_ui()
                self._clear_soft_limit_recovery_state("控制器断开")
                self._gcode_running = False
                self._gcode_first_run_confirmed = False
                if hasattr(self, 'Worker') and self.Worker is not None:
                    self.Worker.GCode_St_all = False
                self.ZMC.Rapidstop()
                self.ZMC.Enable_Axs(0)
                self._axes_enabled = False
                if hasattr(self, 'Worker') and self.Worker is not None:
                    self.Worker.Wstatus = False
                    if self.Worker.isRunning() and not self.Worker.wait(2000):
                        raise SpindleSafetyError("安全轮询线程未能在2秒内退出，拒绝断开")
                self.ZMC.Disconnect()
                Iui.Connect.setText('Connect')
                self.SpindleBackendMode.setEnabled(True)
                # 改动1：connect 恢复默认色
                Iui.Connect.setStyleSheet("")
                # 隐藏三色灯
                Iui.StatusLight.setVisible(False)
                # 禁用所有运动相关按键
                self._zmc_inited = False
                Iui.InitZMC.setStyleSheet("")
                Bt_State = False
                for _n in ('InitZMC', 'X_N_B', 'Y_N_B', 'Z_N_B', 'X_P_B', 'Y_P_B',
                           'Z_P_B', 'DZZ_Run_B', 'GoHome', 'Reset_CSys', 'Gcode_run',
                           'GoZero', 'EStop_Btn', 'Resume_Btn', 'pushButton_23'):
                    getattr(Iui, _n).setEnabled(Bt_State)
                # 模式切换还原
                self.ui.pushButton_22.setText("手轮\n模式")
                self.ui.pushButton_22.setStyleSheet("")
                self.ui.checkBox_7.setChecked(True)
                self.ui.checkBox_8.setChecked(False)
                # G54 按钮恢复
                for b in (self.ui.pushButton_33, self.ui.pushButton_34, self.ui.pushButton_35):
                    b.setStyleSheet("")
                # 不断开 G54 偏置（持久化到文件）
                self.Exit_Mouse_Mode()
                self._refresh_motion_controls()
                self.Append_Log("已断开连接")
                self.Append_Log("提示：重新连接后请重做 InitZMC 和使能")
        except Exception as e:
            import traceback
            err_msg = f"连接异常: {type(e).__name__}: {e}"
            print(err_msg)
            traceback.print_exc()
            self.Append_Log(err_msg)
            if Iui.Connect.text() == 'Disconnect':
                self._set_safety_fault_ui(
                    f"断开失败，连接保持：{e}。不要切断软件连接来替代实体急停/STO")

    def _disable_controls_after_init(self):
        """复位 InitZMC 时禁用所有控制按钮"""
        Iui = self.ui
        self._axes_enabled = False
        self._halt_manual_motion_ui()
        for _n in ('X_N_B', 'X_P_B', 'Y_N_B', 'Y_P_B',
                   'Z_N_B', 'Z_P_B', 'DZZ_Run_B', 'GoHome', 'Reset_CSys',
                   'Gcode_run', 'GoZero', 'Ax_enable'):
            w = getattr(Iui, _n, None)
            if w is not None:
                w.setEnabled(False)

    def Init_ZMC(self):
        Iui = self.ui
        # 如果已经 Init 过，再次点击则复位
        if getattr(self, '_zmc_inited', False):
            try:
                if not self._stop_gcode_safely("InitZMC复位", rapidstop=True):
                    return
                self._halt_manual_motion_ui()
                self.ZMC.Enable_Axs(0)
                self._axes_enabled = False
                self._clear_soft_limit_recovery_state("InitZMC复位")
            except Exception as e:
                self.ZMC.estop_latched = True
                self._set_safety_fault_ui(f"InitZMC复位失败，控制状态保持：{e}")
                return
            self._zmc_inited = False
            Iui.InitZMC.setStyleSheet("")
            Bt_State = False
            # 禁用所有控制按钮（回到未 Init 状态）
            self._disable_controls_after_init()
            Iui.InitZMC.setEnabled(True)  # 自身保持可点击
            self._refresh_motion_controls()
            self.Append_Log("InitZMC 已复位")
            return

        # 首次 Init：任何 DLL 错误都不得解锁控制按钮。
        try:
            self.ZMC.Init_ZMC404()
            self.ZMC.Safe_Det()
            self._check_safety_monitor_health()
            self.Append_Log("InitZMC 轴参数配置成功")
        except Exception as e:
            self._zmc_inited = False
            self._axes_enabled = False
            self._disable_controls_after_init()
            Iui.InitZMC.setEnabled(True)
            self._set_safety_fault_ui(f"InitZMC 失败，控制按钮保持锁定：{e}")
            return
        if self.ZMC.estop_latched:
            self._zmc_inited = True
            self._disable_controls_after_init()
            Iui.InitZMC.setEnabled(True)
            Iui.Resume_Btn.setEnabled(True)
            self._set_safety_fault_ui("初始化完成，但急停已锁存；必须确认停机并执行恢复")
            return
        self._zmc_inited = True
        self._axes_enabled = False
        # Init_ZMC404 使用固定初始速度；下一次手动点动必须重新应用
        # 面板中的手动速度档，而不能假定它仍与默认值相同。
        self._manual_speed_profile_dirty = True
        # 按钮变绿
        Iui.InitZMC.setStyleSheet("background-color: #2ea84f; color: white; font-weight: bold;")
        Iui.DZZ_Run_B.setEnabled(True)
        if self._is_test_mode():
            self._apply_test_mode_control_policy()
            self.Update_StatusLight('test')
            self.Append_Log("TEST 初始化完成：主轴与G代码仿真已解锁，真实运动轴保持禁用")
        else:
            self.Append_Log("InitZMC 完成 ✅ 轴参数已配置，可以进行使能操作")
            self.Append_Log("警告：控制器侧硬件看门狗未安装；实体测试必须使用独立 STO/接触器")
        self._refresh_motion_controls()
        self._refresh_spindle_controls()

    def Ax_En(self):
        if self._is_test_mode():
            self.Append_Log("TEST模式禁止真实轴使能")
            return False
        try:
            if not self._axes_enabled:
                self._require_motion_interlocks(require_axes=False, require_idle=True)
                self.ZMC.Enable_Axs(1)
                self._axes_enabled = True
                self.Update_StatusLight('enable')
                self.Append_Log("运动轴使能命令已接受")
            else:
                self._halt_manual_motion_ui()
                self.ZMC.Rapidstop()
                self.ZMC.Enable_Axs(0)
                self._axes_enabled = False
                self._clear_soft_limit_recovery_state("轴下使能")
                self.Update_StatusLight('disable')
                self.Append_Log("运动轴已急停运动并下使能")
            self._refresh_motion_controls()
            return True
        except Exception as e:
            self._axes_enabled = False
            try:
                self.ZMC.Enable_Axs(0)
            except Exception:
                pass
            self._set_safety_fault_ui(f"轴使能状态变更失败：{e}")
            return False

    def Change_Move_Type(self):
        if getattr(self, '_mouse_mode', False):
            self.Append_Log("运动模式切换拒绝：请先退出手轮模式")
            return False
        if self._soft_limit_recovery_active():
            self.Append_Log("运动模式切换拒绝：软限位越界恢复期间固定为相对单步点动")
            return False
        if getattr(self, '_gcode_running', False):
            self.Append_Log("运动模式切换拒绝：G代码正在执行")
            return False
        # 切换模式本身不下发运动；只在轴已使能时要求当前空闲。
        # 这保留离线/未使能时的界面预选能力，真正点动仍会通过
        # _require_motion_interlocks 做完整命令入口校验。
        if bool(getattr(self.ZMC, 'is_connected', False)) and self._axes_enabled:
            try:
                self._require_motion_interlocks(require_axes=True, require_idle=True)
            except Exception as e:
                self.Append_Log(f"运动模式切换拒绝：{e}")
                return False
        if self.Move_Type == 0:
            self.ui.Move_Type.setText('绝对')
            self.ui.Move_Type.setStyleSheet(
                "background-color: #d12f2f; color: white; font-weight: bold;")
            self.Move_Type = 1
            self.Append_Log("[警告] 已切换到绝对模式，注意限位！")
            return True
        else:
            self.ui.Move_Type.setText('相对')
            self.ui.Move_Type.setStyleSheet(
                "background-color: #218838; color: white; font-weight: bold;")
            self.Move_Type = 0
            self.Append_Log("已切回相对模式")
            return True

    def Get_M_dist(self):
        text_value = self.ui.DMove_dist.text().strip()
        if not text_value:
            raise SpindleSafetyError("点动距离为空")
        value = float(text_value)
        if not math.isfinite(value) or value < 0.001 or value > 100:
            raise SpindleSafetyError("点动距离必须为0.001~100mm的有限数值")
        self.M_dist_data = value
        return value

    def Validate_M_dist(self):
        """UI-only distance validation; never leak an exception from a Qt slot."""
        try:
            value = self.Get_M_dist()
            self.ui.lineEdit_2.setText(str(value))
            self.Append_Log(f"点动距离已设为 {value:g} mm")
            return True
        except Exception as e:
            self.Append_Log(f"[输入拒绝] {e}")
            return False

    def M_N(self, ax, source='unclassified'):
        try:
            dist = -self.Get_M_dist()
            current, target = self._manual_move_checked(ax, dist, source=source)
            self.Append_Log(
                f"[点动] {ax}-: {current:.3f} → {target:.3f} mm")
            return True
        except Exception as e:
            self.Append_Log(f"[安全拦截] {ax}- 点动拒绝：{e}")
            return False

    def M_P(self, ax, source='unclassified'):
        try:
            dist = self.Get_M_dist()
            current, target = self._manual_move_checked(ax, dist, source=source)
            self.Append_Log(
                f"[点动] {ax}+: {current:.3f} → {target:.3f} mm")
            return True
        except Exception as e:
            self.Append_Log(f"[安全拦截] {ax}+ 点动拒绝：{e}")
            return False

    def Adj_Dzz_zs(self):
        try:
            raw = self.ui.DZZ_r_data.text().strip()
            value = self.ZMC._normalize_spindle_rpm(
                float(raw) if raw else 0, allow_zero=False)
            if (getattr(self.ZMC, 'spindle_state', 'FAULT') != 'STOPPED'
                    or not bool(getattr(self.ZMC, 'spindle_stop_confirmed', False))):
                raise SpindleSafetyError("仅在主轴已确认停止时允许修改转速设定")
            self.ui.DZZ_r_data.setText(f"{value:g}")
            self.ui.DZZ_r_data.deselect()
            # REAL MARK2 stages the value without writing DA0.  The TEST
            # backend sends SET_SPEED inside its own atomic Start sequence,
            # after consuming the previous confirmed STOP request.
            if not self._is_test_mode():
                self.ZMC.Set_Dzz_zs(value)
            return value
        except Exception as e:
            self.Append_Log(f"转速设置被拒绝：{e}")
            return None

    def Start_Dzz(self):
        # LinuxCNC-style interaction: the controller state is authoritative;
        # button text is a rendered status and never selects the command path.
        state = getattr(self.ZMC, 'spindle_state', 'FAULT')
        if state == 'STOPPED':
            if self._soft_limit_recovery_active():
                self.Append_Log(
                    "主轴启动拒绝：软限位越界恢复期间必须保持主轴已确认停止")
                return
            if self.ZMC.estop_latched or not self.ZMC.spindle_stop_confirmed:
                self._set_safety_fault_ui(
                    "主轴未处于已确认零输出状态，拒绝启动")
                return
            rpm = self.Adj_Dzz_zs()
            if rpm is None:
                return
            if not self._confirm_spindle_start():
                self.Append_Log("主轴启动已取消：未确认磁悬浮/水冷状态")
                return
            try:
                self.ZMC.start_spindle(rpm, "UI_Start")
            except Exception as e:
                self._set_safety_fault_ui(f"主轴启动失败并已锁存：{e}")
                return
            self._refresh_spindle_controls()
            self.Append_Log(f"主轴启动命令及输出读回已确认，设定 {rpm:g}rpm")
        elif state in ('STARTING', 'RUNNING', 'STOPPING'):
            # 停止永远立即执行，绝不受启动防抖或时间窗口影响。
            try:
                self.ZMC.stop_spindle("UI_Stop")
            except Exception as e:
                self._set_safety_fault_ui(f"主轴停机未确认：{e}")
                return
            self._refresh_spindle_controls()
            self.Append_Log("主轴停止命令及 IO7/DA0 读回均已确认")
        else:
            self._set_safety_fault_ui(
                f"当前主轴状态 {state} 不允许启动；请先排除故障并恢复")

    def ZMC_gohome(self):
        if self._soft_limit_recovery_active():
            self.Append_Log("回原点拒绝：软限位越界恢复期间仅允许界面单步点动")
            return False
        # GoHome 内部会恢复控制器默认速度；完成后仍以手动面板为准。
        self._manual_speed_profile_dirty = True
        self.ZMC.GoHome()
        timeout = 0
        while timeout < 300:  # 最长等 6s（每 20ms 轮询 × 300）
            A = self.ZMC.Move_Det()
            if A[0] == -1 and A[1] == -1 and A[2] == -1:
                break  # 所有轴都 idle，回零完成
            Tsleep(0.02)
            timeout += 1
        if timeout >= 300:
            self.Append_Log("警告：回零超时，轴可能未完全回零")
        self.X_data = [-43, 0, 262]
        self.Y_data = [-33, 0, 250]
        self.Z_data = [-260, 0, 22]
        self.F5_data('ALL')
        self.Append_Log("回零完成：各轴已回到机械原点，限位值已重置")
        self.Append_Log("提示：回零后建议按 Reset_CSys 重置坐标系")

    def R_CSys(self):
        """重设坐标系：用 QTimer 分步轮询轴状态，避免阻塞主线程"""
        if self._soft_limit_recovery_active():
            self.Append_Log("重设坐标系拒绝：软限位越界恢复期间禁止改变坐标基准")
            return False
        if hasattr(self, '_rcsys_timer'):
            return  # 已经在执行中
        self._rcsys_retry = 0
        self._rcsys_timer = QTimer(self)
        self._rcsys_timer.setInterval(20)
        def _rcsys_poll():
            self._rcsys_retry += 1
            A = self.ZMC.Move_Det()
            if A[0] == -1 and A[1] == -1 and A[2] == -1:
                # 所有轴已停止，执行 R_CSys
                self._rcsys_timer.stop()
                del self._rcsys_timer
                del self._rcsys_retry
                self.ZMC.R_CSys()
                self.X_data = [i - self.X_data[1] for i in self.X_data]
                self.Y_data = [i - self.Y_data[1] for i in self.Y_data]
                self.Z_data = [i - self.Z_data[1] for i in self.Z_data]
                self.F5_data('ALL')
                self.Append_Log("坐标系已重置：当前各轴位置记为 0")
                self.Append_Log("提示：坐标限位已随零点平移，仍有效")
            elif self._rcsys_retry > 250:  # ~5s 超时
                self._rcsys_timer.stop()
                del self._rcsys_timer
                del self._rcsys_retry
                self.Append_Log("警告：重设坐标系超时，轴可能未停止")
        self._rcsys_timer.timeout.connect(_rcsys_poll)
        self._rcsys_timer.start()
        self.Append_Log("等待轴停止后重设坐标系...")

    def GoZero(self):
        if self._soft_limit_recovery_active():
            self.Append_Log("回零点拒绝：软限位越界恢复期间仅允许界面单步点动")
            return False
        # main3.py 里 ZMC.GoZero 用法，直接调用
        self.ZMC.GoZero()
        self.Append_Log("回零点：各轴正在回到 0 位置")

    def _cancel_gcode_dwell(self):
        """取消未完成的 G04；停止/急停/故障后绝不允许定时器继续推进。"""
        timer = getattr(self, '_gcode_dwell_timer', None)
        if timer is not None and timer.isActive():
            timer.stop()
        self.ZMC.G04_dwell_ms = 0
        self.ZMC.T1sigl = 0

    def _clear_gcode_motion_tracking(self):
        self.ZMC.GCode_expected_dpos = None
        self.ZMC.GCode_motion_deadline = 0.0
        self.ZMC.GCode_motion_description = ""

    def _arm_gcode_motion_tracking(self, current, expected, rapid, line_num):
        distances = [abs(float(expected[axis]) - float(current[axis]))
                     for axis in expected]
        path_length = math.sqrt(sum(value * value for value in distances))
        if rapid:
            rapid_mm_s = (float(self.ZMC.AxSpd_G0_Z_D)
                          if 'Z' in expected
                          else float(self.ZMC.AxSpd_G0_D))
            feed_mm_min = rapid_mm_s * 60.0
        else:
            feed_mm_min = self.ZMC.current_gcode_feed_mm_min
            if feed_mm_min is None or feed_mm_min <= 0:
                raise SpindleSafetyError(
                    "G1执行前尚未建立有效F进给，拒绝运动")
        nominal_seconds = path_length / (feed_mm_min / 60.0)
        timeout_seconds = max(
            GCODE_MOTION_MIN_TIMEOUT_S,
            nominal_seconds * GCODE_MOTION_TIMEOUT_FACTOR
            + GCODE_MOTION_TIMEOUT_MARGIN_S)
        self.ZMC.GCode_expected_dpos = dict(expected)
        self.ZMC.GCode_motion_deadline = time.monotonic() + timeout_seconds
        self.ZMC.GCode_motion_description = (
            f"line {line_num}，预计{nominal_seconds:.2f}s，"
            f"超时{timeout_seconds:.2f}s")

    def _start_gcode_dwell(self, dwell_ms):
        """开始非阻塞 G04，返回是否实际进入了暂停。"""
        self._cancel_gcode_dwell()
        dwell_ms = max(0, int(dwell_ms))
        if dwell_ms <= 0:
            return False
        self.ZMC.G04_dwell_ms = dwell_ms
        self.ZMC.T1sigl = 1
        self._gcode_dwell_timer.start(dwell_ms)
        return True

    def _finish_gcode_dwell(self):
        """G04 到时只解除暂停；下一条代码仍由 Worker 的安全轮询推进。"""
        self.ZMC.G04_dwell_ms = 0
        self.ZMC.T1sigl = 0

    def _stop_gcode_safely(self, reason, rapidstop=True):
        """所有 G 代码退出路径都必须经过这里并确认主轴输出为零。"""
        self._cancel_gcode_dwell()
        self._clear_gcode_motion_tracking()
        if hasattr(self, 'Worker') and self.Worker is not None:
            self.Worker.GCode_St_all = False
        self._gcode_running = False
        self.ZMC.Move_status = False
        errors = []
        rapidstop_failed = False
        if rapidstop:
            try:
                self.ZMC.Rapidstop()
            except Exception as e:
                rapidstop_failed = True
                errors.append(f"Rapidstop失败: {e}")
        if rapidstop_failed:
            try:
                self.ZMC.Enable_Axs(0)
                self._axes_enabled = False
                self.Append_Log("Rapidstop失败后已执行备用轴下使能")
            except Exception as e:
                errors.append(f"备用轴下使能失败: {e}")
        try:
            self.ZMC.stop_spindle(f"GCode:{reason}")
        except Exception as e:
            errors.append(f"主轴停机未确认: {e}")
        self.ui.Gcode_run.setText('run')
        if errors:
            self.ZMC.estop_latched = True
            self._set_safety_fault_ui(f"{reason}；{'；'.join(errors)}")
            return False
        self._refresh_spindle_controls()
        self._refresh_motion_controls()
        self.Append_Log(f"{reason}：主轴停止已确认")
        return True

    def Highlight_ZD_line(self, num):
        document = self.ui.GCode_text.document()
        block = document.findBlockByLineNumber(num)
        if not block.isValid():
            self._stop_gcode_safely("G代码自然结束", rapidstop=False)
            return
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock,
                            QTextCursor.MoveMode.KeepAnchor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setBackground(QColor('red'))
        self.ui.GCode_text.setExtraSelections([selection])

    def Highlight_line(self):
        try:
            self.H_L += 1
        except Exception:
            self.H_L = 0
        self.Highlight_ZD_line(self.H_L)

    def _tokenize_gcode(self, line):
        """将 G01X10Y20 拆成 ['G01','X10','Y20']，同时兼容 G01 X10 Y20（空格分隔）"""
        tokens = []
        parts = line.split()
        for part in parts:
            if not part:
                continue
            buf = part[0]
            for ch in part[1:]:
                if ch.isalpha():  # 遇到字母切分新 token
                    if buf:
                        tokens.append(buf)
                    buf = ch
                else:
                    buf += ch
            if buf:
                tokens.append(buf)
        return tokens

    def deal_Gcode(self):
        self.Mat_Gcode = []
        text = self.ui.GCode_text.toPlainText().upper()
        Gcode_errmsg = ''
        modal_motion = None
        modal_feed_mode = 0  # 0=G94 mm/min, 1=G95 mm/rev
        modal_feed_mm_min = None
        modal_spindle_rpm = None
        if text == '':
            Gcode_errmsg = '空'
        else:
            lines = text.splitlines()
            index_L = 0
            for line in lines:
                index_L += 1
                if Gcode_errmsg != '':
                    break
                cmd_code = []
                cmd_value = []
                # 兼容全角分号
                line = line.replace('；', ';')
                # 去掉注释
                if ';' in line:
                    line = line.split(';')[0]
                line = line.strip()
                if not line:
                    continue  # 空行跳过
                cmds = self._tokenize_gcode(line)
                _line_ok = True
                seen_singletons = set()
                line_g_values = []
                line_m_values = []
                for cc in cmds:
                    if not cc:
                        continue
                    letter = cc[0]
                    if letter not in {'G', 'M', 'X', 'Y', 'Z', 'I', 'J', 'K',
                                      'F', 'S', 'P', 'N'}:
                        Gcode_errmsg = f'第 {index_L} 行不支持指令字母 "{letter}"'
                        _line_ok = False
                        break
                    if len(cc) == 1:
                        Gcode_errmsg = f'第 {index_L} 行语法错误："{cc}" 后缺少数值'
                        _line_ok = False
                        break
                    try:
                        parsed_value = float(cc[1:])
                    except ValueError:
                        self.Mat_Gcode = []
                        Gcode_errmsg = (f'第 {index_L} 行语法错误：无法解析 "{cc}"'
                                        f'，{cc[0]} 后缺少数值')
                        _line_ok = False
                        break
                    if not math.isfinite(parsed_value):
                        Gcode_errmsg = f'第 {index_L} 行包含非有限数值 "{cc}"'
                        _line_ok = False
                        break
                    if letter == 'N':
                        continue
                    if letter in {'X', 'Y', 'Z', 'I', 'J', 'K', 'F', 'S', 'P', 'M'}:
                        if letter in seen_singletons:
                            Gcode_errmsg = f'第 {index_L} 行重复参数 "{letter}"'
                            _line_ok = False
                            break
                        seen_singletons.add(letter)
                    if letter == 'G':
                        if parsed_value not in (0, 1, 2, 3, 4, 21, 54, 90, 91, 94, 95):
                            Gcode_errmsg = f'第 {index_L} 行不支持 G{parsed_value:g}'
                            _line_ok = False
                            break
                        line_g_values.append(parsed_value)
                    elif letter == 'M':
                        if parsed_value not in (3, 4, 5):
                            Gcode_errmsg = f'第 {index_L} 行不支持 M{parsed_value:g}'
                            _line_ok = False
                            break
                        line_m_values.append(parsed_value)
                    cmd_code.append(letter)
                    cmd_value.append(parsed_value)
                if _line_ok:
                    motion_words = [g for g in line_g_values if g in (0, 1, 2, 3)]
                    if len(motion_words) > 1:
                        Gcode_errmsg = f'第 {index_L} 行同时包含多个运动G指令'
                        _line_ok = False
                    elif motion_words:
                        modal_motion = motion_words[-1]
                    distance_modes = [g for g in line_g_values if g in (90, 91)]
                    feed_modes = [g for g in line_g_values if g in (94, 95)]
                    if len(distance_modes) > 1 or len(feed_modes) > 1:
                        Gcode_errmsg = f'第 {index_L} 行包含互相冲突的模态G指令'
                        _line_ok = False
                    if 4 in line_g_values and motion_words:
                        Gcode_errmsg = f'第 {index_L} 行不允许G04与G0/G1混写'
                        _line_ok = False
                    if feed_modes:
                        new_feed_mode = 0 if feed_modes[-1] == 94 else 1
                        if new_feed_mode != modal_feed_mode:
                            modal_feed_mm_min = None
                        modal_feed_mode = new_feed_mode
                    if any(g in (2, 3) for g in motion_words):
                        Gcode_errmsg = (
                            f'第 {index_L} 行为圆弧指令；本轮G54安全验收仅开放G0/G1，'
                            '拒绝未校验的G2/G3')
                        _line_ok = False
                    if any(c in cmd_code for c in ('I', 'J', 'K')):
                        Gcode_errmsg = (
                            f'第 {index_L} 行包含I/J/K；G2/G3尚未开放，拒绝忽略圆弧参数')
                        _line_ok = False
                    if 'P' in cmd_code and 4 not in line_g_values:
                        Gcode_errmsg = f'第 {index_L} 行P参数仅允许用于G04暂停'
                        _line_ok = False
                    axis_words = [c for c in cmd_code if c in ('X', 'Y', 'Z')]
                    if axis_words and line_m_values:
                        Gcode_errmsg = f'第 {index_L} 行不允许运动坐标与M指令混写'
                        _line_ok = False
                    if line_m_values and 'F' in cmd_code:
                        Gcode_errmsg = f'第 {index_L} 行不允许F进给与M指令混写'
                        _line_ok = False
                    if axis_words and modal_motion is None and 4 not in line_g_values:
                        Gcode_errmsg = f'第 {index_L} 行有坐标但尚未建立G0/G1运动模态'
                        _line_ok = False
                    line_spindle_rpm = None
                    if 3 in line_m_values:
                        if 'S' not in cmd_code:
                            Gcode_errmsg = (
                                f'第 {index_L} 行M3必须在同一行明确给出S转速')
                            _line_ok = False
                        elif cmd_value[cmd_code.index('S')] <= 0:
                            Gcode_errmsg = f'第 {index_L} 行M3的S转速必须大于0'
                            _line_ok = False
                        elif cmd_value[cmd_code.index('S')] > 45000:
                            Gcode_errmsg = f'第 {index_L} 行M3的S转速超过45000rpm上限'
                            _line_ok = False
                        else:
                            line_spindle_rpm = cmd_value[cmd_code.index('S')]
                    elif 'S' in cmd_code:
                        Gcode_errmsg = (
                            f'第 {index_L} 行S参数仅允许与M3写在同一行，'
                            '避免转速被静默忽略')
                        _line_ok = False
                    if 'F' in cmd_code:
                        feed = cmd_value[cmd_code.index('F')]
                        if feed <= 0:
                            Gcode_errmsg = f'第 {index_L} 行进给F必须大于0'
                            _line_ok = False
                        elif modal_feed_mode == 0:
                            modal_feed_mm_min = feed
                        else:
                            rpm_for_feed = (line_spindle_rpm
                                            if line_spindle_rpm is not None
                                            else modal_spindle_rpm)
                            if rpm_for_feed is None or rpm_for_feed <= 0:
                                Gcode_errmsg = (
                                    f'第 {index_L} 行G95设置F时主轴没有有效S转速')
                                _line_ok = False
                            else:
                                modal_feed_mm_min = rpm_for_feed * feed
                        if (modal_feed_mm_min is not None
                                and modal_feed_mm_min > GCODE_MAX_FEED_XY_MM_MIN):
                            Gcode_errmsg = (
                                f'第 {index_L} 行有效进给 {modal_feed_mm_min:g} mm/min'
                                f'超过安全上限 {GCODE_MAX_FEED_XY_MM_MIN:g}')
                            _line_ok = False
                    if line_spindle_rpm is not None:
                        modal_spindle_rpm = line_spindle_rpm
                    if 5 in line_m_values:
                        modal_spindle_rpm = None
                        if modal_feed_mode == 1:
                            modal_feed_mm_min = None
                    if (axis_words and 4 not in line_g_values
                            and modal_motion == 1
                            and modal_feed_mm_min is None):
                        Gcode_errmsg = (
                            f'第 {index_L} 行G1运动前尚未建立有效F进给')
                        _line_ok = False
                    if (axis_words and 'Z' in axis_words
                            and 4 not in line_g_values
                            and modal_motion == 1
                            and modal_feed_mm_min is not None
                            and modal_feed_mm_min > GCODE_MAX_FEED_Z_MM_MIN):
                        Gcode_errmsg = (
                            f'第 {index_L} 行含Z轴，有效进给 {modal_feed_mm_min:g} mm/min'
                            f'超过Z轴安全上限 {GCODE_MAX_FEED_Z_MM_MIN:g}')
                        _line_ok = False
                    if 4 in line_g_values:
                        dwell_words = [c for c in cmd_code if c in ('P', 'X')]
                        other_axes = [c for c in cmd_code if c in ('Y', 'Z')]
                        if len(dwell_words) != 1 or other_axes:
                            Gcode_errmsg = (
                                f'第 {index_L} 行G04必须且只能使用P毫秒或X秒')
                            _line_ok = False
                        else:
                            dwell_value_check = cmd_value[
                                cmd_code.index(dwell_words[0])]
                            dwell_ms_check = (dwell_value_check
                                              if dwell_words[0] == 'P'
                                              else dwell_value_check * 1000.0)
                            if not 1 <= dwell_ms_check <= GCODE_MAX_DWELL_MS:
                                Gcode_errmsg = (
                                    f'第 {index_L} 行G04暂停必须在1～'
                                    f'{GCODE_MAX_DWELL_MS}ms内')
                                _line_ok = False
                if _line_ok:
                    # Execution indexes address only parsed commands, whereas
                    # the editor also contains blank/comment-only lines.  Keep
                    # the source line separately so display/highlighting never
                    # becomes the execution-boundary check.
                    self.Mat_Gcode.append({
                        'cmd': cmd_code,
                        'value': cmd_value,
                        'source_line': index_L - 1,
                    })
        if Gcode_errmsg != '':
            self.Mat_Gcode = []
            self.Append_Log(f"[G代码预检失败] {Gcode_errmsg}")
            QMessageBox.warning(self, "warning", Gcode_errmsg)

    def Gcode2ZMC(self, num):
        if not 0 <= num < len(self.Mat_Gcode):
            raise SpindleSafetyError(
                f"G代码内部索引越界：{num}，有效范围 0～{len(self.Mat_Gcode) - 1}")
        cmd_code = self.Mat_Gcode[num]['cmd']
        cmd_value = self.Mat_Gcode[num]['value']
        # Set commands are line-local.  Never carry an old M3/M5/G04 into a
        # later coordinate line.
        self.ZMC.SetCmd = -1
        Main_par_C = []
        Main_par_V = []
        Att_par_C = []
        Att_par_V = []
        spindle_speed = None
        dwell_value = None
        feed_value = None
        for i in range(len(cmd_code)):
            cmd = cmd_code[i]
            value = cmd_value[i]
            if cmd == 'G':
                if value == 90:
                    self.ZMC.Move_type = 0
                elif value == 91:
                    self.ZMC.Move_type = 1
                elif value == 4:
                    self.ZMC.SetCmd = 4
                elif value in [0, 1, 2, 3]:
                    self.ZMC.MoveCmd = value
                elif value == 94:
                    self.ZMC.Fd_Type = 0
                elif value == 95:
                    self.ZMC.Fd_Type = 1
                elif value in (21, 54):
                    pass
            elif cmd in ['X', 'Y', 'Z']:
                Main_par_C.append(cmd)
                Main_par_V.append(value)
            elif cmd == 'S':
                spindle_speed = value
            elif cmd == 'P':
                dwell_value = value
            elif cmd in ['I', 'J', 'K']:
                Att_par_C.append(cmd)
                Att_par_V.append(value)
            elif cmd == 'F':
                # Apply only after the complete line has established G94/G95;
                # token order (F before or after G95) must not change meaning.
                feed_value = value
            elif cmd == 'M':
                if value == 3:
                    self.ZMC.SetCmd = 203
                elif value == 4:
                    self.ZMC.SetCmd = 204
                elif value == 5:
                    self.ZMC.SetCmd = 205
        if feed_value is not None:
            self.ZMC.Set_AxSpd(feed_value)
        if self.ZMC.SetCmd != -1:
            A = self.ZMC.SetCmd
            if A == 4:
                dwell_ms = 0
                if dwell_value is not None:
                    dwell_ms = int(dwell_value)
                elif Main_par_C and Main_par_C[0] == 'X':
                    dwell_ms = int(Main_par_V[0] * 1000)
                else:
                    self.Append_Log(f"  Gcode line {num}: G04 缺少暂停时间参数，跳过")
                if self._start_gcode_dwell(dwell_ms):
                    self.Append_Log(
                        f"  Gcode line {num}: G04 非阻塞暂停 {dwell_ms}ms（安全轮询保持运行）")
            elif A == 203:
                if spindle_speed is None:
                    raise SpindleSafetyError(
                        "M3缺少同行S转速，已拒绝使用旧值或默认值")
                rpm = spindle_speed
                self.ZMC.start_spindle(rpm, f"GCode_M3_line_{num}")
                self._refresh_spindle_controls()
                self.Append_Log(f"  Gcode line {num}: M3 启动主轴，设定 {rpm:g}rpm")
            elif A == 204:
                raise SpindleSafetyError("M4反转未实现独立方向输出，已拒绝执行")
            elif A == 205:
                self.ZMC.stop_spindle("Gcode_M5")
                self._refresh_spindle_controls()
            self.ZMC.SetCmd = -1
        else:
            A = self.ZMC.MoveCmd
            # 安全：若无运动轴参数（如仅有 F/G90/G91 的行），跳过执行
            if not Main_par_C:
                return
            if A not in (0, 1):
                raise SpindleSafetyError("当前G代码坐标行没有有效的G0/G1运动模态")
            if not self._is_test_mode():
                self._require_motion_interlocks(require_axes=True, require_idle=True)

            current = self._current_command_positions()
            if self.ZMC.Move_type == 0 and not self._is_test_mode():
                # G90：G54工件绝对坐标 -> DPOS
                execute_values = self._g54_absolute_dpos_targets(
                    Main_par_C, Main_par_V)
            elif self.ZMC.Move_type == 0:
                # Raspberry Pi TEST has no real axes or G54 frame.
                execute_values = [float(item) for item in Main_par_V]
            else:  # G91：相对增量不受G54偏置影响
                execute_values = [float(item) for item in Main_par_V]
            # 软限位检查（G0/G1/G2/G3 均检查终点）
            _limit_map = {'X': self.X_data, 'Y': self.Y_data, 'Z': self.Z_data}
            expected_targets = {}
            for _ax, _value in zip(Main_par_C, execute_values):
                _lim = _limit_map[_ax]
                _target = (current[_ax] + _value
                           if self.ZMC.Move_type == 1 else _value)
                if not (float(_lim[0]) - 0.001 <= _target <= float(_lim[2]) + 0.001):
                    self.Append_Log(
                        f"[安全拦截] G代码 line {num}: {_ax} 目标 {_target:.3f}"
                        f" 超出限位 [{float(_lim[0]):.3f}, {float(_lim[2]):.3f}]"
                        f"，G代码已停止")
                    self._stop_gcode_safely(
                        f"G代码软限位中止（line {num}）", rapidstop=True)
                    return
                expected_targets[_ax] = float(_target)
            if (A == 1 and 'Z' in Main_par_C
                    and (self.ZMC.current_gcode_feed_mm_min is None
                         or self.ZMC.current_gcode_feed_mm_min
                            > GCODE_MAX_FEED_Z_MM_MIN)):
                raise SpindleSafetyError(
                    f"含Z轴的G1有效进给必须在0～"
                    f"{GCODE_MAX_FEED_Z_MM_MIN:g} mm/min内")
            if A == 0:
                self.ZMC.AxSpd_G0()
                self._arm_gcode_motion_tracking(
                    current, expected_targets, rapid=True, line_num=num)
                try:
                    self.ZMC.ZMC_MMove_all(Main_par_C, execute_values)
                except Exception:
                    self._clear_gcode_motion_tracking()
                    raise
                self.Append_Log(
                    f"  Gcode line {num}: G0 快速定位，DPOS目标 "
                    + " ".join(f"{a}{v:.3f}" for a, v in zip(Main_par_C, execute_values)))
            elif A == 1:
                self._arm_gcode_motion_tracking(
                    current, expected_targets, rapid=False, line_num=num)
                try:
                    self.ZMC.ZMC_MMove_all(Main_par_C, execute_values)
                except Exception:
                    self._clear_gcode_motion_tracking()
                    raise
                self.Append_Log(
                    f"  Gcode line {num}: G1 直线插补，DPOS目标 "
                    + " ".join(f"{a}{v:.3f}" for a, v in zip(Main_par_C, execute_values)))
            if self._is_test_mode():
                self._clear_gcode_motion_tracking()

    def Run_Gcode(self):
        if self.ZMC.estop_latched or not self.ZMC.safety_inputs_verified:
            self._set_safety_fault_ui("急停锁存或安全输入未验证，禁止执行G代码")
            return
        if not self._is_test_mode() and self._soft_limit_recovery_active():
            self.Append_Log(
                "G代码启动拒绝：存在软限位越界恢复状态；"
                "请仅用界面单步点动回到正常软件范围")
            self._refresh_motion_controls()
            return
        if not self._is_test_mode() and not self._axes_enabled:
            self.Append_Log("G 代码执行失败：请先按「使能」开启轴电源")
            return
        if not self._gcode_running:
            if not self._is_test_mode():
                try:
                    self._require_motion_interlocks(require_axes=True, require_idle=True)
                except Exception as e:
                    self.Append_Log(f"G代码启动拒绝：{e}")
                    return
            self.deal_Gcode()
            if self.Mat_Gcode == []:
                self.Append_Log("G 代码为空，请输入或粘贴代码后重试")
                return
            else:
                if (not self._is_test_mode()
                        and not self._gcode_first_run_confirmed):
                    if not self._confirm_spindle_start():
                        self.Append_Log(
                            "首次G代码运行已取消：未确认磁悬浮/水冷状态")
                        return
                    self._gcode_first_run_confirmed = True
                    self.Append_Log(
                        "本次连接的首次G代码运行：磁悬浮/水冷已由操作者确认")
                if self._is_test_mode():
                    self.Append_Log("[TEST] G代码中的XYZ运动仅仿真推进，不会发送到真实控制器")
                # AUTO 运行中 G0/F 会改变控制器当前速度。无论自然结束、
                # 用户停止还是异常中止，随后第一次手动点动均须恢复手动档。
                self._manual_speed_profile_dirty = True
                self.ZMC.SetCmd = -1
                self.ZMC.MoveCmd = -1
                self.ZMC.Move_type = 0
                self.ZMC.Fd_Type = 0
                self.ZMC.current_gcode_feed_mm_min = None
                self._cancel_gcode_dwell()
                self._clear_gcode_motion_tracking()
                self.ui.Gcode_run.setText('stop')
                self._gcode_running = True
                self.Worker.GCode_St_all = True
                self._halt_manual_motion_ui()
                self._refresh_motion_controls()
                self.Now_Run_GCode = -1
                self.Run_Next_Gcode()
                self.Append_Log("G 代码开始执行...")
        else:
            self._stop_gcode_safely("用户停止G代码", rapidstop=True)

    def Run_Next_Gcode(self):
        if self._gcode_running:
            self.ZMC.Now_GCode_RST = True
            try:
                next_index = self.Now_Run_GCode + 1
                # The parsed command list is the sole execution boundary.
                # Editor blocks may include blank/comment lines or a trailing
                # newline, so their count must never authorize list access.
                if next_index >= len(self.Mat_Gcode):
                    self._stop_gcode_safely(
                        "G代码自然结束", rapidstop=False)
                    return
                self.Now_Run_GCode = next_index
                source_line = self.Mat_Gcode[next_index].get(
                    'source_line', next_index)
                self.Highlight_ZD_line(source_line)
                if self._gcode_running:
                    self.Gcode2ZMC(self.Now_Run_GCode)
            except Exception as e:
                self.Append_Log(f"G代码执行异常（line {self.Now_Run_GCode}）：{e}")
                self._stop_gcode_safely("G代码异常中止", rapidstop=True)
            finally:
                self.ZMC.Now_GCode_RST = False

    # ===== 新增功能 =====
    def Emergency_Stop(self, reason="UI_EStop"):
        """软件急停：先锁存并停主轴，再停轴；任何失败都明确显示。"""
        self._cancel_gcode_dwell()
        self._halt_manual_motion_ui()
        self._axes_enabled = False
        self.ZMC.estop_latched = True
        spindle_confirmed = self.ZMC.latch_estop(reason)
        errors = []
        if not spindle_confirmed:
            errors.append(self.ZMC.last_safety_error or "主轴停机未确认")
        try:
            self.ZMC.Rapidstop()
        except Exception as e:
            errors.append(f"Rapidstop失败: {e}")
        try:
            self.ZMC.Enable_Axs(0)
        except Exception as e:
            errors.append(f"轴下使能失败: {e}")
        # Worker remains the independent status/heartbeat path.  It must not
        # repeat the same software E-stop command sequence.
        if hasattr(self, 'Worker') and self.Worker is not None:
            self.Worker.GCode_St_all = False
        if self.ui.Gcode_run.text() == 'stop':
            self.ui.Gcode_run.setText('run')
        if errors:
            detail = "；".join(errors)
        elif self._is_test_mode():
            detail = "虚拟主轴 RUN=0、RPM=0 已确认"
        else:
            detail = "主轴 IO7/DA0 停机读回已确认"
        self._set_safety_fault_ui(f"急停已锁存（{reason}）：{detail}")
        self.Append_Log("提示：排除危险后，按「恢复」重新使能")

    def Resume_From_Stop(self):
        # 安全确认：检查硬件急停状态
        try:
            safe = self.ZMC.Safe_Det()
            # safe[6] = IO6 (机箱急停), safe[7] = IO16 (操作台急停), NC: 0=按下, 1=松开
            if safe[6] is not None and _scalar_value(safe[6]) == 0:
                self.Append_Log("恢复失败：机箱急停按钮未复位（IO6=0），请旋出")
                return
            if safe[7] is not None and _scalar_value(safe[7]) == 0:
                self.Append_Log("恢复失败：操作台急停按钮未复位（IO16=0），请旋出")
                return
        except Exception as e:
            self.Append_Log(f"恢复失败：读取 IO 状态出错 {e}")
            return
        confirm_text = (
            "请确认虚拟主轴已停止，并解除树莓派故障注入。\n\n确定恢复 TEST 吗？"
            if self._is_test_mode() else
            "请确认：\n"
            "1. 急停按钮已复位（已旋出）\n"
            "2. 所有轴在安全位置\n"
            "3. 无其他报警\n\n"
            "确定解除急停锁存吗？\n（恢复后轴仍保持未使能）")
        reply = QMessageBox.question(self, "恢复确认", confirm_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            self.Append_Log("恢复已取消")
            return
        try:
            if hasattr(self, 'Worker') and self.Worker is not None:
                self.Worker.EStop_Flag = False
            self.ZMC.clear_estop_latch()
            # LinuxCNC-style separation: resetting E-stop never powers axes
            # automatically.  Machine/axis enable remains a separate action.
            self.ZMC.Enable_Axs(0)
            self._axes_enabled = False
        except Exception as e:
            self.ZMC.estop_latched = True
            self.ZMC.latch_estop("Resume_Failure")
            self._set_safety_fault_ui(f"恢复失败，重新锁存急停：{e}")
            return
        self.ui.Ax_enable.setText('未使能')
        self.ui.Ax_enable.setStyleSheet(
            "background-color: #FFD700; color: #222; font-weight: bold;")
        self.Update_StatusLight('test' if self._is_test_mode() else 'disable')
        if self.ui.Connect.text() == 'Disconnect':
            self.ui.Connect.setStyleSheet(
                "background-color: #2ea84f; color: white; font-weight: bold;")
        self._apply_test_mode_control_policy()
        self._refresh_motion_controls()
        self._refresh_spindle_controls()
        if self._is_test_mode():
            self.Append_Log("TEST 故障锁存已解除；真实运动轴仍保持禁用")
        else:
            self.Append_Log("急停锁存已解除；轴仍保持未使能，需人工按使能后才能运动")

    def Export_Log(self):
        try:
            default_name = f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            path, _ = QFileDialog.getSaveFileName(
                self, "导出日志", default_name, "Text Files (*.txt)")
            if not path:
                return
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.ui.Log_text.toPlainText())
            self.Append_Log(f"日志已导出到: {path}")
        except Exception as e:
            self.Append_Log(f"导出日志失败: {e}")

    # ===== 关闭 =====
    def closeEvent(self, event):
        # 连接状态下，只有停机和下使能均确认成功才允许退出。
        self._cancel_gcode_dwell()
        self._halt_manual_motion_ui()
        self._gcode_running = False
        if hasattr(self, 'Worker') and self.Worker is not None:
            self.Worker.GCode_St_all = False
        errors = []
        connected = bool(getattr(self.ZMC, 'is_connected', False))
        if connected:
            try:
                self.ZMC.stop_spindle("closeEvent")
            except Exception as e:
                errors.append(f"主轴停机未确认: {e}")
        if connected:
            try:
                self.ZMC.Rapidstop()
            except Exception as e:
                errors.append(f"Rapidstop失败: {e}")
            try:
                self.ZMC.Enable_Axs(0)
                self._axes_enabled = False
            except Exception as e:
                errors.append(f"轴下使能失败: {e}")
        if errors:
            self.ZMC.estop_latched = True
            self._set_safety_fault_ui("程序拒绝关闭；" + "；".join(errors))
            event.ignore()
            return
        if hasattr(self, 'Worker') and self.Worker is not None:
            self.Worker.Wstatus = False
            try:
                self.Worker.wait(2000)
            except Exception:
                pass
        self.ui.Ax_enable.setText('未使能')
        self._axes_enabled = False
        if self.ui.Connect.text() == 'Disconnect':
            try:
                self.ZMC.Disconnect()
            except Exception as e:
                self.ZMC.estop_latched = True
                self._set_safety_fault_ui(f"程序拒绝关闭；控制器断开失败: {e}")
                event.ignore()
                return
            self.ui.Connect.setText('Connect')
        self._disconnect_handwheel_serial(log_result=False)
        event.accept()


if __name__ == '__main__':
    # 固定以程序自身目录运行，避免从桌面/快捷方式启动时相对路径失效。
    os.chdir(_BASE)
    app = QApplication(sys.argv)
    try:
        app.setStyle('Fusion')
    except Exception:
        pass

    w = MainWindow()
    w.show()
    sys.exit(app.exec())
