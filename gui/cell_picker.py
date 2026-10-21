"""
音符选择弹窗。

一拍一拍地填谱子时，弹出这个面板选音。为了照顾不懂乐理的用户，
每个音同时给了三样信息：

    简谱数字   1 2 3 ...        （"就是 1 到 7 七个音"）
    唱名       do re mi ...      （听过别人的说法）
    实际按键   Z X C ...         （真正按下去的东西）

不同音高用不同颜色区分（低音蓝 / 中音绿 / 高音橙 / 更高紫），
这样不用看文字也能一眼分辨高低。
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from kouqin.layout import Layout
from kouqin.tune import (
    OCTAVE_LABELS,
    OCTAVE_ORDER,
    HOLD,
    REST,
    STEP_DIGITS,
    STEP_NAMES,
    Cell,
)

from .theme import Palette, make_button, make_label, octave_colors, px
from .widgets import RoundButton


class CellPicker(tk.Toplevel):
    """
    选择一拍用哪个音。

    :param on_pick: 选中后的回调 ``(cell) -> None``
    """

    def __init__(self, master, layout: Layout, current: Cell,
                 on_pick: Callable[[Cell], None],
                 title: str = "选择这一拍的音") -> None:
        super().__init__(master)
        self.layout = layout
        self.current = current
        self.on_pick = on_pick
        self.octave = current.octave if current.is_note else 0

        self.title(title)
        self.configure(bg=Palette["bg"])
        self.resizable(False, False)
        self.transient(master)

        self._build()
        self._center_on(master)

        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        self.focus_set()

    # ------------------------------------------------------------------ #

    def _center_on(self, master) -> None:
        self.update_idletasks()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
            y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    def _build(self) -> None:
        root = tk.Frame(self, bg=Palette["bg"], padx=px(18), pady=px(16))
        root.pack(fill=tk.BOTH, expand=True)

        make_label(root, "先选音高，再点要用的音", bg=Palette["bg"],
                   dim=True).pack(anchor="w", pady=(0, px(8)))

        # ---- 音高档位 ----
        oct_bar = tk.Frame(root, bg=Palette["bg"])
        oct_bar.pack(fill=tk.X, pady=(0, px(12)))

        self._oct_buttons = {}
        colors = octave_colors()
        for level in OCTAVE_ORDER:
            bg, fg = colors[level]
            button = RoundButton(oct_bar, text=OCTAVE_LABELS[level], command=lambda lv=level: self._set_octave(lv),
                                 colors=(bg, None, fg), height=34, size=10, bold=True, padx=14,
                                 bg=Palette["bg"])
            button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=px(3))
            self._oct_buttons[level] = button

        # ---- 音格 ----
        grid = tk.Frame(root, bg=Palette["bg"])
        grid.pack(fill=tk.X)

        self._note_buttons = []
        for index in range(7):
            row, col = divmod(index, 4)
            button = self._make_note_button(grid, index)
            button.grid(row=row, column=col, padx=px(3), pady=px(3), sticky="nsew")
            self._note_buttons.append(button)
        for col in range(4):
            grid.grid_columnconfigure(col, weight=1, uniform="note")

        # ---- 底部 ----
        bottom = tk.Frame(root, bg=Palette["bg"])
        bottom.pack(fill=tk.X, pady=(px(14), 0))

        make_button(bottom, "— 延长（接着吹上一个音）", self._pick_hold,
                    kind="accent" if self.current.is_hold else "normal").pack(side=tk.LEFT)
        make_button(bottom, "休止（这一拍不吹）", self._pick_rest, kind="ghost").pack(side=tk.LEFT, padx=(px(8), 0))
        make_button(bottom, "取消", self.destroy, kind="ghost").pack(side=tk.RIGHT)

        self._refresh()

    def _make_note_button(self, parent, index: int) -> RoundButton:
        bg, fg = octave_colors()[0]
        return RoundButton(parent, text="\n\n", command=lambda i=index: self._pick_note(i),
                           colors=(bg, None, fg), height=78, size=11, bold=True, width=7,
                           radius=10, bg=Palette["bg"])

    # ------------------------------------------------------------------ #

    def _set_octave(self, level: int) -> None:
        self.octave = level
        self._refresh()

    def _refresh(self) -> None:
        colors = octave_colors()
        bg, fg = colors[self.octave]

        for level, button in self._oct_buttons.items():
            active = level == self.octave
            level_bg, level_fg = colors[level]
            # 选中的档位反色：实心深色底、浅色字
            button.configure(colors=(level_fg, None, level_bg) if active else (level_bg, None, level_fg))

        # 当前音高下每个音对应的按键提示
        for index, button in enumerate(self._note_buttons):
            cell = Cell(index, self.octave)
            combo = self.layout.resolve(cell.pitch)
            key_text = combo.tokens() if combo else "超范围"
            selected = (self.current.is_note
                        and self.current.step == index
                        and self.current.octave == self.octave)
            button.configure(
                text=f"{STEP_DIGITS[index]}\n{STEP_NAMES[index]}\n{key_text}",
                colors=(Palette["accent"], None, Palette["on_accent"]) if selected else (bg, None, fg),
            )

    def _pick_note(self, index: int) -> None:
        self._finish(Cell(index, self.octave))

    def _pick_rest(self) -> None:
        self._finish(REST)

    def _pick_hold(self) -> None:
        self._finish(HOLD)

    def _finish(self, cell: Cell) -> None:
        callback = self.on_pick
        self.destroy()
        if callback:
            callback(cell)
