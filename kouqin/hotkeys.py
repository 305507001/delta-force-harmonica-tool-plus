"""
全局热键监听。

实现方式选择了「轮询按下沿」而不是 ``RegisterHotKey`` 或键盘钩子：

* ``RegisterHotKey`` 需要占用窗口消息循环，一旦目标键被别的程序抢先注册就失效；
* ``WH_KEYBOARD_LL`` 低级钩子侵入性太强，会拖慢整机键盘响应，杀软也容易盯上。

``GetAsyncKeyState`` 每个按键只读一次状态位，开销可以忽略，
而且天然支持"按一下触发一次"（边沿检测），不抢占也不吞键。

跟弹模式判定玩家按得对不对，用的也是同一个办法（见 :class:`KeyWatcher`）。
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

__all__ = ["GlobalHotkeys", "VK", "KeyWatcher"]

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("全局热键仅支持 Windows")

from .sender import SCANCODES

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.MapVirtualKeyW.argtypes = (ctypes.c_uint, ctypes.c_uint)
_user32.MapVirtualKeyW.restype = ctypes.c_uint

_MAPVK_VSC_TO_VK = 1

#: 鼠标键的虚拟键码
_MOUSE_VK: Dict[str, int] = {"left": 0x01, "right": 0x02, "middle": 0x04}

#: 符号键的虚拟键码（MapVirtualKey 换算失败时的兜底，按美式键盘）
_OEM_VK: Dict[str, int] = {
    ",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA, "'": 0xDE, "[": 0xDB,
    "]": 0xDD, "\\": 0xDC, "-": 0xBD, "=": 0xBB, "`": 0xC0,
}

#: 常用虚拟键码
VK: Dict[str, int] = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "esc": 0x1B, "space": 0x20,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63,
    "num4": 0x64, "num5": 0x65, "num6": 0x66, "num7": 0x67,
    "num8": 0x68, "num9": 0x69,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "insert": 0x2D, "delete": 0x2E,
}

#: 热键名 -> 中文说明，界面上展示用
VK_LABEL: Dict[str, str] = {
    "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
    "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
    "esc": "Esc", "space": "空格",
}


class GlobalHotkeys(threading.Thread):
    """
    后台轮询全局热键。

    :param bindings: ``{键名: 回调}``，键名取自 :data:`VK`
    :param poll_interval: 轮询间隔（秒），0.02 ≈ 50Hz，手感上接近即时
    """

    def __init__(self, bindings: Optional[Dict[str, Callable[[], None]]] = None,
                 poll_interval: float = 0.02) -> None:
        super().__init__(name="kouqin-hotkeys", daemon=True)
        self._bindings: Dict[int, Callable[[], None]] = {}
        self._labels: Dict[int, str] = {}
        self._down: Dict[int, bool] = {}
        self._poll = poll_interval
        # 注意：不能叫 _stop —— threading.Thread 内部有个同名方法，覆盖它会在 join 时炸
        self._stopped = threading.Event()
        self._suspended = False
        if bindings:
            for name, callback in bindings.items():
                self.bind(name, callback)

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #

    def bind(self, key: str, callback: Callable[[], None]) -> bool:
        """注册一个热键。返回是否成功（键名无法识别时失败）。"""
        vk = VK.get(key.lower())
        if vk is None:
            return False
        self._bindings[vk] = callback
        self._labels[vk] = VK_LABEL.get(key.lower(), key.upper())
        self._down[vk] = False
        return True

    def unbind(self, key: str) -> None:
        vk = VK.get(key.lower())
        if vk is not None:
            self._bindings.pop(vk, None)
            self._labels.pop(vk, None)
            self._down.pop(vk, None)

    def clear(self) -> None:
        self._bindings.clear()
        self._labels.clear()
        self._down.clear()

    @property
    def bindings(self) -> Dict[str, str]:
        """``{显示名: 是否已注册}``，供界面展示。"""
        return {label: True for label in self._labels.values()}

    def suspend(self) -> None:
        """临时停用（例如用户正在录入自定义热键时）。"""
        self._suspended = True
        self._down = {vk: False for vk in self._down}

    def resume(self) -> None:
        self._suspended = False

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        while not self._stopped.is_set():
            if not self._suspended:
                for vk, callback in self._bindings.items():
                    pressed = bool(_user32.GetAsyncKeyState(vk) & 0x8000)
                    if pressed and not self._down.get(vk, False):
                        try:
                            callback()
                        except Exception:
                            # 热键回调里出错不能拖垮监听线程
                            pass
                    self._down[vk] = pressed
            time.sleep(self._poll)

    def shutdown(self) -> None:
        self._stopped.set()
        if self.is_alive() and self is not threading.current_thread():
            self.join(timeout=0.5)


def key_to_vk(key: str) -> Optional[int]:
    """
    音键名 → 虚拟键码。

    先按扫描码（也就是键盘上的物理位置）换算——注入器发的是扫描码，游戏认的也是
    物理位置，这样即使系统用的不是美式键盘布局，"程序提示的键"和"游戏里响的键"
    仍然是同一个。换算失败再退回按字符查表。
    """
    key = (key or "").lower()
    scan = SCANCODES.get(key)
    if scan is not None:
        vk = _user32.MapVirtualKeyW(scan, _MAPVK_VSC_TO_VK)
        if vk:
            return vk
    if len(key) == 1 and key.isalnum():
        return ord(key.upper())
    return _OEM_VK.get(key)


class KeyWatcher:
    """
    读取「此刻按着哪些音键 / 鼠标键」，给跟弹模式判定用。

    和 :class:`GlobalHotkeys` 一样只调用 ``GetAsyncKeyState`` 读状态位：
    不装钩子、不拦截、不改动任何输入，也完全不接触游戏进程——
    玩家的每一下按键游戏都照常收到，本程序只是在旁边"看一眼"。

    由界面定时器调用 :meth:`poll`（约 60 次/秒），不另开线程。
    """

    def __init__(self, keys: Sequence[str]) -> None:
        self._vks: Dict[str, int] = {}
        for key in keys:
            vk = key_to_vk(key)
            if vk:
                self._vks[key] = vk
        self._down: Dict[str, bool] = {key: False for key in self._vks}
        self.prime()

    def prime(self) -> None:
        """记下当前状态，并清掉"上次查询以来按过"的残留位，免得一开始就误判一次按键。"""
        for key, vk in self._vks.items():
            self._down[key] = bool(_user32.GetAsyncKeyState(vk) & 0x8000)

    def poll(self) -> Tuple[List[str], List[str], List[str]]:
        """
        :returns: ``(这次新按下的音键, 正按着的音键, 正按着的鼠标键)``
        """
        pressed: List[str] = []
        held: List[str] = []
        for key, vk in self._vks.items():
            state = _user32.GetAsyncKeyState(vk)
            down = bool(state & 0x8000)
            # 最低位 = 上次查询以来按下过：两次轮询之间"按下又松开"的快速点按靠它接住。
            # 只在之前是松开状态时才认，长按时系统的自动重复会反复置这一位。
            tapped = bool(state & 0x0001)
            if (down or tapped) and not self._down[key]:
                pressed.append(key)
            if down:
                held.append(key)
            self._down[key] = down
        mouse = [name for name, vk in _MOUSE_VK.items()
                 if _user32.GetAsyncKeyState(vk) & 0x8000]
        return pressed, held, mouse
