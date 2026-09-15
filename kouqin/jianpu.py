"""
简谱文本解析。

这是本工具相对"只认 MIDI"方案的补充：很多时候想吹的就是一小段旋律，
专门去做一个 MIDI 文件反而麻烦。这里定义了一套可直接手写的简谱语法：

    音级      1 2 3 4 5 6 7        唱名 do re mi fa sol la si
              0                    休止符
    变化音    #4   升 fa            b7   降 si
    八度      '    高八度（可叠写 ''）
              ,    低八度（可叠写 ,, ）
    时值      /    减半（'' 缺省=四分音符）
              //   再减半
              .    附点（×1.5）
    延长线    -    独立写，给前一个音再加一个单位
    小节线    |    仅作分隔，不发音
    绝对音高  [60] 直接写 MIDI 音高，方便精确控制
    速度      T=90 设置 BPM
    注释      ; 开头一直到行尾

示例：

    T=96
    5 5 6 5 | 1' 7 - - | 6 6 5 4 | 3 - - - |

这套写法没有强行兼容任何现有的简谱方言，而是以"键盘上敲得顺手、
屏幕上一眼看得懂"为原则设计的。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .smf import NOTE_NAMES, Note, name_to_pitch

__all__ = ["JianpuScore", "parse_jianpu", "JIANPU_TEMPLATE"]

#: 唱名 1..7 对应的半音偏移
_DEGREE_SEMITONE = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 11}

#: 单个音符记号的正则
_NOTE_RE = re.compile(r"^(?P<acc>[#b♯♭]?)(?P<degree>[0-7])(?P<oct>[',]*)(?P<rest>[/.]*)$")

#: 绝对音高 [60]
_ABS_RE = re.compile(r"^\[(\d{1,3})\]$")

#: 速度设置
_TEMPO_RE = re.compile(r"^(?:T|BPM)\s*=\s*(\d{1,3}(?:\.\d+)?)$", re.IGNORECASE)

#: 科学音名 C4 / F#5
_NAME_RE = re.compile(r"^[A-Ga-g][#b]?-?\d$")


@dataclass
class JianpuScore:
    """解析结果。"""

    notes: List[Note] = field(default_factory=list)
    bpm: float = 90.0
    warnings: List[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.notes)


#: 界面里「新建简谱」时的模板
JIANPU_TEMPLATE = """; 《口琴工坊》简谱模板
; 1 2 3 4 5 6 7 = do re mi fa sol la si ，0 = 休止
; ' 升八度   , 降八度   / 时值减半   . 附点   - 延长一米   | 小节线
; T=90 设置速度(BPM)

T=90
5 5 6 5 | 1' 7 - - | 6 6 5 4 | 3 - - - |
2 2 3 1 | 5 5 4 - | 3 3 2 1 | 1 - - - |
"""


def parse_jianpu(text: str, bpm: float = 90.0) -> JianpuScore:
    """
    解析简谱文本。

    :param text: 谱面文本
    :param bpm:  默认速度，可被文本里的 ``T=`` 覆盖
    :returns: :class:`JianpuScore`
    """
    score = JianpuScore(bpm=float(bpm))

    # 逐行处理：先去注释，再切 token
    tokens: List[Tuple[str, int]] = []       # (token, 行号)
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split(";", 1)[0]
        for token in line.replace("|", " | ").split():
            if token != "|":
                tokens.append((token, lineno))

    if not tokens:
        return score

    unit_ms = 60_000.0 / score.bpm           # 一个单位（四分音符）的毫秒数
    cursor = 0.0
    last_index: Optional[int] = None         # 上一个已生成的音符下标，供延长线用

    for token, lineno in tokens:
        # --- 速度 ---
        tempo_match = _TEMPO_RE.match(token)
        if tempo_match:
            value = float(tempo_match.group(1))
            if 20 <= value <= 400:
                score.bpm = value
                unit_ms = 60_000.0 / value
            else:
                score.warnings.append(f"第 {lineno} 行：速度 {value} 超出合理范围，已忽略")
            continue

        # --- 延长线 ---
        if token == "-":
            if last_index is None:
                score.warnings.append(f"第 {lineno} 行：延长线前面没有音符，已忽略")
            else:
                score.notes[last_index].duration += unit_ms
                cursor += unit_ms
            continue

        # --- 绝对音高 [60] ---
        abs_match = _ABS_RE.match(token)
        if abs_match:
            pitch = int(abs_match.group(1))
            if 0 <= pitch <= 127:
                score.notes.append(Note(cursor, unit_ms, pitch))
                cursor += unit_ms
                last_index = len(score.notes) - 1
            else:
                score.warnings.append(f"第 {lineno} 行：音高 {pitch} 越界")
            continue

        # --- 科学音名 C4 / F#5 ---
        if _NAME_RE.match(token):
            pitch = name_to_pitch(token)
            if pitch is not None:
                score.notes.append(Note(cursor, unit_ms, pitch))
                cursor += unit_ms
                last_index = len(score.notes) - 1
                continue

        # --- 简谱音符 ---
        note_match = _NOTE_RE.match(token)
        if not note_match:
            score.warnings.append(f"第 {lineno} 行：无法识别的记法「{token}」")
            continue

        degree = int(note_match.group("degree"))
        octave_marks = note_match.group("oct")
        rest_marks = note_match.group("rest")

        # 时值：默认一个单位，/ 减半，. 附点
        duration = unit_ms
        for ch in rest_marks:
            if ch == "/":
                duration /= 2.0
            elif ch == ".":
                duration *= 1.5

        if degree == 0:
            # 休止：只推进时间，不产生音符
            cursor += duration
            last_index = None
            continue

        shift = sum(12 if c == "'" else -12 for c in octave_marks)
        accidental = 0
        if note_match.group("acc") in ("#", "♯"):
            accidental = 1
        elif note_match.group("acc") in ("b", "♭"):
            accidental = -1

        # 以中音区为基准：1 -> C4
        pitch = 60 + _DEGREE_SEMITONE[degree] + accidental + shift
        if not 0 <= pitch <= 127:
            score.warnings.append(f"第 {lineno} 行：音高越界，已忽略「{token}」")
            continue

        score.notes.append(Note(cursor, duration, pitch))
        cursor += duration
        last_index = len(score.notes) - 1

    return score


def describe(score: JianpuScore) -> str:
    """给出一句话摘要，界面上显示用。"""
    if not score.notes:
        return "谱面为空"
    lo = min(n.pitch for n in score.notes)
    hi = max(n.pitch for n in score.notes)
    length = max(n.end for n in score.notes) / 1000.0
    return (
        f"{len(score.notes)} 个音，音域 {NOTE_NAMES[lo % 12]}{lo // 12 - 1} ~ "
        f"{NOTE_NAMES[hi % 12]}{hi // 12 - 1}，时长 {length:.1f}s，速度 {score.bpm:.0f} BPM"
    )
