"""
谱曲页 —— 点格子填曲子。

核心就一张表：每小节几拍，每一拍一个格子，点一下弹个面板选音。
不需要懂时值、小节线、调号；想哪里响就点哪里，想哪里停就填「休止」。

界面上的三个贴心设计：
    1. 格子按音高上色（低音蓝 / 中音绿 / 高音橙 / 更高紫），
       不用看文字就知道音是高是低。
    2. 格子里直接印着该按哪个键，看完就能对着键盘比划。
    3. 想让一个音拖长，后面的格子填「— 延长」；连续两格填同一个音会吹两下。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Callable, List, Optional

from kouqin.tune import (
    OCTAVE_COLORS,
    OCTAVE_LABELS,
    REST,
    Tune,
    Cell,
)

from .cell_picker import CellPicker
from .theme import Palette, make_button, make_combobox, make_label

# 画布尺寸参数
CELL_W = 58
CELL_H = 68
CELL_GAP = 5
BAR_PAD = 8
BAR_HEAD = 30
BAR_GAP = 16
ROW_GAP = 14
MARGIN = 14

#: 速度档位（对用户只说快慢，不强迫理解 BPM）
SPEED_CHOICES = [("很慢", 60), ("慢", 75), ("中速", 90), ("稍快", 105), ("快", 120)]
BAR_CHOICES = [2, 3, 4, 6]


class ComposerPage(tk.Frame):
    """点格子谱曲。"""

    def __init__(self, master, app) -> None:
        super().__init__(master, bg=Palette["bg"])
        self.app = app
        self.tune = Tune(name="我的曲子", bpm=90.0, beats_per_bar=4)
        self.tune.add_bars(4)
        self._hits: List[tuple] = []
        self._sel_index: Optional[int] = None
        self._saved_name: str = ""

        self._build()
        self._sync_controls()

    # ================================================================== #
    # 界面
    # ================================================================== #

    def _build(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_canvas()
        self._build_footer()

    def _build_header(self) -> None:
        bar = tk.Frame(self, bg=Palette["panel"], height=52)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)

        make_label(bar, "曲名").pack(side=tk.LEFT, padx=(12, 6), pady=12)
        self.var_name = tk.StringVar(value=self.tune.name)
        entry = tk.Entry(
            bar, textvariable=self.var_name, width=18,
            bg=Palette["entry_bg"], fg=Palette["text"], insertbackground=Palette["accent"],
            relief=tk.FLAT, bd=0, font=("Microsoft YaHei UI", 10), highlightthickness=1,
            highlightbackground=Palette["border"], highlightcolor=Palette["accent"],
        )
        entry.pack(side=tk.LEFT, pady=12, ipady=4)
        self.var_name.trace_add("write", lambda *_: self._on_name_change())

        tk.Frame(bar, bg=Palette["border"], width=1).pack(side=tk.LEFT, fill=tk.Y, padx=12, pady=10)

        make_label(bar, "速度").pack(side=tk.LEFT, padx=(0, 6))
        self._speed_buttons = []
        for label, bpm in SPEED_CHOICES:
            button = tk.Button(
                bar, text=label, command=lambda b=bpm: self._set_bpm(b),
                bg=Palette["panel_alt"], fg=Palette["text_dim"],
                activebackground=Palette["border"], activeforeground=Palette["text"],
                relief=tk.FLAT, bd=0, padx=9, pady=3, cursor="hand2",
                font=("Microsoft YaHei UI", 9), highlightthickness=1,
                highlightbackground=Palette["border"],
            )
            button.pack(side=tk.LEFT, padx=2)
            self._speed_buttons.append((bpm, button))

        tk.Frame(bar, bg=Palette["border"], width=1).pack(side=tk.LEFT, fill=tk.Y, padx=12, pady=10)

        make_label(bar, "每小节").pack(side=tk.LEFT, padx=(0, 6))
        self.var_beats = tk.StringVar(value=f"{self.tune.beats_per_bar} 拍")
        combo = make_combobox(bar, [f"{n} 拍" for n in BAR_CHOICES], self.var_beats, width=6)
        combo.pack(side=tk.LEFT, pady=12)
        combo.bind("<<ComboboxSelected>>", lambda e: self._set_beats())

        self.lbl_hint = make_label(bar, "", dim=True)
        self.lbl_hint.pack(side=tk.RIGHT, padx=12)

    def _build_canvas(self) -> None:
        wrap = tk.Frame(self, bg=Palette["bg"])
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            wrap, bg=Palette["bg"], highlightthickness=0,
            yscrollincrement=40,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        scroll = tk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.canvas.yview,
                              bg=Palette["panel_alt"], troughcolor=Palette["panel"],
                              activebackground=Palette["border"], width=12)
        scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scroll.set)

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

    def _build_footer(self) -> None:
        bar = tk.Frame(self, bg=Palette["panel_alt"])
        bar.grid(row=2, column=0, sticky="ew")

        make_button(bar, "+ 加一小节", lambda: self._add_bars(1)).pack(side=tk.LEFT, padx=(10, 4), pady=8)
        make_button(bar, "- 减一小节", lambda: self._add_bars(-1), kind="ghost").pack(side=tk.LEFT, padx=4, pady=8)
        make_button(bar, "清空", self._clear, kind="ghost").pack(side=tk.LEFT, padx=4, pady=8)

        make_label(bar, text="", bg=Palette["panel_alt"]).pack(side=tk.LEFT, expand=True)

        make_button(bar, "试听", self._preview, kind="ghost").pack(side=tk.RIGHT, padx=4, pady=8)
        make_button(bar, "保存", self._save, kind="ghost").pack(side=tk.RIGHT, padx=4, pady=8)
        make_button(bar, "保存并演奏", self._save_and_play, kind="accent").pack(side=tk.RIGHT, padx=(4, 10), pady=8)

    # ================================================================== #
    # 绘制
    # ================================================================== #

    def _bar_geometry(self, beats: int):
        inner = beats * CELL_W + max(0, beats - 1) * CELL_GAP
        width = BAR_HEAD + inner + BAR_PAD
        return width, inner

    def _redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        self._hits.clear()

        width = canvas.winfo_width() or 900
        beats = self.tune.beats_per_bar
        bar_w, _ = self._bar_geometry(beats)

        usable = max(bar_w + BAR_GAP, width - MARGIN * 2)
        per_row = max(1, int((usable + BAR_GAP) // (bar_w + BAR_GAP)))

        bars = self.tune.bars()
        row_height = CELL_H + ROW_GAP + 16

        for bar_index, bar_cells in enumerate(bars):
            row, col = divmod(bar_index, per_row)
            x0 = MARGIN + col * (bar_w + BAR_GAP)
            y0 = MARGIN + row * row_height

            self._draw_bar(x0, y0, bar_index, bar_cells)

        row_count = (len(bars) + per_row - 1) // max(1, per_row)
        total_h = MARGIN * 2 + row_count * row_height
        canvas.configure(scrollregion=(0, 0, width, max(total_h, canvas.winfo_height())))

        self._update_hint()

    def _draw_bar(self, x0: float, y0: float, bar_index: int, bar_cells: List[Cell]) -> None:
        canvas = self.canvas
        beats = self.tune.beats_per_bar
        bar_w, inner = self._bar_geometry(beats)

        # 小节外框
        canvas.create_rectangle(x0, y0, x0 + bar_w, y0 + CELL_H + 12,
                                fill=Palette["panel"], outline=Palette["border"], width=1)
        canvas.create_text(x0 + BAR_HEAD / 2, y0 + (CELL_H + 12) / 2,
                           text=str(bar_index + 1), fill=Palette["text_dim"],
                           font=("Consolas", 10, "bold"))

        first = bar_index * beats
        for offset, cell in enumerate(bar_cells):
            if offset >= beats:
                break
            cx = x0 + BAR_HEAD + offset * (CELL_W + CELL_GAP)
            cy = y0 + 6
            self._draw_cell(cx, cy, first + offset, cell)

    def _draw_cell(self, x: float, y: float, index: int, cell: Cell) -> None:
        canvas = self.canvas
        x1, y1 = x, y
        x2, y2 = x + CELL_W, y + CELL_H

        selected = index == self._sel_index

        if cell.is_hold:
            self._draw_hold(x1, y1, x2, y2, index, selected)
            return

        if cell.is_rest:
            fill, fg = Palette["panel_alt"], "#4d5866"
            outline = Palette["sel"] if selected else Palette["border"]
            canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline,
                                    width=2 if selected else 1)
            canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2, text="·",
                               fill=fg, font=("Consolas", 22, "bold"))
            canvas.create_text((x1 + x2) / 2, y2 - 11, text="休止",
                               fill="#3f4a58", font=("Microsoft YaHei UI", 8))
            self._hits.append((index, x1, y1, x2, y2))
            return

        bg, accent = OCTAVE_COLORS.get(cell.octave, OCTAVE_COLORS[0])
        outline = Palette["sel"] if selected else accent
        canvas.create_rectangle(x1, y1, x2, y2, fill=bg, outline=outline,
                                width=2 if selected else 1)

        # 数字：主角，放最大
        canvas.create_text((x1 + x2) / 2, y1 + 22, text=cell.digit,
                           fill=accent, font=("Microsoft YaHei UI", 19, "bold"))
        # 唱名
        canvas.create_text((x1 + x2) / 2, y1 + 43, text=cell.solfa,
                           fill=accent, font=("Consolas", 9))

        # 实际按键（最关键的信息，放右下角）
        combo = self.app.layout.resolve(cell.pitch)
        if combo:
            canvas.create_text(x2 - 5, y2 - 9, anchor="se", text=combo.tokens(),
                               fill=accent, font=("Consolas", 8, "bold"))
        else:
            canvas.create_text(x2 - 5, y2 - 9, anchor="se", text="超范围",
                               fill="#e06060", font=("Microsoft YaHei UI", 7))

        # 八度小标记（低音/高音才有）
        if cell.octave != 0:
            canvas.create_text(x1 + 5, y1 + 9, anchor="nw",
                               text=OCTAVE_LABELS[cell.octave][0],
                               fill=accent, font=("Microsoft YaHei UI", 8, "bold"))

        self._hits.append((index, x1, y1, x2, y2))

    def _held_note(self, index: int) -> Optional[Cell]:
        """「延长」格子接的是哪个音（往前找到第一个音；中间碰到休止就没有）。"""
        for cell in reversed(self.tune.cells[:index]):
            if cell.is_note:
                return cell
            if cell.is_rest:
                return None
        return None

    def _draw_hold(self, x1: float, y1: float, x2: float, y2: float,
                   index: int, selected: bool) -> None:
        """「延长」格子：用被延长那个音的颜色画一道横杠，一眼看出是谁拖长了。"""
        canvas = self.canvas
        note = self._held_note(index)
        if note is None:
            # 前面没有音可接：吹不出声，按休止的样子画、但标出来
            bg, accent = Palette["panel_alt"], "#6b5a3a"
        else:
            bg, accent = OCTAVE_COLORS.get(note.octave, OCTAVE_COLORS[0])
        outline = Palette["sel"] if selected else accent
        canvas.create_rectangle(x1, y1, x2, y2, fill=bg, outline=outline,
                                width=2 if selected else 1)
        canvas.create_line(x1 + 8, (y1 + y2) / 2 - 6, x2 - 8, (y1 + y2) / 2 - 6,
                           fill=accent, width=4)
        canvas.create_text((x1 + x2) / 2, y2 - 14,
                           text="延长" if note is not None else "延长（无音）",
                           fill=accent, font=("Microsoft YaHei UI", 8))
        self._hits.append((index, x1, y1, x2, y2))

    # ================================================================== #
    # 交互
    # ================================================================== #

    def _on_click(self, event) -> None:
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        for index, x1, y1, x2, y2 in self._hits:
            if x1 <= x <= x2 and y1 <= y <= y2:
                self._sel_index = index
                self._redraw()
                self._edit_cell(index)
                return
        self._sel_index = None
        self._redraw()

    def _edit_cell(self, index: int) -> None:
        if index >= len(self.tune.cells):
            return
        current = self.tune.cells[index]
        CellPicker(
            self.app, self.app.layout, current,
            on_pick=lambda cell, i=index: self._apply_cell(i, cell),
            title=f"第 {index // self.tune.beats_per_bar + 1} 小节 · 第 {index % self.tune.beats_per_bar + 1} 拍",
        )

    def _apply_cell(self, index: int, cell: Cell) -> None:
        self.tune.set_cell(index, cell)
        # 填完自动跳到下一格，连续填谱更顺手
        self._sel_index = min(index + 1, len(self.tune.cells) - 1)
        self._redraw()

    # ================================================================== #
    # 参数
    # ================================================================== #

    def _on_name_change(self) -> None:
        name = self.var_name.get().strip()
        if name:
            self.tune.name = name

    def _set_bpm(self, bpm: int) -> None:
        self.tune.bpm = float(bpm)
        self._sync_controls()
        self._redraw()

    def _set_beats(self) -> None:
        text = self.var_beats.get()
        try:
            beats = int(text.split()[0])
        except (ValueError, IndexError):
            return
        if beats == self.tune.beats_per_bar:
            return
        old = self.tune
        rebuilt = Tune(name=old.name, bpm=old.bpm, beats_per_bar=beats, cells=list(old.cells))
        rebuilt.ensure_length(beats * max(2, old.bar_count))
        self.tune = rebuilt
        self._sync_controls()
        self._redraw()

    def _add_bars(self, delta: int) -> None:
        if delta > 0:
            self.tune.add_bars(delta)
        else:
            beats = self.tune.beats_per_bar
            if self.tune.length <= beats:
                return
            del self.tune.cells[-beats:]
        self._redraw()

    def _clear(self) -> None:
        if not messagebox.askyesno("清空", "确定要清空当前曲子吗？此操作不可撤销。", parent=self):
            return
        beats = self.tune.beats_per_bar
        self.tune = Tune(name=self.var_name.get().strip() or "我的曲子",
                         bpm=self.tune.bpm, beats_per_bar=beats)
        self.tune.add_bars(4)
        self._sel_index = None
        self._sync_controls()
        self._redraw()

    def _sync_controls(self) -> None:
        for bpm, button in self._speed_buttons:
            active = abs(bpm - self.tune.bpm) < 0.5
            button.configure(
                bg=Palette["accent_dark"] if active else Palette["panel_alt"],
                fg="#ffffff" if active else Palette["text_dim"],
                highlightbackground=Palette["accent"] if active else Palette["border"],
            )
        self.var_beats.set(f"{self.tune.beats_per_bar} 拍")
        self.var_name.set(self.tune.name)

    def _update_hint(self) -> None:
        count = self.tune.note_count()
        seconds = self.tune.duration_ms() / 1000.0
        self.lbl_hint.configure(
            text=f"{self.tune.bar_count} 小节 · {count} 个音 · 约 {seconds:.0f} 秒"
        )

    # ================================================================== #
    # 外部接口
    # ================================================================== #

    def load_tune(self, tune: Tune) -> None:
        """载入一首曲子来编辑。"""
        self.tune = tune.copy()
        self._saved_name = tune.name
        self._sel_index = None
        self._sync_controls()
        self._redraw()
        self.canvas.yview_moveto(0)

    def new_tune(self) -> None:
        beats = self.app.settings.beats_per_bar
        self.tune = Tune(name="我的曲子", bpm=self.app.settings.default_bpm, beats_per_bar=beats)
        self.tune.add_bars(4)
        self._saved_name = ""
        self._sel_index = None
        self._sync_controls()
        self._redraw()
        self.canvas.yview_moveto(0)

    def current_tune(self) -> Tune:
        self.tune.name = self.var_name.get().strip() or "我的曲子"
        return self.tune

    # ================================================================== #
    # 试听 / 保存
    # ================================================================== #

    def _preview(self) -> None:
        tune = self.current_tune()
        notes = tune.to_notes()
        if not notes:
            messagebox.showinfo("试听", "还没有填任何音呢。", parent=self)
            return
        if self.app.preview.play_notes(notes, bpm=tune.bpm):
            self.app.log(f"试听《{tune.name}》：{len(notes)} 个音")
        else:
            self.app.log("试听失败：系统没有可用的 MIDI 输出设备")

    def _save(self, silent: bool = False) -> bool:
        tune = self.current_tune()
        if tune.is_empty():
            if not silent:
                messagebox.showinfo("保存", "曲子还是空的，先填几个音吧。", parent=self)
            return False

        if self._saved_name and self._saved_name != tune.name and self.app.library.exists(tune.name):
            if not messagebox.askyesno("重名", f"已经有一首叫《{tune.name}》的曲子了，覆盖它吗？", parent=self):
                return False

        try:
            path = self.app.library.save(tune)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return False

        self._saved_name = tune.name
        self._update_hint()
        self.app.refresh_library()
        if not silent:
            self.app.log(f"已保存《{tune.name}》到曲库")
        return True

    def _save_and_play(self) -> None:
        if not self._save(silent=True):
            return
        self.app.play_tune(self.current_tune())
