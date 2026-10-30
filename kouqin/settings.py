"""
用户配置。

配置文件放在程序目录下的「数据/settings.json」——绿色便携，
拷走整个文件夹就能带着自己的键位设置换电脑。
如果程序目录不可写（比如放在只读盘），自动退回到用户目录。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from .engine import TIMING_PRESETS
from .layout import BASE_KEYS, DEFAULT_MOD_ASSIGN, Layout, MouseMod

__all__ = ["AppSettings", "ROOT_DIR", "DATA_DIR", "LIBRARY_DIR", "settings_path"]

def _program_dir() -> str:
    """
    程序所在的文件夹。

    源码运行时就是项目根（本文件在 kouqin/ 下，往上一级）。
    打包成 exe（PyInstaller）后 __file__ 指向解包出来的内部目录——单文件版甚至是
    每次运行完就删掉的临时目录——所以要改用 exe 自己所在的位置。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


#: 程序根目录
ROOT_DIR = _program_dir()

#: 用户数据目录
DATA_DIR = os.path.join(ROOT_DIR, "数据")

#: 默认曲库目录
LIBRARY_DIR = os.path.join(DATA_DIR, "曲库")

#: 配置文件路径
SETTINGS_FILE = "settings.json"

#: 悬浮窗位置预设（custom = 用户拖动过的位置）
OVERLAY_ANCHORS = ("top", "upper", "lower", "bottom", "custom")

#: 界面外观：跟随系统 / 浅色 / 深色
APPEARANCES = ("system", "light", "dark")

#: 能当快捷键的键（都是游戏里一般不用、也不会和音键冲突的键）：(键名, 显示名)
HOTKEY_CHOICES = (
    [(f"f{i}", f"F{i}") for i in range(1, 13)]
    + [("insert", "Insert"), ("delete", "Delete"), ("home", "Home"), ("end", "End"),
       ("pageup", "PageUp"), ("pagedown", "PageDown")]
    + [(f"num{i}", f"小键盘 {i}") for i in range(10)]
)
HOTKEY_NAMES = {key: label for key, label in HOTKEY_CHOICES}
#: 三个快捷键：(设置字段, 默认键, 用途)
HOTKEY_ACTIONS = (
    ("hotkey_play", "f6", "开始 / 暂停 / 继续"),
    ("hotkey_pause", "f7", "暂停 / 继续"),
    ("hotkey_stop", "f8", "停止"),
)


def hotkey_label(key: str) -> str:
    return HOTKEY_NAMES.get((key or "").lower(), (key or "").upper())

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


def _installed_in_program_files() -> bool:
    """是不是用安装包装进了 Program Files 的 exe。"""
    if not getattr(sys, "frozen", False):
        return False
    here = os.path.normcase(os.path.abspath(ROOT_DIR)) + os.sep
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(var)
        if base and here.startswith(os.path.normcase(os.path.abspath(base)) + os.sep):
            return True
    return False


def _resolve_data_dir() -> str:
    """
    优先用程序目录（绿色便携，拷走整个文件夹就带走了设置）。

    写不了、或者是装在 Program Files 里的安装版，就退到用户目录——
    以管理员身份运行时 Program Files 其实也写得进去，但设置不该跟着"整台电脑"走，
    卸载重装也不该丢。
    """
    override = os.environ.get("KOUQIN_DATA_DIR", "").strip()
    if override:
        # 测试用：把设置和曲库放到别处，不碰使用者自己的数据
        os.makedirs(override, exist_ok=True)
        return override
    if not _installed_in_program_files() and _writable(DATA_DIR):
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
    legato: bool = True                 # 换不同的键时不留空隙（同一个键连按仍会留一帧）

    # --- 编辑 ---
    beats_per_bar: int = 4
    default_bpm: float = 90.0

    # --- 悬浮提示窗 / 跟弹 ---
    overlay_in_auto: bool = True        # 自动演奏时也显示悬浮提示
    guide_wait: bool = False            # 跟弹：等我按对再继续
    guide_speed: float = 1.0            # 跟弹速度（只影响跟弹，不影响自动演奏）
    overlay_lookahead: float = 3.0      # 提前几秒显示后面的音
    overlay_scale: float = 1.0          # 悬浮窗大小
    overlay_alpha: float = 0.95         # 悬浮窗上音符、键帽、文字的不透明度
    overlay_backdrop: float = 0.45      # 悬浮窗底板的透明度（0 = 不透明，0.9 = 几乎全透明）
    hit_effects: bool = True            # 按中时放命中特效（鸽子 + 音符）
    overlay_keyboard: bool = True       # 显示下方的键盘提示行
    overlay_anchor: str = "upper"       # 悬浮窗位置预设：top / upper / lower / bottom / custom
    overlay_x: Optional[int] = None     # 拖动过的位置（overlay_anchor 为 custom 时用）
    overlay_y: Optional[int] = None
    overlay_pos_scale: float = 0.0      # 保存位置时的屏幕缩放（0 = 老配置，当时程序不感知 DPI，坐标是缩放前的）

    # --- 在线曲库 ---
    online_library_base: str = ""       # 空 = 用默认地址（kouqin.online.DEFAULT_BASE）

    # --- 快捷键 ---
    hotkey_play: str = "f6"             # 开始 / 暂停 / 继续（倒计时中按是取消）
    hotkey_pause: str = "f7"            # 只管暂停 / 继续，没在播放时按什么都不做
    hotkey_stop: str = "f8"             # 停止并松开所有按键

    # --- 外观 ---
    appearance: str = "system"          # system / light / dark
    ui_size: float = 1.0                # 界面大小：1.0 标准，1.15 大，1.3 更大……（乘在屏幕缩放上）

    # --- 其它 ---
    follow_playhead: bool = True
    installed_presets: bool = False      # 是否已经铺过内置曲目
    preset_revision: int = 0             # 曲库里的内置曲目是第几版（见 tune.PRESET_REVISION）
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
        hotkeys = [getattr(self, field) for field, _default, _what in HOTKEY_ACTIONS]
        if len(set(hotkeys)) != len(hotkeys):
            problems.append("三个快捷键不能有重复的")
        if set(hotkeys) & set(keys):
            problems.append("快捷键和音键重复了")
        return problems

    def timing_preset(self):
        return TIMING_PRESETS.get(self.timing, TIMING_PRESETS["normal"])

    def copy(self) -> "AppSettings":
        # 用 replace 而不是逐个字段抄：以后加字段不会漏抄（早先每加一项都得记得改这里）
        return replace(self, keys=list(self.keys), mod_assign=dict(self.mod_assign))

    # ------------------------------------------------------------------ #
    # 读写
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        data = {"version": 1}
        data.update(asdict(self))
        return data

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

        settings.overlay_in_auto = bool(data.get("overlay_in_auto", True))
        settings.guide_wait = bool(data.get("guide_wait", False))
        settings.guide_speed = clamp(data.get("guide_speed", 1.0), 0.25, 1.5, 1.0)
        settings.overlay_lookahead = clamp(data.get("overlay_lookahead", 3.0), 1.0, 8.0, 3.0)
        settings.overlay_scale = clamp(data.get("overlay_scale", 1.0), 0.6, 2.0, 1.0)
        settings.overlay_alpha = clamp(data.get("overlay_alpha", 0.95), 0.3, 1.0, 0.95)
        settings.overlay_backdrop = clamp(data.get("overlay_backdrop", 0.45), 0.0, 0.9, 0.45)
        settings.hit_effects = bool(data.get("hit_effects", True))
        if data.get("appearance") in APPEARANCES:
            settings.appearance = data["appearance"]
        settings.ui_size = clamp(data.get("ui_size", 1.0), 0.8, 1.6, 1.0)
        chosen = {}
        for field, default, _what in HOTKEY_ACTIONS:
            key = str(data.get(field, default) or default).lower()
            chosen[field] = key if key in HOTKEY_NAMES else default
        if len(set(chosen.values())) == len(chosen):          # 配置文件里手改成重复的，就全退回默认
            for field, key in chosen.items():
                setattr(settings, field, key)
        settings.overlay_pos_scale = clamp(data.get("overlay_pos_scale", 0.0), 0.0, 4.0, 0.0)
        settings.overlay_keyboard = bool(data.get("overlay_keyboard", True))
        for attr in ("overlay_x", "overlay_y"):
            value = data.get(attr)
            # 位置可以是负数（主屏左边 / 上边的副屏），但必须是个正常的整数
            if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) < 100_000:
                setattr(settings, attr, int(value))
        anchor = data.get("overlay_anchor")
        if anchor in OVERLAY_ANCHORS:
            settings.overlay_anchor = anchor
        elif settings.overlay_x is not None and settings.overlay_y is not None:
            settings.overlay_anchor = "custom"      # v2.1.0 拖动过位置的老配置
        settings.legato = bool(data.get("legato", True))
        base = data.get("online_library_base", "")
        if isinstance(base, str) and base.strip().lower().startswith(("https://", "http://")):
            settings.online_library_base = base.strip()
        settings.preset_revision = clamp(data.get("preset_revision", 0), 0, 999, 0)

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
        keep_revision = self.preset_revision
        keep_disclaimer = self.disclaimer_accepted
        keep_guide = self.guide_shown
        fresh = AppSettings()
        for attr in ("keys", "mod_assign", "base_octave", "timing", "countdown",
                     "speed", "beats_per_bar", "default_bpm", "follow_playhead",
                     "overlay_in_auto", "guide_wait", "guide_speed", "overlay_lookahead",
                     "overlay_scale", "overlay_alpha", "overlay_keyboard",
                     "overlay_anchor", "overlay_x", "overlay_y", "legato",
                     "online_library_base", "overlay_backdrop", "appearance", "ui_size",
                     "overlay_pos_scale", "hotkey_play", "hotkey_pause", "hotkey_stop", "hit_effects"):
            setattr(self, attr, getattr(fresh, attr))
        self.installed_presets = keep_presets
        self.preset_revision = keep_revision
        self.disclaimer_accepted = keep_disclaimer
        self.guide_shown = keep_guide
