"""
曲库页 —— 点一下就能吹。

这个页面是给"游戏开成窗口化、程序摆在旁边"这种用法准备的：
曲名列表 + 一个够大的播放按钮，不用切窗口、不用记快捷键，
鼠标点一下就开吹。

    曲库                                    [我的曲库 | 在线曲库]
    「跟弹」你自己按，「演奏」程序替你按
    [⌕ 搜索曲名            ]                  导入  恢复内置  ＋新建
    ╭──────────────────────────────────────────────────────────╮
    │ 小星星  [正在演奏]                    ✎  🗑  [跟弹] [演奏] │
    │ 12 小节 · 42 个音 · 约 29 秒 · 跟弹难度 ●●○○○              │
    ├──────────────────────────────────────────────────────────┤
    │ ……                                                       │
    ╰──────────────────────────────────────────────────────────╯
    ╭ 正在演奏 · 小星星   ━━━━━━●───────  0:11 / 0:29  ⏸暂停 ■停止 ╮

每首曲子两个按钮：
    「跟弹」 游戏画面上浮出按键提示，玩家自己按（程序一个键都不按）
    「演奏」 程序自动替你按
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from kouqin.engine import compile_notes
from kouqin.practice import difficulty
from kouqin.tune import Tune

from .online_page import OnlinePanel
from .theme import Palette, enable_wheel_scroll, make_button, make_label, mono_font, px, title_font, ui_font
from .widgets import RoundButton, RoundedPanel, SearchEntry, Segmented, make_badge, rounded_photo

#: 页面左右留白
_GUTTER = 18


class LibraryPage(tk.Frame):
    """曲库列表。"""

    def __init__(self, master, app) -> None:
        super().__init__(master, bg=Palette["bg"])
        self.app = app
        self._playing_name: Optional[str] = None
        self._row_widgets: List[tuple] = []
        self._badges: Dict[str, RoundButton] = {}
        self._difficulty_cache: Dict[tuple, Tuple[int, str]] = {}
        self._tab = "local"
        self.online: Optional[OnlinePanel] = None
        self.var_query = tk.StringVar()
        self._search_job: Optional[str] = None

        self._build()

    # ================================================================== #
    # 界面
    # ================================================================== #

    def _build(self) -> None:
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_toolbar()
        self._build_list()
        self._build_player_bar()

    def _build_header(self) -> None:
        P = Palette
        head = tk.Frame(self, bg=P["bg"])
        head.grid(row=0, column=0, sticky="ew", padx=px(_GUTTER), pady=(px(14), px(8)))
        text = tk.Frame(head, bg=P["bg"])
        text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(text, text="曲库", font=title_font(18), bg=P["bg"], fg=P["text"], anchor="w").pack(fill=tk.X)
        self.lbl_count = make_label(text, "", dim=True, anchor="w")
        self.lbl_count.pack(fill=tk.X, pady=(px(4), 0))
        self.tabs = Segmented(head, [("local", "我的曲库"), ("online", "在线曲库")], command=self.show_tab,
                              value="local", size=9)
        self.tabs.pack(side=tk.RIGHT, anchor="s")

    def _build_toolbar(self) -> None:
        P = Palette
        bar = tk.Frame(self, bg=P["bg"])
        bar.grid(row=1, column=0, sticky="ew", padx=px(_GUTTER), pady=(0, px(10)))
        self._local_actions = bar
        search = SearchEntry(bar, self.var_query, placeholder="搜索曲名", width=24)
        search.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.var_query.trace_add("write", lambda *_: self._schedule_search())
        make_button(bar, "新建曲子", self._new_tune, icon_name="add").pack(side=tk.RIGHT, padx=(px(6), 0))
        make_button(bar, "恢复内置曲目", self._restore_presets, kind="ghost").pack(side=tk.RIGHT, padx=(px(6), 0))
        make_button(bar, "导入曲谱", self._import_tune, kind="ghost", icon_name="import").pack(
            side=tk.RIGHT, padx=(px(10), 0))

    def show_tab(self, key: str) -> None:
        """切换「我的曲库」/「在线曲库」。在线曲库第一次打开时才创建、才联网。"""
        self._tab = key
        self.tabs.set(key)
        if key == "online":
            if self.online is None:
                self.online = OnlinePanel(self, self.app)
                self.online.grid(row=1, column=0, rowspan=2, sticky="nsew")
            self._local_wrap.grid_remove()
            self._local_actions.grid_remove()
            self.lbl_count.configure(text="Dr-hydra 公开曲库 · 玩家上传的曲谱 · 只读")
            self.online.grid()
            self.online.tkraise()
            self.online.on_show()
        else:
            if self.online is not None:
                self.online.grid_remove()
            self._local_actions.grid()
            self._local_wrap.grid()
            self.refresh()

    def _build_list(self) -> None:
        P = Palette
        wrap = tk.Frame(self, bg=P["bg"])
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)
        self._local_wrap = wrap

        self.canvas = tk.Canvas(wrap, bg=P["bg"], highlightthickness=0, yscrollincrement=px(20))
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns", padx=(0, px(6)))
        self.canvas.configure(yscrollcommand=scroll.set)

        self.inner = tk.Frame(self.canvas, bg=P["bg"])
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_resize)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        enable_wheel_scroll(self.canvas)

    def _build_player_bar(self) -> None:
        P = Palette
        card = RoundedPanel(self, fill=P["panel"], outline=P["border_strong"], radius=14, padx=14, pady=8)
        card.grid(row=3, column=0, sticky="ew", padx=px(_GUTTER), pady=(px(8), px(10)))
        self.bar = body = card.body
        body.grid_columnconfigure(1, weight=1)

        info = tk.Frame(body, bg=P["panel"], width=px(210), height=px(44))
        info.grid(row=0, column=0, sticky="w")
        info.pack_propagate(False)
        self.lbl_mode = make_label(info, "没在播放", size=9, dim=True, anchor="w")
        self.lbl_mode.pack(fill=tk.X)
        self.lbl_now = make_label(info, "选一首曲子开始", bold=True, size=11, anchor="w")
        self.lbl_now.pack(fill=tk.X)

        # 进度条：点一下跳过去，按住拖动松开后跳（拖的过程中不跳，免得一路乱响）
        self.progress = tk.Canvas(body, bg=P["panel"], highlightthickness=0, height=px(24), cursor="hand2")
        self.progress.grid(row=0, column=1, sticky="ew", padx=(px(8), px(10)))
        self._ratio = 0.0
        self._scrub: Optional[float] = None       # 正在拖的位置（0~1），None = 没在拖
        self.progress.bind("<ButtonPress-1>", self._scrub_move)
        self.progress.bind("<B1-Motion>", self._scrub_move)
        self.progress.bind("<ButtonRelease-1>", self._scrub_end)
        self.progress.bind("<Configure>", lambda e: self._draw_progress())

        self.lbl_time = make_label(body, "", dim=True, font=mono_font(10))
        self.lbl_time.grid(row=0, column=2, padx=(0, px(10)))
        self.lbl_score = make_label(body, "", fg=P["guide"])
        self.lbl_score.grid(row=0, column=3, padx=(0, px(10)))

        self.btn_pause = make_button(body, "暂停", self.app.toggle_pause, icon_name="pause")
        self.btn_pause.grid(row=0, column=4, padx=(0, px(6)))
        self.btn_pause.configure(state=tk.DISABLED)
        self.btn_stop = make_button(body, "停止", self._stop, kind="danger")
        self.btn_stop.grid(row=0, column=5)
        self.btn_stop.configure(state=tk.DISABLED)

    # ================================================================== #
    # 列表渲染
    # ================================================================== #

    def _schedule_search(self) -> None:
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(180, self.refresh)

    def refresh(self) -> None:
        """重新读曲库并刷新列表。"""
        self._search_job = None
        for child in self.inner.winfo_children():
            child.destroy()
        self._row_widgets.clear()
        self._badges.clear()

        names = self.app.library.names()
        if self._tab == "local":
            self.lbl_count.configure(text=f"{len(names)} 首曲子 · 「跟弹」你自己按，「演奏」程序替你按")
        query = self.var_query.get().strip().lower()
        shown = [n for n in names if query in n.lower()] if query else names

        P = Palette
        if not shown:
            self._empty_state(query)
            return
        card = RoundedPanel(self.inner, fill=P["panel"], radius=12, padx=4, pady=4)
        card.pack(fill=tk.X, padx=(px(_GUTTER), px(_GUTTER - 6)), pady=(0, px(8)))
        for index, name in enumerate(shown):
            if index:
                tk.Frame(card.body, bg=P["border"], height=px(1)).pack(fill=tk.X, padx=px(10))
            self._add_row(card.body, name)

    def _empty_state(self, query: str) -> None:
        P = Palette
        box = tk.Frame(self.inner, bg=P["bg"])
        box.pack(fill=tk.X, pady=px(48))
        if query:
            make_label(box, f"没找到「{query}」", size=11, bold=True).pack()
            make_label(box, "换个词试试，或者去「在线曲库」里找找", dim=True).pack(pady=(px(4), 0))
            return
        make_label(box, "开始你的第一首曲子", size=11, bold=True).pack()
        make_label(box, "在谱曲页点格子编一首，或者导入别人分享的曲谱", dim=True).pack(pady=(px(4), px(12)))
        make_button(box, "新建曲子", self._new_tune, kind="accent", icon_name="add").pack()

    def _difficulty(self, name: str, tune: Tune) -> Tuple[int, str]:
        """跟弹难度。编译有点开销，按"文件修改时间 + 键位 + 速度"缓存。"""
        try:
            stamp = os.path.getmtime(self.app.library.path_of(name))
        except OSError:
            stamp = 0.0
        key = (name, stamp, self.app.layout.summary(), self.app.settings.guide_speed)
        cached = self._difficulty_cache.get(key)
        if cached is None:
            _events, report = compile_notes(tune.to_notes(), self.app.layout,
                                            self.app.settings.timing_preset(),
                                            speed=self.app.settings.guide_speed)
            cached = difficulty(report.slots)
            self._difficulty_cache[key] = cached
        return cached

    def _add_row(self, parent, name: str) -> None:
        tune = self.app.library.load(name)
        if tune is None:
            return
        P = Palette
        bg = P["panel"]
        row = tk.Frame(parent, bg=bg)
        row.pack(fill=tk.X)

        # --- 右侧按钮（先放，免得曲名太长把按钮挤出去）：编辑 删除 跟弹 演奏 ---
        buttons = tk.Frame(row, bg=bg)
        buttons.pack(side=tk.RIGHT, padx=(px(8), px(10)), pady=px(7))
        if tune.is_grid:
            RoundButton(buttons, icon_name="edit", kind="icon", height=32, padx=8,
                        command=lambda n=name: self.app.edit_tune(n)).pack(side=tk.LEFT)
        RoundButton(buttons, icon_name="delete", kind="icon", height=32, padx=8,
                    command=lambda n=name: self._delete(n)).pack(side=tk.LEFT, padx=(px(2), 0))
        make_button(buttons, "跟弹", lambda n=name: self.app.play_tune_by_name(n, "guide"),
                    kind="guide").pack(side=tk.LEFT, padx=(px(8), 0))
        make_button(buttons, "演奏", lambda n=name: self.app.play_tune_by_name(n, "auto"),
                    kind="accent").pack(side=tk.LEFT, padx=(px(6), 0))

        # --- 左侧信息 ---
        info = tk.Frame(row, bg=bg)
        info.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(px(14), 0), pady=px(7))
        line1 = tk.Frame(info, bg=bg)
        line1.pack(anchor="w", fill=tk.X)
        title = tk.Label(line1, text=tune.name, bg=bg, fg=P["text"], font=ui_font(12, True), anchor="w")
        title.pack(side=tk.LEFT)
        if tune.source.get("kind") == "online":
            make_badge(line1, "在线曲库", "guide").pack(side=tk.LEFT, padx=(px(8), 0))
        if name == self._playing_name:
            badge = make_badge(line1, self._playing_text(), self._playing_tone())
            badge.pack(side=tk.LEFT, padx=(px(8), 0))
            self._badges[name] = badge

        seconds = tune.duration_ms() / 1000.0
        if tune.is_grid:
            detail = f"{tune.bar_count} 小节 · {tune.note_count()} 个音 · 约 {_duration(seconds)}"
        else:
            uploader = tune.source.get("uploader") or "匿名玩家"
            detail = (f"{tune.note_count()} 个音 · 约 {_duration(seconds)} · "
                      f"来自在线曲库 · 上传者 {uploader} · 原样保存，不能编辑")
        line2 = tk.Frame(info, bg=bg)
        line2.pack(anchor="w", pady=(px(1), 0))
        tk.Label(line2, text=detail, bg=bg, fg=P["text_dim"], font=ui_font(10), anchor="w").pack(side=tk.LEFT)
        if not tune.is_empty():
            stars, hint = self._difficulty(name, tune)
            tk.Label(line2, text=" · 跟弹难度 ", bg=bg, fg=P["text_dim"], font=ui_font(10)).pack(side=tk.LEFT)
            tk.Label(line2, text="●" * stars, bg=bg, fg=P["text_dim"], font=ui_font(9)).pack(side=tk.LEFT)
            tk.Label(line2, text="●" * (5 - stars), bg=bg, fg=P["border_strong"], font=ui_font(9)).pack(side=tk.LEFT)
            tk.Label(line2, text=f"  {hint.split('，')[-1]}", bg=bg, fg=P["text_muted"],
                     font=ui_font(10)).pack(side=tk.LEFT)

        self._row_widgets.append((name, row, bg, title))

    def _on_inner_resize(self, event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_resize(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    # ================================================================== #
    # 操作
    # ================================================================== #

    def _new_tune(self) -> None:
        self.app.new_tune()

    def _delete(self, name: str) -> None:
        if not messagebox.askyesno("删除曲子", f"确定删除《{name}》吗？删掉就找不回来了。",
                                   parent=self.app):
            return
        if self._playing_name == name:
            self.app.stop()
        if self.app.library.delete(name):
            self.app.log(f"已删除《{name}》")
            self.refresh()

    def _import_tune(self) -> None:
        path = filedialog.askopenfilename(
            title="导入曲谱", filetypes=[("口琴曲谱", "*.json"), ("所有文件", "*.*")], parent=self.app,
        )
        if not path:
            return
        tune = self.app.library.import_file(path)
        if tune is None:
            messagebox.showerror("导入失败", "这个文件不是有效的口琴曲谱。", parent=self.app)
            return
        self.app.log(f"已导入《{tune.name}》")
        self.refresh()

    def _restore_presets(self) -> None:
        count = self.app.library.install_presets(overwrite=False)
        if count:
            self.app.log(f"已放入 {count} 首内置曲目")
        else:
            self.app.log("内置曲目都已经在曲库里了")
        self.refresh()

    # ================================================================== #
    # 播放状态
    # ================================================================== #

    def _verb(self) -> str:
        return "跟弹" if self.app.play_mode == "guide" else "演奏"

    def _color(self) -> str:
        return Palette["guide"] if self.app.play_mode == "guide" else Palette["accent"]

    def _playing_tone(self) -> str:
        return "guide" if self.app.play_mode == "guide" else "accent"

    def _playing_text(self) -> str:
        if self.app.player.state == "paused":
            return "已暂停"
        return f"正在{self._verb()}"

    def _update_badge(self) -> None:
        badge = self._badges.get(self._playing_name or "")
        if badge is not None:
            badge.configure(text=self._playing_text(), kind=f"badge_{self._playing_tone()}")

    def set_playing(self, name: Optional[str]) -> None:
        self._playing_name = name
        self.btn_stop.configure(state=tk.NORMAL if name else tk.DISABLED)
        if name:
            self.lbl_mode.configure(text=f"正在{self._verb()}", fg=self._color())
            self.lbl_now.configure(text=name)
        self.refresh()

    def set_progress(self, ratio: float, text: str) -> None:
        """更新进度（``text`` 为空表示不显示时间）。时间按「分:秒」显示，不跳百分之一秒。"""
        self._ratio = max(0.0, min(1.0, ratio))
        if self._scrub is None:
            total = self.app.player.total
            self.lbl_time.configure(text=f"{_clock(self._ratio * total)} / {_clock(total)}" if text else "")
            self._draw_progress()

    def _seekable(self) -> bool:
        return self.app.player.state in ("playing", "paused") and self.app.player.total > 0

    def _draw_progress(self) -> None:
        P = Palette
        c = self.progress
        c.delete("all")
        width = max(40, c.winfo_width())
        mid = max(12, c.winfo_height()) // 2
        pad = px(9)                                 # 两头留出拖动圆点的位置
        ratio = self._scrub if self._scrub is not None else self._ratio
        x = pad + (width - 2 * pad) * ratio
        thick = px(4)
        c.create_line(pad, mid, width - pad, mid, fill=P["panel_alt"] if P["panel_alt"] != P["panel"] else P["border"],
                      width=thick, capstyle=tk.ROUND)
        if ratio > 0:
            c.create_line(pad, mid, x, mid, fill=self._color(), width=thick, capstyle=tk.ROUND)
        if self._seekable() or self._scrub is not None:
            size = px(16) if self._scrub is None else px(18)
            knob = rounded_photo(c, size, size, size // 2, P["panel"], self._color(), P["panel"], border=px(2))
            c.create_image(x, mid, image=knob)

    def _ratio_at(self, x: int) -> float:
        width = max(40, self.progress.winfo_width())
        pad = px(9)
        return max(0.0, min(1.0, (x - pad) / (width - 2 * pad)))

    def x_for_ratio(self, ratio: float) -> float:
        """进度条上 ``ratio`` 处的横坐标（:meth:`_ratio_at` 的反函数，测试用）。"""
        width = max(40, self.progress.winfo_width())
        pad = px(9)
        return pad + (width - 2 * pad) * ratio

    def _scrub_move(self, event) -> None:
        if not self._seekable():
            return
        self._scrub = self._ratio_at(event.x)
        total = self.app.player.total
        self.lbl_time.configure(text=f"→ {_clock(self._scrub * total)} / {_clock(total)}")
        self._draw_progress()

    def _scrub_end(self, event) -> None:
        if self._scrub is None:
            return
        target = self._ratio_at(event.x) * self.app.player.total
        self._scrub = None
        if not self.app.seek_to(target):
            self._draw_progress()

    def set_score(self, text: str) -> None:
        """跟弹时播放条上的实时成绩。"""
        self.lbl_score.configure(text=text)

    def show_idle(self) -> None:
        self._playing_name = None
        self.btn_stop.configure(state=tk.DISABLED)
        self.btn_pause.configure(state=tk.DISABLED, text="暂停", icon_name="pause")
        self.lbl_mode.configure(text="没在播放", fg=Palette["text_dim"])
        self.lbl_now.configure(text="选一首曲子开始")
        self.lbl_score.configure(text="")
        self.lbl_time.configure(text="")
        self._scrub = None
        self._ratio = 0.0
        self._draw_progress()
        self.refresh()

    def show_playing(self, name: str) -> None:
        if self._playing_name != name:
            self._playing_name = name
            self.refresh()
        self.lbl_mode.configure(text=f"正在{self._verb()}", fg=self._color())
        self.lbl_now.configure(text=name)
        self.btn_stop.configure(state=tk.NORMAL)
        self.btn_pause.configure(state=tk.NORMAL, text="暂停", icon_name="pause")
        if self.app.play_mode != "guide":
            self.lbl_score.configure(text="")
        self._update_badge()

    def show_countdown(self, seconds: float, name: str) -> None:
        self.lbl_mode.configure(text=f"{seconds:.0f} 秒后开始{self._verb()} · 快切到游戏", fg=Palette["warn"])
        self.lbl_now.configure(text=name)
        self.btn_stop.configure(state=tk.NORMAL)
        self.btn_pause.configure(state=tk.DISABLED)

    def show_paused(self, name: str) -> None:
        self.lbl_mode.configure(text=f"已暂停 · {self.app.hotkey_text('pause')} 继续", fg=Palette["text_dim"])
        self.lbl_now.configure(text=name)
        self.btn_pause.configure(state=tk.NORMAL, text="继续", icon_name="play")
        self._update_badge()

    def show_done(self, name: str) -> None:
        self._playing_name = None
        self.lbl_mode.configure(text=f"{self._verb()}完成", fg=self._color())
        self.lbl_now.configure(text=name)
        self.btn_stop.configure(state=tk.DISABLED)
        self.btn_pause.configure(state=tk.DISABLED, text="暂停", icon_name="pause")
        self.refresh()

    def _stop(self) -> None:
        self.app.stop()


def _clock(ms: float) -> str:
    seconds = int(max(0.0, ms) // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    return f"{seconds // 60} 分 {seconds % 60:02d} 秒"
