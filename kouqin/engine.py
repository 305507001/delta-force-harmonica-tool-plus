"""
演奏时序引擎。

职责：把「音符表」编译成「按毫秒排好序的输入事件流」，并负责在现场把它放出去。

三个关键设计（也是这类工具最容易翻车的地方）：

1. 帧对齐余量
   游戏是按渲染帧读键盘状态的。如果修饰键（鼠标）和音键的按下时间差小于一帧，
   游戏很可能读到「修饰键还没生效」的那一帧，音就吹错八度甚至完全不响。
   所以所有时序参数都以帧为单位（60FPS 一帧 ≈ 16.7ms），并且
   **最小间隔用物理毫秒计算，不随播放速度压缩**——倍速播放只压缩音符本身的
   时长，不压缩这些保命余量。

2. 单声部槽位排布
   口琴一次只能发一个音。前一个音松开到下一个音按下之间要隔多久，看两个音的关系：
       同一个键连按        至少 gap——中间必须真的松开过，游戏才认第二下
       要换鼠标修饰键      至少 mod_lead——修饰键在前一个音松开后、下一个音按下前切换
       换了一个键          不用隔（连奏）：松开和按下在同一时刻，先松后按
   留不出这段间隔时，优先把前一个音**缩短**——但绝不短于 min_hold，所以不会切出
   零时长的按键；实在缩不出来（比如和弦里几个音同时起），才把这个音**顺延**
   到前一个音之后。排出来的"槽位"互不重叠，也不会一个音一个音地越拖越晚。
   （早先每两个音之间都固定空一个 gap，连奏的旋律听起来一顿一顿的。）

3. 修饰键差分切换
   同一段旋律往往连续多个音用同一套修饰键，此时完全不用松手切换。
   引擎只在修饰键组合真正变化时才发鼠标事件，既省注入量又减少抖动。
   代价是：暂停 / 定位会先松开全部按键，继续之前播放器必须按事件流把
   "此刻本该按着"的修饰键重新按回去，否则后面的音会整段吹错八度。
"""

from __future__ import annotations

import bisect
import ctypes
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .layout import Combo, FoldMode, Layout, MouseMod
from .sender import Sender

__all__ = [
    "Timing",
    "TIMING_PRESETS",
    "Event",
    "Slot",
    "CompileReport",
    "compile_notes",
    "Player",
    "PlayState",
    "NullSender",
]

#: 一帧的物理时长（60FPS）
FRAME_MS = 1000.0 / 60.0


@dataclass(frozen=True)
class Timing:
    """
    按键时序参数，单位毫秒。

    :param mod_lead: 修饰键比音键提前多久按下
    :param mod_tail: 修饰键比音键晚多久松开
    :param min_hold: 每个音至少要按住多久
    :param gap:     相邻两个音之间至少留出的空档（保证按键被完整释放）
    """

    name: str
    label: str
    mod_lead: float
    mod_tail: float
    min_hold: float
    gap: float
    note: str = ""

    def describe(self) -> str:
        return (
            f"修饰提前 {self.mod_lead:.0f}ms / 最短按住 {self.min_hold:.0f}ms / "
            f"同键连按间隔 {self.gap:.0f}ms"
        )


#: 三档兼容预设。游戏吞键时往上换一档，追求速度时往下换。
TIMING_PRESETS: Dict[str, Timing] = {
    "safe": Timing(
        "safe", "稳健",
        mod_lead=FRAME_MS * 2, mod_tail=FRAME_MS * 2,
        min_hold=FRAME_MS * 2, gap=FRAME_MS * 2,
        note="余量最大，适合低配机/帧数不稳/漏音严重时",
    ),
    "normal": Timing(
        "normal", "标准",
        mod_lead=FRAME_MS, mod_tail=FRAME_MS,
        min_hold=FRAME_MS * 1.2, gap=FRAME_MS,
        note="默认档，绝大多数机器够用",
    ),
    "tight": Timing(
        "tight", "灵敏",
        mod_lead=FRAME_MS * 0.5, mod_tail=FRAME_MS * 0.5,
        min_hold=FRAME_MS, gap=FRAME_MS * 0.5,
        note="节奏最紧凑，适合快曲，但对机器帧率有要求",
    ),
}

#: 事件种类
EV_MOUSE_DOWN = "md"
EV_MOUSE_UP = "mu"
EV_KEY_DOWN = "kd"
EV_KEY_UP = "ku"


@dataclass(order=False)
class Event:
    """一条待注入的输入事件。"""

    t: float          # 相对演奏开始的毫秒
    kind: str         # EV_*
    arg: str          # 按键名 / 鼠标键名
    seq: int = 0      # 同一时刻的稳定排序依据
    pitch: int = -1   # 归属音高，便于界面高亮

    def __lt__(self, other: "Event") -> bool:  # 供排序
        if self.t != other.t:
            return self.t < other.t
        return self.seq < other.seq

    @property
    def is_modifier(self) -> bool:
        return self.kind in (EV_MOUSE_DOWN, EV_MOUSE_UP)


@dataclass(frozen=True)
class Slot:
    """
    排布好的一个音：什么时候按、按多久、怎么按。

    事件流是给注入器看的，槽位是给人看的——跟弹悬浮窗直接读它，
    保证"屏幕上提示的时间"和"自动演奏真正按下的时间"是同一套。
    """

    start: float      # 音键按下的时刻（毫秒，已含倍速）
    hold: float       # 按住多久（毫秒）
    combo: Combo      # 按键方案
    pitch: int        # 原谱音高（未移调、未折叠），界面高亮用
    played: int       # 实际吹出来的音高（移调 + 折叠之后）

    @property
    def end(self) -> float:
        return self.start + self.hold


@dataclass
class CompileReport:
    """编译结果统计，直接显示给用户。"""

    total_notes: int = 0
    played: int = 0
    folded: int = 0          # 超音域被折叠回来的
    skipped: int = 0         # 超音域被丢弃的
    event_count: int = 0
    raw_length_ms: float = 0.0     # 原始时长
    play_length_ms: float = 0.0    # 排布后时长
    max_polyphony: int = 1
    slots: List[Slot] = field(default_factory=list)   # 排布结果，跟弹提示用

    def summary(self) -> str:
        bits = [f"共 {self.total_notes} 个音"]
        if self.folded:
            bits.append(f"{self.folded} 个折叠")
        if self.skipped:
            bits.append(f"{self.skipped} 个超界跳过")
        bits.append(f"{self.played} 个可奏")
        bits.append(self.mouse_text())
        return "，".join(bits)

    def mouse_text(self) -> str:
        """
        这首曲子要按几次鼠标修饰键。

        中音区的自然音（Z ~ 逗号）不用按鼠标，很多简单曲子全程一次都不按——
        写出来免得以为是鼠标没按下去。
        """
        counts: Dict[str, int] = {}
        for slot in self.slots:
            for mod in slot.combo.mods:
                counts[mod] = counts.get(mod, 0) + 1
        need = sum(1 for slot in self.slots if slot.combo.mods)
        if not need:
            return "全程不用按鼠标"
        names = {"left": "左键", "right": "右键", "middle": "中键"}
        detail = " / ".join(f"{names.get(m, m)} {counts[m]}" for m in ("left", "right", "middle") if m in counts)
        return f"{need} 个要按鼠标（{detail}）"

    def duration_text(self) -> str:
        total = self.play_length_ms / 1000.0
        return f"{int(total // 60):02d}:{total % 60:05.2f}"


# --------------------------------------------------------------------------- #
# 编译
# --------------------------------------------------------------------------- #


def compile_notes(
    notes: Sequence,
    layout: Layout,
    timing: Timing,
    speed: float = 1.0,
    transpose: int = 0,
    fold: str = FoldMode.FOLD,
    track_channel: int = 0,
    legato: bool = True,
) -> Tuple[List[Event], CompileReport]:
    """
    把音符序列编译成事件流。

    :param notes:      :class:`kouqin.smf.Note` 序列
    :param speed:      播放倍速，只压缩音符时长，不压缩时序余量
    :param transpose:  移调半音数
    :param fold:       超音域处理策略，见 :class:`kouqin.layout.FoldMode`
    :param legato:     True = 换键时不留空隙（默认）；False = 每两个音之间都留 gap（兼容性最好）
    :returns: ``(事件列表, 统计报告)``，排布好的槽位在 ``报告.slots`` 里
    """
    report = CompileReport()
    report.total_notes = len(notes)
    if not notes:
        return [], report

    speed = max(0.05, float(speed))
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))

    # ---- 第一步：解析出每个音的按键方案，顺手统计音域情况 ----
    planned: List[Tuple[float, float, Combo, int, int]] = []
    for note in ordered:
        pitch = note.pitch + transpose
        played = pitch
        combo = layout.lookup(pitch)
        if combo is None:
            if fold == FoldMode.SKIP:
                report.skipped += 1
                continue
            played = layout.fold_pitch(pitch)
            combo = layout.lookup(played)
            report.folded += 1
        if combo is None:
            report.skipped += 1
            continue
        report.played += 1
        planned.append((note.start / speed, note.duration / speed, combo, note.pitch, played))

    report.max_polyphony = _max_polyphony(ordered)
    report.raw_length_ms = max((n.end for n in ordered), default=0.0)

    # ---- 第二步：单声部槽位排布 ----
    slots = _arrange_slots(planned, timing, legato)

    # ---- 第三步：生成事件（修饰键差分切换） ----
    events: List[Event] = []
    seq = 0
    current_mods: List[str] = []

    for slot in slots:
        start, hold, combo, pitch = slot.start, slot.hold, slot.combo, slot.pitch
        need = list(combo.mods)

        to_release = [m for m in current_mods if m not in need]
        to_press = [m for m in need if m not in current_mods]

        if to_release or to_press:
            # 修饰键切换统一放在音键之前 mod_lead 毫秒
            swap_at = max(0.0, start - timing.mod_lead)
            # 先松开多余的：避免出现"左键和右键同时按住"这种无效组合
            for mod in to_release:
                events.append(Event(swap_at, EV_MOUSE_UP, mod, seq, pitch))
                seq += 1
                current_mods.remove(mod)
            # 再按下新增的，按固定顺序，保证组合稳定
            for mod in sorted(to_press, key=lambda m: MouseMod.ORDER.index(m)):
                events.append(Event(swap_at, EV_MOUSE_DOWN, mod, seq, pitch))
                seq += 1
                current_mods.append(mod)

        events.append(Event(start, EV_KEY_DOWN, combo.key, seq, pitch))
        seq += 1
        events.append(Event(start + hold, EV_KEY_UP, combo.key, seq, pitch))
        seq += 1

    # ---- 第四步：收尾，把所有修饰键松开 ----
    tail_at = (slots[-1].end + timing.mod_tail) if slots else 0.0
    for mod in list(current_mods):
        events.append(Event(tail_at, EV_MOUSE_UP, mod, seq, pitch=-1))
        seq += 1
        current_mods.remove(mod)

    events.sort()
    report.event_count = len(events)
    report.play_length_ms = max((e.t for e in events), default=0.0)
    report.slots = slots
    return events, report


def _separation(prev: Combo, nxt: Combo, timing: Timing, legato: bool) -> float:
    """前一个音松开到下一个音按下，至少要隔多久（毫秒）。"""
    need = timing.mod_lead if tuple(prev.mods) != tuple(nxt.mods) else 0.0
    if prev.key == nxt.key or not legato:
        need = max(need, timing.gap)
    return need


def _arrange_slots(planned: Sequence[Tuple[float, float, Combo, int, int]],
                   timing: Timing, legato: bool = True) -> List[Slot]:
    """
    单声部槽位排布（思路见模块文档第 2 条）。

    早先的写法是"前一个音照原样按满，后一个音一律排到它后面再加 gap"。
    首尾紧贴的连奏（格子谱里几乎全是这样）因此每个音都比谱面晚一个 gap，
    越拖越晚，直到遇上休止符才一下子追回来——听起来就是乐句越吹越慢、然后抢拍。
    """
    slots: List[Slot] = []
    for start, duration, combo, pitch, played in planned:
        hold = max(duration, timing.min_hold)
        if slots:
            prev = slots[-1]
            sep = _separation(prev.combo, combo, timing, legato)
            if start < prev.end + sep:
                # 前一个音最晚只能按到 start - sep，而且不能短于 min_hold
                room = start - sep - prev.start
                if room >= timing.min_hold:
                    slots[-1] = replace(prev, hold=room)
                else:
                    slots[-1] = replace(prev, hold=timing.min_hold)
                    start = prev.start + timing.min_hold + sep
        slots.append(Slot(start, hold, combo, pitch, played))

    # 第一个音就要按修饰键时，开头没有提前量可用（切换时刻会被截到 0，
    # 修饰键和音键同一毫秒按下）。整体往后挪一点，把这份余量补出来。
    if slots and slots[0].combo.mods and slots[0].start < timing.mod_lead:
        offset = timing.mod_lead - slots[0].start
        slots = [replace(s, start=s.start + offset) for s in slots]
    return slots


def _max_polyphony(notes: Sequence) -> int:
    """统计原始谱面同时发声的最大音符数，用来判断这是不是单声部旋律。"""
    points: List[Tuple[float, int]] = []
    for n in notes:
        points.append((n.start, 1))
        points.append((n.end, -1))
    points.sort(key=lambda p: (p[0], p[1]))
    cur = peak = 0
    for _, delta in points:
        cur += delta
        peak = max(peak, cur)
    return peak


# --------------------------------------------------------------------------- #
# 播放
# --------------------------------------------------------------------------- #


def _fine_timer(on: bool) -> None:
    """
    Windows：演奏期间把本进程的系统定时器精度调到 1ms，结束后还原。

    默认精度约 15.6ms——Python 3.11 之前的 time.sleep、界面定时器（悬浮窗刷新）
    都按这个粒度醒来，事件会晚将近一帧，悬浮窗也只有 30 帧左右。
    音乐软件、游戏都这么做；Windows 10 2004 起这个设置只影响本进程。
    """
    if sys.platform != "win32":
        return
    try:
        winmm = ctypes.WinDLL("winmm")
        (winmm.timeBeginPeriod if on else winmm.timeEndPeriod)(1)
    except (OSError, AttributeError):
        pass


class PlayState:
    """播放状态机取值。"""

    IDLE = "idle"
    COUNTDOWN = "countdown"
    PLAYING = "playing"
    PAUSED = "paused"
    DONE = "done"


class NullSender:
    """
    什么都不发的注入器。

    跟弹模式用它驱动播放器：时间照走、进度照报，但一个按键都不往系统里发——
    键由玩家自己按，程序只负责看和提示。
    """

    class _Stats:
        def summary(self) -> str:
            return "跟弹模式，未发送任何按键"

    def __init__(self) -> None:
        self.stats = NullSender._Stats()

    def key_down(self, key: str) -> bool:
        return True

    def key_up(self, key: str) -> bool:
        return True

    def mouse_down(self, button: str) -> bool:
        return True

    def mouse_up(self, button: str) -> bool:
        return True

    def release_all(self) -> None:
        pass


class Player:
    """
    事件流播放器。

    在后台线程按时间戳把事件喂给 :class:`kouqin.sender.Sender`，
    对外暴露 播放 / 暂停 / 继续 / 停止 / 定位 以及进度回调。

    线程约定：真正发按键的只有播放线程（暂停 / 定位 / 停止时的"全部松开"除外）；
    界面线程改状态时和播放线程用同一把锁串行，所以不会出现"刚松开又被按下"的抢跑。
    回调一律在锁外调用——界面回调里会碰 Tk，持锁去等主线程会死锁。
    """

    def __init__(self, sender: Optional[Sender] = None) -> None:
        self.sender = sender or Sender()
        self._thread: Optional[threading.Thread] = None
        self._session = 0                  # 第几场演奏；stop() / play() 都会 +1，旧线程据此知道自己作废了
        self._events: List[Event] = []
        self._times: List[float] = []
        self._index = 0
        self._origin = 0.0        # perf_counter 基准
        self._shift = 0.0         # 因暂停而损失的时间
        self._countdown_ms = 0.0
        self._countdown_deadline = 0.0
        self._paused_at = 0.0
        self._pause_stamp = 0.0
        self._pending_ms = 0.0   # 下次播放的起点（seek 会改它）
        self._state = PlayState.IDLE
        self._lock = threading.RLock()
        self._resume = threading.Event()
        self._resume.set()
        self._abort = threading.Event()
        self._epoch = 0                    # 每次定位 +1：播放线程据此知道"刚才在等的那个事件作废了"
        self._resync = False               # 下一次发事件前，先把修饰键按回编排要求的状态
        self._mods_down: List[str] = []    # 本播放器按下、还没松开的鼠标修饰键
        self._gate_ms: Optional[float] = None   # 等待闸门，见 set_gate()

        # 回调
        self.on_progress: Optional[Callable[[float, int, int], None]] = None
        self.on_state: Optional[Callable[[str], None]] = None
        self.on_finish: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> str:
        return self._state

    @property
    def held_mods(self) -> Tuple[str, ...]:
        """播放器自己按下、还没松开的鼠标修饰键（悬浮窗据此分辨"这下左键是程序按的"）。"""
        return tuple(self._mods_down)

    @property
    def is_active(self) -> bool:
        return self._state in (PlayState.PLAYING, PlayState.PAUSED, PlayState.COUNTDOWN)

    @property
    def position(self) -> float:
        """当前演奏位置（毫秒）。"""
        if self._state == PlayState.PAUSED:
            return self._paused_at
        if self._state == PlayState.PLAYING and self._origin:
            return max(0.0, self._clock_ms())
        # 未开始 / 倒计时中：返回即将开始的位置（定位过就是定位点，否则是 0）。
        # （早先倒计时期间会拿上一次演奏留下的时间基准来算，第二首起位置是乱的。）
        return self._pending_ms

    @property
    def total(self) -> float:
        return self._times[-1] if self._times else 0.0

    @property
    def countdown_remaining(self) -> float:
        """倒计时剩余秒数（不在倒计时状态时返回 0）。"""
        if self._state == PlayState.COUNTDOWN and self._countdown_deadline:
            return max(0.0, self._countdown_deadline - time.perf_counter())
        return 0.0

    @property
    def gate(self) -> Optional[float]:
        return self._gate_ms

    @property
    def waiting(self) -> bool:
        """是否正停在等待闸门上（跟弹的「等我按对再继续」）。"""
        gate = self._gate_ms
        if gate is None or self._state != PlayState.PLAYING or not self._origin:
            return False
        return self._raw_ms() >= gate

    def _set_state(self, state: str) -> None:
        self._state = state
        if self.on_state:
            try:
                self.on_state(state)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 演奏时钟
    # ------------------------------------------------------------------ #

    def _raw_ms(self) -> float:
        return (time.perf_counter() - self._origin - self._shift) * 1000.0

    def _clock_ms(self) -> float:
        """演奏时钟（毫秒）。设了闸门时，时钟走到闸门处就停住。"""
        raw = self._raw_ms()
        gate = self._gate_ms
        if gate is not None and raw > gate:
            return gate
        return raw

    def _settle_gate(self) -> None:
        """
        时钟已被闸门拦住时，把时间基准挪到"恰好停在闸门处"。

        拦住期间真实时间还在流逝；不做这一步，闸门一撤时钟就会猛地跳到
        "本来该走到的位置"，等待期间的音会一口气全冒出来。
        """
        gate = self._gate_ms
        if gate is None or self._state != PlayState.PLAYING or not self._origin:
            return
        if self._raw_ms() > gate:
            self._shift = time.perf_counter() - self._origin - gate / 1000.0

    def set_gate(self, ms: Optional[float]) -> None:
        """
        设置等待闸门：演奏时钟走到 ``ms`` 就原地停住，直到闸门被挪走（传 None 撤掉）。

        跟弹的「等我按对再继续」就靠它。这不是暂停：状态仍是 PLAYING，
        也不会松开任何按键。闸门只会让时钟停下，不会让它倒退。
        """
        with self._lock:
            self._settle_gate()
            if ms is None:
                self._gate_ms = None
                return
            ms = max(0.0, float(ms))
            if self._state == PlayState.PLAYING and self._origin:
                ms = max(ms, self._clock_ms())
            self._gate_ms = ms

    # ------------------------------------------------------------------ #
    # 控制
    # ------------------------------------------------------------------ #

    def load(self, events: Sequence[Event]) -> None:
        """载入事件流（会自动按时间排序）。"""
        self._events = sorted(events)
        self._times = [e.t for e in self._events]
        self._index = 0

    def play(self, countdown_ms: float = 0.0) -> None:
        """
        开始演奏。

        :param countdown_ms: 倒计时毫秒数，给玩家切回游戏窗口的时间
        """
        if self.is_active:
            return
        if not self._events:
            return
        self._abort.clear()
        self._resume.set()
        self._shift = 0.0
        # 从上次定位的位置接着放（没定位过就是 0）
        self._index = bisect.bisect_left(self._times, self._pending_ms)
        self._resync = True        # 从中途开始时，先把这个位置该按着的修饰键按上
        self._countdown_ms = max(0.0, countdown_ms)
        # 状态当场切好再起线程：调用方一返回就能看到 is_active。
        # （早先等播放线程自己去切，线程起得慢一点——比如 Windows 上加载 winmm 那几毫秒——
        #   调用方就会以为已经放完了；倒计时的界面刷新也会因此偶尔不动。）
        if self._countdown_ms > 0:
            self._countdown_deadline = time.perf_counter() + self._countdown_ms / 1000.0
            self._set_state(PlayState.COUNTDOWN)
        else:
            self._start_clock()
            self._set_state(PlayState.PLAYING)
        with self._lock:
            self._session += 1
            session = self._session
        self._thread = threading.Thread(target=self._run, args=(session,), name="kouqin-player", daemon=True)
        self._thread.start()

    def _start_clock(self) -> None:
        """从「下次播放的起点」开始计时。"""
        with self._lock:
            # 时间基准要把"定位偏移"算进去，否则从中间开始时事件会提前弹出
            self._origin = time.perf_counter() - self._pending_ms / 1000.0
            self._shift = 0.0
            self._pause_stamp = self._origin

    def pause(self) -> None:
        with self._lock:
            if self._state != PlayState.PLAYING:
                return
            # 基准时间只在这里记录一次。
            # （早先的写法是在播放循环里不断刷新它，导致恢复时算出的暂停时长几乎为零，
            #   表现出来就是"暂停了但曲子一点没等"，这里已经修正。）
            self._settle_gate()
            self._paused_at = self.position
            self._pause_stamp = time.perf_counter()
            self._resume.clear()
            self._release_held()           # 暂停时立刻松手，避免长按卡键
            self._set_state(PlayState.PAUSED)

    def resume(self) -> None:
        with self._lock:
            if self._state != PlayState.PAUSED:
                return
            # 补偿暂停期间流逝的时间
            self._shift += time.perf_counter() - self._pause_stamp
            # 暂停时修饰键全松开了，继续之前要按回去（由播放线程来按，见 _restore_mods）
            self._resync = True
            self._resume.set()
            self._set_state(PlayState.PLAYING)

    def toggle(self) -> None:
        """空闲则开始，播放则暂停，暂停则继续。绑定到单个热键时很好用。"""
        if self._state == PlayState.PLAYING:
            self.pause()
        elif self._state == PlayState.PAUSED:
            self.resume()

    def stop(self) -> None:
        """
        中止演奏并释放所有按键。哪个线程调都行（F8 热键线程直接调它）。

        **先松手、再等线程**：按键要在按下 F8 的那一刻就松开。
        早先是先等播放线程退出（最多 1 秒）再松手，而播放线程正卡在"把进度交给界面线程"那一步、
        界面线程又在这里等它——两边互相等到超时，表现就是按了 F8 要过一两秒才停、最后一个音一直响。
        现在松完手只稍等一下；线程万一一时出不来也不死等，它出来后看到场次变了会自己退，不会再碰按键。
        """
        self._abort.set()
        self._resume.set()
        with self._lock:
            self._session += 1             # 正在跑的那个线程从此作废
            self._release_held()
            self._index = 0
            self._pending_ms = 0.0
            self._gate_ms = None
            self._epoch += 1
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.2)
        if self._state != PlayState.IDLE:          # 已经停了就别再通知一遍（F8 和界面会各调一次）
            self._set_state(PlayState.IDLE)

    def seek(self, ms: float) -> None:
        """
        跳到指定毫秒位置。

        三种情况都要正确处理：
            播放中  重建时间基准，让后续事件按新位置对齐
            暂停中  改暂停位置，并把时间基准对齐到"暂停那一刻就在新位置"
                    （早先只改了暂停位置，继续演奏时时钟又回到了旧位置）
            未播放  记成"下次从这儿开始"
        """
        if not self._events:
            return
        with self._lock:
            ms = max(0.0, min(ms, self.total))
            self._index = bisect.bisect_left(self._times, ms)
            self._epoch += 1               # 播放线程正在等的那个事件作废
            self._release_held()           # 位置跳变前先松手，避免残留修饰键
            self._resync = True            # 继续前按回新位置该有的修饰键
            self._gate_ms = None
            self._shift = 0.0
            self._pending_ms = ms
            if self._state == PlayState.PLAYING:
                self._origin = time.perf_counter() - ms / 1000.0
            elif self._state == PlayState.PAUSED:
                # resume() 会把暂停时长加进 _shift，这里让"暂停那一刻"正好对应 ms
                self._origin = self._pause_stamp - ms / 1000.0
                self._paused_at = ms

    def seek_relative(self, delta_ms: float) -> None:
        if self._state in (PlayState.PLAYING, PlayState.PAUSED):
            self.seek(self.position + delta_ms)

    # ------------------------------------------------------------------ #
    # 线程主体
    # ------------------------------------------------------------------ #

    def _run(self, session: int) -> None:
        _fine_timer(True)

        def current() -> bool:
            return session == self._session and not self._abort.is_set()

        try:
            # 倒计时阶段：让人有时间切回游戏（状态已经由 play() 切成 COUNTDOWN）
            if self._state == PlayState.COUNTDOWN:
                while time.perf_counter() < self._countdown_deadline:
                    if not current():
                        return                     # stop() 已经把状态切回空闲了
                    time.sleep(0.02)
                self._countdown_deadline = 0.0
                # 先定基准再切状态：别人一看到 PLAYING 就会来读 position
                self._start_clock()
                self._set_state(PlayState.PLAYING)
            last_report = 0.0

            while True:
                with self._lock:
                    if not current() or self._index >= len(self._events):
                        break
                    paused = not self._resume.is_set()
                    if not paused:
                        if self._resync:
                            self._resync = False
                            self._restore_mods()
                        epoch = self._epoch
                        event = self._events[self._index]
                    else:
                        # 暂停中：顺手再松一次手，防止 pause() 松键的同一瞬间本线程又按下了键
                        self._release_held()
                if paused:
                    # 这里只是等着，不要去动 _pause_stamp（那是 pause() 的职责）
                    self._resume.wait(0.05)
                    continue

                # 等到该事件的时间点
                target = event.t
                while True:
                    if not current() or not self._resume.is_set() or self._epoch != epoch:
                        break
                    remain = (target - self._clock_ms()) / 1000.0
                    if remain <= 0:
                        break
                    if remain > 0.002:
                        time.sleep(min(remain - 0.001, 0.02))
                    elif self._gate_ms is not None and self._raw_ms() > self._gate_ms:
                        time.sleep(0.005)      # 被闸门拦住时别空转
                    # 剩余不足 2ms，直接忙等，保证精度

                with self._lock:
                    if not current() or not self._resume.is_set() or self._epoch != epoch:
                        continue
                    self._emit(event)
                    self._index += 1
                    index, count = self._index, len(self._events)

                # 进度回调做节流，别把 UI 线程刷爆
                now_ms = self._clock_ms()
                if self.on_progress and (now_ms - last_report > 40 or index >= count):
                    last_report = now_ms
                    try:
                        self.on_progress(now_ms, index, count)
                    except Exception:
                        pass

            if current():
                self._set_state(PlayState.DONE)
            # 被 stop() 停掉的：stop() 已经松过手、切过状态，这里什么都别碰（可能已经开始下一首了）
        finally:
            with self._lock:
                mine = session == self._session
                if mine:
                    self._release_held()
            _fine_timer(False)
            if mine and self._state == PlayState.DONE and self.on_finish:
                try:
                    self.on_finish()
                except Exception:
                    pass

    def _emit(self, event: Event) -> None:
        """把一个事件发给注入器。"""
        if event.kind == EV_KEY_DOWN:
            self.sender.key_down(event.arg)
        elif event.kind == EV_KEY_UP:
            self.sender.key_up(event.arg)
        elif event.kind == EV_MOUSE_DOWN:
            self.sender.mouse_down(event.arg)
            if event.arg not in self._mods_down:
                self._mods_down.append(event.arg)
        elif event.kind == EV_MOUSE_UP:
            self.sender.mouse_up(event.arg)
            if event.arg in self._mods_down:
                self._mods_down.remove(event.arg)

    def _release_held(self) -> None:
        self.sender.release_all()
        self._mods_down.clear()

    def _mods_at(self, index: int) -> List[str]:
        """事件流走到 index 时，按编排应该按住哪些鼠标修饰键。"""
        held: List[str] = []
        for event in self._events[:index]:
            if event.kind == EV_MOUSE_DOWN:
                if event.arg not in held:
                    held.append(event.arg)
            elif event.kind == EV_MOUSE_UP and event.arg in held:
                held.remove(event.arg)
        return held

    def _restore_mods(self) -> None:
        """
        把鼠标修饰键按回「事件流走到当前位置时本该按着」的状态。

        暂停 / 定位都会先松开全部按键，可编译时修饰键是差分生成的——只在组合
        变化时才有按下事件。不补这一步，继续演奏后同一组合的音会全部少按一个
        鼠标键（整段错八度），一直错到组合变化为止。
        """
        wanted = self._mods_at(self._index)
        for mod in [m for m in self._mods_down if m not in wanted]:
            self.sender.mouse_up(mod)
            self._mods_down.remove(mod)
        pressed = False
        for mod in wanted:
            if mod not in self._mods_down:
                self.sender.mouse_down(mod)
                self._mods_down.append(mod)
                pressed = True
        if pressed:
            # 刚按下的修饰键要留一帧给游戏读到，后面的音整体顺延一帧
            self._shift += FRAME_MS / 1000.0
