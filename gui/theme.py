"""
界面配色与控件工厂。

统一放在这里，好处是改一次全局变样，不用在业务代码里到处找颜色字符串。
整体走深灰蓝底色 + 青色强调，长时间盯着不刺眼，也方便在游戏旁边放一块。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

__all__ = [
    "Palette", "apply_theme", "make_button", "make_label", "make_combobox", "make_spinbox",
    "make_check", "make_slider", "enable_wheel_scroll", "blend", "add_hover",
]

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
    "guide": "#2b9fd8",          # 跟弹模式的主题色（蓝），和"自动演奏"的青绿区分开
    "guide_dark": "#173247",
    "warn": "#e0a04a",
    "danger": "#e2606a",
    "entry_bg": "#0e1118",
    "sel": "#2b6cff",
}


def blend(color: str, other: str, amount: float) -> str:
    """两个 #rrggbb 颜色按比例混合（amount=0 是 color，1 是 other）。"""
    amount = max(0.0, min(1.0, amount))
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def add_hover(widget: tk.Widget) -> None:
    """
    鼠标移上去稍微提亮一点。

    Windows 下的 tk.Button 没有悬停效果，一排按钮看不出哪个能点。
    提亮色在移入时按"当前底色"现算，所以别处改了按钮底色（比如导航页签切换）也不会被冲掉。
    """

    def enter(_event) -> None:
        if str(widget.cget("state")) == tk.DISABLED:
            return
        base = widget.cget("bg")
        widget._kq_base = base
        widget._kq_hover = blend(base, "#ffffff", 0.08)
        widget.configure(bg=widget._kq_hover)

    def leave(_event) -> None:
        base = getattr(widget, "_kq_base", None)
        if base and widget.cget("bg") == getattr(widget, "_kq_hover", None):
            widget.configure(bg=base)
        widget._kq_base = None

    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", leave, add="+")


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
    # 在线曲库的表格
    style.configure("Treeview", background=Palette["panel"], fieldbackground=Palette["panel"],
                    foreground=Palette["text"], rowheight=30, borderwidth=0,
                    font=("Microsoft YaHei UI", 10))
    style.map("Treeview", background=[("selected", Palette["guide_dark"])],
              foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=Palette["panel_alt"], foreground=Palette["text_dim"],
                    relief="flat", borderwidth=0, font=("Microsoft YaHei UI", 9, "bold"))
    style.map("Treeview.Heading", background=[("active", Palette["border"])])
    return style


def make_button(master, text: str, command=None, kind: str = "normal",
                width: Optional[int] = None, **kwargs) -> tk.Button:
    """造一个统一样式的按钮。``kind`` 决定强调程度。"""
    colors = {
        "normal": (Palette["panel_alt"], Palette["text"], Palette["border"]),
        "accent": (Palette["accent_dark"], "#eafff8", Palette["accent"]),
        "guide": (Palette["guide_dark"], "#d6efff", Palette["guide"]),
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
    add_hover(button)
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


def make_check(master, text: str, variable, command=None, bg: Optional[str] = None) -> tk.Checkbutton:
    """深色风格的勾选框。"""
    bg = bg or Palette["panel"]
    return tk.Checkbutton(
        master, text=text, variable=variable, command=command,
        bg=bg, fg=Palette["text"], selectcolor=Palette["entry_bg"],
        activebackground=bg, activeforeground=Palette["text"],
        font=("Microsoft YaHei UI", 9), bd=0, highlightthickness=0,
        cursor="hand2", anchor="w",
    )


def make_slider(master, variable, frm: float, to: float, resolution: float = 1.0,
                length: int = 160, command=None, bg: Optional[str] = None) -> tk.Scale:
    """深色风格的横向滑块（不显示数值，数值由调用方用标签显示）。"""
    bg = bg or Palette["panel"]
    return tk.Scale(
        master, variable=variable, from_=frm, to=to, resolution=resolution,
        orient=tk.HORIZONTAL, length=length, showvalue=False,
        command=command, bg=bg, fg=Palette["text"], troughcolor=Palette["entry_bg"],
        activebackground=Palette["accent"], highlightthickness=0, bd=0,
        sliderrelief=tk.FLAT, sliderlength=18, width=10, cursor="hand2",
    )


#: 已登记"可以用滚轮滚动"的画布
_WHEEL_TARGETS: list = []


def enable_wheel_scroll(canvas: tk.Canvas) -> None:
    """
    让鼠标停在列表里任何位置（包括行里的文字、按钮上）都能用滚轮滚动。

    只给画布本身绑 <MouseWheel> 的话，指针一落到画布里嵌的行控件上，
    滚轮事件就被那个控件吃掉了——表现为"只有行与行的缝隙里能滚"。
    这里统一在顶层接住滚轮，再看指针底下的控件属于哪块画布。
    """
    _WHEEL_TARGETS.append(canvas)
    root = canvas.winfo_toplevel()
    if getattr(root, "_kq_wheel_bound", False):
        return
    root._kq_wheel_bound = True

    def on_wheel(event) -> None:
        try:
            widget = event.widget.winfo_containing(event.x_root, event.y_root)
        except (tk.TclError, AttributeError, KeyError):
            # 指针落在 Tk 内部控件上（比如下拉框弹出的列表），不归我们管
            return
        while widget is not None:
            if widget in _WHEEL_TARGETS:
                try:
                    widget.yview_scroll(-1 if event.delta > 0 else 1, "units")
                except tk.TclError:
                    pass
                return
            widget = getattr(widget, "master", None)

    root.bind_all("<MouseWheel>", on_wheel, add="+")
