"""
《口琴工坊》—— 三角洲行动口琴自动演奏工具（自研实现）

────────────────────────────────────────────────────────────────────────
⚠ 免责声明

本项目仅供个人学习、技术研究与娱乐使用。**严禁在任何在线对局中使用。**

使用自动化输入工具在在线游戏中可能违反游戏用户协议，并可能导致账号被
警告、限制功能、临时封禁甚至永久封禁。作者明确不建议、不支持、不鼓励
在任何在线对局中使用本项目；因使用本项目产生的一切后果，全部由使用者
本人自行承担，作者与贡献者不承担任何责任。

完整条款见项目根目录的 DISCLAIMER.md 与 LICENSE。
────────────────────────────────────────────────────────────────────────

模块划分::

    tune       曲子模型 + 本地曲库（一拍一格，给不懂乐理的人用）
    settings   用户配置（键位可自定义，绿色便携）
    smf        MIDI 文件读写（纯标准库，两遍法解析 + 变速时间轴）
    layout     口琴键位模型（音域枚举 / 八度折叠 / 整曲移调寻优）
    engine     演奏时序引擎（帧对齐余量 / 单声部槽位排布 / 修饰键差分 / 等待闸门）
    practice   跟弹判定（按对没有、准不准、难度估计；纯逻辑）
    sender     SendInput 扫描码级键鼠注入
    hotkeys    GetAsyncKeyState 全局热键 + 跟弹时读玩家按键
    preview    MCI 本地试听
    jianpu     简谱文本解析
    exporter   CSV / Lua / 键位表导出
    online     在线曲库（只读：Dr-hydra 公开曲库，DFHS1 谱面解码）

最简用法（直接用曲库里的曲子演奏）::

    from kouqin import TuneLibrary, LIBRARY_DIR, AppSettings, Player, compile_notes

    settings = AppSettings.load()
    library = TuneLibrary(LIBRARY_DIR)
    tune = library.load("小星星")
    events, report = compile_notes(tune.to_notes(), settings.to_layout(),
                                   settings.timing_preset())
    player = Player()
    player.load(events)
    player.play(countdown_ms=3000)
"""

from .tune import (
    Cell,
    HOLD,
    REST,
    PRESET_TUNES,
    Tune,
    TuneLibrary,
    notes_to_tune,
    OCTAVE_COLORS,
    OCTAVE_LABELS,
    OCTAVE_ORDER,
    STEP_DIGITS,
    STEP_NAMES,
)
from .settings import AppSettings, DATA_DIR, LIBRARY_DIR, ROOT_DIR, settings_path
from .smf import (
    Song,
    Track,
    Note,
    MidiError,
    read_midi,
    parse_midi,
    write_midi,
    pitch_name,
    name_to_pitch,
)
from .layout import (
    Layout,
    Combo,
    MouseMod,
    FoldMode,
    DEFAULT_LAYOUT,
    BASE_KEYS,
    SCALE,
    MOD_FEATURE_LABELS,
)
from .engine import (
    Player,
    Event,
    Slot,
    NullSender,
    Timing,
    PlayState,
    CompileReport,
    TIMING_PRESETS,
    compile_notes,
    FRAME_MS,
)
from .practice import Judge, Verdict, difficulty
from .sender import Sender, SCANCODES
from .hotkeys import GlobalHotkeys, KeyWatcher, VK
from .preview import Preview
from .jianpu import parse_jianpu, JianpuScore, JIANPU_TEMPLATE
from .exporter import (
    export_csv,
    export_json,
    export_keytable_csv,
    export_keytable_md,
    export_lua,
)

__version__ = "2.6.0"

__all__ = [
    # 曲子与曲库
    "Cell", "REST", "HOLD", "Tune", "TuneLibrary", "PRESET_TUNES", "notes_to_tune",
    "OCTAVE_COLORS", "OCTAVE_LABELS", "OCTAVE_ORDER", "STEP_DIGITS", "STEP_NAMES",
    # 配置
    "AppSettings", "DATA_DIR", "LIBRARY_DIR", "ROOT_DIR", "settings_path",
    # MIDI
    "Song", "Track", "Note", "MidiError",
    "read_midi", "parse_midi", "write_midi", "pitch_name", "name_to_pitch",
    # 键位
    "Layout", "Combo", "MouseMod", "FoldMode", "DEFAULT_LAYOUT",
    "BASE_KEYS", "SCALE", "MOD_FEATURE_LABELS",
    # 演奏
    "Player", "Event", "Slot", "NullSender", "Timing", "PlayState", "CompileReport",
    "TIMING_PRESETS", "compile_notes", "FRAME_MS",
    # 跟弹
    "Judge", "Verdict", "difficulty",
    # 底层
    "Sender", "SCANCODES", "GlobalHotkeys", "KeyWatcher", "VK", "Preview",
    # 简谱与导出
    "parse_jianpu", "JianpuScore", "JIANPU_TEMPLATE",
    "export_csv", "export_json", "export_keytable_csv", "export_keytable_md", "export_lua",
    "__version__",
]
