"""
跟弹练习：判定玩家自己按的键对不对、准不准。

纯逻辑模块——不碰 Windows API，也不碰界面。
输入只有"在歌曲的第几毫秒按下了哪个音键、当时按着哪些鼠标键"，
输出是每个音的判定结果和统计数字，悬浮窗拿去画就行。

判定规则尽量宽松，目标是帮人学会，不是考试：

    * 按下的时刻离某个音的起点在 ±GOOD_MS 以内，才算"冲着这个音按的"；
      离哪个音都很远的按键直接忽略（玩家可能只是在试键）
    * 音键对、鼠标键也对 → 命中；±PERFECT_MS 以内算「完美」，否则「不错」
    * 按错了不会把这个音判死：判定窗口关闭前改按对的，照样算命中
    * 窗口关闭还没按对 → 漏
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from .layout import MouseMod

__all__ = [
    "PERFECT_MS",
    "GOOD_MS",
    "Mark",
    "Verdict",
    "Judge",
    "difficulty",
    "describe_mods",
]

#: 离音符起点这么近算「完美」（毫秒）
PERFECT_MS = 80.0

#: 判定窗口半宽：超出这个范围的按键不算冲着这个音按的（毫秒）
GOOD_MS = 160.0


class Mark:
    """每个音的判定状态。"""

    PENDING = "pending"
    PERFECT = "perfect"
    GOOD = "good"
    MISS = "miss"

    HIT = (PERFECT, GOOD)


@dataclass(frozen=True)
class Verdict:
    """一次判定，悬浮窗拿它做那一下"完美 / 漏了"的提示。"""

    index: int        # 对应第几个音
    kind: str         # perfect / good / wrong / miss
    offset: float     # 按早了是负数，按晚了是正数（毫秒）
    at: float         # 判定发生时的歌曲时间（毫秒）
    text: str         # 给人看的一句话


def describe_mods(mods: Iterable[str]) -> str:
    """修饰键组合的口语说法，例如「按住右键 + 中键」。"""
    mods = [m for m in MouseMod.ORDER if m in set(mods)]
    if not mods:
        return "不按鼠标"
    return "按住" + " + ".join(MouseMod.LABEL[m].replace("鼠标", "") for m in mods)


class Judge:
    """
    跟弹判定器。

    :param slots: 排布好的音（:class:`kouqin.engine.Slot`，按起点排好序）
    :param lenient: 等待模式下打开——时钟会停在音符上等人，不存在"按早按晚"，
                    按对就算完美
    """

    def __init__(self, slots: Sequence, perfect_ms: float = PERFECT_MS,
                 good_ms: float = GOOD_MS, lenient: bool = False) -> None:
        self.slots = list(slots)
        self.perfect_ms = perfect_ms
        self.good_ms = good_ms
        self.lenient = lenient
        self.marks: List[str] = [Mark.PENDING] * len(self.slots)
        self.offsets: List[float] = [0.0] * len(self.slots)
        self.cursor = 0                  # 第一个还没判定的音
        self.perfect = 0
        self.good = 0
        self.missed = 0
        self.wrong = 0                   # 按错的次数（不计入音符总数）
        self.combo = 0
        self.best_combo = 0
        self.last: Optional[Verdict] = None

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #

    @property
    def total(self) -> int:
        return len(self.slots)

    @property
    def hits(self) -> int:
        return self.perfect + self.good

    @property
    def judged(self) -> int:
        return self.hits + self.missed

    @property
    def accuracy(self) -> float:
        """已判定的音里命中的比例（0~1）。"""
        return self.hits / self.judged if self.judged else 0.0

    def status_text(self) -> str:
        """演奏中的一行小字。"""
        text = f"命中 {self.hits} · 漏 {self.missed}"
        if self.combo >= 3:
            text += f" · 连击 {self.combo}"
        return text

    def summary(self) -> str:
        """结束时的一句总结。"""
        if not self.total:
            return "没有可判定的音"
        percent = self.hits * 100.0 / self.total
        text = f"命中 {self.hits}/{self.total}（{percent:.0f}%）· 完美 {self.perfect} · 最大连击 {self.best_combo}"
        if self.wrong:
            text += f" · 按错 {self.wrong} 次"
        return text

    # ------------------------------------------------------------------ #
    # 判定
    # ------------------------------------------------------------------ #

    def next_pending(self) -> Optional[int]:
        """下一个还没判定的音的序号；全判完了返回 None。"""
        for index in range(self.cursor, len(self.slots)):
            if self.marks[index] == Mark.PENDING:
                return index
        return None

    def press(self, key: str, mods: Iterable[str], t: float) -> Optional[Verdict]:
        """
        玩家在歌曲时间 ``t`` 按下了音键 ``key``，当时按着鼠标键 ``mods``。

        :returns: 这次按键的判定；离所有音都很远时返回 None（不算对也不算错）
        """
        self.sweep(t)
        held = frozenset(mods)

        window: List[int] = []
        for index in range(self.cursor, len(self.slots)):
            start = self.slots[index].start
            if start - self.good_ms > t:
                break
            if self.marks[index] == Mark.PENDING and abs(t - start) <= self.good_ms:
                window.append(index)
        if not window:
            return None

        # 窗口里可能同时有两个音（快歌），优先算给按对的那个
        for index in window:
            combo = self.slots[index].combo
            if combo.key == key and frozenset(combo.mods) == held:
                return self._hit(index, t)

        index = window[0]
        combo = self.slots[index].combo
        if combo.key == key:
            text = "鼠标键不对：" + describe_mods(combo.mods)
        else:
            text = "按错了"
        self.wrong += 1
        self.combo = 0
        verdict = Verdict(index, "wrong", t - self.slots[index].start, t, text)
        self.last = verdict
        return verdict

    def sweep(self, t: float) -> List[Verdict]:
        """时间走过了判定窗口还没按对的音，判成漏。"""
        verdicts: List[Verdict] = []
        while self.cursor < len(self.slots):
            index = self.cursor
            if self.marks[index] != Mark.PENDING:
                self.cursor += 1
                continue
            if self.slots[index].start + self.good_ms < t:
                verdicts.append(self._miss(index, t))
                self.cursor += 1
                continue
            break
        return verdicts

    def finish(self) -> List[Verdict]:
        """曲子结束：剩下没判的全部算漏。"""
        verdicts = [self._miss(i, self.slots[i].start) for i in range(self.cursor, len(self.slots))
                    if self.marks[i] == Mark.PENDING]
        self.cursor = len(self.slots)
        return verdicts

    def _hit(self, index: int, t: float) -> Verdict:
        offset = t - self.slots[index].start
        if self.lenient or abs(offset) <= self.perfect_ms:
            kind, text = Mark.PERFECT, "完美"
            self.perfect += 1
        else:
            kind = Mark.GOOD
            text = "不错 · 稍早" if offset < 0 else "不错 · 稍晚"
            self.good += 1
        if self.lenient:
            text = "对了"
        self.marks[index] = kind
        self.offsets[index] = offset
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        while self.cursor < len(self.slots) and self.marks[self.cursor] != Mark.PENDING:
            self.cursor += 1
        verdict = Verdict(index, kind, offset, t, text)
        self.last = verdict
        return verdict

    def _miss(self, index: int, t: float) -> Verdict:
        self.marks[index] = Mark.MISS
        self.missed += 1
        self.combo = 0
        verdict = Verdict(index, Mark.MISS, 0.0, t, "漏了")
        self.last = verdict
        return verdict


def difficulty(slots: Sequence) -> Tuple[int, str]:
    """
    估一首曲子"自己跟着按"有多难，返回 ``(1~5 星, 一句说明)``。

    主要看三件事：每秒几个音、鼠标键要换多少次、要不要按中键升半音。
    只是给选曲时做个参考，不追求精确。
    """
    if not slots:
        return 1, "空曲子"
    count = len(slots)
    span = max(slots[-1].end - slots[0].start, 1.0) / 1000.0
    per_second = count / span

    switches = 0
    previous: Tuple[str, ...] = ()
    for slot in slots:
        mods = tuple(slot.combo.mods)
        if mods != previous:
            switches += 1
        previous = mods
    switch_ratio = switches / count
    sharp_ratio = sum(1 for s in slots if MouseMod.MIDDLE in s.combo.mods) / count

    score = per_second * (1.0 + 0.9 * switch_ratio + 0.6 * sharp_ratio)
    stars = 1
    for limit in (0.9, 1.5, 2.3, 3.3):
        if score > limit:
            stars += 1

    if not any(slot.combo.mods for slot in slots):
        hint = "不用按鼠标"
    elif switch_ratio > 0.35:
        hint = "鼠标键换得勤"
    else:
        hint = "偶尔要换鼠标键"
    return stars, f"每秒 {per_second:.1f} 个音，{hint}"
