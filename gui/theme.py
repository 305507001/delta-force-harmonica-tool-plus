"""
界面配色与控件工厂。

统一放在这里，好处是改一次全局变样，不用在业务代码里到处找颜色字符串。
整体走深灰蓝底色 + 青色强调，长时间盯着不刺眼，也方便在游戏旁边放一块。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

__all__ = ["Palette", "apply_theme", "make_button", "make_label", "make_combobox", "make_spinbox"]

# 想换皮肤改这里就够了
Palette = {
    "bg": "#11141b",
    "panel": "#181d26",
    "panel_alt": "#1e242f",
    "border": "#2a3240",
    "text": "#d7dee9",
    "text_dim": "#7f8b9c",
    "accent": "#35d0a5",
    "accent_dark": "#1f8a6c",
    "warn": "#e0a04a",
    "danger": "#e2606a",
    "entry_bg": "#0e1118",
    "sel": "#2b6cff",
}


def apply_theme(root: tk.Misc) -> ttk.Style:
    """把 ttk 控件也刷成深色（Windows 默认主题不支持改色，所以切到 clam）。"""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(
        ".",
        background=Palette["panel"],
        foreground=Palette["text"],
        fieldbackground=Palette["entry_bg"],
        bordercolor=Palette["border"],
        lightcolor=Palette["panel_alt"],
        darkcolor=Palette["panel"],
        troughcolor=Palette["panel_alt"],
        focuscolor=Palette["accent"],
    )
    style.configure("TCombobox",
                    fieldbackground=Palette["entry_bg"],
                    background=Palette["panel_alt"],
                    foreground=Palette["text"],
                    arrowcolor=Palette["text_dim"],
                    borderwidth=1,
                    padding=4)
    style.map("TCombobox",
              fieldbackground=[("readonly", Palette["entry_bg"])],
              foreground=[("readonly", Palette["text"])],
              background=[("active", Palette["border"])])
    style.configure("TSpinbox",
                    fieldbackground=Palette["entry_bg"],
                    background=Palette["panel_alt"],
                    foreground=Palette["text"],
                    arrowcolor=Palette["text_dim"],
                    borderwidth=1,
                    padding=4)
    style.configure("TScrollbar",
                    background=Palette["panel_alt"],
                    troughcolor=Palette["panel"],
                    bordercolor=Palette["panel"],
                    arrowcolor=Palette["text_dim"])
    style.configure("TProgressbar",
                    background=Palette["accent"],
                    troughcolor=Palette["panel_alt"],
                    bordercolor=Palette["panel"],
                    lightcolor=Palette["accent"],
                    darkcolor=Palette["accent_dark"])
    style.configure("Vertical.TScrollbar", background=Palette["panel_alt"])
    return style


def make_button(master, text: str, command=None, kind: str = "normal",
                width: Optional[int] = None, **kwargs) -> tk.Button:
    """造一个统一样式的按钮。``kind`` 决定强调程度。"""
    colors = {
        "normal": (Palette["panel_alt"], Palette["text"], Palette["border"]),
        "accent": (Palette["accent_dark"], "#eafff8", Palette["accent"]),
        "danger": ("#4a2228", "#ffd9dd", Palette["danger"]),
        "ghost": (Palette["panel"], Palette["text_dim"], Palette["border"]),
    }[kind]

    button = tk.Button(
        master,
        text=text,
        command=command,
        bg=colors[0],
        fg=colors[1],
        activebackground=Palette["border"],
        activeforeground=Palette["text"],
        disabledforeground="#4a5464",
        highlightthickness=1,
        highlightbackground=colors[2],
        highlightcolor=Palette["accent"],
        relief=tk.FLAT,
        bd=0,
        padx=10,
        pady=4,
        cursor="hand2",
        font=("Microsoft YaHei UI", 9),
        **kwargs,
    )
    if width:
        button.configure(width=width)
    return button


def make_label(master, text: str = "", dim: bool = False, size: int = 9,
               bold: bool = False, **kwargs) -> tk.Label:
    """
    统一样式的文字标签。

    ``text`` 可以省略——如果调用方传了 ``textvariable``，
    标签内容就交给那个变量驱动（状态栏就是这么用的）。
    """
    label = tk.Label(
        master,
        bg=kwargs.pop("bg", Palette["panel"]),
        fg=Palette["text_dim"] if dim else Palette["text"],
        font=("Microsoft YaHei UI", size, "bold" if bold else "normal"),
        **kwargs,
    )
    if text:
        label.configure(text=text)
    return label


def make_combobox(master, values, variable, width: int = 10, readonly: bool = True):
    box = ttk.Combobox(
        master,
        values=list(values),
        textvariable=variable,
        width=width,
        state="readonly" if readonly else "normal",
        font=("Microsoft YaHei UI", 9),
    )
    return box


def make_spinbox(master, variable, frm: float, to: float, increment: float = 1.0, width: int = 6):
    return ttk.Spinbox(
        master,
        from_=frm,
        to=to,
        increment=increment,
        textvariable=variable,
        width=width,
        font=("Microsoft YaHei UI", 9),
        justify="center",
    )
