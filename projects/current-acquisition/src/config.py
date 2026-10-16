"""
配置持久化 — 读写 config.json，记忆用户偏好。
线程安全：仅在主线程调用。
"""
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULTS = {
    "serial": {
        "port": "",
        "baudrate": 921600,
    },
    "view": {
        "view_range": 10.0,
        "v_range": 0.625,
        "v_auto_range": False,
        "i_range": 10.0,
        "i_auto_range": False,
        "channel": 0,
    },
    "window": {
        "width": 1400,
        "height": 800,
        "x": 100,
        "y": 100,
    },
    "export_path": "",
    "calibration": {
        "auto_save": True,           # 校准后自动保存校准文件
        "auto_load_last": False,     # 启动时自动加载上次校准（默认关闭，避免误加载）
    },
}


def load():
    """读取配置，不存在或损坏则返回默认值。不弹窗。"""
    if not os.path.exists(CONFIG_PATH):
        return dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 合并默认值，填充缺失键
        merged = dict(DEFAULTS)
        for section in DEFAULTS:
            if section in data and isinstance(data[section], dict):
                merged[section].update(data[section])
        return merged
    except (json.JSONDecodeError, IOError):
        return dict(DEFAULTS)


def save(cfg: dict):
    """写入配置。失败静默忽略。"""
    try:
        # 只保存与默认不同的键（精简）
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except (IOError, TypeError):
        pass  # 失败不弹窗，不崩溃


def update_serial(port: str, baudrate: int):
    cfg = load()
    cfg["serial"]["port"] = port
    cfg["serial"]["baudrate"] = baudrate
    save(cfg)


def update_view(view_range: float, v_range: float = None, v_auto_range: bool = None,
                i_range: float = None, i_auto_range: bool = None,
                channel: int = None):
    cfg = load()
    cfg["view"]["view_range"] = view_range
    if v_range is not None:
        cfg["view"]["v_range"] = v_range
    if v_auto_range is not None:
        cfg["view"]["v_auto_range"] = v_auto_range
    if i_range is not None:
        cfg["view"]["i_range"] = i_range
    if i_auto_range is not None:
        cfg["view"]["i_auto_range"] = i_auto_range
    if channel is not None:
        cfg["view"]["channel"] = channel
    save(cfg)


def update_window(x: int, y: int, width: int, height: int):
    cfg = load()
    cfg["window"].update({"x": x, "y": y, "width": width, "height": height})
    save(cfg)


def update_export_path(path: str):
    cfg = load()
    cfg["export_path"] = path
    save(cfg)


def update_channels(enabled_mask: int, display_list: list):
    """保存通道选择。enabled_mask 位掩码 bit0~bit7 对应 CH1~CH8。"""
    cfg = load()
    if "channels" not in cfg:
        cfg["channels"] = {}
    cfg["channels"]["enabled_mask"] = int(enabled_mask)
    cfg["channels"]["display_list"] = [int(i) for i in display_list]
    save(cfg)


def update_calibration_settings(auto_save: bool = None, auto_load_last: bool = None):
    """保存校准配置。"""
    cfg = load()
    if "calibration" not in cfg:
        cfg["calibration"] = {"auto_save": True, "auto_load_last": False}
    if auto_save is not None:
        cfg["calibration"]["auto_save"] = bool(auto_save)
    if auto_load_last is not None:
        cfg["calibration"]["auto_load_last"] = bool(auto_load_last)
    save(cfg)
