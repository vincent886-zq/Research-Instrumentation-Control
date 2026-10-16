"""
校准配置文件管理 — 保存/加载/管理 HSTS016L 零点校准文件 (.cal)。

支持功能:
  - 校准完成后自动保存到 calibration_profiles/ 目录
  - 手动命名保存
  - 一键加载任意历史校准文件（立即生效，无需重新对下位机校准）
  - 校准文件列表管理（查看、删除、添加备注）
  - 重启后自动恢复上次校准

文件格式: JSON (.cal)
  字段:
    version       : 格式版本 (当前 1)
    timestamp     : 校准时间 (ISO 8601)
    label         : 用户可修改的标签/描述
    vzero         : 8 通道 Vzero 电压值 [V]
    i_offsets     : 8 通道电流偏移值 [A]
    enabled_mask  : 当时启用的通道位掩码
    notes         : 用户备注
"""
import json
import os
from datetime import datetime
from typing import Optional

# ── 校准文件存放目录 ──
CAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration_profiles")

# ── 默认配置文件名 ──
LAST_CAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_cal.txt")


def _ensure_dir():
    """确保校准文件目录存在。"""
    os.makedirs(CAL_DIR, exist_ok=True)


def auto_filename() -> str:
    """生成自动保存的文件名: auto_YYYYMMDD_HHMMSS.cal"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"auto_{ts}.cal"


# ═══════════════════════════════════════════════════════════
#  保存
# ═══════════════════════════════════════════════════════════

def save_calibration(vzero_values: list,
                     i_offsets: list,
                     enabled_mask: int,
                     label: str = "",
                     filename: str = None,
                     notes: str = "") -> str:
    """保存校准数据到 .cal 文件。

    Args:
        vzero_values: 8 通道 Vzero 电压值
        i_offsets: 8 通道电流偏移值
        enabled_mask: 通道启用位掩码
        label: 标签/描述
        filename: 文件名（不含路径），None 则自动生成
        notes: 备注

    Returns:
        保存的完整文件路径，失败返回空字符串。
    """
    _ensure_dir()
    if filename is None:
        filename = auto_filename()
    if not filename.endswith(".cal"):
        filename += ".cal"

    now = datetime.now()
    data = {
        "version": 1,
        "timestamp": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "label": label or f"校准 {now.strftime('%Y-%m-%d %H:%M:%S')}",
        "vzero": [round(v, 6) for v in vzero_values],
        "i_offsets": [round(v, 6) for v in i_offsets],
        "enabled_mask": int(enabled_mask),
        "notes": notes,
    }

    filepath = os.path.join(CAL_DIR, filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return filepath
    except (IOError, OSError) as e:
        print(f"[calibration_manager] 保存失败: {e}")
        return ""


# ═══════════════════════════════════════════════════════════
#  加载
# ═══════════════════════════════════════════════════════════

def load_calibration(filepath: str) -> Optional[dict]:
    """加载 .cal 校准文件。

    Args:
        filepath: .cal 文件完整路径

    Returns:
        字典包含 vzero, i_offsets, enabled_mask 等字段，失败返回 None。
    """
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 兼容旧格式
        if "vzero" not in data or len(data.get("vzero", [])) != 8:
            return None
        if "i_offsets" not in data or len(data.get("i_offsets", [])) != 8:
            # 从 vzero 计算 i_offsets
            data["i_offsets"] = [(v - 2.5) * 16.0 for v in data["vzero"]]
        return data
    except (json.JSONDecodeError, IOError, OSError) as e:
        print(f"[calibration_manager] 加载失败: {e}")
        return None


def get_last_calibration_file() -> str:
    """读取上次使用的校准文件路径。"""
    try:
        if os.path.exists(LAST_CAL_FILE):
            with open(LAST_CAL_FILE, "r", encoding="utf-8") as f:
                path = f.read().strip()
                if path and os.path.exists(path):
                    return path
    except (IOError, OSError):
        pass
    return ""


def save_last_calibration_file(filepath: str):
    """记录本次使用的校准文件路径（用于下次启动恢复）。"""
    try:
        with open(LAST_CAL_FILE, "w", encoding="utf-8") as f:
            f.write(filepath.strip())
    except (IOError, OSError):
        pass


# ═══════════════════════════════════════════════════════════
#  列表 & 管理
# ═══════════════════════════════════════════════════════════

def list_calibrations() -> list:
    """列出 calibration_profiles/ 目录下所有 .cal 文件。

    Returns:
        list of dict: [{
            "filename": "auto_20260727_143000.cal",
            "filepath": "完整路径",
            "label": "标签",
            "timestamp": "ISO时间",
            "mtime": 文件修改时间戳,
        }, ...]
        按 mtime 降序排列（最新的在前）。
    """
    _ensure_dir()
    results = []
    try:
        for fname in os.listdir(CAL_DIR):
            if not fname.endswith(".cal"):
                continue
            filepath = os.path.join(CAL_DIR, fname)
            mtime = os.path.getmtime(filepath)
            # 尝试读取文件头获取 label 和 timestamp
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                label = data.get("label", fname)
                ts = data.get("timestamp", "")
            except (json.JSONDecodeError, IOError):
                label = fname
                ts = ""
            results.append({
                "filename": fname,
                "filepath": filepath,
                "label": label,
                "timestamp": ts,
                "mtime": mtime,
            })
    except (IOError, OSError):
        pass
    results.sort(key=lambda x: x["mtime"], reverse=True)
    return results


def delete_calibration(filepath: str) -> bool:
    """删除指定的校准文件。"""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            return True
    except (IOError, OSError):
        pass
    return False


def update_calibration_label(filepath: str, new_label: str) -> bool:
    """更新校准文件的 label 字段。"""
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["label"] = new_label
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except (json.JSONDecodeError, IOError, OSError):
        return False


def update_calibration_notes(filepath: str, new_notes: str) -> bool:
    """更新校准文件的 notes 字段。"""
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["notes"] = new_notes
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except (json.JSONDecodeError, IOError, OSError):
        return False
