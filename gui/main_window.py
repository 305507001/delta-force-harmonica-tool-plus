"""
主窗口 —— 一个壳，把几个页面装起来。

    ┌──────────┬───────────────────────────────────┐
    │ 口琴工坊  │                                   │
    │          │                                   │
    │ ♪ 曲库    │            当前页面                │
    │ ✎ 谱曲    │                                   │
    │ ⋯ 进阶    │                                   │
    │          │                                   │
    │ ■ 停止    ├───────────────────────────────────┤
    │ ⚙ 设置    │  状态 / 日志                       │
    └──────────┴───────────────────────────────────┘

设计原则：**默认界面只留不懂音乐的人也看得懂的东西**。
MIDI 导入、钢琴卷帘这些给熟手用的功能全部收进「进阶」页，
不点进去就不会被吓到。

播放、热键、曲库、键位这些是全局共享的，放在这里统一管，
各页面自己不持有播放器，避免出现两个页面同时抢着发按键。
"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import time
import tkinter as tk
from typing import Callable, Dict, List, Optional, Tuple

from kouqin.engine import NullSender, PlayState, Player, compile_notes
from kouqin.hotkeys import GlobalHotkeys
from kouqin.layout import FoldMode, Layout
from kouqin.preview import Preview
from kouqin.settings import AppSettings, LIBRARY_DIR, hotkey_label, settings_path
from kouqin.tune import PRESET_REVISION, Tune, TuneLibrary

from .composer_page import ComposerPage
from .disclaimer import ask_disclaimer
from .library_page import LibraryPage
from .overlay import OverlayController
from .settings_page import SettingsPage
from .theme import (
    Palette, apply_theme, enable_dpi_awareness, make_button, make_label, mono_font, px, screen_scale,
    set_appearance, title_font, ui_scale,
)
from .widgets import RoundButton

APP_NAME = "口琴工坊"
APP_SUB = "三角洲行动 · 口琴自动演奏 / 跟弹提示"

#: 两种演奏方式：auto = 程序替你按；guide = 跟弹，程序只在悬浮窗里提示、一个键都不按
MODE_AUTO = "auto"
MODE_GUIDE = "guide"

#: 侧栏导航：(标识, 显示名, 图标)。设置单独放在侧栏底部
TABS = (
    ("library", "曲库", "library"),
    ("compose", "谱曲", "compose"),
    ("advanced", "进阶", "advanced"),
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
        # 4K 这类高缩放屏幕：先告诉 Windows 程序自己按 DPI 缩放，不然整个窗口会被拉大、发糊
        enable_dpi_awareness()
        super().__init__()
        # 外观和大小要在建任何控件之前定下来：各页面建控件时直接读配色和缩放
        self.settings = AppSettings.load()
        self.theme_name = set_appearance(self.settings.appearance)
        self.restart_requested = False
        self.title(f"{APP_NAME} — {APP_SUB}")
        apply_theme(self, self.settings.ui_size)
        self.configure(bg=Palette["bg"])
        self._dark_title_bar()

        # ---------------- 共享状态 ----------------
        self.layout: Layout = self.settings.to_layout()
        self.library = TuneLibrary(LIBRARY_DIR)
        self.preview = Preview()
        self.player = Player()
        self.hotkeys = GlobalHotkeys(poll_interval=0.02)
        self.current_tune: Optional[Tune] = None
        self._countdown_timer: Optional[str] = None
        self.play_mode = MODE_AUTO
        self._play_name = ""
        # 跟弹时换上"什么都不发"的注入器；_input_sender 记着自动演奏该用的那个
        self._null_sender = NullSender()
        self._input_sender = self.player.sender
        self.overlay = OverlayController(self)

        self._install_presets_if_needed()
        self._build()
        self._fit_window()
        # 别的线程（播放、热键、联网）交给界面线程的活儿都走这个队列，界面线程每 10ms 取一次。
        # 不能直接在别的线程里调 self.after(...)：Tk 会让那个线程停下来等界面线程处理完，
        # 界面线程这时要是正在等它（比如停止演奏时等播放线程退出），两边就互相卡住。
        self._ui_calls: "queue.SimpleQueue" = queue.SimpleQueue()
        self._pump_job: Optional[str] = None
        self._pump_ui()
        self._bind_player()
        self._bind_hotkeys()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(120, self._startup)

    # ================================================================== #
    # 初始化
    # ================================================================== #

    def _install_presets_if_needed(self) -> None:
        """
        第一次运行时把内置曲目铺进曲库，让用户开箱就有的玩。

        内置曲谱改过版（PRESET_REVISION 变大）时，把曲库里没改过的旧副本换成新版。
        """
        self._upgraded_presets: List[str] = []
        if self.settings.preset_revision < PRESET_REVISION:
            try:
                self._upgraded_presets = self.library.upgrade_presets()
            except OSError:
                pass
            self.settings.preset_revision = PRESET_REVISION
            self.settings.save()
        if self.settings.installed_presets and self.library.names():
            return
        try:
            self.library.install_presets(overwrite=False)
        except OSError:
            pass
        self.settings.installed_presets = True
        self.settings.save()

    def _fit_window(self) -> None:
        """
        窗口大小按内容算：刚好能把每个页面（包括在线曲库）的按钮都完整摆下，再留一点余量；
        最小尺寸也定成这么大，免得拖小了把按钮挤没。上次关掉时调过大小就用上次的。
        """
        library = self.page_library
        library.layout_tab("online")             # 在线曲库那一栏最宽，先摆出来量一下（不联网）
        self.update_idletasks()
        need_w, need_h = self.winfo_reqwidth(), self.winfo_reqheight()
        library.layout_tab("local")
        self.update_idletasks()
        need_w, need_h = max(need_w, self.winfo_reqwidth()), max(need_h, self.winfo_reqheight())
        left, top, area_w, area_h = _work_area(self)
        self._need = (min(need_w, area_w), min(need_h, area_h))
        self.minsize(*self._need)

        s = self.settings
        width, height = need_w + px(24), need_h + px(40)
        if s.window_w and s.window_h and abs(s.window_scale - ui_scale()) < 0.01:
            width, height = max(s.window_w, need_w), max(s.window_h, need_h)
        width, height = min(width, area_w), min(height, area_h - px(30))   # 标题栏另算
        x = left + max(0, (area_w - width) // 2)
        y = top + max(0, (area_h - height) // 3)
        self.geometry(f"{width}x{height}+{x}+{y}")
        if s.window_zoomed and sys.platform == "win32":
            self.state("zoomed")

    def _ensure_fits(self) -> None:
        """后建出来的页面（高级）要的地方更大时，把窗口和最小尺寸跟着放大。"""
        self.update_idletasks()
        _left, _top, area_w, area_h = _work_area(self)
        need_w = min(max(self._need[0], self.winfo_reqwidth()), area_w)
        need_h = min(max(self._need[1], self.winfo_reqheight()), area_h)
        if (need_w, need_h) == self._need:
            return
        self._need = (need_w, need_h)
        self.minsize(need_w, need_h)
        if self.winfo_width() < need_w or self.winfo_height() < need_h:
            self.geometry(f"{max(self.winfo_width(), need_w)}x{max(self.winfo_height(), need_h)}")

    def _remember_size(self) -> None:
        s = self.settings
        try:
            s.window_zoomed = self.state() == "zoomed"
            if not s.window_zoomed and self.state() == "normal":
                s.window_w, s.window_h = self.winfo_width(), self.winfo_height()
                s.window_scale = ui_scale()
        except tk.TclError:
            pass

    def _dark_title_bar(self) -> None:
        """深色外观时把 Windows 的标题栏也换成深色（Win10 1809+ / Win11 才有，别的系统什么都不做）。"""
        if sys.platform != "win32" or self.theme_name != "dark":
            return
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id()) or self.winfo_id()
            value = ctypes.c_int(1)
            for attribute in (20, 19):          # DWMWA_USE_IMMERSIVE_DARK_MODE（新 / 旧编号）
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                               ctypes.sizeof(value)) == 0:
                    break
        except (AttributeError, OSError):
            pass

    def _build(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_nav()
        self._build_pages()
        self._build_bottom()

    def _build_nav(self) -> None:
        P = Palette
        side = tk.Frame(self, bg=P["panel_alt"], width=px(168))
        side.grid(row=0, column=0, rowspan=2, sticky="ns")
        side.pack_propagate(False)
        tk.Frame(side, bg=P["border"], width=px(1)).pack(side=tk.RIGHT, fill=tk.Y)
        inner = tk.Frame(side, bg=P["panel_alt"])
        inner.pack(fill=tk.BOTH, expand=True, padx=px(8), pady=(px(14), px(10)))

        tk.Label(inner, text=APP_NAME, font=title_font(15), bg=P["panel_alt"], fg=P["text"],
                 anchor="w").pack(fill=tk.X, padx=px(10))
        make_label(inner, "三角洲行动 · 口琴", size=8, fg=P["text_muted"], anchor="w").pack(
            fill=tk.X, padx=px(10), pady=(px(2), px(12)))

        def nav_button(key: str, label: str, icon_name: str) -> RoundButton:
            button = RoundButton(inner, text=label, icon_name=icon_name, kind="nav", anchor="w",
                                 height=40, size=11, padx=12, command=lambda: self.show_page(key))
            self._tab_buttons[key] = button
            return button

        self._tab_buttons: Dict[str, RoundButton] = {}
        for key, label, icon_name in TABS:
            nav_button(key, label, icon_name).pack(fill=tk.X, pady=px(1))

        tk.Frame(inner, bg=P["panel_alt"]).pack(fill=tk.BOTH, expand=True)

        # 演奏中的状态 + 全局停止：任何页面都能按到，出问题不用去翻页面
        self.lbl_nav_status = make_label(inner, "", dim=True, anchor="w")
        self.lbl_nav_status.pack(fill=tk.X, padx=px(10), pady=(0, px(4)))
        self.btn_stop = make_button(inner, f"停止演奏  {self.hotkey_text('stop')}", self.stop, kind="danger")
        self.btn_stop.pack(fill=tk.X, pady=(0, px(12)))
        self.btn_stop.configure(state=tk.DISABLED)

        nav_button("settings", "设置", "settings").pack(fill=tk.X, pady=px(1))
        RoundButton(inner, text="使用步骤", icon_name="help", kind="nav", anchor="w", height=40, size=11,
                    padx=12, command=self.show_guide).pack(fill=tk.X, pady=px(1))
        self.lbl_hotkeys = make_label(inner, self._hotkey_hint(), size=8, fg=P["text_muted"], anchor="w",
                                      justify=tk.LEFT)
        self.lbl_hotkeys.pack(fill=tk.X, padx=px(10), pady=(px(10), 0))

    def _build_pages(self) -> None:
        holder = tk.Frame(self, bg=Palette["bg"])
        holder.grid(row=0, column=1, sticky="nsew")
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
        P = Palette
        bottom = tk.Frame(self, bg=P["bg"])
        bottom.grid(row=1, column=1, sticky="ew")
        bottom.grid_columnconfigure(0, weight=1)
        tk.Frame(bottom, bg=P["border"], height=px(1)).grid(row=0, column=0, sticky="ew")

        self.lbl_status = make_label(bottom, "就绪", dim=True, anchor="w")
        self.lbl_status.grid(row=1, column=0, sticky="ew", padx=px(16), pady=(px(4), 0))

        # 注意：控件叫 log_view，方法叫 log()。
        # 早先两者同名，结果 self.log 被控件覆盖，调用 self.log(...) 直接炸。
        self.log_view = tk.Text(
            bottom, height=2, bg=P["bg"], fg=P["text_muted"], relief=tk.FLAT, bd=0,
            font=mono_font(9), wrap="none", padx=px(16), pady=px(1), insertbackground=P["text"],
            selectbackground=P["sel_bg"], highlightthickness=0,
        )
        self.log_view.grid(row=2, column=0, sticky="ew", pady=(px(1), px(6)))
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
            self._ensure_fits()

        page = self.pages.get(key)
        if page is None:
            return
        page.tkraise()
        self._active_page = key

        for name, button in self._tab_buttons.items():
            button.configure(kind="nav_on" if name == key else "nav")

        if key == "settings":
            self.page_settings.on_show()
        elif key == "library":
            self.page_library.refresh()
        elif key == "advanced" and self.page_advanced is not None:
            self.page_advanced.on_show()

    # ================================================================== #
    # 播放
    # ================================================================== #

    def call_soon(self, callback: Callable, *args) -> None:
        """让界面线程尽快执行 ``callback(*args)``。任何线程都能调，不会卡住调用的线程。"""
        self._ui_calls.put((callback, args))

    def _pump_ui(self) -> None:
        while True:
            try:
                callback, args = self._ui_calls.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)
            except tk.TclError:
                pass                                   # 控件已经销毁（比如正在换外观）
        try:
            self._pump_job = self.after(10, self._pump_ui)
        except tk.TclError:
            self._pump_job = None

    def _bind_player(self) -> None:
        self.player.on_progress = lambda ms, i, n: self.call_soon(self._on_progress, ms, i, n)
        self.player.on_state = lambda s: self.call_soon(self._on_state, s)
        self.player.on_finish = lambda: self.call_soon(self._on_finish)

    def _hotkey_stop(self) -> None:
        """停止键（在热键线程里跑）：先当场停掉播放、松开所有按键，界面的事再交给界面线程。"""
        self.player.stop()
        self.call_soon(self.stop)

    def _hotkey_pause(self) -> None:
        """
        暂停键（在热键线程里跑）：只管暂停 / 继续，当场生效、暂停时立刻松开所有按键。

        没在播放、或者还在倒计时，按了什么都不做——不会像开始键那样把曲子从头放起来。
        界面跟着播放器的状态回调更新（走 call_soon 队列）。
        """
        state = self.player.state
        if state == PlayState.PLAYING:
            self.player.pause()
        elif state == PlayState.PAUSED:
            self.player.resume()

    def hotkey_text(self, action: str) -> str:
        """``play`` / ``pause`` / ``stop`` 现在绑的是哪个键（显示用）。"""
        return hotkey_label(getattr(self.settings, f"hotkey_{action}", ""))

    def _hotkey_hint(self) -> str:
        return (f"{self.hotkey_text('play')} 开始 / 暂停\n"
                f"{self.hotkey_text('pause')} 暂停 / 继续　{self.hotkey_text('stop')} 停止")

    def _bind_hotkeys(self) -> None:
        self._apply_hotkeys()
        self.hotkeys.start()

    def _apply_hotkeys(self) -> None:
        bad = self.hotkeys.replace({
            self.settings.hotkey_play: lambda: self.call_soon(self.toggle_play),
            self.settings.hotkey_pause: self._hotkey_pause,
            self.settings.hotkey_stop: self._hotkey_stop,
        })
        if bad:
            self.log("这些快捷键认不出来，没有生效：" + "、".join(bad))

    def play_tune(self, tune: Tune, mode: str = MODE_AUTO) -> None:
        """
        演奏一首曲子。

        :param mode: ``auto`` 自动演奏（程序替你按键）；
                     ``guide`` 跟弹（悬浮窗提示，玩家自己按，程序不发送任何按键）
        """
        notes = tune.to_notes()
        if not notes:
            self.log(f"《{tune.name}》里还没有音，先去「谱曲」页填几拍吧")
            return

        guide = mode == MODE_GUIDE
        events, report = compile_notes(
            notes, self.layout, self.settings.timing_preset(),
            speed=self.settings.guide_speed if guide else self.settings.speed,
            fold=FoldMode.FOLD, legato=self.settings.legato,
        )
        if not events:
            self.log("编译后没有可演奏的音符，检查一下设置里的键位")
            return

        self._start_session(events, report, tune.name, mode)
        self.current_tune = tune

        self.show_page("library")
        self.page_library.show_playing(tune.name)
        self.btn_stop.configure(state=tk.NORMAL)

        if self.settings.countdown > 0:
            self.log(f"《{tune.name}》{self._mode_note(mode)}：{report.summary()}，"
                     f"{self.settings.countdown:.0f} 秒后开始，快切到游戏")
        else:
            self.log(f"《{tune.name}》{self._mode_note(mode)}：{report.summary()}，开始")

        self.player.play(countdown_ms=self.settings.countdown * 1000.0)
        self._start_countdown_ticker()

    def play_notes(self, notes, name: str = "进阶模式", transpose: int = 0,
                   fold: str = FoldMode.FOLD, speed: Optional[float] = None,
                   mode: str = MODE_AUTO) -> None:
        """演奏一组现成的音符（进阶页 / 卷帘用）。"""
        if not notes:
            self.log("没有可演奏的音符")
            return
        if speed is None:
            speed = self.settings.guide_speed if mode == MODE_GUIDE else self.settings.speed
        events, report = compile_notes(
            notes, self.layout, self.settings.timing_preset(),
            speed=speed, transpose=transpose, fold=fold, legato=self.settings.legato,
        )
        if not events:
            self.log("编译后没有可演奏的事件，检查键位设置或超音域策略")
            return

        self._start_session(events, report, name, mode)
        self.current_tune = None

        self.btn_stop.configure(state=tk.NORMAL)
        self.log(f"{name}{self._mode_note(mode)}：{report.summary()}，"
                 f"{self.settings.countdown:.0f} 秒后开始")
        self.player.play(countdown_ms=self.settings.countdown * 1000.0)
        self._start_advanced_ticker(name)

    def _start_session(self, events, report, name: str, mode: str) -> None:
        """换曲前的公共准备：停掉上一首、选注入器、装事件流、打开悬浮提示。"""
        self.player.stop()
        self.overlay.stop()
        self._use_sender(guide=mode == MODE_GUIDE)
        self.player.sender.stats.__init__()          # 重置注入计数
        self.player.load(events)
        self.play_mode = mode
        self._play_name = name

        if mode == MODE_GUIDE or self.settings.overlay_in_auto:
            shown = self.overlay.start(mode, name, report.slots, self.layout, self.player.total,
                                       wait=self.settings.guide_wait)
            if not shown and mode == MODE_GUIDE:
                self.log("悬浮提示窗打不开，这次跟弹只能看主界面的进度")

    def _use_sender(self, guide: bool) -> None:
        """
        跟弹换上「什么都不发」的注入器，自动演奏再换回来。

        换回的是"上次自动演奏用的那个"而不是新建一个系统注入器——
        冒烟测试会把注入器换成只记录的假货，这里不能自作主张把真的换回去。
        """
        current = self.player.sender
        if guide:
            if not isinstance(current, NullSender):
                self._input_sender = current
                self.player.sender = self._null_sender
        elif isinstance(current, NullSender):
            self.player.sender = self._input_sender

    @staticmethod
    def _mode_note(mode: str) -> str:
        return "（跟弹：看提示自己按，程序不按键）" if mode == MODE_GUIDE else ""

    def _doing(self) -> str:
        return "跟弹中" if self.play_mode == MODE_GUIDE else "演奏中"

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
                text = f"{name}　{_mmss(self.player.position)} / {_mmss(total)}"
                judge = self.overlay.judge
                if self.play_mode == MODE_GUIDE and judge is not None:
                    text += "　" + judge.status_text()
                self.lbl_status.configure(text=text)
                self._countdown_timer = self.after(200, tick)
                return
            self._countdown_timer = None

        tick()

    def play_tune_by_name(self, name: str, mode: str = MODE_AUTO) -> None:
        tune = self.library.load(name)
        if tune is None:
            self.log(f"读不到《{name}》，可能文件被删了")
            self.page_library.refresh()
            return
        self.play_tune(tune, mode)

    def toggle_play(self) -> None:
        """开始键（默认 F6）：没在播放时开始，倒计时中取消，演奏中暂停，暂停中继续。"""
        state = self.player.state
        if state == PlayState.COUNTDOWN:
            self.stop()
        elif state in (PlayState.PLAYING, PlayState.PAUSED):
            self.player.toggle()
        elif state == PlayState.IDLE and self.current_tune is not None:
            self.play_tune(self.current_tune, self.play_mode)

    def toggle_pause(self) -> None:
        """播放条上的「暂停 / 继续」按钮。"""
        if self.player.state in (PlayState.PLAYING, PlayState.PAUSED):
            self.player.toggle()

    def seek_to(self, ms: float) -> bool:
        """拖进度条：跳到歌曲的 ``ms`` 毫秒处（演奏中 / 暂停中才能跳）。"""
        if self.player.state not in (PlayState.PLAYING, PlayState.PAUSED):
            return False
        self.player.seek(ms)
        position = self.player.position
        self.overlay.on_seek(position)
        self._on_progress(position, 0, 0)
        if self.player.state == PlayState.PAUSED:
            self.lbl_status.configure(text=f"已暂停（按 {self.hotkey_text('pause')} 继续）　"
                                           f"{_mmss(position)} / {_mmss(self.player.total)}")
        return True

    def stop(self) -> None:
        self.player.stop()
        self.overlay.stop()
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
        text = f"{self._doing()}　{_mmss(ms)} / {_mmss(total_ms)}"
        judge = self.overlay.judge
        if self.play_mode == MODE_GUIDE and judge is not None:
            text += "　" + judge.status_text()
            self.page_library.set_score(judge.status_text())
        self.lbl_status.configure(text=text)

    def _on_state(self, state: str) -> None:
        name = self.current_tune.name if self.current_tune else self._play_name
        if state == PlayState.PLAYING:
            self.lbl_status.configure(text=f"{self._doing()}…")
            color = Palette["guide"] if self.play_mode == MODE_GUIDE else Palette["accent"]
            self.lbl_nav_status.configure(text=self._doing(), fg=color)
            self.btn_stop.configure(state=tk.NORMAL)
            self.page_library.show_playing(name)
        elif state == PlayState.PAUSED:
            self.lbl_status.configure(text=f"已暂停（按 {self.hotkey_text('pause')} 继续）")
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
        name = self.current_tune.name if self.current_tune else self._play_name
        self._stop_countdown_ticker()
        summary = self.overlay.finish()
        self.lbl_nav_status.configure(text="")
        self.btn_stop.configure(state=tk.DISABLED)
        self.page_library.show_done(name)
        self.page_library.set_progress(1.0, "")
        if self.play_mode == MODE_GUIDE:
            summary = summary or "（悬浮窗没开，没有判定数据）"
            self.lbl_status.configure(text="跟弹完成　" + summary)
            self.log(f"跟弹完成：{summary}")
        else:
            self.lbl_status.configure(text="演奏完成")
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
        if not tune.is_grid:
            self.log(f"《{name}》是从在线曲库原样存下来的，格子谱表达不了，不能在谱曲页编辑")
            return
        self.page_composer.load_tune(tune)
        self.show_page("compose")
        self.log(f"正在编辑《{name}》")

    def apply_settings(self, settings: AppSettings) -> None:
        """设置页保存后调用：落盘 + 重建键位表 + 刷新悬浮窗外观。"""
        self.settings = settings
        self.settings.save()
        self.layout = self.settings.to_layout()
        self.overlay.apply_settings(self.settings)
        self._apply_hotkeys()
        self.lbl_hotkeys.configure(text=self._hotkey_hint())
        self.btn_stop.configure(text=f"停止演奏  {self.hotkey_text('stop')}")
        self.log("设置已生效：" + self.layout.summary())

    def on_overlay_moved(self, x: int, y: int) -> None:
        """
        悬浮窗被拖到了新位置：马上存盘。

        设置页手里也有一份正在编辑的设置副本，一起改掉——
        否则用户之后点「保存设置」，旧坐标会把刚拖好的位置覆盖回去。
        """
        for settings in (self.settings, self.page_settings.settings):
            settings.overlay_x, settings.overlay_y = int(x), int(y)
            settings.overlay_anchor = "custom"
            settings.overlay_pos_scale = screen_scale()
        self.settings.save()
        self.page_settings.on_overlay_moved()

    def on_overlay_adjusting(self, on: bool) -> None:
        """悬浮窗进入 / 退出「调整位置」状态（可能是双击悬浮窗退出的），同步设置页按钮。"""
        self.page_settings.on_overlay_adjusting(on)
        if not on:
            self.overlay.apply_settings(self.settings)

    def open_midi_file(self, path: str) -> None:
        """从外部载入 MIDI（启动参数用）。"""
        self.show_page("advanced")
        if self.page_advanced is not None:
            self.page_advanced.load_midi(path)

    # ================================================================== #
    # 收尾
    # ================================================================== #

    def _startup(self) -> None:
        """启动流程：免责确认 → 首次使用步骤 → 日常提示。"""
        if not self.settings.disclaimer_accepted:
            if not ask_disclaimer(self):
                # 不同意就直接退出，不留下任何东西
                self.destroy()
                return
            self.settings.disclaimer_accepted = True
            self.settings.save()

        self._boot_messages()

        # 第一次用的人最需要这个，所以自动弹一次；
        # 之后随时可以点右上角那个「?」再看
        if not self.settings.guide_shown:
            self.settings.guide_shown = True
            self.settings.save()
            self.after(260, self.show_guide)

    def show_guide(self) -> None:
        """弹出使用步骤。"""
        from .guide import show_guide
        show_guide(self)

    def _boot_messages(self) -> None:
        self.log("─" * 58)
        self.log("⚠ 本项目仅供个人学习、技术研究与娱乐使用。")
        self.log("⚠ 严禁在任何在线对局中使用 —— 可能违反游戏用户协议并导致封号。")
        self.log("⚠ 一切后果由使用者自行承担，作者不承担任何责任。详见 DISCLAIMER.md")
        self.log("─" * 58)
        self.log(f"{APP_NAME} 已启动。热键：{self.hotkey_text('play')} 开始/暂停　"
                 f"{self.hotkey_text('pause')} 暂停/继续　{self.hotkey_text('stop')} 停止")
        self.log("想自己吹？曲库里点「跟弹」：游戏画面上会浮出按键提示，你自己按，程序一个键都不按。")
        if not is_admin():
            self.log("提示：当前不是管理员权限。若游戏里完全没反应，请用「以管理员启动.bat」重开。")
        if not self.preview.available:
            self.log("提示：没检测到系统 MIDI 输出设备，试听按钮没声音（不影响正常演奏）")
        if self._upgraded_presets:
            self.log("内置曲目已更新到新版（重复的音不再被拼成长音）："
                     + "、".join(self._upgraded_presets))
        self.log(f"设置文件：{settings_path()}")

    def restart(self) -> None:
        """换了外观：关掉窗口再按新配色建一遍（演奏会停下，设置已经存好）。"""
        self.restart_requested = True
        self.on_close()

    def on_close(self) -> None:
        if self._pump_job is not None:
            try:
                self.after_cancel(self._pump_job)
            except tk.TclError:
                pass
            self._pump_job = None
        try:
            self.player.stop()
            self.overlay.close()
            self.preview.close()
            self.hotkeys.shutdown()
            self._remember_size()
            self.settings.save()
        finally:
            self.destroy()


def _work_area(window: tk.Misc) -> Tuple[int, int, int, int]:
    """屏幕上去掉任务栏的那块地方：(左, 上, 宽, 高)。只有 Windows 能问到，别的系统估一个。"""
    if sys.platform == "win32":
        try:
            from ctypes import wintypes
            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
                return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        except (AttributeError, OSError):
            pass
    return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight() - px(60)


# ---------------------------------------------------------------------- #


def _mmss(ms: float) -> str:
    total = max(0.0, ms) / 1000.0
    return f"{int(total // 60):02d}:{total % 60:05.2f}"


def run() -> None:
    """启动界面（换外观时窗口会在同一个进程里重建）。"""
    while True:
        window = MainWindow()
        window.mainloop()
        if not window.restart_requested:
            break
