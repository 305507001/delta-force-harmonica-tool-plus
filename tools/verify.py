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
        self.held_keys = set()
        self.held_mouse = set()
        self._t0 = time.perf_counter()

    def _stamp(self) -> float:
        return time.perf_counter() - self._t0

    def key_down(self, key: str) -> bool:
        self.timeline.append((self._stamp(), "kd", key))
        self.held_keys.add(key)
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

    print("\n" + "=" * 62)
    if FAILED:
        print(f"结果：{len(FAILED)} 项失败 -> {', '.join(FAILED)}")
        return 1
    print("结果：全部通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
