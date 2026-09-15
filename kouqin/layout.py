"""
口琴键位模型。

游戏里那把口琴的发声规则可以抽象成三件事：

    1. 一排 8 个基础音键        -> 一个八度的自然音阶（do re mi fa sol la si do）
    2. 鼠标按键作为"变调修饰"   -> 左键降八度 / 右键升八度 / 中键升半音
    3. 一次只出一个音           -> 单声部，后按的音会顶掉前一个

于是任意音高都能用「修饰键组合 + 基础键」唯一表示。
本模块负责：

    * 枚举全音域，给出每个音高的最优按键方案（修饰键越少越优先）
    * 反查：给一个任意音高，判断能否演奏 / 怎么折叠回音域内
    * 生成键位总表，供界面和自测页使用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "BASE_KEYS",
    "SCALE",
    "MouseMod",
    "Combo",
    "Layout",
    "DEFAULT_LAYOUT",
    "FoldMode",
]

#: 8 个基础音键（按音阶顺序），对应 do re mi fa sol la si do
BASE_KEYS: Tuple[str, ...] = ("z", "x", "c", "v", "b", "n", "m", ",")

#: 自然音阶的半音偏移
SCALE: Tuple[int, ...] = (0, 2, 4, 5, 7, 9, 11, 12)

#: 默认键的显示名。没登记的自定义键走 key_label() 的兜底。
_KEY_LABEL = {
    "z": "Z", "x": "X", "c": "C", "v": "V",
    "b": "B", "n": "N", "m": "M", ",": "逗号",
}

#: 这些键在紧凑写法里直接用原字符，不翻译
_TIGHT_KEYS = frozenset(",./;'[]\\-=`")

#: 符号键的显示名
_SYMBOL_LABEL = {
    ",": "逗号", ".": "句号", "/": "斜杠", ";": "分号",
    "[": "[", "]": "]", "\\": "反斜杠", "-": "减号",
    "=": "等号", "'": "引号", "`": "波浪",
}


def key_label(key: str) -> str:
    """
    按键的显示名。

    用户在设置里可以把音键改成任意字母/数字/符号，所以这里不能只查默认表，
    必须有兜底，否则换了键位后界面会直接抛 KeyError。
    """
    key = (key or "").lower()
    if key in _KEY_LABEL:
        return _KEY_LABEL[key]
    if key in _SYMBOL_LABEL:
        return _SYMBOL_LABEL[key]
    return key.upper() if key else "?"

#: 变调档次 -> 八度偏移
OCTAVE_SHIFT = {"low": -12, "mid": 0, "high": 12}

#: 变调档次 -> 半音偏移
SEMITONE_SHIFT = {"natural": 0, "sharp": 1}


class MouseMod:
    """鼠标修饰键名。"""

    LEFT = "left"      # 降八度
    RIGHT = "right"    # 升八度
    MIDDLE = "middle"  # 升半音

    ORDER = (LEFT, RIGHT, MIDDLE)

    LABEL = {LEFT: "鼠标左键", RIGHT: "鼠标右键", MIDDLE: "鼠标中键"}
    SHORT = {LEFT: "左", RIGHT: "右", MIDDLE: "中"}


@dataclass(frozen=True)
class Combo:
    """一个可执行的按键方案。"""

    key: str                       # 基础音键，如 'z'
    mods: Tuple[str, ...] = ()     # 需要按住的鼠标修饰键

    @property
    def mod_count(self) -> int:
        return len(self.mods)

    def describe(self) -> str:
        """人类可读描述，如「右键+中键+逗号」。"""
        parts = [MouseMod.SHORT[m] + "键" for m in self.mods]
        parts.append(key_label(self.key))
        return "+".join(parts)

    def tokens(self) -> str:
        """
        紧凑写法，如 ``R+M+,``。

        和 describe() 的区别是符号键不翻译成中文——格子里地方小，
        写成「句号」会挤爆，写 "." 反而一眼就懂。
        """
        pre = "".join({"left": "L", "right": "R", "middle": "M"}[m] for m in self.mods)
        label = self.key if self.key in _TIGHT_KEYS else key_label(self.key)
        return f"{pre}+{label}" if pre else label

    def __str__(self) -> str:  # pragma: no cover
        return self.describe()


class FoldMode:
    """超音域音符的处理策略。"""

    SKIP = "skip"        # 原样丢弃，留空拍（保守）
    FOLD = "fold"        # 折叠到最近的可用八度，保留旋律轮廓（推荐）
    TRANSPOSE = "transpose"  # 整曲平移，使原本超界的音最多


#: 三种变调功能的默认鼠标键分配。「不使用」时填 None。
DEFAULT_MOD_ASSIGN: Dict[str, Optional[str]] = {
    "down": MouseMod.LEFT,     # 整排降一个八度
    "up": MouseMod.RIGHT,      # 整排升一个八度
    "sharp": MouseMod.MIDDLE,  # 整排升半音
}

#: 变调功能的中文名，设置界面直接用
MOD_FEATURE_LABELS: Dict[str, str] = {
    "down": "降一个八度",
    "up": "升一个八度",
    "sharp": "升半音",
}


@dataclass
class Layout:
    """
    一套口琴键位配置。

    :param base_octave: 「中音 do」对应的八度号，默认 4 即 C4 = MIDI 60
    :param keys:        基础音键序列，长度必须为 8（do re mi fa sol la si 高do）
    :param mod_assign:  三种变调功能分别绑到哪个鼠标键
    """

    base_octave: int = 4
    keys: Tuple[str, ...] = BASE_KEYS
    mod_assign: Dict[str, Optional[str]] = field(default_factory=lambda: dict(DEFAULT_MOD_ASSIGN))

    def __post_init__(self) -> None:
        if len(self.keys) != 8:
            raise ValueError("基础音键必须正好 8 个")
        self._table: Dict[int, Combo] = {}
        self._build()

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #

    def configure(self, keys: Optional[Sequence[str]] = None,
                  base_octave: Optional[int] = None,
                  mod_assign: Optional[Dict[str, Optional[str]]] = None) -> None:
        """改配置并立即重建键位表（设置页改完就调它）。"""
        if keys is not None:
            keys = tuple(keys)
            if len(keys) != 8:
                raise ValueError("基础音键必须正好 8 个")
            self.keys = keys
        if base_octave is not None:
            self.base_octave = int(base_octave)
        if mod_assign is not None:
            merged = dict(DEFAULT_MOD_ASSIGN)
            merged.update({k: v for k, v in mod_assign.items() if k in DEFAULT_MOD_ASSIGN})
            self.mod_assign = merged
        self._build()

    def _mods_for(self, octave_tag: str, semitone_tag: str) -> List[str]:
        """按当前配置算出某个变调档需要按住哪些鼠标键。"""
        mods: List[str] = []

        if octave_tag == "low":
            mod = self.mod_assign.get("down")
        elif octave_tag == "high":
            mod = self.mod_assign.get("up")
        else:
            mod = None
        if mod:
            mods.append(mod)

        if semitone_tag == "sharp":
            mod = self.mod_assign.get("sharp")
            if mod and mod not in mods:
                mods.append(mod)

        # 固定顺序，避免同一个音在两次编译里生成不同的事件顺序
        base_order = list(MouseMod.ORDER)

        def order_of(name: str) -> int:
            return base_order.index(name) if name in base_order else 99

        mods.sort(key=order_of)
        return mods

    # ------------------------------------------------------------------ #
    # 建表
    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        """
        穷举「变调档 × 基础键」的所有组合，为每个音高选一个最优方案。

        优先级：少用修饰键 > 不做八度平移 > 不做半音平移。
        这样 C4 会用裸键 ``Z``，而 C5 优先用 ``逗号`` 而不是「右键+Z」。
        """
        center = (self.base_octave + 1) * 12
        best: Dict[int, Tuple[float, Combo]] = {}

        for octave_tag, semitone_tag in (
            ("mid", "natural"), ("mid", "sharp"),
            ("low", "natural"), ("high", "natural"),
            ("low", "sharp"), ("high", "sharp"),
        ):
            oct_shift = OCTAVE_SHIFT[octave_tag]
            semi_shift = SEMITONE_SHIFT[semitone_tag]

            # 该档位要求的修饰键如果被用户关掉了，这一档就整体作废
            if octave_tag == "low" and not self.mod_assign.get("down"):
                continue
            if octave_tag == "high" and not self.mod_assign.get("up"):
                continue
            if semitone_tag == "sharp" and not self.mod_assign.get("sharp"):
                continue

            mods = self._mods_for(octave_tag, semitone_tag)

            # 代价：每个修饰键 1 分，八度平移额外惩罚（更容易被游戏漏读）
            cost = len(mods) + (0.5 if octave_tag != "mid" else 0.0)

            for step, semitone in enumerate(SCALE):
                pitch = center + oct_shift + semi_shift + semitone
                combo = Combo(self.keys[step], tuple(mods))
                old = best.get(pitch)
                if old is None or cost < old[0]:
                    best[pitch] = (cost, combo)

        self._table = {p: c for p, (_, c) in best.items()}
        pitches = sorted(self._table)
        self.lowest: int = pitches[0]
        self.highest: int = pitches[-1]

    # ------------------------------------------------------------------ #
    # 描述
    # ------------------------------------------------------------------ #

    def key_hint(self, step: int) -> str:
        """第 step 个音级（0..7）对应的裸键显示名。"""
        return key_label(self.keys[step])

    def summary(self) -> str:
        """一句话描述当前键位，显示在界面上。"""
        keys = " ".join(key_label(k) for k in self.keys)
        mods = []
        for feature in ("down", "up", "sharp"):
            name = self.mod_assign.get(feature)
            if name:
                mods.append(f"{MOD_FEATURE_LABELS[feature]}={MouseMod.LABEL[name]}")
        return f"音键 {keys}　|　" + ("　".join(mods) if mods else "未启用变调")

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    def lookup(self, pitch: int) -> Optional[Combo]:
        """精确查询某个音高的按键方案；超界返回 None。"""
        return self._table.get(pitch)

    def can_play(self, pitch: int) -> bool:
        return pitch in self._table

    def playable_pitches(self) -> List[int]:
        return sorted(self._table)

    def resolve(self, pitch: int, fold: str = FoldMode.FOLD) -> Optional[Combo]:
        """
        取一个音的实际按键方案，超界时按 ``fold`` 策略处理。

        :returns: 按键方案；策略为 SKIP 且超界时返回 None（表示留空拍）
        """
        combo = self._table.get(pitch)
        if combo is not None:
            return combo
        if fold == FoldMode.SKIP:
            return None
        return self._table[self.fold_pitch(pitch)]

    def fold_pitch(self, pitch: int) -> int:
        """
        把一个超界音高折回音域内的最近音（保持音级关系）。

        做法：整体搬若干个八度，让结果落进音域；仍越界时再按音级就近吸附。
        这样旋律的起伏走向不会被打乱。
        """
        if self.can_play(pitch):
            return pitch

        # 先整八度平移
        candidate = pitch
        while candidate < self.lowest:
            candidate += 12
        while candidate > self.highest:
            candidate -= 12
        if self.can_play(candidate):
            return candidate

        # 再按到音域边界的半音距离就近吸附
        playable = self.playable_pitches()
        return min(playable, key=lambda p: (abs(p - pitch), p))

    def transpose_for_fit(self, pitches: Sequence[int]) -> int:
        """
        计算一个「整曲移调量」，让尽量多的音落进音域。

        对每个候选移调量（-24 ~ +24 半音）统计可奏音符数，取最优；
        相同时取绝对值更小的移调量，减少音色失真。
        """
        if not pitches:
            return 0
        best_shift, best_score = 0, -1.0
        for shift in range(-24, 25):
            hit = sum(1 for p in pitches if self.can_play(p + shift))
            score = hit - abs(shift) * 0.01
            if score > best_score:
                best_shift, best_score = shift, score
        return best_shift

    # ------------------------------------------------------------------ #
    # 导出
    # ------------------------------------------------------------------ #

    def table_rows(self) -> List[Tuple[int, str, str, str]]:
        """
        生成键位总表：``(音高, 音名, 紧凑键位, 中文描述)``，按音高升序。
        """
        rows = []
        for pitch in self.playable_pitches():
            combo = self._table[pitch]
            rows.append((pitch, f"{pitch_name_safe(pitch)}", combo.tokens(), combo.describe()))
        return rows


def pitch_name_safe(pitch: int) -> str:
    """避免与 smf 模块循环 import 的轻量音名换算。"""
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[pitch % 12]}{pitch // 12 - 1}"


#: 全局默认键位
DEFAULT_LAYOUT = Layout()
