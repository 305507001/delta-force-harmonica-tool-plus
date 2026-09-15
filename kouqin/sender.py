"""
底层输入注入 —— 通过 Windows ``SendInput`` 模拟键盘与鼠标。

为什么不用 pyautogui / keyboard 这类库：
    这类库大多走 ``keybd_event`` 或虚拟键码（wVk）路径。游戏（尤其用
    RawInput / DirectInput 读输入的那类）在窗口失焦或全屏时会绕过这些
    注入方式，导致"外面看按键发出去了，游戏里一点反应没有"。
    正确做法是标记 ``KEYEVENTF_SCANCODE``，直接投递硬件扫描码，
    这与真实键盘按下时产生的数据包一致，兼容性最高。

本模块另外提供「注入统计」，方便定位是按键没发出去还是游戏没收到。
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

__all__ = ["Sender", "SCANCODES"]

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("输入注入模块仅支持 Windows")

_user32 = ctypes.WinDLL("user32", use_last_error=True)

# --------------------------------------------------------------------------- #
# Win32 结构与常量
# --------------------------------------------------------------------------- #

ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040

_MOUSE_FLAGS = {
    ("left", True): MOUSEEVENTF_LEFTDOWN,
    ("left", False): MOUSEEVENTF_LEFTUP,
    ("right", True): MOUSEEVENTF_RIGHTDOWN,
    ("right", False): MOUSEEVENTF_RIGHTUP,
    ("middle", True): MOUSEEVENTF_MIDDLEDOWN,
    ("middle", False): MOUSEEVENTF_MIDDLEUP,
}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class _INPUTUNION(ctypes.Union):
    _fields_ = (("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT))


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (("type", wintypes.DWORD), ("u", _INPUTUNION))


_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_user32.SendInput.restype = wintypes.UINT
_user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
_user32.MapVirtualKeyW.restype = wintypes.UINT

#: 硬件扫描码（Set 1 make code）。
#: 这里刻意把整张主键盘都列全——因为用户可以在设置里把音键换成任意键，
#: 换成一个表里没有的键就会"按了没反应"，而且很难排查。
SCANCODES: Dict[str, int] = {
    # 数字行
    "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
    "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
    "-": 0x0C, "=": 0x0D,
    # 字母区上排
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14,
    "y": 0x15, "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19,
    "[": 0x1A, "]": 0x1B, "\\": 0x2B,
    # 字母区中排
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22,
    "h": 0x23, "j": 0x24, "k": 0x25, "l": 0x26,
    ";": 0x27, "'": 0x28, "`": 0x29,
    # 字母区下排（游戏默认口琴键就在这排）
    "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F,
    "b": 0x30, "n": 0x31, "m": 0x32, ",": 0x33,
    ".": 0x34, "/": 0x35,
    # 功能键与其它
    "esc": 0x01, "backspace": 0x0E, "tab": 0x0F, "enter": 0x1C,
    "ctrl": 0x1D, "shift": 0x2A, "alt": 0x38, "space": 0x39,
    "f5": 0x3F, "f6": 0x40, "f7": 0x41, "f8": 0x42,
}


@dataclass
class InjectStats:
    """注入计数，用于自检与排障。"""

    key_down: int = 0
    key_up: int = 0
    mouse_down: int = 0
    mouse_up: int = 0
    failed: int = 0
    errors: List[int] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.key_down + self.key_up + self.mouse_down + self.mouse_up

    def summary(self) -> str:
        return (
            f"按键 {self.key_down + self.key_up} 次 / 鼠标 {self.mouse_down + self.mouse_up} 次"
            + (f" / 失败 {self.failed}" if self.failed else "")
        )


class Sender:
    """
    输入注入器。

    线程安全说明：播放引擎在独立线程里调用它，内部不持锁——
    ``SendInput`` 本身是线程安全的，且事件必须严格按序发出，加锁反而会引入抖动。
    """

    def __init__(self) -> None:
        self._held_keys: List[int] = []
        self._held_mouse: List[str] = []
        self.stats = InjectStats()
        self._last_error = 0

    # ------------------------------------------------------------------ #
    # 发消息
    # ------------------------------------------------------------------ #

    def _send(self, *inputs: INPUT) -> bool:
        """批量投递输入事件。返回是否全部成功。"""
        if not inputs:
            return True
        count = len(inputs)
        array = (INPUT * count)(*inputs)
        sent = _user32.SendInput(count, array, ctypes.sizeof(INPUT))
        if sent != count:
            self._last_error = ctypes.get_last_error()
            self.stats.failed += count - sent
            if len(self.stats.errors) < 8:
                self.stats.errors.append(self._last_error)
            return False
        return True

    @staticmethod
    def _key_input(scan: int, key_up: bool) -> INPUT:
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if key_up else 0)
        return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, scan, flags, 0, 0))

    @staticmethod
    def _mouse_input(flags: int) -> INPUT:
        return INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, flags, 0, 0))

    # ------------------------------------------------------------------ #
    # 键盘
    # ------------------------------------------------------------------ #

    def key_down(self, key: str) -> bool:
        scan = SCANCODES.get(key.lower())
        if scan is None:
            return False
        ok = self._send(self._key_input(scan, False))
        if scan not in self._held_keys:
            self._held_keys.append(scan)
        self.stats.key_down += 1
        return ok

    def key_up(self, key: str) -> bool:
        scan = SCANCODES.get(key.lower())
        if scan is None:
            return False
        ok = self._send(self._key_input(scan, True))
        if scan in self._held_keys:
            self._held_keys.remove(scan)
        self.stats.key_up += 1
        return ok

    def key_press(self, key: str, hold_ms: float = 20.0) -> bool:
        self.key_down(key)
        _sleep_ms(hold_ms)
        return self.key_up(key)

    # ------------------------------------------------------------------ #
    # 鼠标
    # ------------------------------------------------------------------ #

    def mouse_down(self, button: str) -> bool:
        flags = _MOUSE_FLAGS.get((button, True))
        if flags is None:
            return False
        ok = self._send(self._mouse_input(flags))
        if button not in self._held_mouse:
            self._held_mouse.append(button)
        self.stats.mouse_down += 1
        return ok

    def mouse_up(self, button: str) -> bool:
        flags = _MOUSE_FLAGS.get((button, False))
        if flags is None:
            return False
        ok = self._send(self._mouse_input(flags))
        if button in self._held_mouse:
            self._held_mouse.remove(button)
        self.stats.mouse_up += 1
        return ok

    # ------------------------------------------------------------------ #
    # 收尾
    # ------------------------------------------------------------------ #

    def release_all(self) -> None:
        """
        松开所有仍按住的按键与鼠标键。

        播放中断、异常退出、切歌时都必须调用，否则会留下"卡键"，
        玩家回到游戏会发现角色一直朝某个方向走（典型的 W 键卡住）。
        """
        for scan in list(self._held_keys):
            self._send(self._key_input(scan, True))
            self._held_keys.remove(scan)
        for button in list(self._held_mouse):
            flags = _MOUSE_FLAGS.get((button, False))
            if flags is not None:
                self._send(self._mouse_input(flags))
            self._held_mouse.remove(button)

    @property
    def last_error(self) -> int:
        return self._last_error

    @property
    def held(self) -> Tuple[Tuple[int, ...], Tuple[str, ...]]:
        return tuple(self._held_keys), tuple(self._held_mouse)


class _SleepGuard:
    """
    高精度休眠。

    ``time.sleep`` 在 Windows 上最小粒度约 15.6ms，对"音符只按一帧"这种
    场景太粗，所以短延时改用忙等 + 时间戳校准，长延时才交给系统调度。
    """

    __slots__ = ()


def _sleep_ms(ms: float) -> None:
    if ms <= 0:
        return
    if ms > 2.0:
        # 先睡掉大部分，剩下的用忙等收尾，兼顾 CPU 占用与精度
        time.sleep((ms - 1.5) / 1000.0)
        ms = 1.5
    deadline = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < deadline:
        pass
