#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
界面截图：把每个页面、浅色 / 深色、还有叠在"游戏画面"上的悬浮窗各截一张，看看真实效果。

给改界面的人用的（GitHub 上的 Windows 自检会跑一遍，截图在那次运行的 Artifacts 里）。
需要 Pillow：``python -m pip install pillow``。

和冒烟测试一样：设置和曲库放在临时文件夹里，不动你自己的数据；播放器换成假注入器，不会真的按键。

用法：
    python tools/ui_screenshots.py 输出文件夹 [light|dark]
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_SANDBOX = tempfile.mkdtemp(prefix="kouqin-shots-")
os.environ["KOUQIN_DATA_DIR"] = _SANDBOX


class _DrySender:
    def __init__(self) -> None:
        self.count = 0

    def key_down(self, key): return True
    def key_up(self, key): return True
    def mouse_down(self, button): return True
    def mouse_up(self, button): return True
    def release_all(self): pass

    class _Stats:
        def summary(self):
            return "（截图，未真实注入）"

    stats = _Stats()


def main() -> int:
    out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "ui-screenshots")
    theme = sys.argv[2] if len(sys.argv) > 2 else "light"
    os.environ["KOUQIN_THEME"] = theme
    os.makedirs(out, exist_ok=True)
    try:
        from PIL import ImageGrab
    except ImportError:
        print("需要 Pillow：python -m pip install pillow")
        return 1

    import tkinter as tk
    from gui.main_window import MainWindow

    window = MainWindow()
    window.player.sender = window._input_sender = _DrySender()
    s = window.settings
    s.disclaimer_accepted = s.guide_shown = True
    s.countdown = 0.0
    sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
    width, height = min(1160, sw - 40), min(760, sh - 80)
    window.geometry(f"{width}x{height}+10+10")
    shots = []

    def grab(name: str, widget: tk.Misc, layered: bool = False) -> None:
        widget.update()
        x, y = widget.winfo_rootx(), widget.winfo_rooty()
        box = (x, y, x + widget.winfo_width(), y + widget.winfo_height())
        image = ImageGrab.grab(bbox=box, include_layered_windows=layered, all_screens=True)
        path = os.path.join(out, f"{theme}_{name}.png")
        image.save(path)
        shots.append(path)
        print(f"  {path}")

    def fake_game() -> tk.Toplevel:
        """一块假的"游戏画面"，悬浮窗叠在上面截图，看半透明的效果。"""
        game = tk.Toplevel(window)
        game.overrideredirect(True)
        game.geometry(f"{min(900, sw - 40)}x{min(420, sh - 120)}+20+60")
        c = tk.Canvas(game, bg="#85b7eb", highlightthickness=0)
        c.pack(fill=tk.BOTH, expand=True)
        c.create_rectangle(0, 300, 2000, 2000, fill="#3b6d11", outline="")
        for x0, w, h, color in ((40, 120, 150, "#5f5e5a"), (180, 200, 220, "#444441"),
                                (400, 150, 180, "#888780"), (570, 120, 130, "#5f5e5a")):
            c.create_rectangle(x0, 300 - h, x0 + w, 300, fill=color, outline="")
        c.create_text(450, 30, text="目标：前往撤离点 B", fill="#ffffff", font=("Microsoft YaHei UI", 14, "bold"))
        c.create_text(450, 56, text="剩余 04:32 · 撤离点距离 316 米", fill="#f1efe8", font=("Microsoft YaHei UI", 10))
        c.create_text(860, 390, text="30 / 120", fill="#ffffff", anchor="e", font=("Microsoft YaHei UI", 16, "bold"))
        return game

    steps = []

    def page(key: str, delay: int = 700):
        def run():
            window.show_page(key)
            window.after(delay, lambda: (grab(key, window), next_step()))
        return run

    def playing():
        window.show_page("library")
        window.play_tune_by_name("送别", "auto")
        window.after(1800, lambda: (grab("playing", window), window.stop(), next_step()))

    def online():
        window.show_page("library")
        window.page_library.show_tab("online")
        window.after(8000, lambda: (grab("online", window), window.page_library.show_tab("local"), next_step()))

    def overlay():
        game = fake_game()
        s.overlay_anchor, s.overlay_x, s.overlay_y = "custom", 60, 130
        window.overlay.apply_settings(s)
        window.play_tune_by_name("送别", "guide")

        def snap():
            game.update()
            grab("overlay_over_game", game, layered=True)
            window.stop()
            game.destroy()
            next_step()
        window.after(2500, snap)

    steps += [page("library"), playing, page("compose"), page("settings"), page("advanced"), online, overlay]

    def next_step():
        if steps:
            window.after(200, steps.pop(0))
        else:
            window.after(200, window.on_close)

    window.after(1200, next_step)
    window.mainloop()
    print(f"共 {len(shots)} 张")
    return 0 if shots else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_SANDBOX, ignore_errors=True)
