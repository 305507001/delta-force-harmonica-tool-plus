"""
跟弹悬浮窗 —— 在游戏画面上方实时显示「下一个该按什么」。

它是本程序自己的一个普通窗口：置顶、半透明、锁定后鼠标点击直接穿透到下面的游戏。
**不读写游戏内存、不注入游戏进程、不截屏、不挂钩子**，和游戏之间没有任何接触——
本质上和音乐软件的"桌面歌词"是同一种窗口，只是内容换成了按键提示。
跟弹模式下程序不发送任何按键，玩家的按键是用 GetAsyncKeyState 读状态位"看"的。

锁定（点击穿透）时窗口收不到鼠标消息，"长按拖动"也是每帧读鼠标左键状态和光标位置做的：
在悬浮窗上按住左键不动半秒，就能拖着走，松开放下（见 :class:`LongPressDrag`）。

全屏独占模式下任何普通窗口都盖不到游戏上面，所以游戏要设成
「无边框窗口化」或「窗口化」（自动演奏本来也有这个要求）。

画面从上到下三块::

    ┌──────────────────────────────────────────────────────────┐
    │ [跟弹] 小星星                        命中 12 · 漏 1   00:12 │  标题行
    │ ┌────────────┐ ║                                          │
    │ │ 鼠标  现在  │ ║(X)━━━   (C)━━   (V)━━━━━━   (,)━━          │  音符从右往左走，
    │ │ 图标  按住  │ ║                                          │  走到竖线时按下
    │ └────────────┘ ║                                          │
    │        [Z] [X] [C] [V] [B] [N] [M] [,]                    │  键盘提示行
    └──────────────────────────────────────────────────────────┘

配色沿用谱曲页的八度配色：绿 = 不用鼠标，蓝 = 降八度那个鼠标键，橙 = 升八度那个鼠标键；
音符右上角的紫色 ♯ 表示还要加按升半音那个鼠标键。左边的鼠标图标实时显示该按住哪个。
"""

from __future__ import annotations

import bisect
import ctypes
import sys
import time
import tkinter as tk
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from kouqin.engine import PlayState, Slot, compile_notes
from kouqin.jianpu import parse_jianpu
from kouqin.layout import Combo, Layout, MouseMod
from kouqin.practice import Judge, Mark, Verdict
from kouqin.tune import OCTAVE_COLORS, STEP_DIGITS

from .theme import Palette, blend

__all__ = ["OverlayController", "OverlayWindow", "Frame", "LongPressDrag"]

# --------------------------------------------------------------------------- #
# 配色与常量
# --------------------------------------------------------------------------- #

#: 透明色键：画布上用这个颜色的地方会被整个挖空（Windows）。画任何东西都不要用它。
TRANSPARENT = "#010203"

PANEL_BG = "#12161d"
LANE_BG = "#191e28"
PAST_BG = "#232a35"
CAP_BG = "#1a2029"
DARK_TEXT = "#0b0e13"

#: 按"这个鼠标键负责什么"给音符上色（和谱曲页的八度配色一致）
GROUP_COLORS: Dict[str, Tuple[str, str]] = {
    "mid": OCTAVE_COLORS[0],     # 绿：不用鼠标
    "down": OCTAVE_COLORS[-1],   # 蓝：降八度
    "up": OCTAVE_COLORS[1],      # 橙：升八度
    "sharp": OCTAVE_COLORS[2],   # 紫：升半音
}

#: 判定提示的颜色
VERDICT_COLORS = {
    Mark.PERFECT: Palette["accent"],
    Mark.GOOD: "#9be37d",
    "wrong": Palette["danger"],
    Mark.MISS: Palette["text_dim"],
}

_FONT_UI = "Microsoft YaHei UI"
_FONT_KEY = "Segoe UI"
_FONT_NUM = "Consolas"

#: 第 8 个音键是高音 do，沿用设置页的写法
_DIGITS = list(STEP_DIGITS) + ["i"]

#: 这些键直接显示原字符
_SYMBOL_KEYS = frozenset(",./;'[]\\-=`")

_IS_WIN = sys.platform == "win32"

#: 位置预设：悬浮窗上边缘放在屏幕高度的几成（屏幕下方那档按下边缘贴底算）。
#: 默认「中间偏上」：游戏自己的口琴按键 UI 和血条、弹药都在屏幕下半部分，
#: 准星在正中，放在准星上方最不挡事（v2.1.0 默认放在 64% 处，会挡住游戏 UI）。
ANCHOR_TOPS = {"top": 0.08, "upper": 0.22, "lower": 0.60}

if _IS_WIN:  # pragma: no cover - 只在 Windows 上走
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int)
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_long)
    _user32.SetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowPos.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_uint)
    _user32.SetWindowPos.restype = ctypes.c_int
    _user32.GetParent.argtypes = (ctypes.c_void_p,)
    _user32.GetParent.restype = ctypes.c_void_p

    class _POINT(ctypes.Structure):
        _fields_ = (("x", ctypes.c_long), ("y", ctypes.c_long))

    _user32.MonitorFromPoint.argtypes = (_POINT, ctypes.c_uint)
    _user32.MonitorFromPoint.restype = ctypes.c_void_p
    _user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.GetCursorPos.argtypes = (ctypes.POINTER(_POINT),)
    _user32.GetCursorPos.restype = ctypes.c_int

    class _CURSORINFO(ctypes.Structure):
        _fields_ = (("cbSize", ctypes.c_uint), ("flags", ctypes.c_uint),
                    ("hCursor", ctypes.c_void_p), ("ptScreenPos", _POINT))

    _user32.GetCursorInfo.argtypes = (ctypes.POINTER(_CURSORINFO),)
    _user32.GetCursorInfo.restype = ctypes.c_int


def _mouse_state() -> Optional[Tuple[bool, int, int, bool]]:
    """(左键按着没有, 光标 x, 光标 y, 光标显示着没有)；不是 Windows 返回 None。"""
    if not _IS_WIN:
        return None
    point = _POINT()
    if not _user32.GetCursorPos(ctypes.byref(point)):
        return None
    info = _CURSORINFO()
    info.cbSize = ctypes.sizeof(_CURSORINFO)
    showing = bool(_user32.GetCursorInfo(ctypes.byref(info)) and info.flags & CURSOR_SHOWING)
    down = bool(_user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
    return down, point.x, point.y, showing


GWL_EXSTYLE = -20
VK_LBUTTON = 0x01
CURSOR_SHOWING = 0x00000001
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_NOOWNERZORDER = 0x0200


def _mmss(ms: float) -> str:
    total = max(0.0, ms) / 1000.0
    return f"{int(total // 60):02d}:{int(total % 60):02d}"


def _key_glyph(key: str) -> str:
    return key if key in _SYMBOL_KEYS else key.upper()


def _rounded_points(x1: float, y1: float, x2: float, y2: float,
                    r_tl: float, r_tr: float, r_br: float, r_bl: float) -> List[float]:
    """
    圆角矩形的平滑多边形顶点（每个角可以单独给半径）。

    Tk 的 smooth 多边形把顶点当成样条控制点，曲线只经过相邻控制点的中点；
    把每条边的两个端点各写两遍，曲线就会老老实实经过它们，边是直的、角是圆的。
    """
    return [
        x1 + r_tl, y1, x1 + r_tl, y1, x2 - r_tr, y1, x2 - r_tr, y1, x2, y1,
        x2, y1 + r_tr, x2, y1 + r_tr, x2, y2 - r_br, x2, y2 - r_br, x2, y2,
        x2 - r_br, y2, x2 - r_br, y2, x1 + r_bl, y2, x1 + r_bl, y2, x1, y2,
        x1, y2 - r_bl, x1, y2 - r_bl, x1, y1 + r_tl, x1, y1 + r_tl, x1, y1,
    ]


def _round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                r: float, **options) -> int:
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return canvas.create_polygon(_rounded_points(x1, y1, x2, y2, r, r, r, r), smooth=True, **options)


# --------------------------------------------------------------------------- #
# 画面数据
# --------------------------------------------------------------------------- #


@dataclass
class Frame:
    """某一时刻悬浮窗要画的全部内容。由控制器每帧生成，窗口只管照着画。"""

    mode: str                       # auto（自动演奏）/ guide（跟弹）/ preview（调整位置时的演示）
    title: str
    state: str                      # PlayState.*
    position: float                 # 当前演奏位置（毫秒）
    total: float                    # 总长（毫秒）
    countdown: float = 0.0          # 倒计时剩余秒数
    wait: bool = False              # 是否开着「等我按对再继续」
    waiting: bool = False           # 此刻是否正停在某个音上等人
    focus: Optional[int] = None     # 「下一个该按」的音
    next_focus: Optional[int] = None
    marks: Sequence[str] = ()       # 每个音的判定状态（跟弹模式）
    held_keys: Sequence[str] = ()   # 玩家正按着的音键（跟弹模式）
    held_mouse: Sequence[str] = ()  # 玩家正按着的鼠标键（跟弹模式）
    sensing: bool = True            # 能不能读到玩家按键（非 Windows 上读不到）
    verdict: Optional[Verdict] = None
    verdict_age: float = 1e9        # 判定发生了多久（毫秒）
    stats: str = ""
    message: str = ""               # 盖在轨道中央的大字
    submessage: str = ""
    unlocked: bool = False          # 正在调整位置


class _Geometry:
    """悬浮窗各部件的位置。所有数字都是 1 倍大小时的像素，再乘缩放比例。"""

    def __init__(self, scale: float, keyboard: bool) -> None:
        s = self.s = scale

        def px(value: float) -> int:
            return int(round(value * s))

        self.px = px
        self.w = px(680)
        self.h = px(188 if keyboard else 136)
        self.keyboard = keyboard
        self.pad = px(12)
        self.header_y = px(20)
        self.lane_top = px(36)
        self.lane_bottom = px(124)
        self.lane_mid = (self.lane_top + self.lane_bottom) / 2 + px(4)   # 上面留一行给判定提示
        self.lane_right = self.w - self.pad
        self.zone_right = px(150)          # 左侧鼠标区的右边界，音符走过判定线后收进这里
        self.line_x = px(180)              # 判定线
        self.head = px(46)                 # 音符头的边长
        self.tail = px(10)                 # 音符尾巴（按住多久）的粗细
        self.mouse_box = (px(24), self.lane_top + px(12), px(58), self.lane_bottom - px(12))
        self.text_x = px(68)
        self.cap_w = px(44)
        self.cap_h = px(40)
        self.cap_gap = px(8)
        self.kb_top = px(134)
        self.kb_x0 = (self.w - (8 * self.cap_w + 7 * self.cap_gap)) / 2


def _groups_for(layout: Optional[Layout]) -> Dict[str, str]:
    """鼠标键 → 它在当前键位里负责的功能（down / up / sharp）。"""
    if layout is None:
        return {}
    return {button: feature for feature, button in layout.mod_assign.items() if button}


def _note_style(combo: Combo, groups: Dict[str, str]) -> Tuple[str, bool, str]:
    """``(八度分组 mid/down/up, 是否升半音, 音符头上的小字)``"""
    group, sharp, small = "mid", False, ""
    for button in combo.mods:
        feature = groups.get(button)
        if feature in ("down", "up"):
            group = feature
            small = MouseMod.SHORT.get(button, "")
        elif feature == "sharp":
            sharp = True
    return group, sharp, small


def _mods_text(mods: Sequence[str]) -> str:
    """左侧提示区的短说法：「右键」「右键+中键」。"""
    ordered = [m for m in MouseMod.ORDER if m in set(mods)]
    return "+".join(MouseMod.SHORT[m] + "键" for m in ordered)


# --------------------------------------------------------------------------- #
# 窗口
# --------------------------------------------------------------------------- #


class LongPressDrag:
    """
    "长按拖动"的判定（纯逻辑，每帧喂一次鼠标状态）。

    锁定的悬浮窗是点击穿透的，收不到鼠标消息，只能每帧看左键按没按、光标在哪：

        在窗口里按下左键、按住不动 ``HOLD_S`` 秒  → 开始拖动，窗口跟着光标走
        松开                                        → 放下，存位置
        按下时光标不在窗口里 / 按住期间先挪开了     → 这次按下不算（多半是在游戏里点别的）

    ``allow`` 为 False 时不会开始新的长按：光标被游戏藏起来了（第一人称视角里），
    或者这下左键是自动演奏自己按的（降八度），都不该把悬浮窗拖走。已经在拖的不受影响。

    注意：窗口是穿透的，这次按下 / 松开同样会落到下面的窗口上。
    """

    HOLD_S = 0.5
    SLOP_PX = 6

    def __init__(self, scale: float = 1.0) -> None:
        self.slop = max(2, round(self.SLOP_PX * scale))
        self.state = "idle"                 # idle / pressing / ignored / dragging
        self._was_down = False
        self._t0 = 0.0
        self._origin = (0, 0)
        self._offset = (0, 0)

    @property
    def dragging(self) -> bool:
        return self.state == "dragging"

    @property
    def busy(self) -> bool:
        """正在长按或拖动（控制器据此保持每帧刷新）。"""
        return self.state in ("pressing", "dragging")

    def step(self, now: float, down: bool, x: int, y: int, rect: Tuple[int, int, int, int],
             allow: bool = True) -> Optional[Tuple[str, int, int]]:
        """
        :param rect: 窗口现在的 (左, 上, 宽, 高)
        :return: ``("start" | "move" | "drop", 窗口左, 窗口上)``，没事发生返回 None
        """
        just_pressed = down and not self._was_down
        self._was_down = down
        left, top, width, height = rect

        if self.state == "idle":
            inside = left <= x < left + width and top <= y < top + height
            if just_pressed and inside and allow:
                self.state = "pressing"
                self._t0 = now
                self._origin = (x, y)
            return None

        if self.state == "pressing":
            if not down:
                self.state = "idle"
                return None
            ox, oy = self._origin
            if not allow or abs(x - ox) > self.slop or abs(y - oy) > self.slop:
                self.state = "ignored"
                return None
            if now - self._t0 >= self.HOLD_S:
                self.state = "dragging"
                self._offset = (x - left, y - top)
                return ("start", left, top)
            return None

        if self.state == "ignored":
            if not down:
                self.state = "idle"
            return None

        # dragging
        nx, ny = x - self._offset[0], y - self._offset[1]
        if not down:
            self.state = "idle"
            return ("drop", nx, ny)
        return ("move", nx, ny)


class OverlayWindow(tk.Toplevel):
    """
    悬浮窗本体：只负责窗口属性（置顶 / 穿透 / 拖动）和照着 :class:`Frame` 画图。

    **一旦建好就不再隐藏 / 显示窗口**：不需要显示时把画布清空，透明色键会让整个窗口
    "消失"。这样游戏在前台时永远不会因为本窗口弹出而被抢走焦点。
    """

    def __init__(self, master, on_moved: Callable[[int, int], None],
                 on_lock: Callable[[], None]) -> None:
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.configure(bg=TRANSPARENT)
        self.title("口琴工坊 · 跟弹提示")

        self._on_moved = on_moved
        self._on_lock = on_lock
        self._scale = 1.0
        self._keyboard = True
        self._alpha = 0.92
        self._lookahead_ms = 3000.0
        self._locked = True
        self._layout: Optional[Layout] = None
        self._groups: Dict[str, str] = {}
        self._slots: List[Slot] = []
        self._starts: List[float] = []
        self._max_hold = 0.0
        self._pps = 0.17                   # 每毫秒走多少像素
        self._cache: Dict[int, dict] = {}
        self._drag: Optional[Tuple[int, int]] = None
        self._visible = True
        # 进程开了高 DPI 感知时（比如 150% 缩放的屏幕），Tk 的像素就是物理像素，
        # 按 96 DPI 设计的尺寸会显得很小，这里跟着系统缩放一起放大
        try:
            self._dpi = max(1.0, self.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            self._dpi = 1.0
        self.geo = _Geometry(self._dpi, True)
        self._press = LongPressDrag(self._dpi)

        self.canvas = tk.Canvas(self, bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.canvas.bind("<Double-Button-1>", lambda e: self._on_lock())

        for name, value in (("-topmost", True), ("-alpha", self._alpha),
                            ("-transparentcolor", TRANSPARENT)):
            try:
                self.attributes(name, value)
            except tk.TclError:
                pass                       # 非 Windows 没有透明色键，退化成方形底板

        self._build_static()
        self.place_at(None, None)
        self.deiconify()
        self.update_idletasks()
        self._apply_ex_style()
        self._show(False)

    def _show(self, visible: bool) -> None:
        """
        显示 / 隐藏内容。

        不用 withdraw / deiconify：重新显示窗口可能把焦点从游戏抢过来。
        这里只是把画布清掉、透明度降到 0——两样都不会触发窗口激活。
        （透明度这一步也是保险：万一透明色键不生效，空窗口也不会变成一块黑板。）
        """
        if visible == self._visible:
            return
        self._visible = visible
        self.canvas.itemconfigure("static", state="normal" if visible else "hidden")
        try:
            self.attributes("-alpha", self._alpha if visible else 0.0)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ #
    # 窗口属性
    # ------------------------------------------------------------------ #

    def _hwnd(self) -> Optional[int]:
        if not _IS_WIN:
            return None
        hwnd = 0
        try:
            frame = self.wm_frame()
            hwnd = int(frame, 16) if frame else 0
        except (tk.TclError, ValueError):
            hwnd = 0
        if not hwnd:
            hwnd = _user32.GetParent(self.winfo_id()) or 0
        return hwnd or None

    def _apply_ex_style(self) -> None:
        """
        设置扩展窗口样式：

            TOOLWINDOW   不出现在任务栏和 Alt+Tab 里
            NOACTIVATE   永远不抢焦点（点它也不会把游戏切到后台）
            TRANSPARENT  锁定时鼠标点击直接穿透到下面的游戏
        """
        hwnd = self._hwnd()
        if not hwnd:
            return
        style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        # 点击穿透需要分层窗口；分层是 Tk 设置 -alpha / -transparentcolor 时打开的，
        # 这里不自己加——没调过 SetLayeredWindowAttributes 的分层窗口是看不见的
        if self._locked and style & WS_EX_LAYERED:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        self.keep_on_top()

    def keep_on_top(self) -> None:
        """
        重新声明一次"置顶"。

        有些游戏自己也是置顶窗口，切回游戏后会盖住悬浮窗；控制器每隔两秒调一次。
        带 SWP_NOACTIVATE，不会抢焦点。
        """
        hwnd = self._hwnd()
        if hwnd:
            _user32.SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
        else:
            try:
                self.attributes("-topmost", True)
            except tk.TclError:
                pass

    def set_locked(self, locked: bool) -> None:
        """锁定 = 点击穿透、不能拖；解锁 = 可以用鼠标拖到想要的位置。"""
        self._locked = locked
        self.canvas.configure(cursor="" if locked else "fleur")
        self._apply_ex_style()

    @property
    def locked(self) -> bool:
        return self._locked

    def apply_options(self, scale: float, alpha: float, lookahead_s: float, keyboard: bool,
                      x: Optional[int], y: Optional[int], anchor: str = "upper") -> None:
        if (scale, keyboard) != (self._scale, self._keyboard):
            self._scale, self._keyboard = scale, keyboard
            self.geo = _Geometry(scale * self._dpi, keyboard)
            self._build_static()
        self._alpha = alpha
        if self._visible:
            try:
                self.attributes("-alpha", alpha)
            except tk.TclError:
                pass
        self._lookahead_ms = max(1000.0, lookahead_s * 1000.0)
        self._compute_speed()
        self.place_at(x, y, anchor)
        self._apply_ex_style()

    def place_at(self, x: Optional[int], y: Optional[int], anchor: str = "upper") -> None:
        """按预设放（custom 就用拖过的坐标；坐标不在任何一块屏幕上了就退回中间偏上）。"""
        g = self.geo
        if anchor != "custom" or x is None or y is None or not self._on_screen(x, y):
            screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
            x = (screen_w - g.w) // 2
            if anchor == "bottom":
                y = screen_h - g.h - int(screen_h * 0.06)
            else:
                y = int(screen_h * ANCHOR_TOPS.get(anchor, ANCHOR_TOPS["upper"]))
        # Tk 的几何串里 "-100" 表示"离右边 100"，负坐标要写成 "+-100"
        self.geometry(f"{g.w}x{g.h}+{int(x)}+{int(y)}")

    def _on_screen(self, x: int, y: int) -> bool:
        """保存的位置还在不在某块屏幕上（拔掉副屏后要能找回来）。"""
        g = self.geo
        cx, cy = int(x + g.w / 2), int(y + g.h / 2)
        if _IS_WIN:
            return bool(_user32.MonitorFromPoint(_POINT(cx, cy), 0))
        return 0 <= cx < self.winfo_screenwidth() and 0 <= cy < self.winfo_screenheight()

    # ------------------------------------------------------------------ #
    # 拖动
    # ------------------------------------------------------------------ #

    def _drag_start(self, event) -> None:
        if self._locked:
            return
        self._drag = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag_move(self, event) -> None:
        if not self._drag:
            return
        dx, dy = self._drag
        self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _drag_end(self, _event) -> None:
        if not self._drag:
            return
        self._drag = None
        self._on_moved(self.winfo_x(), self.winfo_y())

    # ------------------------------------------------------------------ #
    # 长按拖动（锁定状态下）
    # ------------------------------------------------------------------ #

    @property
    def press_busy(self) -> bool:
        return self._press.busy

    def poll_long_press(self, allow: bool = True) -> None:
        """每帧调一次：锁定、显示着的时候看看是不是有人在长按悬浮窗。"""
        if not self._locked or not self._visible:
            if self._press.dragging:
                self.apply_press(("drop", self.winfo_x(), self.winfo_y()))
            self._press.state = "idle"
            return
        state = _mouse_state()
        if state is None:
            return
        down, x, y, showing = state
        action = self._press.step(time.perf_counter(), down, x, y,
                                  (self.winfo_x(), self.winfo_y(), self.geo.w, self.geo.h),
                                  allow=allow and showing)
        self.apply_press(action)

    def apply_press(self, action: Optional[Tuple[str, int, int]]) -> None:
        """执行 :class:`LongPressDrag` 给出的动作（测试里也直接调它）。"""
        if action is None:
            if self._press.dragging:
                self.canvas.tag_raise("dragmark")
            return
        kind, x, y = action
        if kind == "start":
            self._mark_drag(True)
            return
        self.geometry(f"+{int(x)}+{int(y)}")
        if kind == "move":
            self.canvas.tag_raise("dragmark")
            return
        self._mark_drag(False)
        self._on_moved(int(x), int(y))

    def _mark_drag(self, on: bool) -> None:
        """拖动中描一圈蓝边，告诉玩家"抓住了"。"""
        self.canvas.delete("dragmark")
        if not on:
            return
        g = self.geo
        width = max(2, round(2 * self._dpi))
        self.canvas.create_rectangle(width, width, g.w - width, g.h - width, outline=Palette["guide"],
                                     width=width, tags="dragmark")
        self.canvas.create_text(g.w // 2, g.h - max(8, round(10 * self._dpi)), text="松开鼠标放下",
                                fill=Palette["guide"], tags="dragmark",
                                font=("Microsoft YaHei UI", max(8, round(8 * self._dpi)), "bold"))

    # ------------------------------------------------------------------ #
    # 内容
    # ------------------------------------------------------------------ #

    def set_content(self, slots: Sequence[Slot], layout: Layout) -> None:
        """换一首曲子。"""
        self._slots = list(slots)
        self._starts = [slot.start for slot in self._slots]
        self._max_hold = max((slot.hold for slot in self._slots), default=0.0)
        if layout is not self._layout:
            self._layout = layout
            self._groups = _groups_for(layout)
            self._build_static()
        self._compute_speed()

    def _compute_speed(self) -> None:
        """
        音符的走速（像素 / 毫秒）。

        默认按「提前显示几秒」铺满轨道；碰到快歌时自动加速，让大多数相邻音符的
        头不至于叠在一起——但至少还要能看到 1.2 秒以后的音。
        """
        g = self.geo
        span = max(1.0, g.lane_right - g.line_x)
        speed = span / self._lookahead_ms
        gaps = sorted(b - a for a, b in zip(self._starts, self._starts[1:]) if b - a > 1.0)
        if gaps:
            typical = gaps[len(gaps) // 10]          # 第 10 百分位，个别装饰音不算
            need = (g.head + g.px(6)) / typical
            speed = max(speed, min(need, span / 1200.0))
        self._pps = speed

    # ------------------------------------------------------------------ #
    # 静态部件（底板、轨道、鼠标图标、键帽）——每帧只改颜色和文字
    # ------------------------------------------------------------------ #

    def _build_static(self) -> None:
        c = self.canvas
        g = self.geo
        px = g.px
        c.delete("all")
        self._cache.clear()
        c.configure(width=g.w, height=g.h)
        static = ("static",)
        cover = ("static", "cover")

        _round_rect(c, 1, 1, g.w - 2, g.h - 2, px(14), fill=PANEL_BG,
                    outline=Palette["border"], width=1, tags=static)
        _round_rect(c, g.pad, g.lane_top, g.lane_right, g.lane_bottom, px(10),
                    fill=LANE_BG, outline="", tags=static)

        # 标题行
        self._pill = _round_rect(c, g.pad + px(2), g.header_y - px(10), g.pad + px(46),
                                 g.header_y + px(10), px(10), fill=Palette["guide_dark"],
                                 outline="", tags=static)
        self._pill_text = c.create_text(g.pad + px(24), g.header_y, text="", tags=static,
                                        fill="#d6efff", font=(_FONT_UI, -px(12), "bold"))
        self._title = c.create_text(g.pad + px(56), g.header_y, text="", anchor="w", tags=static,
                                    fill=Palette["text"], font=(_FONT_UI, -px(14), "bold"))
        self._time = c.create_text(g.lane_right - px(2), g.header_y, text="", anchor="e",
                                   tags=static, fill=Palette["text_dim"],
                                   font=(_FONT_NUM, -px(12)))
        self._stats = c.create_text(g.lane_right - px(104), g.header_y, text="", anchor="e",
                                    tags=static, fill=Palette["text"],
                                    font=(_FONT_UI, -px(12)))

        # 进度条
        c.create_rectangle(g.line_x, g.lane_bottom - px(6), g.lane_right - px(10),
                           g.lane_bottom - px(2), fill=PAST_BG, outline="", tags=static)

        # 左侧鼠标区：叠在音符上面，把走过判定线的音符"收"进去
        _round_rect(c, g.pad, g.lane_top, g.zone_right, g.lane_bottom, px(10),
                    fill=LANE_BG, outline="", tags=cover)
        c.create_rectangle(g.zone_right - px(12), g.lane_top, g.zone_right, g.lane_bottom,
                           fill=LANE_BG, outline="", tags=cover)
        c.create_line(g.zone_right, g.lane_top + px(10), g.zone_right, g.lane_bottom - px(10),
                      fill=Palette["border"], tags=cover)

        mx0, my0, mx1, my1 = g.mouse_box
        radius = (mx1 - mx0) / 2
        split = my0 + (my1 - my0) * 0.42
        mid = (mx0 + mx1) / 2
        inset = max(1, px(2))
        _round_rect(c, mx0, my0, mx1, my1, radius, fill="#202733", outline="#3a4454",
                    width=max(1, px(1.5)), tags=cover)
        self._mouse_parts = {
            "left": c.create_polygon(
                _rounded_points(mx0 + inset, my0 + inset, mid - 1, split, radius - inset, 0, 0, 0),
                smooth=True, fill="#2a3240", outline="", width=max(1, px(2)), tags=cover),
            "right": c.create_polygon(
                _rounded_points(mid + 1, my0 + inset, mx1 - inset, split, 0, radius - inset, 0, 0),
                smooth=True, fill="#2a3240", outline="", width=max(1, px(2)), tags=cover),
            "middle": _round_rect(c, mid - px(3.5), my0 + px(7), mid + px(3.5), my0 + px(19),
                                  px(3.5), fill="#3a4454", outline="", width=max(1, px(2)),
                                  tags=cover),
        }
        self._mouse_label = c.create_text(g.text_x, my0 + px(8), text="", anchor="w",
                                          fill=Palette["text_dim"], tags=cover,
                                          font=(_FONT_UI, -px(11)))
        self._mouse_action = c.create_text(g.text_x, (my0 + my1) / 2 + px(1), text="", anchor="w",
                                           fill=Palette["text"], tags=cover,
                                           font=(_FONT_UI, -px(15), "bold"))
        self._mouse_next = c.create_text(g.text_x, my1 - px(7), text="", anchor="w",
                                         fill=Palette["text_dim"], tags=cover,
                                         font=(_FONT_UI, -px(10)))

        # 键盘提示行
        self._caps: List[Tuple[int, int, int]] = []
        if g.keyboard:
            keys = self._layout.keys if self._layout else ("z", "x", "c", "v", "b", "n", "m", ",")
            for index, key in enumerate(keys):
                x0 = g.kb_x0 + index * (g.cap_w + g.cap_gap)
                y0 = g.kb_top
                rect = _round_rect(c, x0, y0, x0 + g.cap_w, y0 + g.cap_h, px(7), fill=CAP_BG,
                                   outline=Palette["border"], width=max(1, px(1.5)), tags=static)
                digit = c.create_text(x0 + px(7), y0 + px(9), text=_DIGITS[index], anchor="w",
                                      fill=Palette["text_dim"], tags=static,
                                      font=(_FONT_NUM, -px(10)))
                label = c.create_text(x0 + g.cap_w / 2, y0 + g.cap_h / 2 + px(3),
                                      text=_key_glyph(key), fill="#c3ccd9", tags=static,
                                      font=(_FONT_KEY, -px(16), "bold"))
                self._caps.append((rect, digit, label))

        if not self._visible:
            c.itemconfigure("static", state="hidden")

    def _set(self, item: int, **options) -> None:
        """只在真的变了时才去改控件（每帧几十次 itemconfigure 也是开销）。"""
        cached = self._cache.setdefault(item, {})
        changed = {k: v for k, v in options.items() if cached.get(k) != v}
        if changed:
            self.canvas.itemconfigure(item, **changed)
            cached.update(changed)

    # ------------------------------------------------------------------ #
    # 每帧绘制
    # ------------------------------------------------------------------ #

    def render(self, frame: Optional[Frame]) -> None:
        c = self.canvas
        c.delete("dyn")
        if frame is None:
            self._show(False)
            return
        self._show(True)

        self._draw_header(frame)
        # 判定线画在音符下面：音符走到线上时按键字母完整露出来，不会被线划过
        self._draw_line(frame)
        self._draw_notes(frame)
        c.tag_raise("cover")
        self._draw_mouse(frame)
        self._draw_keyboard(frame)
        self._draw_feedback(frame)
        self._draw_message(frame)
        if frame.unlocked:
            g = self.geo
            _round_rect(c, 2, 2, g.w - 3, g.h - 3, g.px(14), fill="", outline=Palette["warn"],
                        width=2, tags=("dyn",))

    def _draw_header(self, frame: Frame) -> None:
        g = self.geo
        if frame.mode == "auto":
            label, bg, fg = "自动", Palette["accent_dark"], "#eafff8"
        elif frame.mode == "preview":
            label, bg, fg = "预览", "#3d2447", "#ecd4fb"
        elif frame.wait:
            label, bg, fg = "等待", "#4a3a1c", "#ffe2b0"
        else:
            label, bg, fg = "跟弹", Palette["guide_dark"], "#d6efff"
        self._set(self._pill, fill=bg)
        self._set(self._pill_text, text=label, fill=fg)

        title = frame.title if len(frame.title) <= 18 else frame.title[:17] + "…"
        self._set(self._title, text=title)
        time_text = f"{_mmss(frame.position)} / {_mmss(frame.total)}"
        if self._cache.get(self._time, {}).get("text") != time_text:
            self._set(self._time, text=time_text)
            # 右侧的小字紧贴在时间左边——时间的宽度随字体变，按实际宽度排
            box = self.canvas.bbox(self._time)
            if box:
                self.canvas.coords(self._stats, box[0] - g.px(14), g.header_y)
        if frame.unlocked:
            self._set(self._stats, text="拖动可移动 · 双击锁定", fill=Palette["warn"])
        elif frame.state == PlayState.COUNTDOWN:
            seconds = max(1, int(frame.countdown + 0.999))
            self._set(self._stats, text=f"{seconds} 秒后开始 · 快切到游戏窗口", fill=Palette["warn"])
        else:
            self._set(self._stats, text=frame.stats, fill=Palette["text"])

        # 进度
        if frame.total > 0:
            ratio = max(0.0, min(1.0, frame.position / frame.total))
            x1 = g.line_x + (g.lane_right - g.px(10) - g.line_x) * ratio
            if x1 > g.line_x + 1:
                color = Palette["guide"] if frame.mode == "guide" else Palette["accent"]
                self.canvas.create_rectangle(g.line_x, g.lane_bottom - g.px(6), x1,
                                             g.lane_bottom - g.px(2), fill=blend(color, LANE_BG, 0.25),
                                             outline="", tags=("dyn",))

    def _draw_notes(self, frame: Frame) -> None:
        if not self._slots:
            return
        c = self.canvas
        g = self.geo
        px = g.px
        pos = frame.position
        speed = self._pps
        cy = g.lane_mid
        half = g.head / 2
        t_left = pos - (g.line_x - g.zone_right + g.head) / speed
        t_right = pos + (g.lane_right - g.line_x) / speed
        first = bisect.bisect_left(self._starts, t_left - self._max_hold)
        marks = frame.marks

        for index in range(first, len(self._slots)):
            slot = self._slots[index]
            if slot.start > t_right:
                break
            if slot.end < t_left:
                continue
            group, sharp, small = _note_style(slot.combo, self._groups)
            dark, bright = GROUP_COLORS[group]
            mark = marks[index] if index < len(marks) else Mark.PENDING
            sounding = slot.start <= pos < slot.end
            passed = pos >= slot.end

            if mark in Mark.HIT:
                head_fill, text_fill, outline = blend(bright, PANEL_BG, 0.35), DARK_TEXT, bright
                tail_fill = blend(bright, LANE_BG, 0.45)
            elif mark == Mark.MISS:
                head_fill, text_fill, outline = PAST_BG, Palette["text_dim"], "#6b3a42"
                tail_fill = blend(PAST_BG, LANE_BG, 0.3)
            elif sounding and frame.mode != "guide":
                head_fill, text_fill, outline = bright, DARK_TEXT, "#ffffff"
                tail_fill = bright
            elif passed:
                head_fill, text_fill, outline = PAST_BG, Palette["text_dim"], ""
                tail_fill = blend(PAST_BG, LANE_BG, 0.3)
            else:
                head_fill, text_fill, outline = dark, bright, bright
                tail_fill = blend(dark, bright, 0.25)

            cx = g.line_x + (slot.start - pos) * speed
            if cx + half > g.lane_right:
                continue                       # 头还没完全走进轨道，整个先不画
            # 长音的头早已走进左边的鼠标区时，尾巴从鼠标区边上画起，别伸到底板外面去
            tail_start = max(cx, g.zone_right - px(12))
            tail_end = min(cx + slot.hold * speed, g.lane_right - px(4))
            if tail_end > tail_start + 2:
                _round_rect(c, tail_start, cy - g.tail / 2, tail_end, cy + g.tail / 2, g.tail / 2,
                            fill=tail_fill, outline="", tags=("dyn",))
            if cx + half <= g.zone_right:
                continue                       # 头整个在鼠标区底下，反正被盖住，不画
            _round_rect(c, cx - half, cy - half, cx + half, cy + half, px(9), fill=head_fill,
                        outline=outline, width=max(1, px(2)) if outline else 0, tags=("dyn",))
            glyph = _key_glyph(slot.combo.key)
            if small:
                c.create_text(cx, cy - px(5), text=glyph, fill=text_fill, tags=("dyn",),
                              font=(_FONT_KEY, -px(20), "bold"))
                c.create_text(cx, cy + px(13), text=small, fill=text_fill, tags=("dyn",),
                              font=(_FONT_UI, -px(10), "bold"))
            else:
                c.create_text(cx, cy, text=glyph, fill=text_fill, tags=("dyn",),
                              font=(_FONT_KEY, -px(22), "bold"))
            if sharp:
                sx, sy, r = cx + half - px(5), cy - half + px(5), px(8)
                sharp_dark, sharp_bright = GROUP_COLORS["sharp"]
                fill = PAST_BG if (passed and mark not in Mark.HIT) else sharp_bright
                c.create_oval(sx - r, sy - r, sx + r, sy + r, fill=fill, outline=PANEL_BG,
                              width=max(1, px(1.5)), tags=("dyn",))
                # 用 ASCII 的 "#"：音乐符号 ♯ 在不少系统字体里没有字形
                c.create_text(sx, sy, text="#", fill=DARK_TEXT, tags=("dyn",),
                              font=(_FONT_KEY, -px(11), "bold"))

    def _draw_line(self, frame: Frame) -> None:
        c = self.canvas
        g = self.geo
        px = g.px
        color = Palette["accent"] if frame.mode != "guide" else Palette["guide"]
        width = max(1, px(2))
        if frame.waiting:
            # 等人按的时候让判定线"呼吸"，一眼就知道是在等你
            pulse = (time.perf_counter() * 2.0) % 1.0
            color = blend(Palette["warn"], "#ffffff", 0.3 * (1 - abs(pulse * 2 - 1)))
            width = max(1, px(3))
        c.create_line(g.line_x, g.lane_top + px(6), g.line_x, g.lane_bottom - px(8),
                      fill=color, width=width, tags=("dyn",))
        r = g.head / 2 + px(7)
        c.create_oval(g.line_x - r, g.lane_mid - r, g.line_x + r, g.lane_mid + r,
                      outline=color, width=width, tags=("dyn",))

    def _target(self, frame: Frame) -> Optional[Slot]:
        if frame.focus is None or not (0 <= frame.focus < len(self._slots)):
            return None
        return self._slots[frame.focus]

    def _draw_mouse(self, frame: Frame) -> None:
        target = self._target(frame)
        need = set(target.combo.mods) if target else set()
        held = set(frame.held_mouse)
        for button, item in self._mouse_parts.items():
            feature = self._groups.get(button)
            if button in need and feature:
                fill = GROUP_COLORS["sharp" if feature == "sharp" else feature][1]
            else:
                fill = "#3a4454" if button == "middle" else "#2a3240"
            if frame.mode == "guide" and button in held:
                outline = "#ffffff" if button in need else Palette["danger"]
            else:
                outline = ""
            self._set(item, fill=fill, outline=outline)

        if target is None:
            self._set(self._mouse_label, text="")
            self._set(self._mouse_action, text="", fill=Palette["text"])
            self._set(self._mouse_next, text="")
            return
        group, _sharp, _small = _note_style(target.combo, self._groups)
        if need:
            self._set(self._mouse_label, text="现在按住")
            color = GROUP_COLORS[group][1] if group != "mid" else GROUP_COLORS["sharp"][1]
            self._set(self._mouse_action, text=_mods_text(target.combo.mods), fill=color)
        else:
            self._set(self._mouse_label, text="现在")
            self._set(self._mouse_action, text="不按鼠标", fill=GROUP_COLORS["mid"][1])

        hint = ""
        if frame.next_focus is not None and 0 <= frame.next_focus < len(self._slots):
            upcoming = self._slots[frame.next_focus]
            if set(upcoming.combo.mods) != need and upcoming.start - frame.position < 2500:
                hint = "接着：" + (_mods_text(upcoming.combo.mods) or "松开")
        self._set(self._mouse_next, text=hint)

    def _draw_keyboard(self, frame: Frame) -> None:
        if not self._caps or self._layout is None:
            return
        target = self._target(frame)
        held = set(frame.held_keys)
        group = _note_style(target.combo, self._groups)[0] if target else "mid"
        dark, bright = GROUP_COLORS[group]
        sounding = (target is not None and frame.mode != "guide"
                    and target.start <= frame.position < target.end)
        for (rect, digit, label), key in zip(self._caps, self._layout.keys):
            is_target = target is not None and key == target.combo.key
            if frame.mode == "guide" and key in held:
                if is_target:
                    fill, outline, text, sub = bright, "#ffffff", DARK_TEXT, DARK_TEXT
                else:
                    fill, outline, text, sub = "#4a2228", Palette["danger"], "#ffd9dd", "#ffd9dd"
            elif is_target and sounding:
                fill, outline, text, sub = bright, "#ffffff", DARK_TEXT, DARK_TEXT
            elif is_target:
                fill, outline, text, sub = dark, bright, bright, bright
            else:
                fill, outline, text, sub = CAP_BG, Palette["border"], "#c3ccd9", Palette["text_dim"]
            self._set(rect, fill=fill, outline=outline)
            self._set(label, fill=text)
            self._set(digit, fill=sub)

    def _draw_feedback(self, frame: Frame) -> None:
        c = self.canvas
        g = self.geo
        px = g.px
        x, y = g.line_x + g.head / 2 + px(10), g.lane_top + px(9)
        if frame.waiting and frame.focus is not None:
            target = self._target(frame)
            if target is not None:
                parts = _mods_text(target.combo.mods)
                what = (parts + " + " if parts else "") + _key_glyph(target.combo.key)
                c.create_text(x, y, text=f"等你按：{what}", anchor="w", fill=Palette["warn"],
                              font=(_FONT_UI, -px(12), "bold"), tags=("dyn",))
            return
        verdict = frame.verdict
        if verdict is None or frame.verdict_age > 900:
            return
        color = VERDICT_COLORS.get(verdict.kind, Palette["text"])
        fade = max(0.0, (frame.verdict_age - 350) / 550)
        c.create_text(x, y, text=verdict.text, anchor="w", fill=blend(color, LANE_BG, fade),
                      font=(_FONT_UI, -px(12), "bold"), tags=("dyn",))

    def _draw_message(self, frame: Frame) -> None:
        """暂停 / 结束时盖在轨道中间的一块小提示牌。"""
        c = self.canvas
        g = self.geo
        px = g.px
        cx = (g.line_x + g.lane_right) / 2
        if frame.state == PlayState.PAUSED:
            big, sub, color = "已暂停", "按 F6 继续", Palette["text"]
        elif frame.message:
            big, sub, color = frame.message, frame.submessage, Palette["accent"]
        else:
            if frame.mode == "guide" and not frame.sensing:
                c.create_text(cx, g.lane_top + px(10), text="这台电脑上读不到按键：只显示提示，不做判定",
                              fill=Palette["text_dim"], tags=("dyn",), font=(_FONT_UI, -px(11)))
            return
        title = c.create_text(cx, g.lane_mid - (px(10) if sub else 0), text=big, fill=color,
                              tags=("dyn",), font=(_FONT_UI, -px(20), "bold"))
        items = [title]
        if sub:
            items.append(c.create_text(cx, g.lane_mid + px(16), text=sub, fill=Palette["text"],
                                       tags=("dyn",), font=(_FONT_UI, -px(12))))
        boxes = [c.bbox(item) for item in items]
        boxes = [b for b in boxes if b]
        if not boxes:
            return
        x0 = min(b[0] for b in boxes) - px(18)
        y0 = min(b[1] for b in boxes) - px(8)
        x1 = max(b[2] for b in boxes) + px(18)
        y1 = max(b[3] for b in boxes) + px(8)
        plate = _round_rect(c, x0, y0, x1, y1, px(10), fill=PANEL_BG, outline=Palette["border"],
                            width=1, tags=("dyn",))
        c.tag_lower(plate, title)


# --------------------------------------------------------------------------- #
# 控制器
# --------------------------------------------------------------------------- #

#: 调整位置时滚动播放的演示片段：中音、高音（右键）、低音（左键）、升半音（中键）都有
_PREVIEW_SCORE = "T=110\n1 3 5 1' | 2' 7 5 - | 6, 7, 1 3 | #4 5 1 -"


class OverlayController:
    """
    悬浮窗的"大脑"：每帧读一次播放器状态和玩家按键，算判定、管等待闸门，交给窗口去画。

    主窗口只需要调 start / finish / stop；窗口懒创建，第一次用到时才建。
    """

    TICK_MS = 15                 # 约 60 帧 / 秒，同时也是读按键的频率
                                 # （Windows 默认定时器粒度 15.6ms，写 16 会等成两格 ≈ 31ms）
    TOPMOST_EVERY = 2.0          # 每隔几秒重新声明一次置顶
    SUMMARY_SECONDS = 6.0        # 结束后总结留在屏幕上多久

    def __init__(self, app) -> None:
        self.app = app
        self.window: Optional[OverlayWindow] = None
        self.mode: Optional[str] = None          # None = 当前没有演奏会话
        self.title = ""
        self.slots: List[Slot] = []
        self.total = 0.0
        self.judge: Optional[Judge] = None
        self.watcher = None
        self.wait = False
        self.adjusting = False
        self._gate: Optional[float] = None
        self._tick_id: Optional[str] = None
        self._finished_at: Optional[float] = None
        self._summary = ""
        self._last_pos = 0.0
        self._last_topmost = 0.0
        self._verdict: Optional[Verdict] = None
        self._verdict_stamp = 0.0
        self._preview_slots: List[Slot] = []
        self._preview_origin = 0.0
        self._preview_total = 1.0
        self._reported = False
        self.failed = False

    # ------------------------------------------------------------------ #
    # 窗口
    # ------------------------------------------------------------------ #

    def ensure_window(self) -> Optional[OverlayWindow]:
        if self.window is not None or self.failed:
            return self.window
        try:
            self.window = OverlayWindow(self.app, on_moved=self._on_moved,
                                        on_lock=lambda: self.set_adjusting(False))
        except tk.TclError as exc:
            self.failed = True
            self.app.log(f"悬浮提示窗创建失败（{exc}），本次只能在主界面看进度")
            return None
        self.apply_settings(self.app.settings)
        # 新窗口第一次显示时可能把焦点带走，交还给主窗口
        self.app.after(60, self._return_focus)
        return self.window

    def _return_focus(self) -> None:
        try:
            self.app.focus_force()
        except tk.TclError:
            pass

    def apply_settings(self, settings) -> None:
        if self.window is None:
            return
        self.window.apply_options(settings.overlay_scale, settings.overlay_alpha,
                                  settings.overlay_lookahead, settings.overlay_keyboard,
                                  settings.overlay_x, settings.overlay_y,
                                  settings.overlay_anchor)

    def _on_moved(self, x: int, y: int) -> None:
        self.app.on_overlay_moved(x, y)

    def close(self) -> None:
        self._cancel_tick()
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None

    # ------------------------------------------------------------------ #
    # 会话
    # ------------------------------------------------------------------ #

    def start(self, mode: str, title: str, slots: Sequence[Slot], layout: Layout,
              total: float, wait: bool = False) -> bool:
        """开始一首曲子的提示。``mode`` 是 auto（自动演奏）或 guide（跟弹）。"""
        window = self.ensure_window()
        if window is None:
            return False
        if self.adjusting:
            # 开始演奏时自动锁定，否则悬浮窗会挡住在游戏里的鼠标点击
            self.set_adjusting(False)
        self.mode = mode
        self.title = title
        self.slots = list(slots)
        self.total = total
        self.wait = wait and mode == "guide"
        self.judge = Judge(self.slots, lenient=self.wait) if mode == "guide" else None
        self.watcher = self._make_watcher(layout) if mode == "guide" else None
        self._gate = None
        self._finished_at = None
        self._summary = ""
        self._verdict = None
        self._last_pos = 0.0
        window.set_content(self.slots, layout)
        self._ensure_ticking()
        return True

    @staticmethod
    def _make_watcher(layout: Layout):
        try:
            from kouqin.hotkeys import KeyWatcher
            return KeyWatcher(layout.keys)
        except Exception:                      # 非 Windows：只提示、不判定
            return None

    def finish(self) -> str:
        """曲子正常结束：把判定收尾，总结在屏幕上留几秒。返回总结文字。"""
        if self.mode is None:
            return ""
        if self._finished_at is not None:
            return self._summary              # 已经收过尾了（每帧检查和结束回调可能都会来）
        if self.judge is not None:
            self.judge.finish()
            self._summary = self.judge.summary()
        else:
            self._summary = "演奏完成"
        self._finished_at = time.perf_counter()
        self._ensure_ticking()
        return self._summary

    def stop(self) -> None:
        """立刻撤掉提示（停止演奏时）。"""
        self.mode = None
        self.judge = None
        self.watcher = None
        self._gate = None
        self._finished_at = None
        if self.window is None:
            return
        if self.adjusting:
            self.window.set_content(self._preview_slots, self.app.layout)
        else:
            self.window.render(None)

    @property
    def active(self) -> bool:
        return self.mode is not None and self._finished_at is None

    def on_seek(self, ms: float) -> None:
        """进度条跳转了：判定跟着跳，等待闸门下一帧按新位置重新算。"""
        if self.judge is not None:
            self.judge.seek(ms)
        self._gate = None
        self._verdict = None
        self._last_pos = ms

    # ------------------------------------------------------------------ #
    # 调整位置
    # ------------------------------------------------------------------ #

    def set_adjusting(self, on: bool) -> None:
        """解锁悬浮窗、滚动演示音符，方便拖到合适的位置、看看大小和透明度。"""
        if on and self.ensure_window() is None:
            return
        self.adjusting = on
        if self.window is None:
            return
        self.window.set_locked(not on)
        if on:
            self._build_preview()
            self._ensure_ticking()
        elif self.mode is None:
            self.window.render(None)
        self.app.on_overlay_adjusting(on)

    def _build_preview(self) -> None:
        score = parse_jianpu(_PREVIEW_SCORE)
        _events, report = compile_notes(score.notes, self.app.layout, self.app.settings.timing_preset())
        self._preview_slots = report.slots
        self._preview_total = (report.slots[-1].end + 1200.0) if report.slots else 1.0
        self._preview_origin = time.perf_counter()
        if self.mode is None and self.window is not None:
            self.window.set_content(self._preview_slots, self.app.layout)

    # ------------------------------------------------------------------ #
    # 每帧
    # ------------------------------------------------------------------ #

    def _ensure_ticking(self) -> None:
        if self._tick_id is None and self.window is not None:
            self._tick_id = self.window.after(self.TICK_MS, self._tick)

    def _cancel_tick(self) -> None:
        if self._tick_id is not None and self.window is not None:
            try:
                self.window.after_cancel(self._tick_id)
            except tk.TclError:
                pass
        self._tick_id = None

    def _tick(self) -> None:
        self._tick_id = None
        window = self.window
        if window is None:
            return
        now = time.perf_counter()
        frame: Optional[Frame] = None
        try:
            if self.mode is not None:
                frame = self._session_frame(now)
            elif self.adjusting:
                frame = self._preview_frame(now)
            window.render(frame)
            window.poll_long_press(allow=self._allow_long_press())
        except tk.TclError:
            return                              # 窗口已经关了
        except Exception as exc:                # noqa: BLE001 - 画面出错不能把提示整个停掉
            if not self._reported:
                self._reported = True
                self.app.log(f"悬浮提示窗出错：{type(exc).__name__}: {exc}")
        if frame is not None and now - self._last_topmost > self.TOPMOST_EVERY:
            self._last_topmost = now
            window.keep_on_top()
        if self.mode is not None or self.adjusting or window.press_busy:
            self._tick_id = window.after(self.TICK_MS, self._tick)

    def _allow_long_press(self) -> bool:
        """自动演奏正按着左键（降八度）时，左键是程序按的，不算玩家长按。"""
        player = getattr(self.app, "player", None)
        held = getattr(player, "held_mods", ()) if player is not None else ()
        return "left" not in held

    def _session_frame(self, now: float) -> Optional[Frame]:
        player = self.app.player
        state = player.state

        if self._finished_at is None and state == PlayState.DONE:
            self.finish()                       # 主窗口的结束回调还没到，先自己收尾
        if self._finished_at is not None:
            if now - self._finished_at > self.SUMMARY_SECONDS:
                self.stop()
                if self.adjusting and self.window is not None:
                    self.window.set_content(self._preview_slots, self.app.layout)
                return None
            return Frame(mode=self.mode or "auto", title=self.title, state=PlayState.DONE,
                         position=self._last_pos, total=self.total, wait=self.wait,
                         marks=self.judge.marks if self.judge else (),
                         message="完成！" if self.judge else "演奏完成",
                         submessage=self._summary if self.judge else "")

        pos = player.position
        if state == PlayState.COUNTDOWN:
            # 倒计时期间让音符先"走进来"：倒计时一结束，第一个音正好走到判定线上
            pos -= player.countdown_remaining * 1000.0
        self._last_pos = pos
        held_keys: Sequence[str] = ()
        held_mouse: Sequence[str] = ()
        judge = self.judge
        if self.watcher is not None:
            pressed, held_keys, held_mouse = self.watcher.poll()
            if judge is not None and state == PlayState.PLAYING:
                for key in pressed:
                    verdict = judge.press(key, held_mouse, pos)
                    if verdict is not None:
                        self._show_verdict(verdict, now)
        if judge is not None and state == PlayState.PLAYING:
            for verdict in judge.sweep(pos):
                self._show_verdict(verdict, now)

        # 等待闸门：时钟停在下一个还没按对的音上
        if self.wait and judge is not None and state in (PlayState.PLAYING, PlayState.COUNTDOWN):
            index = judge.next_pending()
            gate = self.slots[index].start if index is not None else None
            if gate != self._gate:
                player.set_gate(gate)
                self._gate = gate

        focus, next_focus = self._focus(pos)
        return Frame(
            mode=self.mode or "auto", title=self.title, state=state, position=pos,
            total=self.total, countdown=player.countdown_remaining, wait=self.wait,
            waiting=player.waiting, focus=focus, next_focus=next_focus,
            marks=judge.marks if judge else (), held_keys=held_keys, held_mouse=held_mouse,
            sensing=self.watcher is not None or self.mode != "guide",
            verdict=self._verdict, verdict_age=(now - self._verdict_stamp) * 1000.0,
            stats=judge.status_text() if judge else "",
        )

    def _show_verdict(self, verdict: Verdict, now: float) -> None:
        self._verdict = verdict
        self._verdict_stamp = now

    def _focus(self, pos: float) -> Tuple[Optional[int], Optional[int]]:
        """「下一个该按」的音，以及再下一个。"""
        count = len(self.slots)
        if self.judge is not None:
            index = self.judge.next_pending()
            if index is None:
                return None, None
            return index, (index + 1 if index + 1 < count else None)
        return _auto_focus(self.slots, [s.start for s in self.slots], pos)

    def _preview_frame(self, now: float) -> Frame:
        elapsed = (now - self._preview_origin) * 1000.0
        pos = elapsed % self._preview_total - 600.0
        slots = self._preview_slots
        focus, next_focus = _auto_focus(slots, [s.start for s in slots], pos)
        # 位置允许是负数：开头留一小段空白，让音符从右边"走进来"
        return Frame(mode="preview", title="调整位置 · 演示", state=PlayState.PLAYING,
                     position=pos, total=self._preview_total, focus=focus,
                     next_focus=next_focus, unlocked=True)


def _auto_focus(slots: Sequence[Slot], starts: Sequence[float],
                pos: float) -> Tuple[Optional[int], Optional[int]]:
    """自动演奏时：正在响的音；两个音之间就是下一个要响的音。"""
    count = len(slots)
    index = bisect.bisect_right(starts, pos) - 1
    if 0 <= index < count and pos < slots[index].end:
        return index, (index + 1 if index + 1 < count else None)
    upcoming = bisect.bisect_left(starts, pos)
    if upcoming >= count:
        return None, None
    return upcoming, (upcoming + 1 if upcoming + 1 < count else None)
