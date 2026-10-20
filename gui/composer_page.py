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
    OCTAVE_LABELS,
    REST,
    Tune,
    Cell,
)

from tkinter import ttk

from .cell_picker import CellPicker
from .theme import Palette, make_button, make_combobox, make_label, octave_colors, px, title_font, ui_font
from .widgets import SearchEntry, Segmented, paint_rounded

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
        P = Palette
        head = tk.Frame(self, bg=P["bg"])
        head.grid(row=0, column=0, sticky="ew", padx=px(28), pady=(px(24), px(12)))
        top = tk.Frame(head, bg=P["bg"])
        top.pack(fill=tk.X)
        tk.Label(top, text="谱曲", font=title_font(22), bg=P["bg"], fg=P["text"], anchor="w").pack(side=tk.LEFT)
        self.lbl_hint = make_label(top, "", dim=True)
        self.lbl_hint.pack(side=tk.RIGHT, anchor="s")
        make_label(head, "点格子选音；想拖长就在后面填「— 延长」，同一个音连填两格会吹两下", dim=True,
                   anchor="w").pack(fill=tk.X, pady=(px(4), px(12)))

        bar = tk.Frame(head, bg=P["bg"])
        bar.pack(fill=tk.X)
        make_label(bar, "曲名").pack(side=tk.LEFT, padx=(0, px(8)))
        self.var_name = tk.StringVar(value=self.tune.name)
        name_box = SearchEntry(bar, self.var_name, width=12, search_icon=False)
        name_box.pack(side=tk.LEFT)
        self.var_name.trace_add("write", lambda *_: self._on_name_change())

        make_label(bar, "速度").pack(side=tk.LEFT, padx=(px(16), px(8)))
        self.seg_speed = Segmented(bar, [(str(bpm), label) for label, bpm in SPEED_CHOICES],
                                   command=lambda key: self._set_bpm(float(key)), value=None, size=9)
        self.seg_speed.pack(side=tk.LEFT)
        self._speed_buttons = [(bpm, self.seg_speed.buttons[str(bpm)]) for _label, bpm in SPEED_CHOICES]

        make_label(bar, "每小节").pack(side=tk.LEFT, padx=(px(16), px(8)))
        self.var_beats = tk.StringVar(value=f"{self.tune.beats_per_bar} 拍")
        combo = make_combobox(bar, [f"{n} 拍" for n in BAR_CHOICES], self.var_beats, width=6)
        combo.pack(side=tk.LEFT)
        combo.bind("<<ComboboxSelected>>", lambda e: self._set_beats())

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

        scroll = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns", padx=(0, px(6)))
        self.canvas.configure(yscrollcommand=scroll.set)

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

    def _build_footer(self) -> None:
        bar = tk.Frame(self, bg=Palette["bg"])
        bar.grid(row=2, column=0, sticky="ew", padx=px(28), pady=(px(10), px(14)))

        make_button(bar, "加一小节", lambda: self._add_bars(1), icon_name="add").pack(side=tk.LEFT)
        make_button(bar, "减一小节", lambda: self._add_bars(-1), kind="ghost").pack(side=tk.LEFT, padx=(px(6), 0))
        make_button(bar, "清空", self._clear, kind="ghost").pack(side=tk.LEFT, padx=(px(6), 0))

        make_button(bar, "保存并演奏", self._save_and_play, kind="accent").pack(side=tk.RIGHT, padx=(px(6), 0))
        make_button(bar, "保存", self._save).pack(side=tk.RIGHT, padx=(px(6), 0))
        make_button(bar, "试听", self._preview, kind="ghost", icon_name="play").pack(side=tk.RIGHT)

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
        paint_rounded(canvas, int(x0), int(y0), int(x0 + bar_w), int(y0 + CELL_H + 12), 10,
                      Palette["panel"], Palette["border"], Palette["bg"])
        canvas.create_text(x0 + BAR_HEAD / 2, y0 + (CELL_H + 12) / 2,
                           text=str(bar_index + 1), fill=Palette["text_muted"],
                           font=ui_font(10, True))

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
            fill, fg = Palette["panel_alt"], Palette["text_muted"]
            self._cell_box(x1, y1, x2, y2, fill, Palette["border"], selected)
            canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2 - 4, text="·",
                               fill=fg, font=ui_font(22, True))
            canvas.create_text((x1 + x2) / 2, y2 - 11, text="休止",
                               fill=fg, font=ui_font(8))
            self._hits.append((index, x1, y1, x2, y2))
            return

        colors = octave_colors()
        bg, accent = colors.get(cell.octave, colors[0])
        self._cell_box(x1, y1, x2, y2, bg, None, selected)

        # 数字：主角，放最大
        canvas.create_text((x1 + x2) / 2, y1 + 22, text=cell.digit,
                           fill=accent, font=ui_font(19, True))
        # 唱名
        canvas.create_text((x1 + x2) / 2, y1 + 43, text=cell.solfa,
                           fill=accent, font=ui_font(8))

        # 实际按键（最关键的信息，放右下角）
        combo = self.app.layout.resolve(cell.pitch)
        if combo:
            canvas.create_text(x2 - 5, y2 - 9, anchor="se", text=combo.tokens(),
                               fill=accent, font=ui_font(8, True))
        else:
            canvas.create_text(x2 - 5, y2 - 9, anchor="se", text="超范围",
                               fill=Palette["danger"], font=ui_font(7))

        # 八度小标记（低音/高音才有）
        if cell.octave != 0:
            canvas.create_text(x1 + 6, y1 + 8, anchor="nw",
                               text=OCTAVE_LABELS[cell.octave][0],
                               fill=accent, font=ui_font(8, True))

        self._hits.append((index, x1, y1, x2, y2))

    def _cell_box(self, x1: float, y1: float, x2: float, y2: float, fill: str,
                  outline: Optional[str], selected: bool) -> None:
        """格子底板：圆角；选中时外面多一圈 2 像素的蓝边。"""
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        surface = Palette["panel"]
        if selected:
            paint_rounded(self.canvas, x1 - 2, y1 - 2, x2 + 2, y2 + 2, 9, Palette["sel"], None, surface)
            paint_rounded(self.canvas, x1, y1, x2, y2, 7, fill, outline, Palette["sel"])
        else:
            paint_rounded(self.canvas, x1, y1, x2, y2, 7, fill, outline, surface)

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
        colors = octave_colors()
        if note is None:
            # 前面没有音可接：吹不出声，按休止的样子画、但标出来
            bg, accent = Palette["panel_alt"], Palette["warn"]
        else:
            bg, accent = colors.get(note.octave, colors[0])
        self._cell_box(x1, y1, x2, y2, bg, None, selected)
        canvas.create_line(x1 + 10, (y1 + y2) / 2 - 6, x2 - 10, (y1 + y2) / 2 - 6,
                           fill=accent, width=4, capstyle=tk.ROUND)
        canvas.create_text((x1 + x2) / 2, y2 - 14,
                           text="延长" if note is not None else "延长（无音）",
                           fill=accent, font=ui_font(8))
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
        match = [bpm for bpm, _button in self._speed_buttons if abs(bpm - self.tune.bpm) < 0.5]
        self.seg_speed.set(str(match[0]) if match else "")
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
