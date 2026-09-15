"""
用户配置。

配置文件放在程序目录下的「数据/settings.json」——绿色便携，
拷走整个文件夹就能带着自己的键位设置换电脑。
如果程序目录不可写（比如放在只读盘），自动退回到用户目录。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .engine import TIMING_PRESETS
from .layout import BASE_KEYS, DEFAULT_MOD_ASSIGN, Layout, MouseMod

__all__ = ["AppSettings", "ROOT_DIR", "DATA_DIR", "LIBRARY_DIR", "settings_path"]

#: 程序根目录（本文件在 kouqin/ 下，往上一级就是项目根）
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 用户数据目录
DATA_DIR = os.path.join(ROOT_DIR, "数据")

#: 默认曲库目录
LIBRARY_DIR = os.path.join(DATA_DIR, "曲库")

#: 配置文件路径
SETTINGS_FILE = "settings.json"

#: 允许作为音键的字符（防止用户填进奇怪的东西）
_ALLOWED_KEYS = set("abcdefghijklmnopqrstuvwxyz0123456789,./;'[]\\-=")


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "w") as fp:
            fp.write("1")
        os.remove(probe)
        return True
    except OSError:
        return False


def _resolve_data_dir() -> str:
    """优先用程序目录，写不了就退到用户目录。"""
    if _writable(DATA_DIR):
        return DATA_DIR
    fallback = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "KouqinStudio")
    os.makedirs(fallback, exist_ok=True)
    return fallback


DATA_DIR = _resolve_data_dir()
LIBRARY_DIR = os.path.join(DATA_DIR, "曲库")


def settings_path() -> str:
    return os.path.join(DATA_DIR, SETTINGS_FILE)


@dataclass
class AppSettings:
    """全部可调项。"""

    # --- 键位 ---
    keys: List[str] = field(default_factory=lambda: list(BASE_KEYS))
    mod_assign: Dict[str, Optional[str]] = field(
        default_factory=lambda: {k: v for k, v in DEFAULT_MOD_ASSIGN.items()}
    )
    base_octave: int = 4

    # --- 演奏 ---
    timing: str = "normal"
    countdown: float = 3.0
    speed: float = 1.0

    # --- 编辑 ---
    beats_per_bar: int = 4
    default_bpm: float = 90.0

    # --- 其它 ---
    follow_playhead: bool = True
    installed_presets: bool = False      # 是否已经铺过内置曲目
    disclaimer_accepted: bool = False    # 是否已确认过免责声明
    guide_shown: bool = False            # 是否已看过使用步骤（只自动弹一次）

    # ------------------------------------------------------------------ #
    # 转换与校验
    # ------------------------------------------------------------------ #

    def to_layout(self) -> Layout:
        """生成键位模型（内部会自动校验并兜底）。"""
        keys = [str(k).lower() for k in self.keys if str(k).strip()]
        if len(keys) != 8 or any(k not in _ALLOWED_KEYS for k in keys):
            keys = list(BASE_KEYS)
        assign = {k: (v or None) for k, v in self.mod_assign.items() if k in DEFAULT_MOD_ASSIGN}
        for feature, default in DEFAULT_MOD_ASSIGN.items():
            assign.setdefault(feature, default)
        octave = self.base_octave if 0 <= self.base_octave <= 8 else 4
        return Layout(base_octave=octave, keys=tuple(keys), mod_assign=assign)

    def validate_keymap(self) -> List[str]:
        """检查键位有没有冲突，返回问题列表（空表示没问题）。"""
        problems: List[str] = []
        keys = [str(k).lower() for k in self.keys]
        if len(keys) != 8:
            problems.append("音键必须正好 8 个")
        duplicates = {k for k in keys if keys.count(k) > 1}
        if duplicates:
            problems.append("有重复的音键：" + "、".join(sorted(duplicates)))
        used = [v for v in self.mod_assign.values() if v]
        if len(used) != len(set(used)):
            problems.append("同一个鼠标键被分配了多个功能")
        return problems

    def timing_preset(self):
        return TIMING_PRESETS.get(self.timing, TIMING_PRESETS["normal"])

    def copy(self) -> "AppSettings":
        return AppSettings(
            keys=list(self.keys),
            mod_assign=dict(self.mod_assign),
            base_octave=self.base_octave,
            timing=self.timing,
            countdown=self.countdown,
            speed=self.speed,
            beats_per_bar=self.beats_per_bar,
            default_bpm=self.default_bpm,
            follow_playhead=self.follow_playhead,
            installed_presets=self.installed_presets,
            disclaimer_accepted=self.disclaimer_accepted,
            guide_shown=self.guide_shown,
        )

    # ------------------------------------------------------------------ #
    # 读写
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "keys": list(self.keys),
            "mod_assign": {k: v for k, v in self.mod_assign.items()},
            "base_octave": self.base_octave,
            "timing": self.timing,
            "countdown": self.countdown,
            "speed": self.speed,
            "beats_per_bar": self.beats_per_bar,
            "default_bpm": self.default_bpm,
            "follow_playhead": self.follow_playhead,
            "installed_presets": self.installed_presets,
            "disclaimer_accepted": self.disclaimer_accepted,
            "guide_shown": self.guide_shown,
        }

    @staticmethod
    def from_dict(data: dict) -> "AppSettings":
        settings = AppSettings()

        keys = data.get("keys")
        if isinstance(keys, list) and len(keys) == 8:
            settings.keys = [str(k).lower() for k in keys]

        assign = data.get("mod_assign")
        if isinstance(assign, dict):
            for feature in DEFAULT_MOD_ASSIGN:
                if feature in assign:
                    value = assign[feature]
                    settings.mod_assign[feature] = value if value in MouseMod.ORDER else None

        def clamp(value, low, high, fallback):
            try:
                number = type(fallback)(value)
            except (TypeError, ValueError):
                return fallback
            return max(low, min(high, number))

        settings.base_octave = clamp(data.get("base_octave", 4), 0, 8, 4)
        settings.countdown = clamp(data.get("countdown", 3.0), 0.0, 15.0, 3.0)
        settings.speed = clamp(data.get("speed", 1.0), 0.25, 4.0, 1.0)
        settings.beats_per_bar = clamp(data.get("beats_per_bar", 4), 1, 12, 4)
        settings.default_bpm = clamp(data.get("default_bpm", 90.0), 30.0, 300.0, 90.0)

        if data.get("timing") in TIMING_PRESETS:
            settings.timing = data["timing"]
        settings.follow_playhead = bool(data.get("follow_playhead", True))
        settings.installed_presets = bool(data.get("installed_presets", False))
        settings.disclaimer_accepted = bool(data.get("disclaimer_accepted", False))
        settings.guide_shown = bool(data.get("guide_shown", False))
        return settings

    def save(self, path: Optional[str] = None) -> bool:
        path = path or settings_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(self.to_dict(), fp, ensure_ascii=False, indent=2)
            return True
        except OSError:
            return False

    @staticmethod
    def load(path: Optional[str] = None) -> "AppSettings":
        path = path or settings_path()
        if not os.path.exists(path):
            return AppSettings()
        try:
            with open(path, "r", encoding="utf-8") as fp:
                return AppSettings.from_dict(json.load(fp))
        except (OSError, json.JSONDecodeError, TypeError):
            return AppSettings()

    def reset(self) -> None:
        """
        恢复出厂设置。

        保留两个标记不动：「已铺曲目」免得下次又铺一遍，
        「已确认免责」免得用户刚点完同意又被弹一次。
        """
        keep_presets = self.installed_presets
        keep_disclaimer = self.disclaimer_accepted
        keep_guide = self.guide_shown
        fresh = AppSettings()
        for attr in ("keys", "mod_assign", "base_octave", "timing", "countdown",
                     "speed", "beats_per_bar", "default_bpm", "follow_playhead"):
            setattr(self, attr, getattr(fresh, attr))
        self.installed_presets = keep_presets
        self.disclaimer_accepted = keep_disclaimer
        self.guide_shown = keep_guide
