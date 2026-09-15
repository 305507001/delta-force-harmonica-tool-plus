"""
全局热键监听。

实现方式选择了「轮询按下沿」而不是 ``RegisterHotKey`` 或键盘钩子：

* ``RegisterHotKey`` 需要占用窗口消息循环，一旦目标键被别的程序抢先注册就失效；
* ``WH_KEYBOARD_LL`` 低级钩子侵入性太强，会拖慢整机键盘响应，杀软也容易盯上。

``GetAsyncKeyState`` 每个按键只读一次状态位，开销可以忽略，
而且天然支持"按一下触发一次"（边沿检测），不抢占也不吞键。
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

__all__ = ["GlobalHotkeys", "VK"]

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("全局热键仅支持 Windows")

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
_user32.GetAsyncKeyState.restype = ctypes.c_short

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
