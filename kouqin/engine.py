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
   口琴一次只能发一个音。遇到与前一个音时间重叠的音符时，不能靠"缩短前一个音"
   来腾地方（会切出零时长的按键，游戏直接吞掉），而是把这个音**顺延到前一个音
   之后**，形成一个个互不重叠的"槽位"。

3. 修饰键差分切换
   同一段旋律往往连续多个音用同一套修饰键，此时完全不用松手切换。
   引擎只在修饰键组合真正变化时才发鼠标事件，既省注入量又减少抖动。
"""

from __future__ import annotations

import bisect
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .layout import Combo, FoldMode, Layout, MouseMod
from .sender import Sender

__all__ = [
    "Timing",
    "TIMING_PRESETS",
    "Event",
    "CompileReport",
    "compile_notes",
    "Player",
    "PlayState",
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
            f"音符间隔 {self.gap:.0f}ms"
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

    def summary(self) -> str:
        bits = [f"共 {self.total_notes} 个音"]
        if self.folded:
            bits.append(f"{self.folded} 个折叠")
        if self.skipped:
            bits.append(f"{self.skipped} 个超界跳过")
        bits.append(f"{self.played} 个可奏")
        return "，".join(bits)

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
) -> Tuple[List[Event], CompileReport]:
    """
    把音符序列编译成事件流。

    :param notes:      :class:`kouqin.smf.Note` 序列
    :param speed:      播放倍速，只压缩音符时长，不压缩时序余量
    :param transpose:  移调半音数
    :param fold:       超音域处理策略，见 :class:`kouqin.layout.FoldMode`
    :returns: ``(事件列表, 统计报告)``
    """
    report = CompileReport()
    report.total_notes = len(notes)
    if not notes:
        return [], report

    speed = max(0.05, float(speed))
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))

    # ---- 第一步：解析出每个音的按键方案，顺手统计音域情况 ----
    planned: List[Tuple[float, float, Combo, int]] = []
    for note in ordered:
        pitch = note.pitch + transpose
        combo = layout.lookup(pitch)
        if combo is None:
            if fold == FoldMode.SKIP:
                report.skipped += 1
                continue
            combo = layout.lookup(layout.fold_pitch(pitch))
            report.folded += 1
        if combo is None:
            report.skipped += 1
            continue
        report.played += 1
        planned.append((note.start / speed, note.duration / speed, combo, note.pitch))

    report.max_polyphony = _max_polyphony(ordered)
    report.raw_length_ms = max((n.end for n in ordered), default=0.0)

    # ---- 第二步：单声部槽位排布 ----
    slots: List[Tuple[float, float, Combo, int]] = []
    cursor = 0.0
    for start, duration, combo, pitch in planned:
        real_start = max(start, cursor)
        hold = max(duration, timing.min_hold)
        slots.append((real_start, hold, combo, pitch))
        # 留出 gap，确保按键被完整释放，避免被游戏合并成一次长按
        cursor = real_start + hold + timing.gap

    # ---- 第三步：生成事件（修饰键差分切换） ----
    events: List[Event] = []
    seq = 0
    current_mods: List[str] = []

    for start, hold, combo, pitch in slots:
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
    tail_at = (slots[-1][0] + slots[-1][1] + timing.mod_tail) if slots else 0.0
    for mod in list(current_mods):
        events.append(Event(tail_at, EV_MOUSE_UP, mod, seq, pitch=-1))
        seq += 1
        current_mods.remove(mod)

    events.sort()
    report.event_count = len(events)
    report.play_length_ms = max((e.t for e in events), default=0.0)
    return events, report


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


class PlayState:
    """播放状态机取值。"""

    IDLE = "idle"
    COUNTDOWN = "countdown"
    PLAYING = "playing"
    PAUSED = "paused"
    DONE = "done"


class Player:
    """
    事件流播放器。

    在后台线程按时间戳把事件喂给 :class:`kouqin.sender.Sender`，
    对外暴露 播放 / 暂停 / 继续 / 停止 / 定位 以及进度回调。
    """

    def __init__(self, sender: Optional[Sender] = None) -> None:
        self.sender = sender or Sender()
        self._thread: Optional[threading.Thread] = None
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
        self._lock = threading.Lock()
        self._resume = threading.Event()
        self._resume.set()
        self._abort = threading.Event()

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
    def is_active(self) -> bool:
        return self._state in (PlayState.PLAYING, PlayState.PAUSED, PlayState.COUNTDOWN)

    @property
    def position(self) -> float:
        """当前演奏位置（毫秒）。"""
        if self._state == PlayState.PAUSED:
            return self._paused_at
        if self._state in (PlayState.PLAYING, PlayState.COUNTDOWN) and self._origin:
            return max(0.0, (time.perf_counter() - self._origin - self._shift) * 1000.0)
        # 未开始演奏时，返回上一次定位的位置（默认 0）
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

    def _set_state(self, state: str) -> None:
        self._state = state
        if self.on_state:
            try:
                self.on_state(state)
            except Exception:
                pass

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
        self._countdown_ms = max(0.0, countdown_ms)
        self._thread = threading.Thread(target=self._run, name="kouqin-player", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        if self._state != PlayState.PLAYING:
            return
        # 基准时间只在这里记录一次。
        # （早先的写法是在播放循环里不断刷新它，导致恢复时算出的暂停时长几乎为零，
        #   表现出来就是"暂停了但曲子一点没等"，这里已经修正。）
        self._paused_at = self.position
        self._pause_stamp = time.perf_counter()
        self._resume.clear()
        self.sender.release_all()      # 暂停时立刻松手，避免长按卡键
        self._set_state(PlayState.PAUSED)

    def resume(self) -> None:
        if self._state != PlayState.PAUSED:
            return
        # 补偿暂停期间流逝的时间
        self._shift += time.perf_counter() - self._pause_stamp
        self._resume.set()
        self._set_state(PlayState.PLAYING)

    def toggle(self) -> None:
        """空闲则开始，播放则暂停，暂停则继续。绑定到单个热键时很好用。"""
        if self._state == PlayState.PLAYING:
            self.pause()
        elif self._state == PlayState.PAUSED:
            self.resume()

    def stop(self) -> None:
        """中止演奏并释放所有按键。"""
        self._abort.set()
        self._resume.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self.sender.release_all()
        self._index = 0
        self._pending_ms = 0.0
        self._set_state(PlayState.IDLE)

    def seek(self, ms: float) -> None:
        """
        跳到指定毫秒位置。

        三种情况都要正确处理：
            播放中  重建时间基准，让后续事件按新位置对齐
            暂停中  只改暂停位置，恢复时从那里继续
            未播放  记成"下次从这儿开始"
        """
        if not self._events:
            return
        ms = max(0.0, min(ms, self.total))
        self._index = bisect.bisect_left(self._times, ms)
        self.sender.release_all()      # 位置跳变前先松手，避免残留修饰键
        self._shift = 0.0
        self._pending_ms = ms
        if self._state == PlayState.PLAYING:
            self._origin = time.perf_counter() - ms / 1000.0
        elif self._state == PlayState.PAUSED:
            self._paused_at = ms

    def seek_relative(self, delta_ms: float) -> None:
        if self._state in (PlayState.PLAYING, PlayState.PAUSED):
            self.seek(self.position + delta_ms)

    # ------------------------------------------------------------------ #
    # 线程主体
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        try:
            # 倒计时阶段：让人有时间切回游戏
            if self._countdown_ms > 0:
                self._set_state(PlayState.COUNTDOWN)
                self._countdown_deadline = time.perf_counter() + self._countdown_ms / 1000.0
                while time.perf_counter() < self._countdown_deadline:
                    if self._abort.is_set():
                        self._countdown_deadline = 0.0
                        self._set_state(PlayState.IDLE)
                        return
                    time.sleep(0.02)
                self._countdown_deadline = 0.0

            self._set_state(PlayState.PLAYING)
            # 时间基准要把"定位偏移"算进去，否则从中间开始时事件会提前弹出
            self._origin = time.perf_counter() - self._pending_ms / 1000.0
            self._shift = 0.0
            self._pause_stamp = self._origin
            last_report = 0.0

            while self._index < len(self._events):
                if self._abort.is_set():
                    break
                if not self._resume.is_set():
                    # 这里只是等着，不要去动 _pause_stamp（那是 pause() 的职责）
                    self._resume.wait(0.05)
                    continue

                event = self._events[self._index]
                target = event.t / 1000.0
                # 等到该事件的时间点
                while True:
                    if self._abort.is_set():
                        break
                    if not self._resume.is_set():
                        break
                    now = time.perf_counter() - self._origin - self._shift
                    remain = target - now
                    if remain <= 0:
                        break
                    if remain > 0.002:
                        time.sleep(min(remain - 0.001, 0.02))
                    # 剩余不足 2ms，直接忙等，保证精度
                if self._abort.is_set() or not self._resume.is_set():
                    continue

                self._emit(event)
                self._index += 1

                # 进度回调做节流，别把 UI 线程刷爆
                now_ms = (time.perf_counter() - self._origin - self._shift) * 1000.0
                if self.on_progress and (now_ms - last_report > 40 or self._index >= len(self._events)):
                    last_report = now_ms
                    try:
                        self.on_progress(now_ms, self._index, len(self._events))
                    except Exception:
                        pass

            if self._abort.is_set():
                self._set_state(PlayState.IDLE)
            else:
                self._set_state(PlayState.DONE)
        finally:
            self.sender.release_all()
            if self._state == PlayState.DONE and self.on_finish:
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
        elif event.kind == EV_MOUSE_UP:
            self.sender.mouse_up(event.arg)
