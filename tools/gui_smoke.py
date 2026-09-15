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
        from kouqin.tune import Cell, REST

        self.Cell = Cell
        self.REST = REST

        self.window = MainWindow()
        self.sender = DrySender()
        self.window.player.sender = self.sender
        self.window.settings.countdown = 0.0        # 测试不等倒计时
        self.window.settings.speed = 4.0            # 加速演完
        # 跳过首次运行的免责确认框和使用步骤，否则测试会卡在模态窗口上
        self.window.settings.disclaimer_accepted = True
        self.window.settings.guide_shown = True

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

        # 手动往格子里塞一段音：do do sol sol mi mi do(长)
        pattern = [(0, 0), (0, 0), (4, 0), (4, 0), (2, 0), (2, 0), (0, 0), (0, 0)]
        for index, (step, octave) in enumerate(pattern):
            page._apply_cell(index, self.Cell(step, octave))
        page._redraw()

        tune = page.current_tune()
        self.say(tune.note_count() == 8, "谱曲页填入音符",
                 f"{tune.note_count()} 拍有音，{tune.bar_count} 小节")
        self.say(len(tune.to_notes()) == 4, "相邻同音自动合并成长音",
                 f"8 拍 -> {len(tune.to_notes())} 个音")

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

    def step_advanced_build(self) -> None:
        self.window.show_page("advanced")
        self.window.update()
        self.say(self.window.page_advanced is not None, "进阶页构建成功")

    def step_cleanup(self) -> None:
        # 把测试曲子和测试键位清掉，别污染用户的曲库和设置
        self.window.library.delete(self.saved_name)
        from kouqin.layout import BASE_KEYS
        self.window.settings.keys = list(BASE_KEYS)
        self.window.settings.countdown = 3.0
        self.window.settings.speed = 1.0
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
