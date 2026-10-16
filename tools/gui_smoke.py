#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
界面冒烟测试。

真的把窗口开起来，依次走一遍四个页面，并且模拟真实操作：

    谱曲页 -> 填几个格子 -> 存进曲库
    设置页 -> 改键位 -> 保存
    曲库页 -> 播放刚存的曲子 -> 等它演完
    进阶页 -> 构建（不载文件）

关键点：跑之前会把播放器的注入器换成 DrySender（只记录、不真按键），
所以**这个测试不会往你的系统里发任何按键**，可以放心跑。

用法：
    python tools/gui_smoke.py
"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class DrySender:
    """只记录不注入。"""

    def __init__(self) -> None:
        self.count = 0
        self.held = set()

    def key_down(self, key):
        self.count += 1
        self.held.add(("k", key))
        return True

    def key_up(self, key):
        self.count += 1
        self.held.discard(("k", key))
        return True

    def mouse_down(self, button):
        self.count += 1
        self.held.add(("m", button))
        return True

    def mouse_up(self, button):
        self.count += 1
        self.held.discard(("m", button))
        return True

    def release_all(self):
        self.held.clear()

    class _Stats:
        def summary(self):
            return "（冒烟测试，未真实注入）"

    stats = _Stats()


class Smoke:
    def __init__(self) -> None:
        from gui.main_window import MainWindow
        from kouqin.tune import HOLD, Cell, REST

        self.Cell = Cell
        self.REST = REST
        self.HOLD = HOLD

        self.window = MainWindow()
        self.sender = DrySender()
        self.window.player.sender = self.sender
        settings = self.window.settings
        self._restore = {name: getattr(settings, name)
                         for name in ("guide_wait", "guide_speed", "overlay_scale")}
        settings.countdown = 0.0        # 测试不等倒计时
        settings.speed = 4.0            # 加速演完
        settings.guide_speed = 4.0
        settings.guide_wait = False     # 开着"等我按对"的话没人按键，测试会一直等
        # 跳过首次运行的免责确认框和使用步骤，否则测试会卡在模态窗口上
        settings.disclaimer_accepted = True
        settings.guide_shown = True

        self.failures = []
        self.notes = []
        self.played = False
        self.saved_name = "冒烟测试曲"

        self.steps = [
            self.step_library_empty_check,
            self.step_compose_fill,
            self.step_compose_save,
            self.step_settings_change_keys,
            self.step_settings_save,
            self.step_library_play,
            self.step_wait_finish,
            self.step_guide_play,
            self.step_guide_finish,
            self.step_auto_after_guide,
            self.step_overlay_adjust,
            self.step_settings_overlay,
            self.step_overlay_long_press,
            self.step_online_library,
            self.step_online_finish,
            self.step_online_save,
            self.step_online_saved_play,
            self.step_online_saved_finish,
            self.step_advanced_build,
            self.step_cleanup,
        ]

    # ------------------------------------------------------------------ #

    def say(self, ok: bool, label: str, detail: str = "") -> None:
        mark = "通过" if ok else "失败"
        print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            self.failures.append(label)

    def run(self) -> None:
        self.window.after(300, self._next)

    def _next(self) -> None:
        if not self.steps:
            return self._finish()
        step = self.steps.pop(0)
        try:
            step()
        except Exception as exc:                       # noqa: BLE001
            self.failures.append(step.__name__)
            print(f"  [异常] {step.__name__} — {type(exc).__name__}: {exc}")
        self.window.after(300, self._next)

    # ------------------------------------------------------------------ #
    # 各步骤
    # ------------------------------------------------------------------ #

    def step_library_empty_check(self) -> None:
        self.window.show_page("library")
        count = len(self.window.library.names())
        self.say(count >= 8, "曲库已完成初始化", f"{count} 首曲子")

    def step_compose_fill(self) -> None:
        self.window.show_page("compose")
        page = self.window.page_composer
        page.new_tune()
        page.var_name.set(self.saved_name)
        page._on_name_change()

        # 手动往格子里塞一段音：do do sol sol mi — do —（两个延长）
        pattern = [(0, 0), (0, 0), (4, 0), (4, 0), (2, 0), None, (0, 0), None]
        for index, item in enumerate(pattern):
            page._apply_cell(index, self.HOLD if item is None else self.Cell(*item))
        page._redraw()

        tune = page.current_tune()
        notes = tune.to_notes()
        self.say(tune.note_count() == 6, "谱曲页填入音符",
                 f"{tune.note_count()} 个音，{tune.bar_count} 小节")
        self.say(len(notes) == 6 and notes[1].pitch == 60 and notes[4].duration > notes[3].duration,
                 "同音连吹两下、延长把音拖长", f"8 拍 -> {len(notes)} 个音")

    def step_compose_save(self) -> None:
        page = self.window.page_composer
        ok = page._save(silent=True)
        self.say(ok and self.window.library.exists(self.saved_name),
                 "保存进曲库", self.saved_name)

    def step_settings_change_keys(self) -> None:
        self.window.show_page("settings")
        page = self.window.page_settings
        # 直接把键位改成另一套，再走保存流程
        page.settings.keys = list("asdfghjk")
        for button in page.key_buttons:
            button.refresh()
        self.say(page.settings.keys[0] == "a", "设置页改键位", "do -> A")

    def step_settings_save(self) -> None:
        page = self.window.page_settings
        page._save()
        layout = self.window.layout
        self.say(layout.lookup(60).key == "a",
                 "键位已生效到引擎", f"C4 -> {layout.lookup(60).describe()}")

    def step_library_play(self) -> None:
        self.window.show_page("library")
        self.window.play_tune_by_name(self.saved_name)
        self.played = self.window.player.is_active
        self.say(self.played, "从曲库点播", "播放器已启动")

    def step_wait_finish(self) -> None:
        # 曲子很短（8 拍 / 4 倍速），等一下就该结束
        deadline = time.time() + 6.0
        while self.window.player.is_active and time.time() < deadline:
            self.window.update()
            time.sleep(0.02)
        injected = self.sender.count
        self.say(not self.window.player.is_active, "演奏正常结束")
        self.say(injected > 0, "演奏过程产生了按键事件", f"{injected} 条")
        self.say(not self.sender.held, "结束后没有残留按键", f"残留 {self.sender.held}")

    def _wait_player(self, limit: float = 6.0) -> None:
        deadline = time.time() + limit
        while self.window.player.is_active and time.time() < deadline:
            self.window.update()
            time.sleep(0.02)

    def step_guide_play(self) -> None:
        from kouqin.engine import NullSender
        self.injected_before_guide = self.sender.count
        self.window.play_tune_by_name(self.saved_name, "guide")
        self.say(isinstance(self.window.player.sender, NullSender), "跟弹换上了不发按键的注入器")
        self.say(self.window.overlay.active, "跟弹时悬浮提示窗已开始工作",
                 "（悬浮窗创建失败）" if self.window.overlay.failed else "")

    def step_guide_finish(self) -> None:
        self._wait_player()
        self.window.update()
        self.say(not self.window.player.is_active, "跟弹正常结束")
        self.say(self.sender.count == self.injected_before_guide, "跟弹全程没有发出任何按键",
                 f"多出 {self.sender.count - self.injected_before_guide} 条")
        judge = self.window.overlay.judge
        self.say(judge is not None and judge.missed == judge.total, "没人按键时每个音都判成漏",
                 judge.summary() if judge else "没有判定器")

    def step_auto_after_guide(self) -> None:
        # 跟弹之后再自动演奏：必须换回测试的假注入器，而不是系统的真注入器
        before = self.sender.count
        self.window.play_tune_by_name(self.saved_name, "auto")
        self.say(self.window.player.sender is self.sender, "自动演奏换回了原来的注入器")
        self._wait_player()
        self.say(self.sender.count > before, "换回后照常发出按键事件", f"{self.sender.count - before} 条")

    def step_overlay_adjust(self) -> None:
        overlay = self.window.overlay
        overlay.set_adjusting(True)
        self.window.update()
        window = overlay.window
        self.say(window is not None and not window.locked, "调整位置时悬浮窗解锁")
        overlay.set_adjusting(False)
        self.window.update()
        self.say(window is not None and window.locked, "调整结束后悬浮窗重新锁定（点击穿透）")

    def step_settings_overlay(self) -> None:
        self.window.show_page("settings")
        page = self.window.page_settings
        page.var_scale.set("大")
        page._on_overlay_option()
        page._save()
        self.say(abs(self.window.settings.overlay_scale - 1.25) < 0.01, "悬浮窗大小设置已保存",
                 f"{self.window.settings.overlay_scale}")

    def step_overlay_long_press(self) -> None:
        from gui.overlay import LongPressDrag
        rect = (100, 100, 300, 120)

        def feed(press, steps):
            out = []
            for t, down, x, y, *rest in steps:
                act = press.step(t, down, x, y, rect, allow=rest[0] if rest else True)
                if act:
                    out.append(act)
            return out

        acts = feed(LongPressDrag(), [(0.0, True, 150, 150), (0.3, True, 152, 151), (0.55, True, 151, 150),
                                      (0.6, True, 251, 350), (0.7, False, 261, 360)])
        self.say(acts == [("start", 100, 100), ("move", 200, 300), ("drop", 210, 310)],
                 "悬浮窗长按半秒后跟着光标走、松开放下", str(acts))
        quick = feed(LongPressDrag(), [(0.0, True, 150, 150), (0.1, True, 190, 150), (0.8, True, 260, 150),
                                       (0.9, False, 260, 150)])
        outside = feed(LongPressDrag(), [(0.0, True, 50, 50), (0.2, True, 150, 150), (0.9, True, 150, 150)])
        blocked = feed(LongPressDrag(), [(0.0, True, 150, 150, False), (0.9, True, 150, 150, True)])
        self.say(not quick and not outside and not blocked,
                 "按下就挪开、从外面拖进来、光标藏着时都不会拖走悬浮窗")

        window = self.window.overlay.ensure_window()
        before = (self.window.settings.overlay_x, self.window.settings.overlay_y, self.window.settings.overlay_anchor)
        window.apply_press(("start", window.winfo_x(), window.winfo_y()))
        marked = bool(window.canvas.find_withtag("dragmark"))
        window.apply_press(("drop", 321, 234))
        self.window.update()
        s = self.window.settings
        self.say(marked and not window.canvas.find_withtag("dragmark")
                 and (s.overlay_x, s.overlay_y, s.overlay_anchor) == (321, 234, "custom"),
                 "长按拖动放下后位置存成「拖动过的位置」", f"{before} -> {(s.overlay_x, s.overlay_y, s.overlay_anchor)}")
        self.window.settings.overlay_x, self.window.settings.overlay_y, self.window.settings.overlay_anchor = before
        self.window.page_settings.settings.overlay_anchor = before[2]
        self.window.settings.save()
        self.window.overlay.apply_settings(self.window.settings)

    def step_online_library(self) -> None:
        # 换成假的在线曲库，测试不联网
        from gui.online_page import OnlinePanel
        from kouqin.online import OnlineEntry
        from kouqin.smf import Note

        class FakeLibrary:
            def catalog(self, force=False):
                return [OnlineEntry("abc123", "测试在线曲", uploader="冒烟", bpm=240, duration_ms=1500,
                                    note_count=3)]

            def playable_notes(self, entry_id):
                notes = [Note(0, 250, 60), Note(250, 250, 64), Note(500, 500, 67)]
                return self.catalog()[0], notes, 0

        self.window.show_page("library")
        page = self.window.page_library
        page.online = OnlinePanel(page, self.window, factory=lambda: FakeLibrary())
        page.online.grid(row=1, column=0, sticky="nsew")
        page.show_tab("online")
        deadline = time.time() + 3
        while not page.online._entries and time.time() < deadline:
            self.window.update()
            time.sleep(0.02)
        self.say(len(page.online._entries) == 1, "在线曲库目录显示出来了", page.online.lbl_status.cget("text"))
        self.injected_before_online = self.sender.count
        page.online.tree.selection_set("0")
        page.online.play_selected("guide")
        deadline = time.time() + 3
        while not self.window.player.is_active and time.time() < deadline:
            self.window.update()
            time.sleep(0.02)
        self.say(self.window.player.is_active and self.window.play_mode == "guide",
                 "在线曲谱下载后开始跟弹", self.window._play_name)

    def step_online_finish(self) -> None:
        self._wait_player()
        self.say(self.sender.count == self.injected_before_online, "在线曲谱跟弹全程没发按键")
        self.window.page_library.show_tab("local")

    def step_online_save(self) -> None:
        page = self.window.page_library
        page.online.tree.selection_set("0")
        page.online.save_selected()
        deadline = time.time() + 3
        while not self.window.library.exists("测试在线曲") and time.time() < deadline:
            self.window.update()
            time.sleep(0.02)
        tune = self.window.library.load("测试在线曲")
        self.say(tune is not None and not tune.is_grid and tune.note_count() == 3,
                 "在线曲谱原样存进我的曲库", page.online.lbl_selected.cget("text"))
        page.online.save_selected()                    # 同一首再存一次
        for _ in range(10):
            self.window.update()
            time.sleep(0.02)
        self.say(not self.window.library.exists("测试在线曲 (2)"), "同一首不会存两份",
                 page.online.lbl_selected.cget("text"))
        page.show_tab("local")
        self.window.update()
        row = next((r for n, r, _bg, _t in page._row_widgets if n == "测试在线曲"), None)
        texts = []
        stack = [row] if row is not None else []
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            try:
                texts.append(str(widget.cget("text")))
            except tk.TclError:
                pass
        self.say(row is not None and "编辑" not in texts and any("来自在线曲库" in t for t in texts),
                 "我的曲库里标了出处、没有编辑按钮")

    def step_online_saved_play(self) -> None:
        self.injected_before_saved = self.sender.count
        self.window.play_tune_by_name("测试在线曲", "guide")
        self.say(self.window.player.is_active, "存下来的在线曲谱能直接跟弹")

    def step_online_saved_finish(self) -> None:
        self._wait_player()
        self.say(self.sender.count == self.injected_before_saved, "跟弹全程没发按键")

    def step_advanced_build(self) -> None:
        self.window.show_page("advanced")
        self.window.update()
        self.say(self.window.page_advanced is not None, "进阶页构建成功")

    def step_cleanup(self) -> None:
        # 把测试曲子和测试键位清掉，别污染用户的曲库和设置
        self.window.library.delete(self.saved_name)
        self.window.library.delete("测试在线曲")
        from kouqin.layout import BASE_KEYS
        self.window.settings.keys = list(BASE_KEYS)
        self.window.settings.countdown = 3.0
        self.window.settings.speed = 1.0
        for name, value in self._restore.items():
            setattr(self.window.settings, name, value)
        self.window.apply_settings(self.window.settings)
        self.window.refresh_library()
        self.say(not self.window.library.exists(self.saved_name), "清理测试数据")

    # ------------------------------------------------------------------ #

    def _finish(self) -> None:
        try:
            self.window.on_close()
        except Exception:                              # noqa: BLE001
            pass
        print()
        if self.failures:
            print(f"结果：{len(self.failures)} 项失败 -> {', '.join(self.failures)}")
            sys.exit(1)
        print("结果：全部通过 ✔")


def main() -> int:
    print("=" * 62)
    print("界面冒烟测试（使用假注入器，不会真的发按键）")
    print("=" * 62)
    smoke = Smoke()
    smoke.run()
    smoke.window.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
