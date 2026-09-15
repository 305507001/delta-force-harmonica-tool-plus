"""
免责声明确认对话框。

第一次运行程序时会弹出来，把最关键的三件事讲清楚：

    1. 这玩意儿只能自己玩（学习/研究/娱乐）
    2. 别拿去打排位（可能封号）
    3. 出了事自己扛（作者不负责）

同意一次就记住了，以后不再打扰。不同意就直接退出。
"""

from __future__ import annotations

import tkinter as tk
from typing import List, Tuple

from .theme import Palette, make_button, make_label

#: 最顶上的两条硬性红线
RED_LINES: Tuple[str, ...] = (
    "本项目仅供个人学习、技术研究与娱乐使用",
    "严禁在任何在线对局中使用",
)

#: 正文分块：(小标题, 内容)
SECTIONS: List[Tuple[str, str]] = (
    (
        "风险告知",
        "在在线游戏中使用自动化输入工具，可能违反游戏用户协议，可能导致账号被警告、"
        "限制功能、临时封禁甚至永久封禁，账号内的虚拟财产与游戏进度可能一并损失。",
    ),
    (
        "作者态度",
        "本项目作者明确不建议、不支持、不鼓励在任何在线对局中使用本项目。"
        "开发过程中的全部验证都只在本地环境完成，从未用于在线对局。",
    ),
    (
        "后果自负",
        "因使用本项目产生的一切后果 —— 包括但不限于账号封禁、虚拟财产损失、"
        "设备异常、法律纠纷 —— 全部由使用者本人自行承担。"
        "作者与贡献者不承担任何责任。",
    ),
    (
        "建议的使用场景",
        "单人模式 · 练习场 · 自定义房间（已征得房内所有人同意） · "
        "或者干脆不进游戏，只跑本地自检脚本来学技术。",
    ),
)


class DisclaimerDialog(tk.Toplevel):
    """模态对话框。``result`` 为 True 表示用户同意。"""

    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("使用须知 — 口琴工坊")
        self.configure(bg=Palette["bg"])
        self.resizable(False, False)
        self.transient(master)
        self.result = False

        self._build()
        self._centre_on(master)

        self.protocol("WM_DELETE_WINDOW", self._decline)
        self.bind("<Escape>", lambda e: self._decline())
        self.grab_set()
        self.focus_set()

    # ------------------------------------------------------------------ #

    def _centre_on(self, master) -> None:
        self.update_idletasks()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
            y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    def _build(self) -> None:
        root = tk.Frame(self, bg=Palette["bg"], padx=22, pady=18)
        root.pack(fill=tk.BOTH, expand=True)

        make_label(root, "使 用 须 知", bold=True, size=15, bg=Palette["bg"]).pack(anchor="w")
        make_label(root, "开始之前，请花 20 秒读完下面这些", dim=True,
                   bg=Palette["bg"]).pack(anchor="w", pady=(4, 14))

        # ---- 红线 ----
        red = tk.Frame(root, bg="#2b1417", highlightthickness=1, highlightbackground="#7a2b34")
        red.pack(fill=tk.X)
        inner = tk.Frame(red, bg="#2b1417", padx=16, pady=13)
        inner.pack(fill=tk.X)
        for line in RED_LINES:
            tk.Label(inner, text=line, bg="#2b1417", fg="#ff8b96",
                     font=("Microsoft YaHei UI", 12, "bold"), anchor="w",
                     wraplength=560, justify=tk.LEFT).pack(anchor="w", pady=2)

        # ---- 正文 ----
        body = tk.Frame(root, bg=Palette["bg"])
        body.pack(fill=tk.BOTH, expand=True, pady=(16, 0))

        for head, text in SECTIONS:
            block = tk.Frame(body, bg=Palette["bg"])
            block.pack(fill=tk.X, pady=(0, 11))

            tk.Label(block, text="· " + head, bg=Palette["bg"], fg=Palette["text"],
                     font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(anchor="w")
            tk.Label(block, text=text, bg=Palette["bg"], fg=Palette["text_dim"],
                     font=("Microsoft YaHei UI", 9), anchor="w", justify=tk.LEFT,
                     wraplength=580).pack(anchor="w", padx=(12, 0), pady=(2, 0))

        make_label(root, "完整条款见程序目录下的 DISCLAIMER.md 与 LICENSE",
                   dim=True, bg=Palette["bg"], size=9).pack(anchor="w", pady=(6, 0))

        # ---- 按钮 ----
        bar = tk.Frame(root, bg=Palette["bg"])
        bar.pack(fill=tk.X, pady=(18, 0))

        make_button(bar, "不同意，退出程序", self._decline, kind="ghost",
                    width=17).pack(side=tk.LEFT)
        make_button(bar, "我已阅读并同意", self._accept, kind="accent",
                    width=17).pack(side=tk.RIGHT)

        make_label(root, "（点「同意」即表示你已阅读、理解并自愿接受全部条款和风险）",
                   dim=True, bg=Palette["bg"], size=8).pack(anchor="e", pady=(8, 0))

    # ------------------------------------------------------------------ #

    def _accept(self) -> None:
        self.result = True
        self.destroy()

    def _decline(self) -> None:
        self.result = False
        self.destroy()


def ask_disclaimer(master) -> bool:
    """弹出免责确认，返回用户是否同意。"""
    dialog = DisclaimerDialog(master)
    master.wait_window(dialog)
    return dialog.result
