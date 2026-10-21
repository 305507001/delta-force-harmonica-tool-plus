"""
界面配色、字体与控件工厂。

统一放在这里，好处是改一次全局变样，不用在业务代码里到处找颜色字符串。

风格是暖色调的"纸面"：浅色是米白底、白卡片、陶土色强调；深色是暖灰底、深卡片。
两套配色的键名完全一样，启动时按设置（或系统的浅色 / 深色）选一套灌进 :data:`Palette`，
各页面建控件时读 ``Palette["..."]`` 就行，不用关心现在是哪一套。

两种演奏方式各有一个颜色，贯穿整个界面：
    陶土色 accent  「演奏」——程序替你按
    蓝色   guide   「跟弹」——你自己按

游戏里的悬浮窗不跟这里走：它叠在游戏画面上，永远是深色（见 overlay.py）。
"""

from __future__ import annotations

import ctypes
import os
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Dict, Optional, Tuple

__all__ = [
    "Palette", "THEMES", "apply_theme", "set_appearance", "resolve_appearance", "is_dark",
    "make_button", "make_label", "make_combobox", "make_spinbox", "make_check", "make_slider",
    "enable_wheel_scroll", "blend", "add_hover", "px", "ui_font", "title_font", "mono_font",
    "icon", "octave_colors", "FONT_UI", "enable_dpi_awareness", "screen_scale", "ui_scale",
]

# --------------------------------------------------------------------------- #
# 两套配色
# --------------------------------------------------------------------------- #

THEMES: Dict[str, Dict[str, str]] = {
    "light": {
        "bg": "#faf9f5",            # 页面底色
        "panel": "#ffffff",         # 卡片、列表、输入框
        "panel_alt": "#f0eee6",     # 侧栏、分段控件的底
        "hover": "#ebe8df",         # 悬停
        "selected": "#e4e1d7",      # 侧栏选中
        "border": "#e6e3da",        # 细分隔线
        "border_strong": "#d3cfc3", # 按钮、输入框的描边
        "text": "#1f1e1d",
        "text_dim": "#6b6963",
        "text_muted": "#9d9b93",
        "accent": "#c96442",        # 陶土色：演奏 / 主要操作
        "accent_dark": "#b4563a",   # 陶土色按下 / 悬停
        "accent_soft": "#f6e6de",   # 陶土色浅底（标签）
        "accent_text": "#a3472b",   # 浅底上的陶土色字
        "on_accent": "#ffffff",
        "guide": "#2d6cb5",         # 蓝：跟弹
        "guide_soft": "#e3edf8",
        "guide_text": "#245a97",
        "warn": "#a8650f",
        "warn_soft": "#fbf0dc",
        "danger": "#b83a35",
        "danger_soft": "#fbe9e7",
        "success": "#3b6d11",
        "entry_bg": "#ffffff",
        "sel": "#2d6cb5",           # 选中描边（谱曲格子等）
        "sel_bg": "#d6e4f5",        # 文字选中底色
    },
    "dark": {
        "bg": "#262624",
        "panel": "#30302e",
        "panel_alt": "#1f1e1d",
        "hover": "#3a3a37",
        "selected": "#3d3d3a",
        "border": "#3c3b38",
        "border_strong": "#54534e",
        "text": "#f5f4ef",
        "text_dim": "#b4b2a9",
        "text_muted": "#86847c",
        "accent": "#d97757",
        "accent_dark": "#c4664a",
        "accent_soft": "#4a3027",
        "accent_text": "#f0a487",
        "on_accent": "#ffffff",
        "guide": "#6ea8e8",
        "guide_soft": "#22344a",
        "guide_text": "#9cc4f0",
        "warn": "#e0a04a",
        "warn_soft": "#43341c",
        "danger": "#e8776f",
        "danger_soft": "#472624",
        "success": "#97c459",
        "entry_bg": "#262624",
        "sel": "#6ea8e8",
        "sel_bg": "#33507a",
    },
}

#: 当前生效的配色。启动时 :func:`set_appearance` 会把选中的那一套灌进来
Palette: Dict[str, str] = dict(THEMES["light"])
Palette["_name"] = "light"

#: 谱曲格子 / 选音面板的八度配色：(底色, 字色)。浅色是浅底深字，深色是深底亮字
_OCTAVES = {
    "light": {-1: ("#e2ecf8", "#1f5fa0"), 0: ("#e5f0df", "#3d6b1f"),
              1: ("#fbebd5", "#94540e"), 2: ("#eee8fa", "#5b46ae")},
    "dark": {-1: ("#1d3a4d", "#79c7ef"), 0: ("#1c4034", "#5fe0ab"),
             1: ("#4a3a1c", "#f0b95c"), 2: ("#3d2447", "#c98cf0")},
}


def resolve_appearance(choice: str) -> str:
    """``system`` / ``light`` / ``dark`` → 实际用 light 还是 dark。"""
    forced = os.environ.get("KOUQIN_THEME", "").strip().lower()
    if forced in ("light", "dark"):
        return forced
    if choice in ("light", "dark"):
        return choice
    return "dark" if _system_is_dark() else "light"


def _system_is_dark() -> bool:
    """Windows「应用模式」是不是深色（读注册表，读不到就当浅色）。"""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except OSError:
        return False


def set_appearance(choice: str) -> str:
    """选定配色（在建任何控件之前调用）。返回实际生效的 light / dark。"""
    name = resolve_appearance(choice)
    Palette.clear()
    Palette.update(THEMES[name])
    Palette["_name"] = name
    return name


def is_dark() -> bool:
    return Palette.get("_name") == "dark"


def octave_colors() -> Dict[int, Tuple[str, str]]:
    return _OCTAVES["dark" if is_dark() else "light"]


def blend(color: str, other: str, amount: float) -> str:
    """两个 #rrggbb 颜色按比例混合（amount=0 是 color，1 是 other）。"""
    amount = max(0.0, min(1.0, amount))
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


# --------------------------------------------------------------------------- #
# 尺寸与字体
# --------------------------------------------------------------------------- #

_SCALE = 1.0          # 界面实际缩放 = 屏幕缩放 × 「界面大小」
_SCREEN = 1.0         # 屏幕缩放（Windows 显示设置里的 125% / 150% / 200%）

#: 界面字体。apply_theme 时按系统里实际有的字体定下来
FONT_UI = "Microsoft YaHei UI"
_FONT_TITLE = "Microsoft YaHei UI"
_FONT_MONO = "Consolas"
_ICON_FONT: Optional[str] = None

#: 标题用的衬线字体，按顺序找第一个装了的（Windows 自带的是宋体）
_TITLE_CANDIDATES = ("Source Han Serif SC", "Noto Serif SC", "思源宋体", "Songti SC",
                     "STZhongsong", "华文中宋", "SimSun", "宋体")
_UI_CANDIDATES = ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC")
_MONO_CANDIDATES = ("Cascadia Mono", "Consolas", "Menlo", "Courier New")
_ICON_CANDIDATES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")

#: 图标：(Segoe 图标字体里的码位, 没有图标字体时的替代文字)
_ICONS = {
    "library": ("", "♪"),
    "compose": ("", "✎"),
    "advanced": ("", "⋯"),
    "settings": ("", "⚙"),
    "help": ("", "?"),
    "edit": ("", "编辑"),
    "delete": ("", "删除"),
    "play": ("", "▶"),
    "pause": ("", "⏸"),
    "stop": ("", "■"),
    "add": ("", "＋"),
    "import": ("", "导入"),
    "refresh": ("", "⟳"),
    "search": ("", "⌕"),
    "download": ("", "存"),
    "link": ("", "↗"),
}


def px(value: float) -> int:
    """把"1 倍大小时的像素"换算成现在的像素（屏幕缩放 × 界面大小）。"""
    return int(round(value * _SCALE))


def screen_scale() -> float:
    """屏幕本身的缩放（不含「界面大小」）。游戏里的悬浮窗用这个，它有自己的大小设置。"""
    return _SCREEN


def ui_scale() -> float:
    return _SCALE


def enable_dpi_awareness() -> None:
    """
    告诉 Windows 这个程序自己会按 DPI 缩放（要在建任何窗口之前调）。

    不说的话，在 4K 屏这类 150% / 200% 缩放下，Windows 会把整个窗口当成图片拉大，字和线条都是糊的。
    说了之后窗口按真实像素画：字体按「点」算，Tk 会自己放大；写死的像素尺寸统一经过 :func:`px` 换算。
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)      # 系统 DPI 感知（Win 8.1+）
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()           # Win 7 / 8
    except (AttributeError, OSError):
        pass


def ui_font(size: int = 10, bold: bool = False) -> tuple:
    return (FONT_UI, size, "bold") if bold else (FONT_UI, size)


def title_font(size: int = 20) -> tuple:
    return (_FONT_TITLE, size)


def mono_font(size: int = 9) -> tuple:
    return (_FONT_MONO, size)


def icon(name: str) -> Tuple[Optional[str], str]:
    """``(图标字体, 字符)``；没有图标字体时字体为 None、字符是替代文字。"""
    glyph, fallback = _ICONS.get(name, ("", name))
    if _ICON_FONT:
        return _ICON_FONT, glyph
    return None, fallback


def _pick(families, candidates) -> Optional[str]:
    lower = {f.lower(): f for f in families}
    for name in candidates:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


# --------------------------------------------------------------------------- #
# 全局主题
# --------------------------------------------------------------------------- #


def apply_theme(root: tk.Misc, size: float = 1.0) -> ttk.Style:
    """
    定缩放和字体、刷 ttk 控件的颜色（Windows 默认主题不支持改色，所以切到 clam）。

    :param size: 「界面大小」，1.0 = 标准。字体（按点算）和所有 px() 尺寸一起放大
    """
    global FONT_UI, _FONT_TITLE, _FONT_MONO, _ICON_FONT, _SCALE, _SCREEN
    try:
        dpi = root.winfo_fpixels("1i")
    except tk.TclError:
        dpi = 96.0
    # KOUQIN_UI_SCALE：测试用，假装屏幕是 2 倍缩放（在普通屏幕上看 4K 下的样子）
    try:
        simulated = max(0.5, min(4.0, float(os.environ.get("KOUQIN_UI_SCALE", "") or 1.0)))
    except ValueError:
        simulated = 1.0
    size = max(0.8, min(2.0, float(size or 1.0)))
    _SCREEN = max(1.0, dpi / 96.0) * simulated
    _SCALE = _SCREEN * size
    factor = simulated * size
    if abs(factor - 1.0) > 1e-3:
        try:
            root.tk.call("tk", "scaling", float(root.tk.call("tk", "scaling")) * factor)
        except tk.TclError:
            pass
    try:
        families = tkfont.families(root)
    except tk.TclError:
        families = ()
    FONT_UI = _pick(families, _UI_CANDIDATES) or FONT_UI
    _FONT_TITLE = _pick(families, _TITLE_CANDIDATES) or FONT_UI
    _FONT_MONO = _pick(families, _MONO_CANDIDATES) or "Courier"
    _ICON_FONT = _pick(families, _ICON_CANDIDATES)

    P = Palette
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=P["panel"], foreground=P["text"], fieldbackground=P["entry_bg"],
                    bordercolor=P["border_strong"], lightcolor=P["panel"], darkcolor=P["panel"],
                    troughcolor=P["panel_alt"], focuscolor=P["guide"], selectbackground=P["sel_bg"],
                    selectforeground=P["text"], insertcolor=P["text"], font=ui_font(9))
    for name in ("TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground=P["entry_bg"], background=P["panel"], foreground=P["text"],
                        arrowcolor=P["text_dim"], bordercolor=P["border_strong"],
                        lightcolor=P["entry_bg"], darkcolor=P["entry_bg"], borderwidth=1, padding=px(5),
                        arrowsize=px(12))
        style.map(name, fieldbackground=[("readonly", P["entry_bg"])],
                  foreground=[("readonly", P["text"]), ("disabled", P["text_muted"])],
                  background=[("active", P["hover"]), ("pressed", P["hover"])],
                  bordercolor=[("focus", P["guide"]), ("hover", P["text_muted"])],
                  lightcolor=[("focus", P["entry_bg"])], darkcolor=[("focus", P["entry_bg"])],
                  arrowcolor=[("disabled", P["text_muted"])])
    # 下拉框弹出的列表是个普通 Listbox，只能用 option 数据库改色
    root.option_add("*TCombobox*Listbox.background", P["panel"])
    root.option_add("*TCombobox*Listbox.foreground", P["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", P["sel_bg"])
    root.option_add("*TCombobox*Listbox.selectForeground", P["text"])
    root.option_add("*TCombobox*Listbox.font", ui_font(10))
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    # 细滚动条：只要滑道和滑块，不要两头的箭头
    for orient in ("Vertical", "Horizontal"):
        name = f"{orient}.TScrollbar"
        style.layout(name, [(f"{orient}.Scrollbar.trough", {"sticky": "nswe", "children": [
            (f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure(name, troughcolor=P["bg"], background=P["border_strong"],
                        bordercolor=P["bg"], lightcolor=P["border_strong"], darkcolor=P["border_strong"],
                        arrowsize=px(9), gripcount=0, relief="flat")
        style.map(name, background=[("active", P["text_muted"]), ("pressed", P["text_muted"])],
                  lightcolor=[("active", P["text_muted"])], darkcolor=[("active", P["text_muted"])])

    style.configure("TProgressbar", background=P["accent"], troughcolor=P["panel_alt"],
                    bordercolor=P["panel_alt"], lightcolor=P["accent"], darkcolor=P["accent"])

    # 在线曲库的表格
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("Treeview", background=P["panel"], fieldbackground=P["panel"], foreground=P["text"],
                    rowheight=px(34), borderwidth=0, relief="flat", font=ui_font(11))
    style.map("Treeview", background=[("selected", P["guide_soft"])],
              foreground=[("selected", P["text"])])
    style.configure("Treeview.Heading", background=P["panel"], foreground=P["text_dim"], relief="flat",
                    borderwidth=0, bordercolor=P["border"], lightcolor=P["panel"], darkcolor=P["border"],
                    padding=(px(8), px(6)), font=ui_font(10))
    style.map("Treeview.Heading", background=[("active", P["hover"])],
              foreground=[("active", P["text"])])
    return style


def add_hover(widget: tk.Widget) -> None:
    """
    普通 tk.Button 鼠标移上去稍微变一点。

    圆角按钮（:class:`gui.widgets.RoundButton`）自己会处理悬停，不用调这个。
    提亮色在移入时按"当前底色"现算，所以别处改了按钮底色也不会被冲掉。
    """

    def enter(_event) -> None:
        if str(widget.cget("state")) == tk.DISABLED:
            return
        base = widget.cget("bg")
        widget._kq_base = base
        widget._kq_hover = blend(base, "#000000" if not is_dark() else "#ffffff", 0.05)
        widget.configure(bg=widget._kq_hover)

    def leave(_event) -> None:
        base = getattr(widget, "_kq_base", None)
        if base and widget.cget("bg") == getattr(widget, "_kq_hover", None):
            widget.configure(bg=base)
        widget._kq_base = None

    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", leave, add="+")


# --------------------------------------------------------------------------- #
# 控件工厂
# --------------------------------------------------------------------------- #


def make_button(master, text: str, command=None, kind: str = "normal",
                width: Optional[int] = None, icon_name: Optional[str] = None, **kwargs):
    """
    造一个统一样式的圆角按钮（:class:`gui.widgets.RoundButton`）。

    ``kind``：normal 普通 / accent 陶土色实心（演奏、保存这类主要操作）/ guide 蓝字（跟弹）/
    danger 红字 / ghost 无底色。
    """
    from .widgets import RoundButton
    for unused in ("padx", "pady", "font", "fg", "relief", "bd"):
        kwargs.pop(unused, None)
    return RoundButton(master, text=text, command=command, kind=kind, width=width,
                       icon_name=icon_name, **kwargs)


def make_label(master, text: str = "", dim: bool = False, size: int = 10,
               bold: bool = False, **kwargs) -> tk.Label:
    """
    统一样式的文字标签。

    ``text`` 可以省略——如果调用方传了 ``textvariable``，
    标签内容就交给那个变量驱动（状态栏就是这么用的）。
    """
    bg = kwargs.pop("bg", None) or _bg_of(master)
    label = tk.Label(
        master,
        bg=bg,
        fg=kwargs.pop("fg", Palette["text_dim"] if dim else Palette["text"]),
        font=kwargs.pop("font", ui_font(size, bold)),
        **kwargs,
    )
    if text:
        label.configure(text=text)
    return label


def _bg_of(widget) -> str:
    try:
        return str(widget.cget("bg"))
    except (tk.TclError, AttributeError):
        return Palette["panel"]


def make_combobox(master, values, variable, width: int = 10, readonly: bool = True):
    return ttk.Combobox(master, values=list(values), textvariable=variable, width=width,
                        state="readonly" if readonly else "normal", font=ui_font(10))


def make_spinbox(master, variable, frm: float, to: float, increment: float = 1.0, width: int = 6):
    return ttk.Spinbox(master, from_=frm, to=to, increment=increment, textvariable=variable,
                       width=width, font=ui_font(10), justify="center")


def make_check(master, text: str, variable, command=None, bg: Optional[str] = None) -> tk.Checkbutton:
    """勾选框。"""
    bg = bg or _bg_of(master)
    return tk.Checkbutton(
        master, text=text, variable=variable, command=command,
        bg=bg, fg=Palette["text"], selectcolor=Palette["entry_bg"],
        activebackground=bg, activeforeground=Palette["text"],
        font=ui_font(10), bd=0, highlightthickness=0, cursor="hand2", anchor="w",
    )


def make_slider(master, variable, frm: float, to: float, resolution: float = 1.0,
                length: int = 160, command=None, bg: Optional[str] = None) -> tk.Scale:
    """横向滑块（不显示数值，数值由调用方用标签显示）。"""
    bg = bg or _bg_of(master)
    return tk.Scale(
        master, variable=variable, from_=frm, to=to, resolution=resolution,
        orient=tk.HORIZONTAL, length=px(length), showvalue=False,
        command=command, bg=Palette["accent"], fg=Palette["text"], troughcolor=Palette["panel_alt"],
        activebackground=Palette["accent_dark"], highlightthickness=0, bd=0,
        sliderrelief=tk.FLAT, sliderlength=px(16), width=px(8), cursor="hand2",
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
