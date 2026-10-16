"""
使用步骤引导。

第一次打开程序时自动弹出来，把"怎么开始"讲清楚；
之后随时可以点右上角的「?」再看一遍。

内容只讲三件事：怎么开吹、没反应怎么办、还能做什么。
"""

from __future__ import annotations

import tkinter as tk
from typing import List, Tuple

from .theme import Palette, make_button, make_label

#: 三步开始：(序号, 标题, 说明)
STEPS: List[Tuple[str, str, str]] = (
    (
        "1",
        "选一首曲子",
        "在「曲库」页，点曲名右边的「演奏」按钮。\n"
        "程序已经内置 8 首曲子，不需要自己准备任何文件。",
    ),
    (
        "2",
        "切回游戏",
        "看到倒计时就开始吹了，这时点一下游戏画面。\n"
        "游戏必须设成「窗口化」或「无边框窗口化」—— 全屏独占收不到模拟按键。",
    ),
    (
        "3",
        "想停就按 F8",
        "任何时候按 F8 立即停止，并松开所有按住的键。\n"
        "也可以点右上角的「停止演奏」。F6 是暂停 / 继续。",
    ),
)

#: 游戏里没反应时的排查清单
TROUBLES: List[str] = (
    "是不是双击「启动.bat」启动的？（不是管理员的话游戏收不到按键）",
    "游戏是不是设成了「窗口化」或「无边框窗口化」？",
    "游戏窗口是不是在最前面？（倒计时时点一下游戏画面）",
    "输入法是不是切成英文了？（中文输入法会拦掉按键）",
    "上面都对了还是漏音 → 到「设置」页把节奏档调成「稳健」再试",
)

#: 还能做什么
EXTRAS: List[Tuple[str, str]] = (
    ("自己吹：跟弹", "曲库里点「跟弹」：游戏画面上浮出按键提示，音符走到竖线时按下。"
                       "绿色不用鼠标，蓝 / 橙色要按住对应的鼠标键，带 # 的再加按中键。程序一个键都不按。"),
    ("自己编曲子", "点「谱曲」页，一格一拍地点音。想让一个音拖长，后面的格子选「— 延长」；"
                   "连续两格填同一个音会吹两下。"),
    ("改键位", "点「设置」页 —— 8 个音键和 3 个鼠标变调键都能改，默认对齐游戏。"),
    ("导入现成的曲子", "点「进阶」页，可以导入 .mid 文件，还能一键转成格子谱存进曲库。"),
)


class GuideDialog(tk.Toplevel):
    """使用步骤窗口。"""

    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("怎么用 — 口琴工坊")
        self.configure(bg=Palette["bg"])
        self.resizable(False, False)
        self.transient(master)
        self._build()
        self._centre_on(master)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.focus_set()

    # ------------------------------------------------------------------ #

    def _centre_on(self, master) -> None:
        self.update_idletasks()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
            y = master.winfo_rooty() + max(0, (master.winfo_height() - self.winfo_height()) // 4)
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    def _build(self) -> None:
        root = tk.Frame(self, bg=Palette["bg"], padx=22, pady=18)
        root.pack(fill=tk.BOTH, expand=True)

        make_label(root, "三步就能开始", bold=True, size=15,
                   bg=Palette["bg"]).pack(anchor="w")
        make_label(root, "不需要懂乐理，也不用准备任何文件", dim=True,
                   bg=Palette["bg"]).pack(anchor="w", pady=(4, 16))

        # ---- 三步 ----
        for number, title, detail in STEPS:
            row = tk.Frame(root, bg=Palette["panel"], highlightthickness=1,
                           highlightbackground=Palette["border"])
            row.pack(fill=tk.X, pady=(0, 8))

            badge = tk.Label(row, text=number, bg=Palette["accent"], fg="#06231a",
                             font=("Microsoft YaHei UI", 13, "bold"), width=3, pady=6)
            badge.pack(side=tk.LEFT, padx=(12, 12), pady=11)

            text_box = tk.Frame(row, bg=Palette["panel"])
            text_box.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=10, padx=(0, 12))
            tk.Label(text_box, text=title, bg=Palette["panel"], fg=Palette["text"],
                     font=("Microsoft YaHei UI", 11, "bold"), anchor="w").pack(anchor="w")
            tk.Label(text_box, text=detail, bg=Palette["panel"], fg=Palette["text_dim"],
                     font=("Microsoft YaHei UI", 9), anchor="w", justify=tk.LEFT,
                     wraplength=430).pack(anchor="w", pady=(3, 0))

        # ---- 出问题怎么办 ----
        trouble = tk.Frame(root, bg="#241d13", highlightthickness=1,
                           highlightbackground="#5a4a2a")
        trouble.pack(fill=tk.X, pady=(14, 0))
        inner = tk.Frame(trouble, bg="#241d13", padx=16, pady=12)
        inner.pack(fill=tk.X)

        tk.Label(inner, text="游戏里没反应？按顺序检查这几条", bg="#241d13",
                 fg=Palette["warn"], font=("Microsoft YaHei UI", 10, "bold"),
                 anchor="w").pack(anchor="w", pady=(0, 7))
        for item in TROUBLES:
            tk.Label(inner, text="· " + item, bg="#241d13", fg="#c9b895",
                     font=("Microsoft YaHei UI", 9), anchor="w", justify=tk.LEFT,
                     wraplength=530).pack(anchor="w", pady=1)

        # ---- 还能做什么 ----
        more = tk.Frame(root, bg=Palette["bg"])
        more.pack(fill=tk.X, pady=(16, 0))
        make_label(more, "还能做什么", bold=True, bg=Palette["bg"]).pack(anchor="w", pady=(0, 6))
        for title, detail in EXTRAS:
            line = tk.Frame(more, bg=Palette["bg"])
            line.pack(fill=tk.X, pady=2)
            tk.Label(line, text=title, bg=Palette["bg"], fg=Palette["accent"],
                     font=("Microsoft YaHei UI", 9, "bold"), width=11,
                     anchor="w").pack(side=tk.LEFT)
            tk.Label(line, text=detail, bg=Palette["bg"], fg=Palette["text_dim"],
                     font=("Microsoft YaHei UI", 9), anchor="w", justify=tk.LEFT,
                     wraplength=460).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ---- 免责 + 按钮 ----
        notice = tk.Frame(root, bg="#2b1417", highlightthickness=1,
                          highlightbackground="#5a3038")
        notice.pack(fill=tk.X, pady=(16, 0))
        tk.Label(notice,
                 text="仅供个人学习、技术研究与娱乐使用　·　严禁在任何在线对局中使用　·　后果自负",
                 bg="#2b1417", fg="#e8a2ab", font=("Microsoft YaHei UI", 9),
                 padx=14, pady=8).pack(anchor="w", fill=tk.X)

        make_button(root, "知道了，开始玩", self.destroy, kind="accent",
                    width=16).pack(anchor="e", pady=(14, 0))


def show_guide(master) -> None:
    """弹出使用步骤窗口（非阻塞）。"""
    GuideDialog(master)
