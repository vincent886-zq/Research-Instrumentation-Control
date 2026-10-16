"""
配置持久化 — 读写 config.json，记忆用户偏好。
线程安全：仅在主线程调用。
"""
import json
import os
from copy import deepcopy

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULTS = {
    "serial": {
        "port": "",
        "baudrate": 921600,
    },
    "view": {
        "view_range": 10.0,
        "y_range": 5.0,
        "y_auto_range": True,
    },
    "window": {
        "width": 1400,
        "height": 800,
        "x": 100,
        "y": 100,
    },
    "channels": {
        "enabled_mask": 0,     # 默认全部未勾选
        "display_list": [],
    },
    "sample_rate": 10000,
    "export_path": "",
}


def load():
    """读取配置，不存在或损坏则返回默认值。"""
    if not os.path.exists(CONFIG_PATH):
        return deepcopy(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = deepcopy(DEFAULTS)
        for section in DEFAULTS:
            if section in data and isinstance(data[section], dict):
                merged[section].update(data[section])
        return merged
    except (json.JSONDecodeError, IOError):
        return deepcopy(DEFAULTS)


def save(cfg: dict):
    """写入配置。失败静默忽略。"""
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except (IOError, TypeError):
        pass


def update_serial(port: str, baudrate: int):
    cfg = load()
    cfg["serial"]["port"] = port
    cfg["serial"]["baudrate"] = baudrate
    save(cfg)


def update_view(view_range: float, y_range: float = None, y_auto_range: bool = None):
    cfg = load()
    cfg["view"]["view_range"] = view_range
    if y_range is not None:
        cfg["view"]["y_range"] = y_range
    if y_auto_range is not None:
        cfg["view"]["y_auto_range"] = y_auto_range
    save(cfg)


def update_window(x: int, y: int, width: int, height: int):
    cfg = load()
    cfg["window"].update({"x": x, "y": y, "width": width, "height": height})
    save(cfg)


def update_channels(enabled_mask: int, display_list: list):
    cfg = load()
    cfg["channels"]["enabled_mask"] = enabled_mask
    cfg["channels"]["display_list"] = display_list
    save(cfg)


def update_sample_rate(rate: int):
    cfg = load()
    cfg["sample_rate"] = rate
    save(cfg)


def update_export_path(path: str):
    cfg = load()
    cfg["export_path"] = path
    save(cfg)
