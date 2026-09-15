"""
主窗口 —— 一个壳，把三页装起来。

    ┌──────────────────────────────────────────────┐
    │  口琴工坊   [曲库] [谱曲] [设置] [进阶]   ■停止 │
    ├──────────────────────────────────────────────┤
    │                                              │
    │                当前页面                       │
    │                                              │
    ├──────────────────────────────────────────────┤
    │  状态栏 / 日志                                 │
    └──────────────────────────────────────────────┘

设计原则：**默认界面只留不懂音乐的人也看得懂的东西**。
MIDI 导入、钢琴卷帘这些给熟手用的功能全部收进「进阶」页，
不点进去就不会被吓到。

播放、热键、曲库、键位这些是全局共享的，放在这里统一管，
各页面自己不持有播放器，避免出现两个页面同时抢着发按键。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
import tkinter as tk
from typing import Dict, List, Optional

from kouqin.engine import PlayState, Player, compile_notes
from kouqin.hotkeys import GlobalHotkeys
from kouqin.layout import FoldMode, Layout
from kouqin.preview import Preview
from kouqin.settings import AppSettings, LIBRARY_DIR, settings_path
from kouqin.tune import Tune, TuneLibrary

from .composer_page import ComposerPage
from .disclaimer import ask_disclaimer
from .library_page import LibraryPage
from .settings_page import SettingsPage
from .theme import Palette, apply_theme, make_button, make_label

APP_NAME = "口琴工坊"
APP_SUB = "三角洲行动 · 口琴自动演奏"

#: 顶部导航：(标识, 显示名)
TABS = (
    ("library", "曲库"),
    ("compose", "谱曲"),
    ("settings", "设置"),
    ("advanced", "进阶"),
)


def is_admin() -> bool:
    """是否以管理员身份运行。向高权限进程注入输入需要同级权限。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class MainWindow(tk.Tk):
    """主界面。"""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} — {APP_SUB}")
        self.geometry("1120x740")
        self.minsize(880, 580)
        self.configure(bg=Palette["bg"])
        apply_theme(self)

        # ---------------- 共享状态 ----------------
        self.settings = AppSettings.load()
        self.layout: Layout = self.settings.to_layout()
        self.library = TuneLibrary(LIBRARY_DIR)
        self.preview = Preview()
        self.player = Player()
        self.hotkeys = GlobalHotkeys(poll_interval=0.02)
        self.current_tune: Optional[Tune] = None
        self._countdown_timer: Optional[str] = None

        self._install_presets_if_needed()
        self._build()
        self._bind_player()
        self._bind_hotkeys()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(120, self._startup)

    # ================================================================== #
    # 初始化
    # ================================================================== #

    def _install_presets_if_needed(self) -> None:
        """第一次运行时把内置曲目铺进曲库，让用户开箱就有的玩。"""
        if self.settings.installed_presets and self.library.names():
            return
        try:
            self.library.install_presets(overwrite=False)
        except OSError:
            pass
        self.settings.installed_presets = True
        self.settings.save()

    def _build(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build_nav()
        self._build_pages()
        self._build_bottom()

    def _build_nav(self) -> None:
        nav = tk.Frame(self, bg=Palette["panel"], height=54)
        nav.grid(row=0, column=0, sticky="ew")
        nav.grid_propagate(False)

        make_label(nav, APP_NAME, bold=True, size=13).pack(side=tk.LEFT, padx=(16, 20), pady=14)

        self._tab_buttons: Dict[str, tk.Button] = {}
        for key, label in TABS:
            button = tk.Button(
                nav, text=label, command=lambda k=key: self.show_page(k),
                bg=Palette["panel"], fg=Palette["text_dim"],
                activebackground=Palette["panel_alt"], activeforeground=Palette["text"],
                relief=tk.FLAT, bd=0, padx=16, pady=6, cursor="hand2",
                font=("Microsoft YaHei UI", 10), highlightthickness=0,
            )
            button.pack(side=tk.LEFT, padx=2, pady=10)
            self._tab_buttons[key] = button

        # 全局停止按钮：任何时候都能按到，出问题不用去翻页面
        self.btn_stop = make_button(nav, "■  停止演奏  (F8)", self.stop, kind="ghost")
        self.btn_stop.pack(side=tk.RIGHT, padx=(4, 16), pady=10)
        self.btn_stop.configure(state=tk.DISABLED)

        self.lbl_nav_status = make_label(nav, "", dim=True)
        self.lbl_nav_status.pack(side=tk.RIGHT, padx=12, pady=14)

    def _build_pages(self) -> None:
        holder = tk.Frame(self, bg=Palette["bg"])
        holder.grid(row=1, column=0, sticky="nsew")
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)

        self.pages: Dict[str, tk.Frame] = {}
        self.page_library = LibraryPage(holder, self)
        self.page_composer = ComposerPage(holder, self)
        self.page_settings = SettingsPage(holder, self)

        # 进阶页依赖较多，按需创建，避免启动变慢
        self.page_advanced = None

        self.pages["library"] = self.page_library
        self.pages["compose"] = self.page_composer
        self.pages["settings"] = self.page_settings

        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")

        self.show_page("library")

    def _build_bottom(self) -> None:
        bottom = tk.Frame(self, bg=Palette["panel"])
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.grid_columnconfigure(1, weight=1)

        self.lbl_status = make_label(bottom, "就绪", bg=Palette["panel"])
        self.lbl_status.grid(row=0, column=0, sticky="w", padx=12, pady=(5, 0))

        self.hint = make_label(bottom, "F6 开始/暂停　F8 停止", dim=True, bg=Palette["panel"])
        self.hint.grid(row=0, column=2, sticky="e", padx=12, pady=(5, 0))

        # 注意：控件叫 log_view，方法叫 log()。
        # 早先两者同名，结果 self.log 被控件覆盖，调用 self.log(...) 直接炸。
        self.log_view = tk.Text(
            bottom, height=3, bg=Palette["panel"], fg=Palette["text_dim"],
            relief=tk.FLAT, bd=0, font=("Consolas", 9), wrap="none", padx=10, pady=2,
            insertbackground=Palette["text"],
        )
        self.log_view.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 4))
        self.log_view.configure(state=tk.DISABLED)

    # ================================================================== #
    # 页面切换
    # ================================================================== #

    def show_page(self, key: str) -> None:
        if key == "advanced" and self.page_advanced is None:
            from .advanced_page import AdvancedPage
            self.page_advanced = AdvancedPage(self.pages["library"].master, self)
            self.page_advanced.grid(row=0, column=0, sticky="nsew")
            self.pages["advanced"] = self.page_advanced

        page = self.pages.get(key)
        if page is None:
            return
        page.tkraise()
        self._active_page = key

        for name, button in self._tab_buttons.items():
            active = name == key
            button.configure(
                fg=Palette["accent"] if active else Palette["text_dim"],
                bg=Palette["panel_alt"] if active else Palette["panel"],
                font=("Microsoft YaHei UI", 10, "bold" if active else "normal"),
            )

        if key == "settings":
            self.page_settings.on_show()
        elif key == "library":
            self.page_library.refresh()
        elif key == "advanced" and self.page_advanced is not None:
            self.page_advanced.on_show()

    # ================================================================== #
    # 播放
    # ================================================================== #

    def _bind_player(self) -> None:
        self.player.on_progress = lambda ms, i, n: self.after(0, self._on_progress, ms, i, n)
        self.player.on_state = lambda s: self.after(0, self._on_state, s)
        self.player.on_finish = lambda: self.after(0, self._on_finish)

    def _bind_hotkeys(self) -> None:
        self.hotkeys.bind("f6", lambda: self.after(0, self.toggle_play))
        self.hotkeys.bind("f8", lambda: self.after(0, self.stop))
        self.hotkeys.start()

    def play_tune(self, tune: Tune) -> None:
        """演奏一首曲子。"""
        notes = tune.to_notes()
        if not notes:
            self.log(f"《{tune.name}》里还没有音，先去「谱曲」页填几拍吧")
            return

        events, report = compile_notes(
            notes, self.layout, self.settings.timing_preset(),
            speed=self.settings.speed, fold=FoldMode.FOLD,
        )
        if not events:
            self.log("编译后没有可演奏的音符，检查一下设置里的键位")
            return

        self.player.stop()
        self.player.sender.stats.__init__()          # 重置注入计数
        self.player.load(events)
        self.current_tune = tune

        self.show_page("library")
        self.page_library.show_playing(tune.name)
        self.btn_stop.configure(state=tk.NORMAL)

        if self.settings.countdown > 0:
            self.log(f"《{tune.name}》：{report.summary()}，"
                     f"{self.settings.countdown:.0f} 秒后开始，快切到游戏")
        else:
            self.log(f"《{tune.name}》：{report.summary()}，开始演奏")

        self.player.play(countdown_ms=self.settings.countdown * 1000.0)
        self._start_countdown_ticker()

    def play_notes(self, notes, name: str = "进阶模式", transpose: int = 0,
                   fold: str = FoldMode.FOLD, speed: Optional[float] = None) -> None:
        """演奏一组现成的音符（进阶页 / 卷帘用）。"""
        if not notes:
            self.log("没有可演奏的音符")
            return
        events, report = compile_notes(
            notes, self.layout, self.settings.timing_preset(),
            speed=speed if speed is not None else self.settings.speed,
            transpose=transpose, fold=fold,
        )
        if not events:
            self.log("编译后没有可演奏的事件，检查键位设置或超音域策略")
            return

        self.player.stop()
        self.player.sender.stats.__init__()
        self.player.load(events)
        self.current_tune = None
        self._play_name = name

        self.btn_stop.configure(state=tk.NORMAL)
        self.log(f"{name}：{report.summary()}，{self.settings.countdown:.0f} 秒后开始")
        self.player.play(countdown_ms=self.settings.countdown * 1000.0)
        self._start_advanced_ticker(name)

    def _start_advanced_ticker(self, name: str) -> None:
        """进阶模式没有曲库页的进度条，用状态栏报进度。"""
        self._stop_countdown_ticker()

        def tick():
            state = self.player.state
            if state == PlayState.COUNTDOWN:
                self.lbl_status.configure(
                    text=f"倒计时 {self.player.countdown_remaining:.1f} 秒，快切到游戏窗口！")
                self._countdown_timer = self.after(120, tick)
                return
            if state in (PlayState.PLAYING, PlayState.PAUSED):
                total = self.player.total or 1.0
                self.lbl_status.configure(
                    text=f"{name}　{_mmss(self.player.position)} / {_mmss(total)}")
                self._countdown_timer = self.after(200, tick)
                return
            self._countdown_timer = None

        tick()

    def play_tune_by_name(self, name: str) -> None:
        tune = self.library.load(name)
        if tune is None:
            self.log(f"读不到《{name}》，可能文件被删了")
            self.page_library.refresh()
            return
        self.play_tune(tune)

    def toggle_play(self) -> None:
        """F6：倒计时中取消，演奏中暂停，暂停中继续。"""
        state = self.player.state
        if state == PlayState.COUNTDOWN:
            self.stop()
        elif state in (PlayState.PLAYING, PlayState.PAUSED):
            self.player.toggle()
        elif state == PlayState.IDLE and self.current_tune is not None:
            self.play_tune(self.current_tune)

    def stop(self) -> None:
        self.player.stop()
        self.preview.stop()
        self._stop_countdown_ticker()
        self.current_tune = None
        self.btn_stop.configure(state=tk.DISABLED)
        self.lbl_status.configure(text="已停止，按键已全部释放")
        self.lbl_nav_status.configure(text="")
        self.page_library.show_idle()

    # ------------------------------------------------------------------ #

    def _on_progress(self, ms: float, index: int, total: int) -> None:
        total_ms = self.player.total or 1.0
        ratio = max(0.0, min(1.0, ms / total_ms))
        self.page_library.set_progress(ratio, f"{_mmss(ms)} / {_mmss(total_ms)}")
        self.lbl_status.configure(text=f"演奏中　{_mmss(ms)} / {_mmss(total_ms)}")

    def _on_state(self, state: str) -> None:
        name = self.current_tune.name if self.current_tune else ""
        if state == PlayState.PLAYING:
            self.lbl_status.configure(text="演奏中…")
            self.lbl_nav_status.configure(text="演奏中", fg=Palette["accent"])
            self.btn_stop.configure(state=tk.NORMAL)
            self.page_library.show_playing(name)
        elif state == PlayState.PAUSED:
            self.lbl_status.configure(text="已暂停（按 F6 继续）")
            self.lbl_nav_status.configure(text="已暂停", fg=Palette["text_dim"])
            self.page_library.show_paused(name)
        elif state == PlayState.COUNTDOWN:
            self.lbl_status.configure(text="倒计时中，快切到游戏窗口！")
            self.lbl_nav_status.configure(text="倒计时…", fg=Palette["warn"])
            self.btn_stop.configure(state=tk.NORMAL)
            self.page_library.show_countdown(self.settings.countdown, name)
        elif state == PlayState.IDLE:
            self.lbl_nav_status.configure(text="")
            self.lbl_status.configure(text="就绪")
            self.btn_stop.configure(state=tk.DISABLED)

    def _on_finish(self) -> None:
        name = self.current_tune.name if self.current_tune else ""
        self._stop_countdown_ticker()
        self.lbl_status.configure(text="演奏完成")
        self.lbl_nav_status.configure(text="")
        self.btn_stop.configure(state=tk.DISABLED)
        self.page_library.show_done(name)
        self.page_library.set_progress(1.0, "")
        stats = self.player.sender.stats
        self.log(f"演奏完成（{stats.summary()}）")
        self.current_tune = None

    def _start_countdown_ticker(self) -> None:
        self._stop_countdown_ticker()

        def tick():
            if self.player.state != PlayState.COUNTDOWN:
                self._countdown_timer = None
                return
            remain = self.player.countdown_remaining
            name = self.current_tune.name if self.current_tune else ""
            self.lbl_status.configure(text=f"倒计时 {remain:.1f} 秒，快切到游戏窗口！")
            self.page_library.show_countdown(remain, name)
            self._countdown_timer = self.after(120, tick)

        tick()

    def _stop_countdown_ticker(self) -> None:
        if self._countdown_timer:
            try:
                self.after_cancel(self._countdown_timer)
            except tk.TclError:
                pass
            self._countdown_timer = None

    # ================================================================== #
    # 给页面调用的接口
    # ================================================================== #

    def log(self, text: str) -> None:
        view = self.log_view
        view.configure(state=tk.NORMAL)
        view.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {text}\n")
        view.see(tk.END)
        # 只留最近 200 行，长时间挂着也不会把内存吃满
        if int(view.index("end-1c").split(".")[0]) > 200:
            view.delete("1.0", "50.0")
        view.configure(state=tk.DISABLED)

    def refresh_library(self) -> None:
        self.page_library.refresh()

    def new_tune(self) -> None:
        self.page_composer.new_tune()
        self.show_page("compose")
        self.log("已新建空白曲子，点格子开始填吧")

    def edit_tune(self, name: str) -> None:
        tune = self.library.load(name)
        if tune is None:
            self.log(f"读不到《{name}》")
            return
        self.page_composer.load_tune(tune)
        self.show_page("compose")
        self.log(f"正在编辑《{name}》")

    def apply_settings(self, settings: AppSettings) -> None:
        """设置页保存后调用：落盘 + 重建键位表。"""
        self.settings = settings
        self.settings.save()
        self.layout = self.settings.to_layout()
        self.log("设置已生效：" + self.layout.summary())

    def open_midi_file(self, path: str) -> None:
        """从外部载入 MIDI（启动参数用）。"""
        self.show_page("advanced")
        if self.page_advanced is not None:
            self.page_advanced.load_midi(path)

    # ================================================================== #
    # 收尾
    # ================================================================== #

    def _startup(self) -> None:
        """启动流程：先过免责确认，再显示日常提示。"""
        if not self.settings.disclaimer_accepted:
            if not ask_disclaimer(self):
                # 不同意就直接退出，不留下任何东西
                self.destroy()
                return
            self.settings.disclaimer_accepted = True
            self.settings.save()

        self._boot_messages()

    def _boot_messages(self) -> None:
        self.log("─" * 58)
        self.log("⚠ 本项目仅供个人学习、技术研究与娱乐使用。")
        self.log("⚠ 严禁在任何在线对局中使用 —— 可能违反游戏用户协议并导致封号。")
        self.log("⚠ 一切后果由使用者自行承担，作者不承担任何责任。详见 DISCLAIMER.md")
        self.log("─" * 58)
        self.log(f"{APP_NAME} 已启动。热键：F6 开始/暂停　F8 停止")
        if not is_admin():
            self.log("提示：当前不是管理员权限。若游戏里完全没反应，请用「以管理员启动.bat」重开。")
        if not self.preview.available:
            self.log("提示：没检测到系统 MIDI 输出设备，试听按钮没声音（不影响正常演奏）")
        self.log(f"设置文件：{settings_path()}")

    def on_close(self) -> None:
        try:
            self.player.stop()
            self.preview.close()
            self.hotkeys.shutdown()
            self.settings.save()
        finally:
            self.destroy()


# ---------------------------------------------------------------------- #


def _mmss(ms: float) -> str:
    total = max(0.0, ms) / 1000.0
    return f"{int(total // 60):02d}:{total % 60:05.2f}"


def run() -> None:
    """启动界面。"""
    MainWindow().mainloop()
