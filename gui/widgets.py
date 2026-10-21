"""
圆角控件：按钮、卡片、分段切换、搜索框、小标签。

Tk 的画布在 Windows 上画圆角没有抗锯齿，边缘一圈锯齿，浅色界面上特别显眼。
这里改成用像素算出带抗锯齿的圆角图片（按"有符号距离"算每个像素被覆盖了多少），
按钮整张画；大卡片只算四个角，中间用矩形拼，几百像素宽的卡片也不卡。
算好的图片按尺寸和颜色缓存，同一种按钮全界面只算一次。

:class:`RoundButton` 的用法和 tk.Button 差不多：``configure(text=, state=, command=)``、
``cget("text")``、``invoke()`` 都能用，原来写给 tk.Button 的调用不用改。
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Dict, Optional, Sequence, Tuple

from . import theme
from .theme import Palette, blend, px

__all__ = ["RoundButton", "RoundedPanel", "Segmented", "SearchEntry", "make_badge",
           "rounded_photo", "divider"]


# --------------------------------------------------------------------------- #
# 抗锯齿圆角图片
# --------------------------------------------------------------------------- #


def _rgb(color: str) -> Tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _cache(widget) -> Dict[tuple, tk.PhotoImage]:
    # 图片属于某个 Tk 解释器；换外观时整个窗口会重建，缓存跟着根窗口走
    root = widget._root()
    store = root.__dict__.get("_kq_photos")
    if store is None:
        store = root.__dict__["_kq_photos"] = {}
    return store


def rounded_photo(widget, width: int, height: int, radius: int, fill: str,
                  outline: Optional[str], bg: str, border: int = 1) -> tk.PhotoImage:
    """一张 ``width × height`` 的圆角矩形图片，圆角外面是 ``bg``。"""
    key = ("rr", width, height, radius, fill, outline, bg, border)
    store = _cache(widget)
    image = store.get(key)
    if image is not None:
        return image
    width, height = max(1, int(width)), max(1, int(height))
    fr, fg_, fb = _rgb(fill)
    orr, og, ob = _rgb(outline) if outline else (fr, fg_, fb)
    br, bgg, bb = _rgb(bg)
    hx, hy = width / 2.0, height / 2.0
    r = max(0.0, min(float(radius), hx, hy))
    bw = border if outline else 0
    rows = []
    row_cache: Dict[float, str] = {}
    for y in range(height):
        qy = abs(y + 0.5 - hy) - (hy - r)
        cached = row_cache.get(qy)
        if cached is not None:
            rows.append(cached)
            continue
        cells = []
        for x in range(width):
            qx = abs(x + 0.5 - hx) - (hx - r)
            sd = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r
            outer = 0.5 - sd
            outer = 0.0 if outer < 0 else (1.0 if outer > 1 else outer)
            if bw:
                inner = 0.5 - (sd + bw)
                inner = 0.0 if inner < 0 else (1.0 if inner > 1 else inner)
            else:
                inner = outer
            edge = outer - inner
            rest = 1.0 - outer
            cells.append("#%02x%02x%02x" % (
                round(br * rest + orr * edge + fr * inner),
                round(bgg * rest + og * edge + fg_ * inner),
                round(bb * rest + ob * edge + fb * inner)))
        line = "{" + " ".join(cells) + "}"
        row_cache[qy] = line
        rows.append(line)
    image = tk.PhotoImage(master=widget._root(), width=width, height=height)
    image.put(" ".join(rows))
    store[key] = image
    return image


def _corner(widget, radius: int, fill: str, outline: Optional[str], bg: str, which: str) -> tk.PhotoImage:
    """大卡片的一个角（``radius+1`` 见方），从一张小圆角图里裁出来。"""
    key = ("corner", radius, fill, outline, bg, which)
    store = _cache(widget)
    image = store.get(key)
    if image is not None:
        return image
    size = radius + 1
    whole = rounded_photo(widget, size * 2, size * 2, radius, fill, outline, bg)
    image = tk.PhotoImage(master=widget._root(), width=size, height=size)
    x0 = 0 if which in ("nw", "sw") else size
    y0 = 0 if which in ("nw", "ne") else size
    image.tk.call(image, "copy", whole, "-from", x0, y0, x0 + size, y0 + size, "-to", 0, 0)
    store[key] = image
    return image


def paint_rounded(canvas: tk.Canvas, x0: int, y0: int, x1: int, y1: int, radius: int,
                  fill: str, outline: Optional[str], bg: str, tag: str = "shape") -> None:
    """在画布上画一块圆角卡片（四个角是抗锯齿图片，其余用矩形拼）。"""
    w, h = x1 - x0, y1 - y0
    r = max(1, min(radius, w // 2 - 1, h // 2 - 1))
    size = r + 1
    edge = outline or fill
    c = canvas
    c.create_rectangle(x0 + size, y0, x1 - size, y1, fill=fill, outline="", tags=tag)
    c.create_rectangle(x0, y0 + size, x1, y1 - size, fill=fill, outline="", tags=tag)
    if outline:
        c.create_rectangle(x0 + size, y0, x1 - size, y0 + 1, fill=edge, outline="", tags=tag)
        c.create_rectangle(x0 + size, y1 - 1, x1 - size, y1, fill=edge, outline="", tags=tag)
        c.create_rectangle(x0, y0 + size, x0 + 1, y1 - size, fill=edge, outline="", tags=tag)
        c.create_rectangle(x1 - 1, y0 + size, x1, y1 - size, fill=edge, outline="", tags=tag)
    for which, x, y in (("nw", x0, y0), ("ne", x1 - size, y0),
                        ("sw", x0, y1 - size), ("se", x1 - size, y1 - size)):
        c.create_image(x, y, image=_corner(canvas, r, fill, outline, bg, which), anchor="nw", tags=tag)


def _surface_of(widget) -> str:
    try:
        return str(widget.cget("bg"))
    except (tk.TclError, AttributeError):
        return Palette["bg"]


def _font(widget, size: int, bold: bool = False, family: Optional[str] = None) -> tkfont.Font:
    store = _cache(widget)
    key = ("font", family or theme.FONT_UI, size, bold)
    font = store.get(key)
    if font is None:
        font = tkfont.Font(root=widget._root(), family=family or theme.FONT_UI, size=size,
                           weight="bold" if bold else "normal")
        store[key] = font
    return font


def divider(master, vertical: bool = False) -> tk.Frame:
    """一像素分隔线。"""
    line = tk.Frame(master, bg=Palette["border"], height=1 if not vertical else 0,
                    width=1 if vertical else 0)
    return line


# --------------------------------------------------------------------------- #
# 按钮
# --------------------------------------------------------------------------- #


def _style(kind: str, surface: str, hover: bool, disabled: bool) -> Tuple[str, Optional[str], str]:
    """(底色, 描边, 字色)"""
    P = Palette
    active = hover and not disabled
    if kind == "accent":
        fill = P["accent_dark"] if active else P["accent"]
        if disabled:
            fill = blend(P["accent"], surface, 0.5)
        return fill, None, P["on_accent"]
    if kind in ("guide", "danger"):
        color = P[kind]
        soft = P[f"{kind}_soft"]
        return (soft if active else P["panel"]), blend(color, P["panel"], 0.5), \
            (P["text_muted"] if disabled else color)
    if kind in ("ghost", "icon", "nav", "seg"):
        fg = P["text_muted"] if disabled else (P["text"] if active else P["text_dim"])
        return (P["hover"] if active else surface), None, fg
    if kind == "nav_on":
        return P["selected"], None, P["text"]
    if kind == "seg_on":
        return P["panel"], P["border_strong"], P["text"]
    if kind == "badge_accent":
        return P["accent_soft"], None, P["accent_text"]
    if kind == "badge_guide":
        return P["guide_soft"], None, P["guide_text"]
    if kind == "badge_warn":
        return P["warn_soft"], None, P["warn"]
    if kind.startswith("badge"):
        return P["panel_alt"], None, P["text_dim"]
    fill = P["hover"] if active else P["panel"]
    return fill, P["border_strong"], (P["text_muted"] if disabled else P["text"])


class RoundButton(tk.Canvas):
    """
    圆角按钮。

    :param kind:      样式，见 :func:`_style`
    :param width:     最少几个字符宽（和 tk.Button 的 width 意思一样）
    :param icon_name: 文字前面加个图标（见 theme.icon）
    :param bg:        按钮背后的颜色（圆角外面露出来的部分），默认取父控件底色
    :param anchor:    ``center`` 居中 / ``w`` 靠左（侧栏导航用）
    """

    def __init__(self, master, text: str = "", command: Optional[Callable] = None,
                 kind: str = "normal", width: Optional[int] = None, icon_name: Optional[str] = None,
                 bg: Optional[str] = None, height: int = 34, radius: Optional[int] = None,
                 size: int = 10, bold: bool = False, anchor: str = "center", padx: int = 14,
                 state: str = "normal", colors: Optional[Tuple[str, Optional[str], str]] = None,
                 **_ignored) -> None:
        self._surface = bg or _surface_of(master)
        super().__init__(master, bg=self._surface, highlightthickness=0, bd=0, takefocus=0)
        self._text = text
        self._command = command
        self._kind = kind
        self._chars = width
        self._icon_name = icon_name
        self._h = px(height)
        self._r = px(radius) if radius is not None else min(px(8), self._h // 2)
        self._size = size
        self._bold = bold
        self._anchor = anchor
        self._padx = px(padx)
        self._state = str(state)
        self._colors = colors                 # 自定义 (底色, 描边, 字色)，比如选音面板按八度上色
        self._hover = False
        self._drawn: Optional[tuple] = None
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda e: self._draw())
        self._relayout()

    # ---------------- tk.Button 兼容接口 ----------------

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        if not kw:
            return super().configure()
        layout = False
        for key in ("text", "kind", "icon_name"):
            if key in kw:
                setattr(self, "_" + key, kw.pop(key))
                layout = True
        if "colors" in kw:
            self._colors = kw.pop("colors")
        if "state" in kw:
            self._state = str(kw.pop("state"))
        if "command" in kw:
            self._command = kw.pop("command")
        if "width" in kw:
            self._chars = kw.pop("width")
            layout = True
        for key in ("bg", "background"):
            if key in kw:
                self._surface = kw.pop(key)
                super().configure(bg=self._surface)
        for key in ("fg", "font", "activebackground", "activeforeground", "relief", "bd", "padx",
                    "pady", "highlightbackground", "highlightthickness", "disabledforeground",
                    "highlightcolor"):
            kw.pop(key, None)
        if kw:
            super().configure(**kw)
        if layout:
            self._relayout()
        else:
            self._draw()

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        if key == "command":
            return self._command
        if key in ("bg", "background"):
            return self._surface
        return super().cget(key)

    def invoke(self):
        if self._state != tk.DISABLED and self._command is not None:
            return self._command()
        return None

    # ---------------- 画 ----------------

    def _content(self):
        family, glyph = theme.icon(self._icon_name) if self._icon_name else (None, "")
        if family is None and self._text and len(glyph) > 1:
            glyph = ""                      # 没有图标字体时替代文字是个词，有文字了就不重复
        return family, glyph

    def _relayout(self) -> None:
        text_font = _font(self, self._size, self._bold)
        family, glyph = self._content()
        width = max((text_font.measure(line) for line in self._text.split("\n")), default=0) if self._text else 0
        if glyph:
            icon_font = _font(self, self._size + 1, False, family) if family else text_font
            width += icon_font.measure(glyph) + (px(6) if self._text else 0)
        if self._chars:
            width = max(width, int(self._chars) * text_font.measure("0"))
        width += self._padx * 2
        if not self._text and glyph:
            width = max(self._h, width)
        super().configure(width=width, height=self._h)
        self._natural = width
        self._drawn = None
        self._draw()

    def _draw(self) -> None:
        try:
            w = self.winfo_width()
        except tk.TclError:
            return
        if w <= 1:
            w = self._natural
        h = self._h
        disabled = self._state == tk.DISABLED
        hover = self._hover and not self._kind.startswith("badge") and self._kind != "nav_on"
        if self._colors:
            fill, outline, fg = self._colors
            if hover:
                fill = blend(fill, "#ffffff" if theme.is_dark() else "#000000", 0.06)
        else:
            fill, outline, fg = _style(self._kind, self._surface, hover, disabled)
        signature = (w, h, fill, outline, fg, self._text, self._icon_name, self._surface)
        if signature == self._drawn:
            return
        self._drawn = signature
        self.delete("all")
        self.create_image(0, 0, anchor="nw",
                          image=rounded_photo(self, w, h, self._r, fill, outline, self._surface))
        text_font = _font(self, self._size, self._bold)
        family, glyph = self._content()
        icon_font = (_font(self, self._size + 1, False, family) if family else text_font) if glyph else None
        parts = []
        if glyph:
            parts.append((glyph, icon_font))
        if self._text:
            parts.append((self._text, text_font))
        if "\n" in self._text and not glyph:
            self.create_text(w / 2, h / 2, text=self._text, font=text_font, fill=fg, justify="center")
            parts = []
        gap = px(6)
        total = sum(f.measure(t) for t, f in parts) + gap * max(0, len(parts) - 1)
        x = self._padx if self._anchor == "w" else (w - total) / 2
        for text, font in parts:
            self.create_text(x, h / 2, text=text, font=font, fill=fg, anchor="w")
            x += font.measure(text) + gap
        clickable = not disabled and not self._kind.startswith("badge") and self._command is not None
        super().configure(cursor="hand2" if clickable else "")

    # ---------------- 交互 ----------------

    def _on_enter(self, _event) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _event) -> None:
        self._hover = False
        self._draw()

    def _on_release(self, event) -> None:
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()


def make_badge(master, text: str, tone: str = "neutral", bg: Optional[str] = None) -> RoundButton:
    """小标签：tone = accent（陶土）/ guide（蓝）/ warn / neutral。"""
    return RoundButton(master, text=text, kind=f"badge_{tone}", bg=bg, height=22, radius=11,
                       size=9, padx=9)


# --------------------------------------------------------------------------- #
# 卡片
# --------------------------------------------------------------------------- #


class RoundedPanel(tk.Canvas):
    """
    圆角卡片。往 ``.body`` 里放东西；卡片高度默认跟着内容走（``fit=True``）。

    ``fit=False`` 时卡片由布局撑开（比如 grid 里 sticky="nsew"），内容填满卡片。
    """

    def __init__(self, master, fill: Optional[str] = None, outline: Optional[str] = "",
                 radius: int = 12, padx: int = 14, pady: Optional[int] = None,
                 bg: Optional[str] = None, fit: bool = True) -> None:
        self._surface = bg or _surface_of(master)
        super().__init__(master, bg=self._surface, highlightthickness=0, bd=0)
        self.fill = fill or Palette["panel"]
        self.outline = Palette["border"] if outline == "" else outline
        self._r = px(radius)
        self._padx = px(padx)
        self._pady = px(pady if pady is not None else padx)
        self._fit = fit
        self.body = tk.Frame(self, bg=self.fill)
        self._window = self.create_window(self._padx, self._pady, window=self.body, anchor="nw")
        self.bind("<Configure>", self._on_resize)
        if fit:
            self.body.bind("<Configure>", self._fit_height, add="+")

    def set_outline(self, color: Optional[str]) -> None:
        self.outline = color
        self._paint(self.winfo_width(), self.winfo_height())

    def _fit_height(self, _event=None) -> None:
        # 高度跟着内容走；宽度只报"内容想要多宽"，布局给得更宽（fill=X）时照样撑满
        height = self.body.winfo_reqheight() + self._pady * 2
        width = self.body.winfo_reqwidth() + self._padx * 2
        if height != int(float(self.cget("height"))) or width != int(float(self.cget("width"))):
            super().configure(height=height, width=width)

    def _on_resize(self, event) -> None:
        self.itemconfigure(self._window, width=max(1, event.width - self._padx * 2))
        if not self._fit:
            self.itemconfigure(self._window, height=max(1, event.height - self._pady * 2))
        self._paint(event.width, event.height)

    def _paint(self, width: int, height: int) -> None:
        if width <= 2 or height <= 2:
            return
        self.delete("shape")
        paint_rounded(self, 0, 0, width, height, self._r, self.fill, self.outline, self._surface)
        self.tag_lower("shape")


class Segmented(RoundedPanel):
    """分段切换（我的曲库 / 在线曲库、浅色 / 深色 / 跟随系统 这类）。"""

    def __init__(self, master, options: Sequence[Tuple[str, str]], command: Optional[Callable] = None,
                 value: Optional[str] = None, bg: Optional[str] = None, size: int = 10,
                 height: int = 30) -> None:
        super().__init__(master, fill=Palette["panel_alt"], outline=Palette["border"], radius=9,
                         padx=2, pady=2, bg=bg)
        self._command = command
        self.buttons: Dict[str, RoundButton] = {}
        for index, (key, label) in enumerate(options):
            button = RoundButton(self.body, text=label, kind="seg", bg=self.fill, height=height,
                                 radius=7, size=size, padx=12, command=lambda k=key: self._pick(k))
            button.pack(side=tk.LEFT, padx=(0 if index == 0 else px(2), 0))
            self.buttons[key] = button
        self.value = None
        self.set(value if value is not None else options[0][0])
        # 宽度跟着按钮走
        self.body.update_idletasks()
        self.configure(width=self.body.winfo_reqwidth() + self._padx * 2)

    def set(self, key: str) -> None:
        self.value = key
        for name, button in self.buttons.items():
            button.configure(kind="seg_on" if name == key else "seg")

    def _pick(self, key: str) -> None:
        if key == self.value:
            return
        self.set(key)
        if self._command:
            self._command(key)


class SearchEntry(RoundedPanel):
    """圆角输入框（带放大镜和占位提示）。``.entry`` 是里面的 tk.Entry。"""

    def __init__(self, master, textvariable: tk.StringVar, placeholder: str = "", width: int = 28,
                 bg: Optional[str] = None, search_icon: bool = True) -> None:
        super().__init__(master, fill=Palette["entry_bg"], outline=Palette["border_strong"],
                         radius=8, padx=10, pady=5, bg=bg)
        self.var = textvariable
        if search_icon:
            family, glyph = theme.icon("search")
            tk.Label(self.body, text=glyph, bg=self.fill, fg=Palette["text_muted"],
                     font=(family or theme.FONT_UI, 10)).pack(side=tk.LEFT, padx=(0, px(6)))
        self.entry = tk.Entry(self.body, textvariable=textvariable, width=width, relief=tk.FLAT, bd=0,
                              highlightthickness=0, bg=self.fill, fg=Palette["text"],
                              insertbackground=Palette["text"], font=theme.ui_font(11),
                              selectbackground=Palette["sel_bg"], selectforeground=Palette["text"])
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=px(2))
        self._placeholder = tk.Label(self.body, text=placeholder, bg=self.fill, fg=Palette["text_muted"],
                                     font=theme.ui_font(11), cursor="xterm")
        self._placeholder.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda e: self._focus(True), add="+")
        self.entry.bind("<FocusOut>", lambda e: self._focus(False), add="+")
        textvariable.trace_add("write", lambda *_: self._update_placeholder())
        self.after_idle(self._update_placeholder)

    def _focus(self, on: bool) -> None:
        self.set_outline(Palette["guide"] if on else Palette["border_strong"])
        self._update_placeholder()

    def _update_placeholder(self) -> None:
        try:
            if self.var.get() or self.focus_get() is self.entry:
                self._placeholder.place_forget()
            else:
                self._placeholder.place(in_=self.entry, x=0, rely=0.5, anchor="w")
        except (tk.TclError, KeyError):
            pass
