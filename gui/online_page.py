"""
在线曲库面板 —— 曲库页里的「在线曲库」标签。

曲谱来自 Dr-hydra「Delta Force Harmonica」项目的公开曲库，由玩家上传，版权归原作者；
本程序只读：浏览目录、点开哪首下载哪首，在本机演奏或跟弹，不上传任何数据。

几百首曲子用 ttk.Treeview 一张表列出来——比一行一个 Frame 快得多，几千行也不卡。
联网全部放在后台线程，结果用 after() 交回界面线程。
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Callable, List, Optional

from kouqin.online import (
    DEFAULT_BASE, SOURCE_NAME, SOURCE_URL, OnlineEntry, OnlineError, OnlineLibrary, search, to_tune,
)
from kouqin.settings import DATA_DIR

from .theme import Palette, make_button, make_label, px
from .widgets import RoundedPanel, SearchEntry

#: 表格列：(列名, 标题, 宽度, 是否随窗口拉伸)
_COLUMNS = (
    ("title", "曲名", 300, True),
    ("composer", "作曲 / 改编", 150, True),
    ("uploader", "上传者", 110, False),
    ("duration", "时长", 64, False),
    ("bpm", "速度", 56, False),
    ("notes", "音数", 56, False),
    ("updated", "更新", 92, False),
)


def default_cache_dir() -> str:
    return os.path.join(DATA_DIR, "在线曲库缓存")


class OnlinePanel(tk.Frame):
    """
    :param app:     主窗口（用它的 play_notes / log / settings）
    :param factory: 造 :class:`OnlineLibrary` 的函数，测试时换成假的，免得真去联网
    """

    def __init__(self, master, app, factory: Optional[Callable[[], OnlineLibrary]] = None) -> None:
        super().__init__(master, bg=Palette["bg"])
        self.app = app
        self._factory = factory or self._default_factory
        self._entries: List[OnlineEntry] = []
        self._shown: List[OnlineEntry] = []
        self._loading = False
        self._busy_id: Optional[str] = None
        self._search_job: Optional[str] = None
        self._loaded_once = False
        self._build()

    def _default_factory(self) -> OnlineLibrary:
        base = getattr(self.app.settings, "online_library_base", "") or DEFAULT_BASE
        return OnlineLibrary(base, default_cache_dir())

    # ================================================================== #
    # 界面
    # ================================================================== #

    def _build(self) -> None:
        P = Palette
        gutter = px(18)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        top = tk.Frame(self, bg=P["bg"])
        top.grid(row=0, column=0, sticky="ew", padx=gutter, pady=(0, px(12)))
        self.var_query = tk.StringVar()
        search = SearchEntry(top, self.var_query, placeholder="曲名、作曲、上传者、标签都能搜", width=30)
        search.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.var_query.trace_add("write", lambda *_: self._schedule_search())
        make_button(top, "刷新目录", lambda: self.load(force=True), kind="ghost",
                    icon_name="refresh").pack(side=tk.RIGHT, padx=(px(6), 0))
        self.lbl_status = make_label(top, "", dim=True)
        self.lbl_status.pack(side=tk.RIGHT, padx=(px(12), 0))

        card = RoundedPanel(self, fill=P["panel"], radius=12, padx=6, pady=6, fit=False)
        card.grid(row=1, column=0, sticky="nsew", padx=(gutter, gutter - px(6)))
        table = card.body
        table.grid_rowconfigure(0, weight=1)
        table.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=[c[0] for c in _COLUMNS], show="headings",
                                 selectmode="browse")
        for key, title, width, stretch in _COLUMNS:
            self.tree.heading(key, text=title, anchor="w" if key in ("title", "composer", "uploader") else "center")
            anchor = "w" if key in ("title", "composer", "uploader") else "center"
            self.tree.column(key, width=px(width), minwidth=px(40), stretch=stretch, anchor=anchor)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient=tk.VERTICAL, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.bind("<Double-1>", lambda e: self.play_selected("guide"))
        self.tree.bind("<Return>", lambda e: self.play_selected("guide"))

        bottom = tk.Frame(self, bg=P["bg"])
        bottom.grid(row=2, column=0, sticky="ew", padx=gutter, pady=(px(10), 0))
        self.btn_auto = make_button(bottom, "演奏", lambda: self.play_selected("auto"), kind="accent")
        self.btn_auto.pack(side=tk.RIGHT, padx=(px(6), 0))
        self.btn_guide = make_button(bottom, "跟弹", lambda: self.play_selected("guide"), kind="guide")
        self.btn_guide.pack(side=tk.RIGHT, padx=(px(6), 0))
        self.btn_save = make_button(bottom, "存到我的曲库", self.save_selected, icon_name="download")
        self.btn_save.pack(side=tk.RIGHT)
        self.lbl_selected = make_label(bottom, "双击一首曲子直接跟弹", dim=True, anchor="w")
        self.lbl_selected.pack(side=tk.LEFT, fill=tk.X, expand=True)

        foot = tk.Frame(self, bg=P["bg"])
        foot.grid(row=3, column=0, sticky="ew", padx=gutter, pady=(px(6), 0))
        make_label(foot, f"曲谱来自 {SOURCE_NAME}，由玩家上传，版权归原作者 · 本程序只读",
                   size=8, fg=P["text_muted"]).pack(side=tk.LEFT)
        make_button(foot, "曲库项目主页", lambda: webbrowser.open(SOURCE_URL), kind="ghost",
                    icon_name="link", height=24, size=8, padx=8).pack(side=tk.LEFT, padx=(px(6), 0))
        self._set_buttons(False)

    def _set_buttons(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        self.btn_guide.configure(state=state)
        self.btn_auto.configure(state=state)
        self.btn_save.configure(state=state)

    # ================================================================== #
    # 目录
    # ================================================================== #

    def on_show(self) -> None:
        """第一次切到这个标签时才去拉目录（不打开就不联网）。"""
        if not self._loaded_once:
            self._loaded_once = True
            self.load(force=False)

    def load(self, force: bool = False) -> None:
        if self._loading:
            return
        self._loading = True
        self.lbl_status.configure(text="正在读取在线曲库目录…", fg=Palette["text_dim"])

        def work() -> None:
            try:
                library = self._factory()
                entries = library.catalog(force=force)
                error = None
            except OnlineError as exc:
                entries, error = [], str(exc)
            except Exception as exc:                 # noqa: BLE001 - 后台线程出错要报给界面
                entries, error = [], f"{type(exc).__name__}: {exc}"
            self._post(lambda: self._catalog_done(entries, error))

        threading.Thread(target=work, name="kouqin-online-catalog", daemon=True).start()

    def _post(self, callback: Callable[[], None]) -> None:
        """后台线程把结果交回界面线程（不在后台线程里直接碰 Tk，见 MainWindow.call_soon）。"""
        call_soon = getattr(self.app, "call_soon", None)
        if call_soon is not None:
            call_soon(callback)
            return
        try:
            self.after(0, callback)
        except (tk.TclError, RuntimeError):
            pass                                     # 窗口已经关了

    def _catalog_done(self, entries: List[OnlineEntry], error: Optional[str]) -> None:
        self._loading = False
        if error:
            self.lbl_status.configure(text=f"⚠ {error}", fg=Palette["warn"])
            self.app.log(f"在线曲库：{error}")
            return
        self.show_entries(entries)

    def show_entries(self, entries: List[OnlineEntry]) -> None:
        self._entries = list(entries)
        self._apply_search()

    def _schedule_search(self) -> None:
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(200, self._apply_search)

    def _apply_search(self) -> None:
        self._search_job = None
        self._shown = search(self._entries, self.var_query.get())
        self.tree.delete(*self.tree.get_children())
        for index, entry in enumerate(self._shown):
            self.tree.insert("", tk.END, iid=str(index), values=(
                entry.title, entry.composer, entry.uploader, entry.duration_text,
                f"{entry.bpm:.0f}", entry.note_count, entry.updated_text,
            ))
        total = len(self._entries)
        shown = len(self._shown)
        text = f"共 {total} 首" if shown == total else f"找到 {shown} / {total} 首"
        self.lbl_status.configure(text=text, fg=Palette["text_dim"])
        self._on_select()

    def _selected(self) -> Optional[OnlineEntry]:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return self._shown[int(selection[0])]
        except (ValueError, IndexError):
            return None

    def _on_select(self) -> None:
        entry = self._selected()
        self._set_buttons(entry is not None and self._busy_id is None)
        if entry is None:
            self.lbl_selected.configure(text="双击一首曲子直接跟弹", fg=Palette["text_dim"])
            return
        detail = f"《{entry.title}》 · {entry.note_count} 个音 · {entry.duration_text}"
        if entry.tags:
            detail += " · " + " / ".join(entry.tags[:3])
        self.lbl_selected.configure(text=detail, fg=Palette["text"])

    # ================================================================== #
    # 演奏 / 跟弹
    # ================================================================== #

    def play_selected(self, mode: str) -> None:
        def play(info: OnlineEntry, notes, transpose: int) -> None:
            self.app.log(f"在线曲库《{info.title}》（上传者 {info.uploader}）：{len(notes)} 个音"
                         + (f"，谱面自带移调 {transpose:+d}" if transpose else ""))
            self.app.play_notes(notes, name=info.title, transpose=transpose, mode=mode)

        self._download(play)

    def save_selected(self) -> None:
        """把选中的曲谱原样存进「我的曲库」，之后不联网也能点播、跟弹。"""
        entry = self._selected()
        if entry is None:
            return
        saved = self._saved_name(entry.id)
        if saved:
            self.lbl_selected.configure(text=f"《{saved}》已经在我的曲库里了", fg=Palette["success"])
            return
        self._download(self._save)

    def _saved_name(self, entry_id: str) -> Optional[str]:
        """这首在线曲谱以前存过的话，返回它在我的曲库里的名字。"""
        library = self.app.library
        for name in library.names():
            tune = library.load(name)
            if tune is not None and tune.source.get("kind") == "online" and tune.source.get("id") == entry_id:
                return tune.name
        return None

    def _save(self, info: OnlineEntry, notes, transpose: int) -> None:
        tune = to_tune(info, notes, transpose)
        base = tune.name
        index = 2
        while self.app.library.exists(tune.name):
            tune.name = f"{base} ({index})"
            index += 1
        try:
            self.app.library.save(tune)
        except OSError as exc:
            self.lbl_selected.configure(text=f"⚠ 保存失败：{exc}", fg=Palette["warn"])
            return
        self.app.refresh_library()
        self.lbl_selected.configure(text=f"✔ 已存进我的曲库：《{tune.name}》", fg=Palette["success"])
        self.app.log(f"已把在线曲库《{info.title}》存进我的曲库：{len(notes)} 个音，原样保存"
                     f"（上传者 {info.uploader}；不联网也能点播、跟弹）")

    def _download(self, then: Callable[[OnlineEntry, list, int], None]) -> None:
        """后台下载选中的曲谱，下好后在界面线程里调 ``then(目录信息, 单声部音符, 移调)``。"""
        entry = self._selected()
        if entry is None or self._busy_id is not None:
            return
        self._busy_id = entry.id
        self._set_buttons(False)
        self.lbl_selected.configure(text=f"正在下载《{entry.title}》…", fg=Palette["guide"])

        def work() -> None:
            try:
                library = self._factory()
                info, notes, transpose = library.playable_notes(entry.id)
                result, error = (info, notes, transpose), None
            except OnlineError as exc:
                result, error = None, str(exc)
            except Exception as exc:                 # noqa: BLE001
                result, error = None, f"{type(exc).__name__}: {exc}"
            self._post(lambda: self._download_done(entry, then, result, error))

        threading.Thread(target=work, name="kouqin-online-score", daemon=True).start()

    def _download_done(self, entry: OnlineEntry, then, result, error: Optional[str]) -> None:
        self._busy_id = None
        self._on_select()
        if error or result is None:
            self.lbl_selected.configure(text=f"⚠ {error}", fg=Palette["warn"])
            self.app.log(f"在线曲库《{entry.title}》：{error}")
            return
        info, notes, transpose = result
        if not notes:
            self.lbl_selected.configure(text="⚠ 这首曲谱里没有音符", fg=Palette["warn"])
            return
        then(info, notes, transpose)
