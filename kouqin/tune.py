"""
曲子模型与本地曲库。

面向"不懂乐理也能用"的场景，这里把一首曲子简化成一张**格子表**：

    每小节固定几拍（默认 4 拍），每一拍就是一个格子，
    格子里填一个音（do re mi fa sol la si 之一，带高八度/低八度），
    或者填「休止」表示这一拍不吹。

不需要知道时值、小节线、调号——想哪里响就点哪里。
想让一个音拖长，就在后面的格子里填「延长 —」（和简谱里的 "-" 一个意思）；
连续两个格子填同一个音，会吹两下。

（修改版改动：原来"连续两格同一个音"会自动拼成长音，结果真正需要重复吹的音
——《小星星》开头的 do do、《欢乐颂》开头的 mi mi——全被吞掉，曲子听着断断续续。
旧版存的曲谱读进来时会自动换算成「延长」，听起来和以前一模一样。）

一首曲子就是一个 JSON 文件，放在「数据/曲库」下，可以直接复制给别人。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .smf import Note

__all__ = [
    "Cell",
    "Tune",
    "TuneLibrary",
    "REST",
    "HOLD",
    "FORMAT_VERSION",
    "PRESET_REVISION",
    "STEP_DIGITS",
    "STEP_NAMES",
    "OCTAVE_LABELS",
    "OCTAVE_ORDER",
    "PRESET_TUNES",
]

# --------------------------------------------------------------------------- #
# 音级定义
# --------------------------------------------------------------------------- #

#: 唱名
STEP_NAMES: Tuple[str, ...] = ("do", "re", "mi", "fa", "sol", "la", "si")

#: 简谱数字
STEP_DIGITS: Tuple[str, ...] = ("1", "2", "3", "4", "5", "6", "7")

#: 自然音阶半音偏移
STEP_SEMITONE: Tuple[int, ...] = (0, 2, 4, 5, 7, 9, 11)

#: 八度档位（用户看得懂的说法，不出现"八度"这个词）
OCTAVE_LABELS: Dict[int, str] = {
    -1: "低音",
    0: "中音",
    1: "高音",
    2: "更高",
}

#: 八度档位的排列顺序（从低到高）
OCTAVE_ORDER: Tuple[int, ...] = (-1, 0, 1, 2)

#: 各八度档位在界面上的主题色
OCTAVE_COLORS: Dict[int, Tuple[str, str]] = {
    -1: ("#1d3a4d", "#79c7ef"),   # 低音：蓝
    0: ("#1c4034", "#5fe0ab"),    # 中音：绿
    1: ("#4a3a1c", "#f0b95c"),    # 高音：橙
    2: ("#3d2447", "#c98cf0"),    # 更高：紫
}


@dataclass(frozen=True)
class Cell:
    """一拍的内容：一个音、休止，或者「延长」（接着吹前一个音）。"""

    step: int = -1        # 0..6 对应 do..si；-1 休止；-2 延长
    octave: int = 0       # -1 低音 / 0 中音 / 1 高音 / 2 更高

    # ---------------- 属性 ----------------

    @property
    def is_rest(self) -> bool:
        return self.step == -1

    @property
    def is_hold(self) -> bool:
        """「延长」：这一拍不换音，前一个音接着吹。"""
        return self.step == -2

    @property
    def is_note(self) -> bool:
        return self.step >= 0

    @property
    def pitch(self) -> int:
        """对应的 MIDI 音高（休止 / 延长返回 0，调用方应先查 is_note）。"""
        if not self.is_note:
            return 0
        return 60 + self.octave * 12 + STEP_SEMITONE[self.step]

    @property
    def digit(self) -> str:
        """格子里显示的大字。"""
        if self.is_hold:
            return "—"
        return "休" if self.is_rest else STEP_DIGITS[self.step]

    @property
    def solfa(self) -> str:
        """唱名。"""
        if self.is_hold:
            return "延长"
        return "休止" if self.is_rest else STEP_NAMES[self.step]

    @property
    def octave_label(self) -> str:
        return OCTAVE_LABELS.get(self.octave, "") if self.is_note else ""

    def describe(self) -> str:
        if self.is_hold:
            return "延长上一个音"
        if self.is_rest:
            return "休止"
        return f"{OCTAVE_LABELS.get(self.octave, '')} {self.solfa}"

    # ---------------- 序列化 ----------------

    def to_json(self):
        if self.is_hold:
            return "-"
        return None if self.is_rest else [self.step, self.octave]

    @staticmethod
    def from_json(raw) -> "Cell":
        if raw is None:
            return REST
        if raw == "-":
            return HOLD
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            step = int(raw[0])
            octave = int(raw[1])
            if 0 <= step <= 6 and octave in OCTAVE_LABELS:
                return Cell(step, octave)
        return REST


#: 休止符常量
REST = Cell(-1, 0)

#: 延长（接着吹前一个音）
HOLD = Cell(-2, 0)

#: 曲谱文件格式版本。1 = 旧版（连续同音自动拼成长音）；2 = 有「延长」记号，同音重复就吹两下
FORMAT_VERSION = 2


def legacy_to_holds(cells: Sequence[Cell]) -> List[Cell]:
    """
    旧版曲谱 → 新版：把"连续同音"换成「音 + 延长」。

    换算后吹出来和旧版完全一样，用户以前编的曲子不会因为升级而变调。
    """
    result: List[Cell] = []
    previous: Optional[Cell] = None
    for cell in cells:
        result.append(HOLD if (cell.is_note and cell == previous) else cell)
        previous = cell
    return result


# --------------------------------------------------------------------------- #
# 曲子
# --------------------------------------------------------------------------- #


@dataclass
class Tune:
    """一首曲子 = 曲名 + 速度 + 每小节拍数 + 一串格子。"""

    name: str = "未命名"
    bpm: float = 90.0
    beats_per_bar: int = 4
    cells: List[Cell] = field(default_factory=list)

    # ---------------- 基本操作 ----------------

    @property
    def length(self) -> int:
        return len(self.cells)

    @property
    def bar_count(self) -> int:
        return (len(self.cells) + self.beats_per_bar - 1) // self.beats_per_bar

    def bars(self) -> List[List[Cell]]:
        """按小节切分，最后一小节不足的用休止补齐。"""
        result: List[List[Cell]] = []
        for start in range(0, len(self.cells), self.beats_per_bar):
            chunk = self.cells[start:start + self.beats_per_bar]
            while len(chunk) < self.beats_per_bar:
                chunk.append(REST)
            result.append(chunk)
        return result

    def add_bars(self, count: int = 1) -> None:
        self.cells.extend([REST] * (self.beats_per_bar * count))

    def ensure_length(self, beats: int) -> None:
        """把格子数补齐到整小节。"""
        while len(self.cells) % self.beats_per_bar != 0 or len(self.cells) < beats:
            self.cells.append(REST)

    def set_cell(self, index: int, cell: Cell) -> None:
        if 0 <= index < len(self.cells):
            self.cells[index] = cell

    def trim_trailing_rests(self, keep_bars: int = 1) -> None:
        """删掉尾部多余的空小节，但至少留 keep_bars 个小节。"""
        last = -1
        for i, cell in enumerate(self.cells):
            if not cell.is_rest:            # 延长也算有内容：它是前一个音的一部分
                last = i
        keep = max(self.beats_per_bar * keep_bars, ((last // self.beats_per_bar) + 1) * self.beats_per_bar)
        if keep < len(self.cells):
            del self.cells[keep:]

    def is_empty(self) -> bool:
        return not any(cell.is_note for cell in self.cells)

    def note_count(self) -> int:
        """一共要吹几下（延长不算新的一下）。"""
        return sum(1 for c in self.cells if c.is_note)

    def duration_ms(self) -> float:
        return len(self.cells) * (60_000.0 / max(1.0, self.bpm))

    # ---------------- 转成引擎吃的音符 ----------------

    def to_notes(self) -> List[Note]:
        """
        转成音符表。

        每个音格都是新吹的一下；后面跟着的「延长」格子把它拖长。
        前面没有音可接的「延长」（开头、休止之后）当休止处理。
        """
        unit = 60_000.0 / max(1.0, self.bpm)
        notes: List[Note] = []
        i = 0
        while i < len(self.cells):
            cell = self.cells[i]
            if not cell.is_note:
                i += 1
                continue
            j = i + 1
            while j < len(self.cells) and self.cells[j].is_hold:
                j += 1
            notes.append(Note(start=i * unit, duration=(j - i) * unit, pitch=cell.pitch))
            i = j
        return notes

    # ---------------- 序列化 ----------------

    def to_dict(self) -> dict:
        return {
            "version": FORMAT_VERSION,
            "name": self.name,
            "bpm": round(float(self.bpm), 2),
            "beats_per_bar": self.beats_per_bar,
            "cells": [c.to_json() for c in self.cells],
        }

    @staticmethod
    def from_dict(data: dict) -> "Tune":
        bpb = int(data.get("beats_per_bar", 4) or 4)
        bpb = bpb if 1 <= bpb <= 12 else 4
        cells = [Cell.from_json(raw) for raw in data.get("cells", [])]
        try:
            version = int(data.get("version", 1) or 1)
        except (TypeError, ValueError):
            version = 1
        if version < 2:
            cells = legacy_to_holds(cells)
        return Tune(
            name=str(data.get("name", "未命名")),
            bpm=float(data.get("bpm", 90.0) or 90.0),
            beats_per_bar=bpb,
            cells=cells,
        )

    def copy(self, new_name: Optional[str] = None) -> "Tune":
        return Tune(
            name=new_name if new_name is not None else self.name,
            bpm=self.bpm,
            beats_per_bar=self.beats_per_bar,
            cells=list(self.cells),
        )


# --------------------------------------------------------------------------- #
# 本地曲库
# --------------------------------------------------------------------------- #

_ILLEGAL = re.compile(r'[\\/:*?"<>|]')


class TuneLibrary:
    """一个文件夹就是一个曲库。"""

    def __init__(self, folder: str) -> None:
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

    # ---------------- 路径 ----------------

    @staticmethod
    def safe_name(name: str) -> str:
        cleaned = _ILLEGAL.sub("_", name.strip()) or "未命名"
        return cleaned[:60]

    def path_of(self, name: str) -> str:
        return os.path.join(self.folder, self.safe_name(name) + ".json")

    def exists(self, name: str) -> bool:
        return os.path.exists(self.path_of(name))

    # ---------------- 读写 ----------------

    def names(self) -> List[str]:
        """列出曲库里所有曲名，按文件修改时间倒序（新加的排前面）。"""
        items: List[Tuple[float, str]] = []
        for entry in os.listdir(self.folder):
            if not entry.lower().endswith(".json"):
                continue
            path = os.path.join(self.folder, entry)
            try:
                items.append((os.path.getmtime(path), os.path.splitext(entry)[0]))
            except OSError:
                continue
        items.sort(key=lambda x: -x[0])
        return [name for _, name in items]

    def load(self, name: str) -> Optional[Tune]:
        path = self.path_of(name)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, json.JSONDecodeError):
            return None
        tune = Tune.from_dict(data)
        if not tune.name:
            tune.name = name
        return tune

    def save(self, tune: Tune) -> str:
        path = self.path_of(tune.name)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(tune.to_dict(), fp, ensure_ascii=False, indent=1)
        return path

    def delete(self, name: str) -> bool:
        path = self.path_of(name)
        if os.path.exists(path):
            try:
                os.remove(path)
                return True
            except OSError:
                return False
        return False

    def rename(self, old: str, new: str) -> bool:
        if self.exists(new) or not self.exists(old):
            return False
        try:
            os.rename(self.path_of(old), self.path_of(new))
            return True
        except OSError:
            return False

    def import_file(self, path: str, new_name: Optional[str] = None) -> Optional[Tune]:
        """从外部 JSON 导入一首曲子。"""
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, json.JSONDecodeError):
            return None
        tune = Tune.from_dict(data)
        if new_name:
            tune.name = new_name
        elif not tune.name:
            tune.name = os.path.splitext(os.path.basename(path))[0]
        # 重名时加后缀
        base = tune.name
        index = 2
        while self.exists(tune.name):
            tune.name = f"{base} ({index})"
            index += 1
        self.save(tune)
        return tune

    def install_presets(self, overwrite: bool = False) -> int:
        """把内置曲目铺进曲库，返回新增数量。"""
        count = 0
        for tune in PRESET_TUNES:
            if overwrite or not self.exists(tune.name):
                self.save(tune.copy())
                count += 1
        return count

    def upgrade_presets(self) -> List[str]:
        """
        把曲库里「没改过的旧版内置曲目」换成新版，返回换掉的曲名。

        旧版内置曲谱按"连续同音 = 长音"写，重复的音（do do、mi mi）被拼成了长音。
        只有和旧版内置曲谱一模一样的副本才会被换掉；用户改过的、删掉的都不动。
        """
        replaced: List[str] = []
        for tune in PRESET_TUNES:
            legacy = _LEGACY_PRESETS.get(tune.name)
            path = self.path_of(tune.name)
            if legacy is None or not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict) or int(data.get("version", 1) or 1) >= FORMAT_VERSION:
                continue
            old = Tune.from_dict(data)
            if (old.cells == legacy.cells and old.beats_per_bar == legacy.beats_per_bar
                    and abs(old.bpm - legacy.bpm) < 0.01):
                self.save(tune.copy())
                replaced.append(tune.name)
        return replaced


def notes_to_tune(notes: Sequence[Note], name: str, bpm: float = 90.0,
                  beats_per_bar: int = 4, max_bars: int = 96) -> Tune:
    """
    把一列音符量化成格子谱。

    用途：把任意 MIDI 旋律转成"点格子"格式，存进曲库后就能和其他曲子一样点播。
    做法是把每个音的起点四舍五入到最近的拍，起点那一格填音，后面占用的拍填「延长」；
    遇到重叠（原曲有和弦）时，后来的音会盖掉前一个——反正口琴也只能吹单音。
    原曲里同一个音连着吹几下，量化后也还是几下（不会被拼成一个长音）。
    """
    bpm = max(20.0, min(400.0, float(bpm)))
    unit = 60_000.0 / bpm
    beats_per_bar = max(1, min(12, int(beats_per_bar)))
    limit = max_bars * beats_per_bar

    tune = Tune(name=name, bpm=bpm, beats_per_bar=beats_per_bar)
    tune.cells = [REST] * limit

    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))
    for note in ordered:
        step = round((note.pitch - 60) % 12)
        if step not in STEP_SEMITONE:
            # 变化音（比如 #4）在格子模型里表达不了，就近落到自然音上
            step = min(STEP_SEMITONE, key=lambda s: abs(s - step))
        octave = (note.pitch - 60 - step) // 12
        octave = max(-1, min(2, octave))
        cell = Cell(STEP_SEMITONE.index(step), octave)

        start_beat = int(round(note.start / unit))
        span = max(1, int(round(note.duration / unit)))
        for offset in range(span):
            index = start_beat + offset
            if 0 <= index < limit:
                tune.cells[index] = cell if offset == 0 else HOLD

    tune.trim_trailing_rests(keep_bars=1)
    return tune


# --------------------------------------------------------------------------- #
# 内置曲目
# --------------------------------------------------------------------------- #

#: 简谱字符串 -> 格子：数字是音，' 升八度，, 降八度，- 是延长（接着吹前一个音），0 是休止。
#: 每首曲子都按"一拍一格"重排过，所以不会出现半拍、附点这些让人挠头的东西
#: （原曲里的八分音符按一拍记，旋律的先后顺序不变）。


def _parse_grid(text: str, octave: int = 0) -> List[Cell]:
    """
    把紧凑的格子记号解析成 Cell 列表。

    记号： ``1``~``7`` 音级，``'`` 升一个八度，``,`` 降一个八度，
    ``-`` 延长前一个音，``0`` / ``x`` / ``.`` 表示休止，``|`` 只是视觉分隔会被忽略。
    """
    cells: List[Cell] = []
    for token in text.replace("|", " ").split():
        token = token.strip()
        if not token:
            continue
        if token[0] == "-":
            cells.append(HOLD)
            continue
        if token[0] in "0x.":
            cells.append(REST)
            continue
        if token[0] not in "1234567":
            continue
        step = int(token[0]) - 1
        level = octave
        for char in token[1:]:
            if char == "'":
                level += 1
            elif char == ",":
                level -= 1
        level = max(-1, min(2, level))
        cells.append(Cell(step, level))
    return cells


def _tune(name: str, bpm: float, beats: int, grid: str) -> Tune:
    cells = _parse_grid(grid)
    # 补到整小节
    while len(cells) % beats != 0:
        cells.append(REST)
    return Tune(name=name, bpm=bpm, beats_per_bar=beats, cells=cells)


#: 内置曲目的版本：曲谱改动后 +1，用户曲库里没改过的旧副本会在启动时换成新版
PRESET_REVISION = 2

PRESET_TUNES: List[Tune] = [
    _tune("小星星", 100, 4,
          "1 1 5 5 | 6 6 5 - | 4 4 3 3 | 2 2 1 - |"
          "5 5 4 4 | 3 3 2 - | 5 5 4 4 | 3 3 2 - |"
          "1 1 5 5 | 6 6 5 - | 4 4 3 3 | 2 2 1 - |"),

    _tune("两只老虎", 108, 4,
          "1 2 3 1 | 1 2 3 1 | 3 4 5 - | 3 4 5 - |"
          "5 6 5 4 | 3 - 1 - | 5 6 5 4 | 3 - 1 - |"
          "1 5, 1 - | 1 5, 1 - |"),

    _tune("生日快乐", 100, 3,
          "5 5 6 | 5 1' 7 | - 5 5 | 6 5 2' |"
          "1' - 5 | 5 5' 3' | 1' 7 6 | 4' 4' 3' |"
          "1' 2' 1' | - - 0 |"),

    _tune("欢乐颂", 112, 4,
          "3 3 4 5 | 5 4 3 2 | 1 1 2 3 | 3 2 2 - |"
          "3 3 4 5 | 5 4 3 2 | 1 1 2 3 | 2 1 1 - |"),

    # 这一首保持原作者的编排不变（只是把"连续同音 = 长音"改写成明确的「延长」，听起来和以前一样）
    _tune("世上只有妈妈好", 84, 4,
          "6 5 3 - | 5 6 1' - | 5 3 2 - | - 3 5 - |"
          "6 5 3 - | 5 6 1' - | 5 3 2 - | 1 - - 0 |"),

    _tune("送别", 76, 4,
          "5 3 5 1' | - 6 1' 5 | - 5 1 2 | 3 2 1 2 | - - 0 "
          "5 3 5 1' 7 | 6 1' 5 - | 5 2 3 4 7, | 1 - - 0 |"
          "6 1' 1' - | 7 6 7 1' | - 6 7 1' | 6 6 5 3 1 | 2 - - 0 |"
          "5 3 5 1' 7 | 6 1' 5 - | 5 2 3 4 7, | 1 - - 0 |"),

    _tune("玛丽有只小羊羔", 96, 4,
          "3 2 1 2 | 3 3 3 - | 2 2 2 - | 3 5 5 - |"
          "3 2 1 2 | 3 3 3 3 | 2 2 3 2 | 1 - - 0 |"),

    # 这一首保持原作者的编排不变（同上）
    _tune("划船歌", 92, 4,
          "5 - - 3 | 5 - - 3 | 5 3 5 3 | 2 - - 0 |"
          "5 - - 3 | 5 - - 3 | 5 3 5 3 | 2 - 1 0 |"),
]


def _legacy_tune(name: str, bpm: float, beats: int, grid: str) -> Tune:
    """按旧版规则（连续同音 = 长音）解析的旧内置曲谱，只用来认出用户曲库里没改过的旧副本。"""
    tune = _tune(name, bpm, beats, grid)
    tune.cells = legacy_to_holds(tune.cells)
    return tune


#: v2.1.0 及以前的内置曲谱（原样保留，见 TuneLibrary.upgrade_presets）
_LEGACY_PRESETS: Dict[str, Tune] = {t.name: t for t in (
    _legacy_tune("小星星", 100, 4,
                 "1 1 5 5 | 6 6 5 5 | 4 4 3 3 | 2 2 1 1 |"
                 "5 5 4 4 | 3 3 2 2 | 5 5 4 4 | 3 3 2 2 |"
                 "1 1 5 5 | 6 6 5 5 | 4 4 3 3 | 2 2 1 0 |"),
    _legacy_tune("两只老虎", 108, 4,
                 "1 2 3 1 | 1 2 3 1 | 3 4 5 5 | 3 4 5 5 |"
                 "5 6 5 4 | 3 1 3 1 | 5 6 5 4 | 3 1 3 1 |"
                 "2 5 1 1 | 2 5 1 0 |"),
    _legacy_tune("生日快乐", 100, 3,
                 "5 5 6 | 5 1' 7 | 5 5 6 | 5 2' 1' |"
                 "5 5 5' | 3' 1' 7 | 6 4' 3' | 2' 1' 1' |"),
    _legacy_tune("欢乐颂", 112, 4,
                 "3 3 4 5 | 5 4 3 2 | 1 1 2 3 | 3 3 2 2 |"
                 "3 3 4 5 | 5 4 3 2 | 1 1 2 3 | 2 2 1 0 |"),
    _legacy_tune("世上只有妈妈好", 84, 4,
                 "6 5 3 3 | 5 6 1' 1' | 5 3 2 2 | 2 3 5 5 |"
                 "6 5 3 3 | 5 6 1' 1' | 5 3 2 2 | 1 1 1 0 |"),
    _legacy_tune("送别", 76, 4,
                 "5 3 5 1' | 7 1' 7 6 | 5 3 5 1' | 7 1' 2' 2' |"
                 "5 3 5 1' | 7 1' 7 6 | 5 3 5 2' | 1' 1' 1' 0 |"),
    _legacy_tune("玛丽有只小羊羔", 96, 4,
                 "3 2 1 2 | 3 3 3 0 | 2 2 2 0 | 3 5 5 0 |"
                 "3 2 1 2 | 3 3 3 3 | 2 2 3 2 | 1 0 0 0 |"),
    _legacy_tune("划船歌", 92, 4,
                 "5 5 5 3 | 5 5 5 3 | 5 3 5 3 | 2 2 2 0 |"
                 "5 5 5 3 | 5 5 5 3 | 5 3 5 3 | 2 2 1 0 |"),
)}
