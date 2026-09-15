"""
曲子模型与本地曲库。

面向"不懂乐理也能用"的场景，这里把一首曲子简化成一张**格子表**：

    每小节固定几拍（默认 4 拍），每一拍就是一个格子，
    格子里填一个音（do re mi fa sol la si 之一，带高八度/低八度），
    或者填「休止」表示这一拍不吹。

不需要知道时值、小节线、调号——想哪里响就点哪里。
连续两个格子填同一个音时会自动拼成一个长音，所以长音也不用专门去设。

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
    """一拍的内容。"""

    step: int = -1        # 0..6 对应 do..si；-1 表示休止
    octave: int = 0       # -1 低音 / 0 中音 / 1 高音 / 2 更高

    # ---------------- 属性 ----------------

    @property
    def is_rest(self) -> bool:
        return self.step < 0

    @property
    def pitch(self) -> int:
        """对应的 MIDI 音高（休止符返回 0，调用方应先查 is_rest）。"""
        if self.is_rest:
            return 0
        return 60 + self.octave * 12 + STEP_SEMITONE[self.step]

    @property
    def digit(self) -> str:
        """格子里显示的大字。"""
        return "休" if self.is_rest else STEP_DIGITS[self.step]

    @property
    def solfa(self) -> str:
        """唱名。"""
        return "休止" if self.is_rest else STEP_NAMES[self.step]

    @property
    def octave_label(self) -> str:
        return "" if self.is_rest else OCTAVE_LABELS.get(self.octave, "")

    def describe(self) -> str:
        if self.is_rest:
            return "休止"
        return f"{OCTAVE_LABELS.get(self.octave, '')} {self.solfa}"

    # ---------------- 序列化 ----------------

    def to_json(self):
        return None if self.is_rest else [self.step, self.octave]

    @staticmethod
    def from_json(raw) -> "Cell":
        if raw is None:
            return REST
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            step = int(raw[0])
            octave = int(raw[1])
            if 0 <= step <= 6 and octave in OCTAVE_LABELS:
                return Cell(step, octave)
        return REST


#: 休止符常量
REST = Cell(-1, 0)


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
            if not cell.is_rest:
                last = i
        keep = max(self.beats_per_bar * keep_bars, ((last // self.beats_per_bar) + 1) * self.beats_per_bar)
        if keep < len(self.cells):
            del self.cells[keep:]

    def is_empty(self) -> bool:
        return all(cell.is_rest for cell in self.cells)

    def note_count(self) -> int:
        return sum(1 for c in self.cells if not c.is_rest)

    def duration_ms(self) -> float:
        return len(self.cells) * (60_000.0 / max(1.0, self.bpm))

    # ---------------- 转成引擎吃的音符 ----------------

    def to_notes(self) -> List[Note]:
        """
        转成音符表。

        相邻的相同格子会自动粘成一个长音——这样用户想表达长音，
        只要连着点两格同一个音就行，不用学任何时值记号。
        """
        unit = 60_000.0 / max(1.0, self.bpm)
        notes: List[Note] = []
        i = 0
        while i < len(self.cells):
            cell = self.cells[i]
            if cell.is_rest:
                i += 1
                continue
            j = i + 1
            while j < len(self.cells) and self.cells[j] == cell:
                j += 1
            notes.append(Note(start=i * unit, duration=(j - i) * unit, pitch=cell.pitch))
            i = j
        return notes

    # ---------------- 序列化 ----------------

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "name": self.name,
            "bpm": round(float(self.bpm), 2),
            "beats_per_bar": self.beats_per_bar,
            "cells": [c.to_json() for c in self.cells],
        }

    @staticmethod
    def from_dict(data: dict) -> "Tune":
        bpb = int(data.get("beats_per_bar", 4) or 4)
        bpb = bpb if 1 <= bpb <= 12 else 4
        return Tune(
            name=str(data.get("name", "未命名")),
            bpm=float(data.get("bpm", 90.0) or 90.0),
            beats_per_bar=bpb,
            cells=[Cell.from_json(raw) for raw in data.get("cells", [])],
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


def notes_to_tune(notes: Sequence[Note], name: str, bpm: float = 90.0,
                  beats_per_bar: int = 4, max_bars: int = 96) -> Tune:
    """
    把一列音符量化成格子谱。

    用途：把任意 MIDI 旋律转成"点格子"格式，存进曲库后就能和其他曲子一样点播。
    做法是把每个音的起点四舍五入到最近的拍，再按占用的拍数填满格子；
    遇到重叠（原曲有和弦）时，后来的音会盖掉前一个——反正口琴也只能吹单音。
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
                tune.cells[index] = cell

    tune.trim_trailing_rests(keep_bars=1)
    return tune


# --------------------------------------------------------------------------- #
# 内置曲目
# --------------------------------------------------------------------------- #

#: 简谱字符串 -> 格子。语法和「谱曲」页一样：数字是音，' 升八度，, 降八度，0 或 - 是休止。
#: 每首曲子都按"一拍一格"重排过，所以不会出现半拍、附点这些让人挠头的东西。


def _parse_grid(text: str, octave: int = 0) -> List[Cell]:
    """
    把紧凑的格子记号解析成 Cell 列表。

    记号： ``1``~``7`` 音级，``'`` 升一个八度，``,`` 降一个八度，
    ``0`` / ``-`` / ``x`` 表示休止，``|`` 只是视觉分隔会被忽略。
    """
    cells: List[Cell] = []
    for token in text.replace("|", " ").split():
        token = token.strip()
        if not token:
            continue
        if token[0] in "0-x.":
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


PRESET_TUNES: List[Tune] = [
    _tune("小星星", 100, 4,
          "1 1 5 5 | 6 6 5 5 | 4 4 3 3 | 2 2 1 1 |"
          "5 5 4 4 | 3 3 2 2 | 5 5 4 4 | 3 3 2 2 |"
          "1 1 5 5 | 6 6 5 5 | 4 4 3 3 | 2 2 1 0 |"),

    _tune("两只老虎", 108, 4,
          "1 2 3 1 | 1 2 3 1 | 3 4 5 5 | 3 4 5 5 |"
          "5 6 5 4 | 3 1 3 1 | 5 6 5 4 | 3 1 3 1 |"
          "2 5 1 1 | 2 5 1 0 |"),

    _tune("生日快乐", 100, 3,
          "5 5 6 | 5 1' 7 | 5 5 6 | 5 2' 1' |"
          "5 5 5' | 3' 1' 7 | 6 4' 3' | 2' 1' 1' |"),

    _tune("欢乐颂", 112, 4,
          "3 3 4 5 | 5 4 3 2 | 1 1 2 3 | 3 3 2 2 |"
          "3 3 4 5 | 5 4 3 2 | 1 1 2 3 | 2 2 1 0 |"),

    _tune("世上只有妈妈好", 84, 4,
          "6 5 3 3 | 5 6 1' 1' | 5 3 2 2 | 2 3 5 5 |"
          "6 5 3 3 | 5 6 1' 1' | 5 3 2 2 | 1 1 1 0 |"),

    _tune("送别", 76, 4,
          "5 3 5 1' | 7 1' 7 6 | 5 3 5 1' | 7 1' 2' 2' |"
          "5 3 5 1' | 7 1' 7 6 | 5 3 5 2' | 1' 1' 1' 0 |"),

    _tune("玛丽有只小羊羔", 96, 4,
          "3 2 1 2 | 3 3 3 0 | 2 2 2 0 | 3 5 5 0 |"
          "3 2 1 2 | 3 3 3 3 | 2 2 3 2 | 1 0 0 0 |"),

    _tune("划船歌", 92, 4,
          "5 5 5 3 | 5 5 5 3 | 5 3 5 3 | 2 2 2 0 |"
          "5 5 5 3 | 5 5 5 3 | 5 3 5 3 | 2 2 1 0 |"),
]
