#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
悬浮窗测速：看它在游戏旁边跑着时大概要吃掉多少 CPU。

不连游戏、不按键：在屏幕上开一个悬浮窗，拿「送别」按真实节奏走一遍，
分几种情况各跑几秒，打印实际帧率、每帧耗时和占单个 CPU 核心的百分比。
只用标准库，不用装别的。

用法：
    python tools/perf_overlay.py [每种情况跑几秒，默认 6]

占用百分比是整个进程的（含 Tk 自己画图），100% = 吃满一个核心。
"""

from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import tkinter as tk

from kouqin import TIMING_PRESETS, Layout, compile_notes
from kouqin.engine import PlayState
from kouqin.tune import PRESET_TUNES
from gui.overlay import Frame, OverlayController, OverlayWindow
from gui.theme import apply_theme


class _App:
    """控制器只在画帧时用到这么一点东西。"""

    def __init__(self) -> None:
        self.settings = None


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    root = tk.Tk()
    root.withdraw()
    apply_theme(root)
    window = OverlayWindow(root, on_moved=lambda x, y: None, on_lock=lambda: None)
    layout = Layout()
    tune = next(t for t in PRESET_TUNES if t.name == "送别")
    _events, report = compile_notes(tune.to_notes(), layout, TIMING_PRESETS["normal"])
    slots = report.slots
    window.set_content(slots, layout)
    total = slots[-1].end
    controller = OverlayController(_App())

    def run(fps: int, effects: bool, paused: bool):
        controller.fps = fps
        controller._drawn = ()
        controller._drawn_at = -1.0
        renders = [0]
        original = window.render

        def counting(frame):
            renders[0] += 1
            original(frame)

        window.render = counting
        next_hit = 0
        cpu0, wall0 = time.process_time(), time.perf_counter()
        busy = 0.0
        try:
            while True:
                now = time.perf_counter()
                if now - wall0 >= seconds:
                    break
                pos = 20000.0 if paused else ((now - wall0) * 1000.0) % total
                if not paused:
                    if next_hit < len(slots) and pos < slots[next_hit].start - 1000:
                        next_hit = 0                      # 绕回开头了
                    while next_hit < len(slots) and slots[next_hit].start <= pos:
                        if effects:
                            window.effect_hit(slots[next_hit].combo.key, now)
                        next_hit += 1
                frame = Frame(mode="auto", title="送别",
                              state=PlayState.PAUSED if paused else PlayState.PLAYING,
                              position=pos, total=total, stats="命中 12 · 漏 1")
                controller._draw(window, frame, now)
                root.update()
                busy += time.perf_counter() - now
                left = OverlayController.TICK_MS / 1000.0 - (time.perf_counter() - now)
                if left > 0:
                    time.sleep(left)
        finally:
            window.render = original
            window.clear_effects()
        wall = time.perf_counter() - wall0
        return renders[0] / wall, (time.process_time() - cpu0) / wall * 100

    cases = [
        ("演奏中 · 60 帧 · 命中特效开", 60, True, False),
        ("演奏中 · 60 帧 · 命中特效关", 60, False, False),
        ("演奏中 · 30 帧 · 命中特效开", 30, True, False),
        ("暂停中 · 60 帧（画面不动）", 60, True, True),
    ]
    print(f"每种情况跑 {seconds:g} 秒；读按键始终每 {OverlayController.TICK_MS} 毫秒一次\n")
    for label, fps, effects, paused in cases:
        drawn, cpu = run(fps, effects, paused)
        print(f"  {label:<22} 实际画 {drawn:5.1f} 帧/秒   CPU {cpu:5.1f}%（单核）")
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
