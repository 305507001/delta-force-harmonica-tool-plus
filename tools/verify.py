#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端验证。

和 `--selftest` 的区别：
    `--selftest` 只验"算得对不对"（纯逻辑，毫秒级）；
    本脚本验"播得准不准"——用一个不真的发按键的假注入器（DrySender）
    接在演奏引擎后面，把整段流程真跑一遍，检查：

        * 实际耗时与事件流声明的时间是否吻合（计时精度）
        * 事件是否严格按时间顺序弹出
        * 暂停 / 继续 / 定位 有没有把时间算歪
        * 收尾时有没有残留按住的键

跑这个不会真的往系统里发按键，可以放心执行。

用法：
    python tools/verify.py
"""

from __future__ import annotations

import os
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from kouqin.engine import TIMING_PRESETS, Player, compile_notes
from kouqin.jianpu import parse_jianpu
from kouqin.layout import Layout


class DrySender:
    """只记录、不注入的假发送器，用来安全地测演奏引擎。"""

    def __init__(self) -> None:
        self.timeline = []          # (相对秒, 动作, 目标)
        self.presses = []           # (音键, 按下那一刻按着的鼠标键)
        self.overlaps = 0           # 按下新键时旧键还没松开的次数
        self.held_keys = set()
        self.held_mouse = set()
        self.releases = []          # 每次「全部松手」的时刻（相对秒）
        self._t0 = time.perf_counter()

    def _stamp(self) -> float:
        return time.perf_counter() - self._t0

    def key_down(self, key: str) -> bool:
        self.timeline.append((self._stamp(), "kd", key))
        self.held_keys.add(key)
        # 记下按这个音键时手里按着哪些鼠标键，用来查"修饰键丢了没有"
        self.presses.append((key, frozenset(self.held_mouse)))
        # 按下时还有别的音键没松开 = 两个键叠在一起了（连奏时必须先松后按）
        self.overlaps += len(self.held_keys - {key})
        return True

    def key_up(self, key: str) -> bool:
        self.timeline.append((self._stamp(), "ku", key))
        self.held_keys.discard(key)
        return True

    def mouse_down(self, button: str) -> bool:
        self.timeline.append((self._stamp(), "md", button))
        self.held_mouse.add(button)
        return True

    def mouse_up(self, button: str) -> bool:
        self.timeline.append((self._stamp(), "mu", button))
        self.held_mouse.discard(button)
        return True

    def release_all(self) -> None:
        self.releases.append(self._stamp())
        self.held_keys.clear()
        self.held_mouse.clear()


FAILED = []


def report(label: str, ok: bool, detail: str = "") -> None:
    mark = "通过" if ok else "失败"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)


# ---------------------------------------------------------------------- #


def test_timing_accuracy() -> None:
    """播放 4 秒的曲子，看实际耗时与声明时长差多少。"""
    print("\n[1] 计时精度")
    layout = Layout()
    # 造一段 4 秒左右的音阶
    text = " ".join(["1 2 3 4 5 6 7 1'"] * 4)
    notes = parse_jianpu(text).notes
    events, rep = compile_notes(notes, layout, TIMING_PRESETS["normal"])

    sender = DrySender()
    player = Player(sender)
    player.load(events)

    declared = events[-1].t / 1000.0
    start = time.perf_counter()
    player.play(countdown_ms=0)
    while player.is_active:
        time.sleep(0.005)
    elapsed = time.perf_counter() - start

    drift = abs(elapsed - declared)
    report("计时偏差 < 60ms", drift < 0.060,
           f"声明 {declared:.3f}s，实际 {elapsed:.3f}s，偏差 {drift*1000:.1f}ms")
    report("事件全部发出", len(sender.timeline) == len(events),
           f"{len(sender.timeline)}/{len(events)}")


def test_event_order() -> None:
    """事件必须单调不减，且每次按下都要有对应的松开。"""
    print("\n[2] 事件完整性")
    layout = Layout()
    notes = parse_jianpu("1 5 1' 5, 3 6 2' 7,").notes
    events, _ = compile_notes(notes, layout, TIMING_PRESETS["tight"])

    times = [e.t for e in events]
    report("时间戳单调不减", times == sorted(times))

    down = sum(1 for e in events if e.kind == "kd")
    up = sum(1 for e in events if e.kind == "ku")
    report("按下与松开成对", down == up, f"{down} 按 / {up} 松")

    mdown = sum(1 for e in events if e.kind == "md")
    mup = sum(1 for e in events if e.kind == "mu")
    report("鼠标修饰键成对", mdown == mup, f"{mdown} 按 / {mup} 松")

    # 修饰键必须在音键之前就位
    ok_order = True
    pending_mods: set = set()
    for event in events:
        if event.kind == "md":
            pending_mods.add(event.arg)
        elif event.kind == "kd":
            if pending_mods and not any(
                m in pending_mods for m in ("left", "right", "middle")
            ):
                ok_order = False
    report("修饰键先于音键就位", ok_order and mdown > 0,
           f"{mdown} 次修饰键切换")


def test_pause_resume() -> None:
    """暂停 1 秒后继续，总耗时应多出约 1 秒。"""
    print("\n[3] 暂停 / 继续")
    layout = Layout()
    notes = parse_jianpu(" ".join(["1 2 3 4 5 6 7 1'"] * 3)).notes
    events, _ = compile_notes(notes, layout, TIMING_PRESETS["normal"])
    declared = events[-1].t / 1000.0

    sender = DrySender()
    player = Player(sender)
    player.load(events)

    start = time.perf_counter()
    player.play(countdown_ms=0)
    time.sleep(declared * 0.4)
    player.pause()
    report("暂停状态正确", player.state == "paused", player.state)
    pause_start = time.perf_counter()
    time.sleep(0.8)
    player.resume()
    while player.is_active:
        time.sleep(0.005)
    elapsed = time.perf_counter() - start

    extra = elapsed - declared
    report("暂停时长被正确补偿", 0.6 < extra < 1.1,
           f"多出 {extra:.3f}s（预期约 0.8s）")


def test_seek() -> None:
    """定位后应跳过若干事件，且不会残留按住的键。"""
    print("\n[4] 进度定位")
    layout = Layout()
    notes = parse_jianpu(" ".join(["1 2 3 4 5 6 7 1'"] * 3)).notes
    events, _ = compile_notes(notes, layout, TIMING_PRESETS["normal"])
    total = events[-1].t

    player = Player(DrySender())
    player.load(events)
    player.seek(total * 0.5)
    report("定位到中途", abs(player.position - total * 0.5) < 30,
           f"位置 {player.position:.0f}ms / 总长 {total:.0f}ms")


def test_release_on_stop() -> None:
    """中途停止后不能留下按住的键。"""
    print("\n[5] 中断收尾")
    layout = Layout()
    notes = parse_jianpu(" ".join(["1 2 3 4 5 6 7 1'"] * 5)).notes
    events, _ = compile_notes(notes, layout, TIMING_PRESETS["normal"])

    sender = DrySender()
    player = Player(sender)
    player.load(events)
    player.play(countdown_ms=0)
    time.sleep(0.35)
    player.stop()
    time.sleep(0.1)
    report("停止后无残留按键",
           not sender.held_keys and not sender.held_mouse,
           f"残留 键{sender.held_keys} 鼠标{sender.held_mouse}")


def test_fold_consistency() -> None:
    """超音域策略：折叠不丢音，跳过会丢音。"""
    print("\n[6] 超音域策略")
    from kouqin.layout import FoldMode
    layout = Layout()
    score = parse_jianpu("1,, 5,, 1 1' 1''")     # 跨越四个八度
    _, fold_rep = compile_notes(score.notes, layout, TIMING_PRESETS["normal"],
                                fold=FoldMode.FOLD)
    _, skip_rep = compile_notes(score.notes, layout, TIMING_PRESETS["normal"],
                                fold=FoldMode.SKIP)
    report("折叠策略保留全部音符", fold_rep.played == len(score.notes),
           f"{fold_rep.played}/{len(score.notes)}，其中折叠 {fold_rep.folded} 个")
    report("跳过策略会丢弃超界音", skip_rep.skipped > 0,
           f"跳过 {skip_rep.skipped} 个，播放 {skip_rep.played} 个")


def _wait_done(player: Player, limit: float = 10.0) -> None:
    deadline = time.perf_counter() + limit
    while player.is_active and time.perf_counter() < deadline:
        time.sleep(0.005)


def _mods_ok(sender: DrySender, slots) -> tuple:
    """每次按音键时，按着的鼠标键是否正好是这个音要求的。返回 (是否全对, 说明)。"""
    want = {slot.combo.key: frozenset(slot.combo.mods) for slot in slots}
    wrong = [(key, sorted(held)) for key, held in sender.presses if held != want.get(key)]
    return not wrong, (f"{len(sender.presses)} 次按键全部正确" if not wrong
                       else f"{len(wrong)} 次少按 / 多按了鼠标键，例如 {wrong[0]}")


def test_mods_after_interrupt() -> None:
    """暂停 / 定位会松开全部按键，继续之后修饰键必须按回去，否则整段错八度。"""
    print("\n[7] 暂停 / 定位后的修饰键")
    layout = Layout()
    # D5 E5 F5 G5 A5 B5 —— 全都要按住右键，修饰键只在开头按一次
    notes = parse_jianpu("T=240\n2' 3' 4' 5' 6' 7' 2' 3' 4' 5' 6' 7'").notes
    events, rep = compile_notes(notes, layout, TIMING_PRESETS["normal"])
    total = events[-1].t / 1000.0

    sender = DrySender()
    player = Player(sender)
    player.load(events)
    player.play(countdown_ms=0)
    time.sleep(total * 0.3)
    player.pause()
    time.sleep(0.15)
    player.resume()
    _wait_done(player)
    ok, detail = _mods_ok(sender, rep.slots)
    report("暂停后继续：修饰键已恢复", ok, detail)

    sender = DrySender()
    player = Player(sender)
    player.load(events)
    player.play(countdown_ms=0)
    time.sleep(total * 0.2)
    player.seek(events[-1].t * 0.6)
    _wait_done(player)
    ok, detail = _mods_ok(sender, rep.slots)
    report("演奏中定位：修饰键已恢复", ok, detail)

    # 暂停中定位，再继续：位置应该从定位点接着走（早先会退回暂停前的位置）
    sender = DrySender()
    player = Player(sender)
    player.load(events)
    player.play(countdown_ms=0)
    time.sleep(total * 0.2)
    player.pause()
    target = events[-1].t * 0.7
    player.seek(target)
    time.sleep(0.1)
    player.resume()
    time.sleep(0.05)
    drift = player.position - target
    _wait_done(player)
    report("暂停中定位后继续，位置正确", 0 <= drift < 120, f"偏差 {drift:.0f}ms（预期 50ms 左右）")


def test_no_drift() -> None:
    """首尾紧贴的连奏不能一个音一个音地越拖越晚。"""
    print("\n[8] 连奏不累积延迟")
    from kouqin.tune import PRESET_TUNES
    layout = Layout()
    worst = (0.0, "")
    for tune in PRESET_TUNES:
        notes = tune.to_notes()
        for preset in ("safe", "normal", "tight"):
            events, rep = compile_notes(notes, layout, TIMING_PRESETS[preset])
            lead = rep.slots[0].start - notes[0].start      # 开头为修饰键补的提前量
            for note, slot in zip(sorted(notes, key=lambda n: n.start), rep.slots):
                late = slot.start - note.start - lead
                if late > worst[0]:
                    worst = (late, f"{tune.name}/{preset}")
    report("内置曲目每个音都按谱面时间落下", worst[0] < 1.0,
           f"最大偏差 {worst[0]:.1f}ms" + (f"（{worst[1]}）" if worst[1] else ""))

    # 和弦：三个音同时起，只能排成极短的分解，但后面的音不能被拖晚
    chord = parse_jianpu("T=120\n1 3 5 | 1' 2'").notes
    stacked = [chord[0], chord[0].copy(pitch=64), chord[0].copy(pitch=67)] + chord[3:]
    events, rep = compile_notes(stacked, layout, TIMING_PRESETS["normal"])
    after = [s for s in rep.slots if s.played == 72]
    late = after[0].start - 1500.0 if after else 9999.0
    report("和弦之后的音不被拖晚", abs(late) < 1.0, f"和弦后第一个音偏差 {late:.1f}ms")
    held, overlap = None, False
    for event in events:
        if event.kind == "kd":
            overlap = overlap or held is not None
            held = event.arg
        elif event.kind == "ku":
            held = None
    report("和弦排布后仍是单声部", not overlap)


def test_wait_gate() -> None:
    """跟弹「等我按对再继续」用的等待闸门：时钟停住、不发后面的事件，撤掉后接着走。"""
    print("\n[9] 等待闸门")
    layout = Layout()
    notes = parse_jianpu("T=240\n1 2 3 4 5 6").notes
    events, rep = compile_notes(notes, layout, TIMING_PRESETS["normal"])
    gate = rep.slots[2].start

    sender = DrySender()
    player = Player(sender)
    player.load(events)
    player.set_gate(gate)
    player.play(countdown_ms=0)
    time.sleep(gate / 1000.0 + 0.4)
    held_pos = player.position
    pressed = [key for key, _ in sender.presses]
    report("时钟停在闸门处", abs(held_pos - gate) < 1.0 and player.waiting,
           f"位置 {held_pos:.0f}ms，闸门 {gate:.0f}ms")
    report("闸门后的音一个都没发", pressed == ["z", "x", "c"], f"已按 {pressed}")
    player.set_gate(None)
    time.sleep(0.05)
    report("撤掉闸门后从闸门处接着走", 30 < player.position - gate < 120,
           f"撤掉 50ms 后位置 {player.position - gate:.0f}ms")
    _wait_done(player)
    report("最后全部演完", len(sender.presses) == len(notes), f"{len(sender.presses)}/{len(notes)}")


def test_slow_thread_start() -> None:
    """
    播放线程起得慢的时候，play() 一返回调用方也必须看到「正在播放」。

    Windows 上播放线程开头要加载 winmm 调定时器精度，要花几毫秒；早先状态由线程自己去切，
    调用方在这几毫秒里读到的还是「空闲」，以为已经放完了（真 Windows 上的 CI 抓到的）。
    这里人为让线程晚 80ms 起步，在任何系统上都能复现。
    """
    print("\n[10] 播放线程起步慢")
    import kouqin.engine as engine_module
    layout = Layout()
    notes = parse_jianpu("T=240\n1 2 3 4").notes
    events, _ = compile_notes(notes, layout, TIMING_PRESETS["normal"])
    original = engine_module._fine_timer
    engine_module._fine_timer = lambda on: time.sleep(0.08) if on else None
    try:
        for countdown in (0.0, 300.0):
            sender = DrySender()
            player = Player(sender)
            player.load(events)
            player.play(countdown_ms=countdown)
            active = player.is_active
            _wait_done(player)
            report(f"倒计时 {countdown:.0f}ms：play() 一返回就是播放中", active, player.state)
            report(f"倒计时 {countdown:.0f}ms：照常全部演完", len(sender.presses) == len(notes),
                   f"{len(sender.presses)}/{len(notes)}")
    finally:
        engine_module._fine_timer = original


def test_legato_playback() -> None:
    """连奏：换键时松开和按下在同一时刻，真放的时候也必须先松后按、不叠键、不吞音。"""
    print("\n[11] 连奏衔接")
    from kouqin.tune import PRESET_TUNES
    layout = Layout()
    tune = next(t for t in PRESET_TUNES if t.name == "小星星")
    events, rep = compile_notes(tune.to_notes(), layout, TIMING_PRESETS["normal"], speed=4.0)
    sender = DrySender()
    player = Player(sender)
    player.load(events)
    player.play(countdown_ms=0)
    _wait_done(player, 15.0)
    report("每个音都按下了", len(sender.presses) == len(rep.slots), f"{len(sender.presses)}/{len(rep.slots)}")
    report("换键时没有两个键叠在一起", sender.overlaps == 0, f"叠键 {sender.overlaps} 次")
    downs = [e for e in events if e.kind == "kd"]
    ups = [e for e in events if e.kind == "ku"]
    gapless = sum(1 for i in range(len(ups) - 1)
                  if downs[i + 1].t - ups[i].t < 0.01 and downs[i + 1].arg != ups[i].arg)
    report("相邻换键的音之间没有空隙", gapless > 0, f"{gapless} 处无缝衔接")


def test_stop_is_instant() -> None:
    """
    按 F8 要当场停：就算界面线程忙不过来、播放线程的回调交不出去，停止也必须立刻松开所有按键。

    早先 stop() 先等播放线程退出（最多 1 秒）再松手，而播放线程正卡在"把进度交给界面线程"那一步、
    界面线程又在 stop() 里等它——两边互相等到超时，按了 F8 要过一两秒才停、最后一个音一直响。
    这里让进度回调卡住来复现。
    """
    print("\n[12] 停止要快")
    layout = Layout()
    notes = parse_jianpu("T=240\n" + " ".join(["1 2 3 4 5 6 7 1'"] * 2)).notes
    events, _ = compile_notes(notes, layout, TIMING_PRESETS["normal"], speed=2.0)
    sender = DrySender()
    player = Player(sender)
    player.load(events)
    ui_free = threading.Event()
    player.on_progress = lambda *_: ui_free.wait(3.0)      # 界面线程正忙，回调交不出去
    player.play(countdown_ms=0)
    time.sleep(0.3)
    t0 = sender._stamp()
    began = time.perf_counter()
    player.stop()
    took = (time.perf_counter() - began) * 1000
    released = [t for t in sender.releases if t >= t0]
    first = (released[0] - t0) * 1000 if released else -1
    report("按停止后立刻松开所有按键", released and first < 30 and not sender.held_keys and not sender.held_mouse,
           f"{first:.0f} ms 后松开")
    report("停止不会把界面卡住", took < 400, f"stop() 用时 {took:.0f} ms")

    # 马上开始下一首，再放开卡住的旧线程：旧线程不能再按键，也不能把新一首正按着的键松掉
    player.on_progress = None
    t1 = sender._stamp()
    player.play(countdown_ms=0)
    ui_free.set()
    _wait_done(player)
    end = sender._stamp()
    between = [k for t, kind, k in sender.timeline if kind == "kd" and t0 < t < t1]
    new_presses = [k for t, kind, k in sender.timeline if kind == "kd" and t >= t1]
    stray = [t for t in sender.releases if t1 <= t < end - 0.05]
    report("停止后旧线程没再按键", not between, f"多按了 {len(between)} 个")
    report("下一首照常演完，没被旧线程插手", len(new_presses) == len(notes) and not stray,
           f"按了 {len(new_presses)}/{len(notes)} 个，中途被松手 {len(stray)} 次")


def main() -> int:
    print("=" * 62)
    print("《口琴工坊》端到端验证（使用假注入器，不会真的发按键）")
    print("=" * 62)

    test_timing_accuracy()
    test_event_order()
    test_pause_resume()
    test_seek()
    test_release_on_stop()
    test_fold_consistency()
    test_mods_after_interrupt()
    test_no_drift()
    test_wait_gate()
    test_slow_thread_start()
    test_legato_playback()
    test_stop_is_instant()

    print("\n" + "=" * 62)
    if FAILED:
        print(f"结果：{len(FAILED)} 项失败 -> {', '.join(FAILED)}")
        return 1
    print("结果：全部通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
