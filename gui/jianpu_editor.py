"""
简谱编辑器窗口。

左边写谱，右边即时校验：一边敲一边告诉你解析出了多少个音、
音域落在哪里、哪一行写错了。写错了不会阻塞，只是标出来，
所以可以随手试着改，不用小心翼翼地一次写对。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from kouqin.jianpu import JIANPU_TEMPLATE, describe, parse_jianpu
from kouqin.smf import NOTE_NAMES

from .theme import Palette, make_button, make_label

SYNTAX_HELP = """\
 1 2 3 4 5 6 7      do re mi fa sol la si
 0                  休止
 #4 / b7            升 fa / 降 si
 1'  1''            高八度 / 高两八度
 1,  1,,            低八度 / 低两八度
 1/  1//            八分音符 / 十六分音符
 1.                 附点（时长 ×1.5）
 -                  延长一个单位（单独写）
 |                  小节线，只作分隔
 [60]               直接写 MIDI 音高
 C4  F#5            也可以直接写音名
 T=90               设置速度（BPM）
 ; 文字             行注释

 例：
 5 5 6 5 | 1' 7 - - |
"""


class JianpuEditor(tk.Toplevel):
    """简谱录入窗口。"""

    def __init__(self, master, initial: str = "", on_apply: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(master)
        self.title("简谱谱曲 — 口琴工坊")
        self.geometry("880x560")
        self.configure(bg=Palette["bg"])
        self.transient(master)
        self.on_apply = on_apply
        self._debounce: Optional[str] = None

        self._build(initial or JIANPU_TEMPLATE)
        self._schedule_parse()

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-Return>", lambda e: self._apply())
        self.after(80, lambda: self.text.focus_set())

    # ------------------------------------------------------------------ #

    def _build(self, initial: str) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)

        # 左：编辑区
        left = tk.Frame(self, bg=Palette["bg"])
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        make_label(left, "谱面", bold=True, bg=Palette["bg"]).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.text = tk.Text(
            left, bg=Palette["entry_bg"], fg=Palette["text"],
            insertbackground=Palette["accent"], relief=tk.FLAT, bd=0,
            font=("Consolas", 12), undo=True, wrap="word", padx=10, pady=8,
            selectbackground=Palette["sel"],
        )
        self.text.grid(row=1, column=0, sticky="nsew")
        self.text.insert("1.0", initial)
        self.text.bind("<KeyRelease>", lambda e: self._schedule_parse())

        scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.text.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)

        # 右：速查 + 校验
        right = tk.Frame(self, bg=Palette["panel"])
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)

        make_label(right, "语法速查", bold=True).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        help_box = tk.Text(
            right, height=15, bg=Palette["entry_bg"], fg=Palette["text_dim"],
            relief=tk.FLAT, bd=0, font=("Consolas", 9), wrap="none", padx=8, pady=6,
        )
        help_box.grid(row=1, column=0, sticky="ew", padx=10)
        help_box.insert("1.0", SYNTAX_HELP)
        help_box.configure(state=tk.DISABLED)

        make_label(right, "解析结果", bold=True).grid(row=2, column=0, sticky="nw", padx=10, pady=(12, 4))
        self.result = tk.Text(
            right, height=8, bg=Palette["entry_bg"], fg=Palette["text"],
            relief=tk.FLAT, bd=0, font=("Microsoft YaHei UI", 9), wrap="word", padx=8, pady=6,
        )
        self.result.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 8))
        self.result.configure(state=tk.DISABLED)
        right.grid_rowconfigure(3, weight=1)

        # 底：按钮
        bar = tk.Frame(self, bg=Palette["panel_alt"])
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        make_button(bar, "取消", self.destroy, kind="ghost").pack(side=tk.RIGHT, padx=(4, 10), pady=8)
        make_button(bar, "载入谱面  (Ctrl+Enter)", self._apply, kind="accent").pack(side=tk.RIGHT, padx=4, pady=8)
        make_button(bar, "恢复模板", self._reset_template, kind="ghost").pack(side=tk.LEFT, padx=10, pady=8)

    # ------------------------------------------------------------------ #

    def _schedule_parse(self) -> None:
        """输入防抖：停手 250ms 才真正解析，避免每敲一下都算一遍。"""
        if self._debounce:
            self.after_cancel(self._debounce)
        self._debounce = self.after(250, self._parse_now)

    def _parse_now(self) -> None:
        self._debounce = None
        text = self.text.get("1.0", "end-1c")
        score = parse_jianpu(text)

        lines = []
        if not score.notes:
            lines.append("✖ 没有解析出音符")
        else:
            lines.append("✔ " + describe(score))
            lo = min(n.pitch for n in score.notes)
            hi = max(n.pitch for n in score.notes)
            lows = [n for n in score.notes if n.pitch < 48]
            highs = [n for n in score.notes if n.pitch > 85]
            lines.append(f"  音域：{NOTE_NAMES[lo % 12]}{lo // 12 - 1} ~ {NOTE_NAMES[hi % 12]}{hi // 12 - 1}")
            if lows or highs:
                lines.append(f"  ⚠ 超出口琴音域 {len(lows) + len(highs)} 个音")
                lines.append("    载入后会按「超音域策略」折叠或跳过")
            else:
                lines.append("  全部落在口琴音域内，可直接演奏")

        if score.warnings:
            lines.append("")
            lines.append(f"⚠ {len(score.warnings)} 条提醒：")
            for warning in score.warnings[:12]:
                lines.append("  · " + warning)

        self.result.configure(state=tk.NORMAL)
        self.result.delete("1.0", tk.END)
        self.result.insert("1.0", "\n".join(lines))
        self.result.configure(state=tk.DISABLED)

    def _apply(self) -> None:
        text = self.text.get("1.0", "end-1c")
        if self.on_apply:
            self.on_apply(text)
        self.destroy()

    def _reset_template(self) -> None:
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", JIANPU_TEMPLATE)
        self._schedule_parse()
