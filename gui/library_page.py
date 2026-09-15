"""
曲库页 —— 点一下就能吹。

这个页面是给"游戏开成窗口化、程序摆在旁边"这种用法准备的：
曲名列表 + 一个够大的播放按钮，不用切窗口、不用记快捷键，
鼠标点一下就开吹。

正在演奏时底部会出现进度条和大号停止按钮，按一下立刻停手。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Callable, List, Optional

from kouqin.tune import Tune

from .theme import Palette, make_button, make_label


class LibraryPage(tk.Frame):
    """曲库列表。"""

    def __init__(self, master, app) -> None:
        super().__init__(master, bg=Palette["bg"])
        self.app = app
        self._playing_name: Optional[str] = None
        self._row_widgets: List[tuple] = []

        self._build()

    # ================================================================== #
    # 界面
    # ================================================================== #

    def _build(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_list()
        self._build_player_bar()

    def _build_header(self) -> None:
        bar = tk.Frame(self, bg=Palette["panel"], height=54)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)

        make_label(bar, "我的曲库", bold=True, size=12).pack(side=tk.LEFT, padx=(14, 8), pady=14)
        self.lbl_count = make_label(bar, "", dim=True)
        self.lbl_count.pack(side=tk.LEFT, pady=14)

        make_button(bar, "新建曲子", self._new_tune, kind="accent").pack(side=tk.RIGHT, padx=(4, 14), pady=10)
        make_button(bar, "恢复内置曲目", self._restore_presets, kind="ghost").pack(side=tk.RIGHT, padx=4, pady=10)
        make_button(bar, "导入曲谱", self._import_tune, kind="ghost").pack(side=tk.RIGHT, padx=4, pady=10)

    def _build_list(self) -> None:
        wrap = tk.Frame(self, bg=Palette["bg"])
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(wrap, bg=Palette["bg"], highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        scroll = tk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.canvas.yview,
                              bg=Palette["panel_alt"], troughcolor=Palette["panel"],
                              activebackground=Palette["border"], width=12)
        scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scroll.set)

        self.inner = tk.Frame(self.canvas, bg=Palette["bg"])
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_resize)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<MouseWheel>", self._on_wheel)

    def _build_player_bar(self) -> None:
        self.bar = tk.Frame(self, bg=Palette["panel_alt"], height=54)
        self.bar.grid(row=2, column=0, sticky="ew")
        self.bar.grid_propagate(False)

        self.lbl_now = make_label(self.bar, "选一首曲子，点「演奏」就开始", bg=Palette["panel_alt"])
        self.lbl_now.pack(side=tk.LEFT, padx=(14, 8), pady=14)

        self.btn_stop = make_button(self.bar, "■  停止", self._stop, kind="danger")
        self.btn_stop.pack(side=tk.RIGHT, padx=(4, 14), pady=10)
        self.btn_stop.configure(state=tk.DISABLED)

        self.progress = tk.Canvas(self.bar, bg=Palette["panel_alt"], highlightthickness=0, height=6)
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=12)

    # ================================================================== #
    # 列表渲染
    # ================================================================== #

    def refresh(self) -> None:
        """重新读曲库并刷新列表。"""
        for child in self.inner.winfo_children():
            child.destroy()
        self._row_widgets.clear()

        names = self.app.library.names()
        self.lbl_count.configure(text=f"（{len(names)} 首）" if names else "")

        if not names:
            box = tk.Frame(self.inner, bg=Palette["bg"])
            box.pack(fill=tk.X, padx=20, pady=60)
            make_label(box, "曲库还是空的", bg=Palette["bg"], size=13, bold=True).pack()
            make_label(box, "点右上角「新建曲子」，或者「恢复内置曲目」先拿几首经典曲子练手",
                       bg=Palette["bg"], dim=True).pack(pady=(8, 0))
            return

        for name in names:
            self._add_row(name)

    def _add_row(self, name: str) -> None:
        tune = self.app.library.load(name)
        if tune is None:
            return

        playing = name == self._playing_name
        bg = Palette["panel_alt"] if playing else Palette["panel"]

        row = tk.Frame(self.inner, bg=bg, highlightthickness=1,
                       highlightbackground=Palette["accent"] if playing else Palette["border"])
        row.pack(fill=tk.X, padx=12, pady=4)

        # --- 左侧信息 ---
        info = tk.Frame(row, bg=bg)
        info.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=14, pady=10)

        title = tk.Label(info, text=("♪  " if playing else "") + tune.name,
                         bg=bg, fg=Palette["accent"] if playing else Palette["text"],
                         font=("Microsoft YaHei UI", 12, "bold"), anchor="w")
        title.pack(anchor="w")

        seconds = tune.duration_ms() / 1000.0
        detail = f"{tune.bar_count} 小节 · {tune.note_count()} 个音 · 约 {seconds:.0f} 秒 · {tune.bpm:.0f} 拍/分"
        tk.Label(info, text=detail, bg=bg, fg=Palette["text_dim"],
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(anchor="w", pady=(3, 0))

        # --- 右侧按钮 ---
        make_button(row, "演奏", lambda n=name: self.app.play_tune_by_name(n),
                    kind="accent" if not playing else "ghost", width=6).pack(side=tk.RIGHT, padx=(2, 12), pady=10)
        make_button(row, "编辑", lambda n=name: self.app.edit_tune(n),
                    kind="ghost", width=5).pack(side=tk.RIGHT, padx=2, pady=10)
        make_button(row, "删除", lambda n=name: self._delete(n),
                    kind="ghost", width=5).pack(side=tk.RIGHT, padx=2, pady=10)

        self._row_widgets.append((name, row, bg, title))

    def _on_inner_resize(self, event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_resize(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

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

    def set_playing(self, name: Optional[str]) -> None:
        self._playing_name = name
        self.btn_stop.configure(state=tk.NORMAL if name else tk.DISABLED)
        if name:
            self.lbl_now.configure(text=f"▶  正在演奏：{name}", fg=Palette["accent"])
        self.refresh()

    def set_progress(self, ratio: float, text: str) -> None:
        self.progress.delete("all")
        width = self.progress.winfo_width() or 200
        height = 6
        self.progress.create_rectangle(0, 0, width, height,
                                       fill=Palette["panel"], outline="")
        self.progress.create_rectangle(0, 0, max(0.0, min(1.0, ratio)) * width, height,
                                       fill=Palette["accent"], outline="")

    def show_idle(self) -> None:
        self._playing_name = None
        self.btn_stop.configure(state=tk.DISABLED)
        self.lbl_now.configure(text="选一首曲子，点「演奏」就开始", fg=Palette["text"])
        self.progress.delete("all")
        self.refresh()

    def show_playing(self, name: str) -> None:
        self.lbl_now.configure(text=f"▶  正在演奏：{name}", fg=Palette["accent"])
        self.btn_stop.configure(state=tk.NORMAL)

    def show_countdown(self, seconds: float, name: str) -> None:
        self.lbl_now.configure(text=f"⏱  {seconds:.0f} 秒后开始：{name}（快切到游戏）",
                               fg=Palette["warn"])
        self.btn_stop.configure(state=tk.NORMAL)

    def show_paused(self, name: str) -> None:
        self.lbl_now.configure(text=f"⏸  已暂停：{name}", fg=Palette["text_dim"])

    def show_done(self, name: str) -> None:
        self.lbl_now.configure(text=f"✔  演奏完成：{name}", fg=Palette["accent"])
        self.btn_stop.configure(state=tk.DISABLED)
        self.refresh()

    def _stop(self) -> None:
        self.app.stop()
