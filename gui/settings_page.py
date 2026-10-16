"""
设置页 —— 键位想换就换。

默认键位就是游戏里的那套（Z X C V B N M 逗号），
如果玩家改过游戏内键位，或者想用更顺手的键，在这里点一下按钮再按一下键盘就行。

交互上做了两件省心的事：
    * 占用检查：新设的键如果已经被别的音用了，两个音自动对调，不会冲突
    * 录入期间全局热键会临时停用，免得按 F6 时突然开始演奏
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Dict, List, Optional, Tuple

from kouqin.engine import TIMING_PRESETS
from kouqin.layout import MOD_FEATURE_LABELS, MouseMod
from kouqin.tune import STEP_DIGITS, STEP_NAMES

from .theme import (
    Palette, enable_wheel_scroll, make_button, make_check, make_combobox, make_label,
    make_slider, make_spinbox,
)

#: tkinter 的 keysym 转成本程序内部用的键名
_KEYSYM_TO_KEY: Dict[str, str] = {
    "comma": ",",
    "period": ".",
    "slash": "/",
    "semicolon": ";",
    "bracketleft": "[",
    "bracketright": "]",
    "backslash": "\\",
    "minus": "-",
    "equal": "=",
    "apostrophe": "'",
    "grave": "`",
}

#: 键名在界面上的显示
_KEY_DISPLAY: Dict[str, str] = {
    ",": "逗号 ,",
    ".": "句号 .",
    "/": "斜杠 /",
    ";": "分号 ;",
    "[": "[",
    "]": "]",
    "\\": "反斜杠 \\",
    "-": "减号 -",
    "=": "等号 =",
    "'": "引号 '",
    "`": "波浪 `",
}

#: 修饰键下拉选项
_MOD_CHOICES = ["不使用", "鼠标左键", "鼠标右键", "鼠标中键"]
_MOD_VALUE = {"不使用": None, "鼠标左键": "left", "鼠标右键": "right", "鼠标中键": "middle"}
_MOD_LABEL = {None: "不使用", "left": "鼠标左键", "right": "鼠标右键", "middle": "鼠标中键"}

#: 悬浮提示窗的下拉选项：(显示名, 数值)
_GUIDE_SPEEDS = [("0.5×（很慢）", 0.5), ("0.6×", 0.6), ("0.75×", 0.75), ("0.9×", 0.9), ("1.0×（原速）", 1.0)]
_LOOKAHEADS = [(f"{n} 秒", float(n)) for n in (2, 3, 4, 5, 6)]
_SCALES = [("小", 0.8), ("中", 1.0), ("大", 1.25), ("特大", 1.5)]


def _label_for(choices, value: float) -> str:
    """数值 → 最接近的那个选项（配置文件里可能是手改过的数）。"""
    return min(choices, key=lambda item: abs(item[1] - value))[0]


def _value_for(choices, label: str, fallback: float) -> float:
    for text, value in choices:
        if text == label:
            return value
    return fallback


class KeyCaptureButton(tk.Button):
    """点一下开始录入，然后按键盘上的任意一个键。"""

    def __init__(self, master, page: "SettingsPage", index: int) -> None:
        self.page = page
        self.index = index
        super().__init__(
            master, command=self.start,
            bg=Palette["entry_bg"], fg=Palette["text"],
            activebackground=Palette["accent_dark"], activeforeground="#ffffff",
            relief=tk.FLAT, bd=0, width=8, pady=6, cursor="hand2",
            font=("Consolas", 12, "bold"), highlightthickness=2,
            highlightbackground=Palette["border"],
        )
        self.refresh()

    def refresh(self) -> None:
        key = self.page.settings.keys[self.index]
        self.configure(text=_KEY_DISPLAY.get(key, key.upper()),
                       highlightbackground=Palette["border"], fg=Palette["text"])

    def start(self) -> None:
        self.page.begin_capture(self.index, self)

    def show_capturing(self, occupied: bool = False) -> None:
        self.configure(text="按下按键…",
                       fg=Palette["warn"] if occupied else Palette["accent"],
                       highlightbackground=Palette["accent"])
        self.page.after(3000, self.refresh)


class SettingsPage(tk.Frame):
    """设置界面。"""

    def __init__(self, master, app) -> None:
        super().__init__(master, bg=Palette["bg"])
        self.app = app
        self.settings = app.settings.copy()
        self._capturing: Optional[Tuple[int, KeyCaptureButton]] = None
        self._capture_binding = None

        self._build()
        self._sync()

    # ================================================================== #
    # 界面
    # ================================================================== #

    def _build(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        wrap = tk.Frame(self, bg=Palette["bg"])
        wrap.grid(row=0, column=0, sticky="nsew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(wrap, bg=Palette["bg"], highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        scroll = tk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.canvas.yview,
                              bg=Palette["panel_alt"], troughcolor=Palette["panel"],
                              activebackground=Palette["border"], width=12)
        scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scroll.set)

        holder = tk.Frame(self.canvas, bg=Palette["bg"])
        self._window = self.canvas.create_window((0, 0), window=holder, anchor="nw")
        holder.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        enable_wheel_scroll(self.canvas)

        self._build_keys_card(holder)
        self._build_mods_card(holder)
        self._build_play_card(holder)
        self._build_overlay_card(holder)
        self._build_compose_card(holder)
        self._build_actions(holder)

    # ------------------------------------------------------------------ #

    def _card(self, parent, title: str, subtitle: str = "") -> tk.Frame:
        card = tk.Frame(parent, bg=Palette["panel"], highlightthickness=1,
                        highlightbackground=Palette["border"])
        card.pack(fill=tk.X, padx=16, pady=(14, 0))

        head = tk.Frame(card, bg=Palette["panel"])
        head.pack(fill=tk.X, padx=16, pady=(14, 2))
        make_label(head, title, bold=True, size=11).pack(anchor="w")
        if subtitle:
            # 说明文字可能很长，窗口缩到最小宽度时要能折行
            make_label(card, subtitle, dim=True, wraplength=820,
                       justify=tk.LEFT).pack(anchor="w", padx=16, pady=(0, 4))

        body = tk.Frame(card, bg=Palette["panel"])
        body.pack(fill=tk.X, padx=16, pady=(6, 16))
        return body

    def _build_keys_card(self, parent) -> None:
        body = self._card(
            parent, "音键设置",
            "默认就是游戏里的那套。改法：点下面的按钮，然后按一下你想用的键。",
        )

        self.key_buttons: List[KeyCaptureButton] = []
        grid = tk.Frame(body, bg=Palette["panel"])
        grid.pack(fill=tk.X)

        labels = list(STEP_DIGITS) + ["i"]     # 最后一格是高音 do
        for index in range(8):
            cell = tk.Frame(grid, bg=Palette["panel"])
            cell.grid(row=index // 4, column=index % 4, padx=(0, 18), pady=6, sticky="w")

            text = f"{labels[index]}  {STEP_NAMES[index % 7]}" + ("（高）" if index == 7 else "")
            tk.Label(cell, text=text, bg=Palette["panel"], fg=Palette["text_dim"],
                     font=("Microsoft YaHei UI", 9), anchor="w", width=11).pack(side=tk.LEFT)

            button = KeyCaptureButton(cell, self, index)
            button.pack(side=tk.LEFT)
            self.key_buttons.append(button)

        make_button(body, "恢复默认键位", self._reset_keys, kind="ghost").pack(anchor="w", pady=(10, 0))

    def _build_mods_card(self, parent) -> None:
        body = self._card(
            parent, "变调鼠标键",
            "口琴靠「按住鼠标键」来升降调。这里可以换键，也可以选「不使用」关掉某一档。",
        )

        self.mod_vars: Dict[str, tk.StringVar] = {}
        row = tk.Frame(body, bg=Palette["panel"])
        row.pack(fill=tk.X)

        for feature in ("down", "up", "sharp"):
            cell = tk.Frame(row, bg=Palette["panel"])
            cell.pack(side=tk.LEFT, padx=(0, 24))

            make_label(cell, MOD_FEATURE_LABELS[feature]).pack(anchor="w", pady=(0, 4))
            var = tk.StringVar(value=_MOD_LABEL[self.settings.mod_assign.get(feature)])
            combo = make_combobox(cell, _MOD_CHOICES, var, width=11)
            combo.pack(anchor="w")
            combo.bind("<<ComboboxSelected>>", lambda e: self._on_mod_changed())
            self.mod_vars[feature] = var

        self.lbl_mod_warn = make_label(body, "", dim=True)
        self.lbl_mod_warn.pack(anchor="w", pady=(8, 0))

    def _build_play_card(self, parent) -> None:
        body = self._card(
            parent, "演奏设置",
            "游戏里漏音（明明按了却没响）就把节奏档往上调一档。",
        )

        row = tk.Frame(body, bg=Palette["panel"])
        row.pack(fill=tk.X)

        make_label(row, "节奏档").pack(side=tk.LEFT, padx=(0, 8))
        self.var_timing = tk.StringVar(value=TIMING_PRESETS[self.settings.timing].label)
        combo = make_combobox(row, [p.label for p in TIMING_PRESETS.values()], self.var_timing, width=8)
        combo.pack(side=tk.LEFT)
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_timing_changed())
        self.lbl_timing = make_label(row, "", dim=True)
        self.lbl_timing.pack(side=tk.LEFT, padx=12)

        row2 = tk.Frame(body, bg=Palette["panel"])
        row2.pack(fill=tk.X, pady=(12, 0))
        make_label(row2, "开始前倒计时").pack(side=tk.LEFT, padx=(0, 8))
        self.var_countdown = tk.DoubleVar(value=self.settings.countdown)
        make_spinbox(row2, self.var_countdown, 0, 15, 1, 4).pack(side=tk.LEFT)
        make_label(row2, "秒（留出切到游戏的时间）", dim=True).pack(side=tk.LEFT, padx=(6, 0))

    def _build_overlay_card(self, parent) -> None:
        body = self._card(
            parent, "悬浮提示窗（跟弹 / 自动演奏）",
            "演奏时在游戏画面上浮出「下一个该按什么」。它只是本程序自己的一个置顶小窗：不读写游戏内存、"
            "不注入游戏进程、不截屏，锁定后鼠标点击直接穿透到游戏。跟弹模式下程序一个键都不按。",
        )
        on_change = self._on_overlay_option

        row = tk.Frame(body, bg=Palette["panel"])
        row.pack(fill=tk.X)
        self.var_overlay_auto = tk.BooleanVar(value=self.settings.overlay_in_auto)
        make_check(row, "自动演奏时也显示悬浮提示", self.var_overlay_auto,
                   command=on_change).pack(side=tk.LEFT, padx=(0, 24))
        self.var_overlay_keyboard = tk.BooleanVar(value=self.settings.overlay_keyboard)
        make_check(row, "显示下方的键盘提示行", self.var_overlay_keyboard,
                   command=on_change).pack(side=tk.LEFT)

        row = tk.Frame(body, bg=Palette["panel"])
        row.pack(fill=tk.X, pady=(8, 0))
        self.var_guide_wait = tk.BooleanVar(value=self.settings.guide_wait)
        make_check(row, "跟弹时「等我按对再继续」", self.var_guide_wait,
                   command=on_change).pack(side=tk.LEFT)
        make_label(row, "练习用：每个音都停下来等你按对，不考节奏", dim=True).pack(side=tk.LEFT, padx=(8, 0))

        row = tk.Frame(body, bg=Palette["panel"])
        row.pack(fill=tk.X, pady=(12, 0))
        make_label(row, "跟弹速度").pack(side=tk.LEFT, padx=(0, 8))
        self.var_guide_speed = tk.StringVar(value=_label_for(_GUIDE_SPEEDS, self.settings.guide_speed))
        combo = make_combobox(row, [t for t, _ in _GUIDE_SPEEDS], self.var_guide_speed, width=11)
        combo.pack(side=tk.LEFT)
        combo.bind("<<ComboboxSelected>>", lambda e: on_change())

        make_label(row, "提前显示").pack(side=tk.LEFT, padx=(24, 8))
        self.var_lookahead = tk.StringVar(value=_label_for(_LOOKAHEADS, self.settings.overlay_lookahead))
        combo = make_combobox(row, [t for t, _ in _LOOKAHEADS], self.var_lookahead, width=6)
        combo.pack(side=tk.LEFT)
        combo.bind("<<ComboboxSelected>>", lambda e: on_change())

        make_label(row, "大小").pack(side=tk.LEFT, padx=(24, 8))
        self.var_scale = tk.StringVar(value=_label_for(_SCALES, self.settings.overlay_scale))
        combo = make_combobox(row, [t for t, _ in _SCALES], self.var_scale, width=5)
        combo.pack(side=tk.LEFT)
        combo.bind("<<ComboboxSelected>>", lambda e: on_change())

        row = tk.Frame(body, bg=Palette["panel"])
        row.pack(fill=tk.X, pady=(12, 0))
        make_label(row, "不透明度").pack(side=tk.LEFT, padx=(0, 8))
        self.var_alpha = tk.DoubleVar(value=round(self.settings.overlay_alpha * 100))
        make_slider(row, self.var_alpha, 30, 100, 1, length=180,
                    command=lambda _v: on_change()).pack(side=tk.LEFT)
        self.lbl_alpha = make_label(row, "", dim=True)
        self.lbl_alpha.pack(side=tk.LEFT, padx=(8, 0))

        row = tk.Frame(body, bg=Palette["panel"])
        row.pack(fill=tk.X, pady=(14, 0))
        self.btn_adjust = make_button(row, "调整位置 / 预览", self._toggle_adjust, kind="guide")
        self.btn_adjust.pack(side=tk.LEFT)
        make_button(row, "恢复默认位置", self._reset_overlay_position, kind="ghost").pack(side=tk.LEFT, padx=8)
        self.lbl_adjust = make_label(row, "点了之后悬浮窗会出现并播放演示，用鼠标拖到合适的位置，双击它锁定",
                                     dim=True)
        self.lbl_adjust.pack(side=tk.LEFT, padx=(8, 0))

        make_label(body, "全屏独占模式下任何窗口都盖不到游戏上——游戏要设成「无边框窗口化」或「窗口化」。",
                   dim=True).pack(anchor="w", pady=(12, 0))

    def _collect_overlay(self) -> None:
        """把悬浮窗那几项收回 self.settings（不落盘）。"""
        self.settings.overlay_in_auto = bool(self.var_overlay_auto.get())
        self.settings.overlay_keyboard = bool(self.var_overlay_keyboard.get())
        self.settings.guide_wait = bool(self.var_guide_wait.get())
        self.settings.guide_speed = _value_for(_GUIDE_SPEEDS, self.var_guide_speed.get(), 1.0)
        self.settings.overlay_lookahead = _value_for(_LOOKAHEADS, self.var_lookahead.get(), 3.0)
        self.settings.overlay_scale = _value_for(_SCALES, self.var_scale.get(), 1.0)
        try:
            self.settings.overlay_alpha = max(0.3, min(1.0, float(self.var_alpha.get()) / 100.0))
        except (tk.TclError, ValueError):
            self.settings.overlay_alpha = 0.92

    def _on_overlay_option(self) -> None:
        """改了悬浮窗选项：当场在悬浮窗上看效果，点「保存设置」才落盘。"""
        self._collect_overlay()
        self.lbl_alpha.configure(text=f"{self.settings.overlay_alpha * 100:.0f}%")
        self.app.overlay.apply_settings(self.settings)
        # 滑块在程序改它的值时也会回调，所以要和已保存的设置比一下，真变了才提示
        fields = ("overlay_in_auto", "overlay_keyboard", "guide_wait", "guide_speed",
                  "overlay_lookahead", "overlay_scale", "overlay_alpha")
        if any(getattr(self.settings, f) != getattr(self.app.settings, f) for f in fields):
            self.lbl_status.configure(text="悬浮窗设置已改动，记得点「保存设置」")

    def _toggle_adjust(self) -> None:
        overlay = self.app.overlay
        if overlay.adjusting:
            overlay.set_adjusting(False)
            return
        if overlay.active:
            self.lbl_adjust.configure(text="正在演奏，先停下再调整位置", fg=Palette["warn"])
            return
        overlay.set_adjusting(True)
        # 预览用的是设置页上还没保存的外观，方便边调边看
        overlay.apply_settings(self.settings)

    def on_overlay_adjusting(self, on: bool) -> None:
        """悬浮窗进入 / 退出调整状态时由主窗口调用（双击悬浮窗也会退出）。"""
        if on:
            self.btn_adjust.configure(text="完成调整（锁定）")
            self.lbl_adjust.configure(text="拖动悬浮窗到合适的位置；双击它或点左边按钮锁定",
                                      fg=Palette["warn"])
        else:
            self.btn_adjust.configure(text="调整位置 / 预览")
            self.lbl_adjust.configure(text="位置已保存。锁定后鼠标点击会直接穿透到游戏",
                                      fg=Palette["text_dim"])

    def _reset_overlay_position(self) -> None:
        for settings in (self.settings, self.app.settings):
            settings.overlay_x = settings.overlay_y = None
        self.app.settings.save()
        self.app.overlay.apply_settings(self.settings)
        self.lbl_adjust.configure(text="已恢复到屏幕下方居中", fg=Palette["text_dim"])

    def _build_compose_card(self, parent) -> None:
        body = self._card(parent, "谱曲默认值", "新建曲子时用这些参数，已保存的曲子不受影响。")

        row = tk.Frame(body, bg=Palette["panel"])
        row.pack(fill=tk.X)

        make_label(row, "每小节拍数").pack(side=tk.LEFT, padx=(0, 8))
        self.var_beats = tk.StringVar(value=f"{self.settings.beats_per_bar} 拍")
        combo = make_combobox(row, [f"{n} 拍" for n in (2, 3, 4, 6)], self.var_beats, width=7)
        combo.pack(side=tk.LEFT)

        make_label(row, "默认速度").pack(side=tk.LEFT, padx=(24, 8))
        self.var_bpm = tk.DoubleVar(value=self.settings.default_bpm)
        make_spinbox(row, self.var_bpm, 40, 200, 5, 6).pack(side=tk.LEFT)

    def _build_actions(self, parent) -> None:
        bar = tk.Frame(parent, bg=Palette["bg"])
        bar.pack(fill=tk.X, padx=16, pady=(20, 4))

        make_button(bar, "恢复全部默认设置", self._reset_all, kind="ghost").pack(side=tk.LEFT)
        make_button(bar, "查看使用须知", self._show_disclaimer, kind="ghost").pack(side=tk.LEFT, padx=8)
        make_button(bar, "保存设置", self._save, kind="accent").pack(side=tk.RIGHT)
        make_button(bar, "放弃修改", self._reload, kind="ghost").pack(side=tk.RIGHT, padx=8)

        self.lbl_status = make_label(bar, "", dim=True, bg=Palette["bg"])
        self.lbl_status.pack(side=tk.RIGHT, padx=16)

        # 右下角常驻的一句提醒
        notice = tk.Frame(parent, bg="#2b1417", highlightthickness=1,
                          highlightbackground="#5a3038")
        notice.pack(fill=tk.X, padx=16, pady=(4, 22))
        tk.Label(
            notice,
            text="仅供个人学习、技术研究与娱乐使用　·　严禁在任何在线对局中使用　·　"
                 "后果自负，作者不承担任何责任",
            bg="#2b1417", fg="#e8a2ab", font=("Microsoft YaHei UI", 9),
            wraplength=900, justify=tk.LEFT, padx=14, pady=9,
        ).pack(anchor="w", fill=tk.X)

    def _show_disclaimer(self) -> None:
        from .disclaimer import ask_disclaimer
        ask_disclaimer(self.app)

    # ================================================================== #
    # 按键录入
    # ================================================================== #

    def begin_capture(self, index: int, button: KeyCaptureButton) -> None:
        self.cancel_capture()
        self._capturing = (index, button)
        button.show_capturing()

        # 录入期间停掉全局热键，否则按 F6 会直接开始演奏
        self.app.hotkeys.suspend()
        top = self.winfo_toplevel()
        self._capture_binding = top.bind("<KeyPress>", self._on_capture_key, add="+")
        top.focus_force()

    def cancel_capture(self) -> None:
        if self._capturing is None:
            return
        _, button = self._capturing
        button.refresh()
        self._capturing = None
        self.app.hotkeys.resume()
        top = self.winfo_toplevel()
        if self._capture_binding:
            try:
                top.unbind("<KeyPress>", self._capture_binding)
            except tk.TclError:
                pass
            self._capture_binding = None

    def _on_capture_key(self, event) -> str:
        if self._capturing is None:
            return ""

        keysym = (event.keysym or "").lower()

        if keysym == "escape":
            self.cancel_capture()
            return "break"

        key = _KEYSYM_TO_KEY.get(keysym)
        if key is None:
            if len(keysym) == 1 and (keysym.isalnum()):
                key = keysym
            else:
                # 功能键之类不能当音键
                index, button = self._capturing
                button.show_capturing(occupied=True)
                return "break"

        index, button = self._capturing
        self._capturing = None
        self.app.hotkeys.resume()
        top = self.winfo_toplevel()
        if self._capture_binding:
            try:
                top.unbind("<KeyPress>", self._capture_binding)
            except tk.TclError:
                pass
            self._capture_binding = None

        keys = list(self.settings.keys)
        # 如果这个键已经被别的音占用了，两个音直接对调，省得用户手动改两次
        if key in keys and keys.index(key) != index:
            other = keys.index(key)
            keys[other] = keys[index]
        keys[index] = key
        self.settings.keys = keys

        for btn in self.key_buttons:
            btn.refresh()
        self._check_keys()
        self.lbl_status.configure(text="键位已改动，记得点「保存设置」")
        return "break"

    # ================================================================== #
    # 同步与校验
    # ================================================================== #

    def _sync(self) -> None:
        for button in self.key_buttons:
            button.refresh()

        for feature, var in self.mod_vars.items():
            var.set(_MOD_LABEL[self.settings.mod_assign.get(feature)])

        self.var_timing.set(TIMING_PRESETS[self.settings.timing].label)
        self._on_timing_changed()
        self.var_countdown.set(self.settings.countdown)
        self.var_beats.set(f"{self.settings.beats_per_bar} 拍")
        self.var_bpm.set(self.settings.default_bpm)
        self.var_overlay_auto.set(self.settings.overlay_in_auto)
        self.var_overlay_keyboard.set(self.settings.overlay_keyboard)
        self.var_guide_wait.set(self.settings.guide_wait)
        self.var_guide_speed.set(_label_for(_GUIDE_SPEEDS, self.settings.guide_speed))
        self.var_lookahead.set(_label_for(_LOOKAHEADS, self.settings.overlay_lookahead))
        self.var_scale.set(_label_for(_SCALES, self.settings.overlay_scale))
        self.var_alpha.set(round(self.settings.overlay_alpha * 100))
        self.lbl_alpha.configure(text=f"{self.settings.overlay_alpha * 100:.0f}%")
        self._check_keys()
        self._on_mod_changed()

    def _on_timing_changed(self) -> None:
        for preset in TIMING_PRESETS.values():
            if preset.label == self.var_timing.get():
                self.lbl_timing.configure(text=preset.note or preset.describe())
                break

    def _on_mod_changed(self) -> None:
        values = [_MOD_VALUE[var.get()] for var in self.mod_vars.values()]
        used = [v for v in values if v]
        if len(used) != len(set(used)):
            self.lbl_mod_warn.configure(text="⚠ 同一个鼠标键被分配了多个功能，保存时会被拒绝",
                                        fg=Palette["warn"])
        elif not used:
            self.lbl_mod_warn.configure(text="⚠ 三个变调功能全部关闭，就只能吹中音区那 8 个音了",
                                        fg=Palette["warn"])
        else:
            self.lbl_mod_warn.configure(text="按键不冲突", fg=Palette["text_dim"])

    def _check_keys(self) -> None:
        keys = self.settings.keys
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        for index, button in enumerate(self.key_buttons):
            conflict = keys[index] in duplicates
            button.configure(highlightbackground=Palette["danger"] if conflict else Palette["border"])

    def _reset_keys(self) -> None:
        from kouqin.layout import BASE_KEYS
        self.settings.keys = list(BASE_KEYS)
        self._sync()
        self.lbl_status.configure(text="已恢复默认键位，记得点「保存设置」")

    def _reload(self) -> None:
        self.cancel_capture()
        self.settings = self.app.settings.copy()
        self._sync()
        self.app.overlay.apply_settings(self.app.settings)     # 撤掉悬浮窗上的预览效果
        self.lbl_status.configure(text="已放弃修改")

    def _reset_all(self) -> None:
        if not messagebox.askyesno("恢复默认", "把所有设置恢复成出厂状态？", parent=self.app):
            return
        self.cancel_capture()
        self.settings = self.app.settings.copy()
        self.settings.reset()
        self._sync()
        self.lbl_status.configure(text="已恢复默认值，记得点「保存设置」")

    # ================================================================== #
    # 保存
    # ================================================================== #

    def _collect(self) -> None:
        """把界面上的值收回 settings。"""
        for feature, var in self.mod_vars.items():
            self.settings.mod_assign[feature] = _MOD_VALUE[var.get()]

        for preset in TIMING_PRESETS.values():
            if preset.label == self.var_timing.get():
                self.settings.timing = preset.name
                break

        try:
            self.settings.countdown = max(0.0, min(15.0, float(self.var_countdown.get())))
        except (tk.TclError, ValueError):
            self.settings.countdown = 3.0

        try:
            self.settings.beats_per_bar = max(1, min(12, int(str(self.var_beats.get()).split()[0])))
        except (ValueError, IndexError):
            self.settings.beats_per_bar = 4

        try:
            self.settings.default_bpm = max(30.0, min(300.0, float(self.var_bpm.get())))
        except (tk.TclError, ValueError):
            self.settings.default_bpm = 90.0

        self._collect_overlay()

    def _save(self) -> None:
        self.cancel_capture()
        self._collect()

        problems = self.settings.validate_keymap()
        if problems:
            messagebox.showerror("设置有冲突", "\n".join(problems), parent=self.app)
            return

        self.app.apply_settings(self.settings)
        self.lbl_status.configure(text="✔ 已保存")
        self.app.log("设置已保存")

    def on_show(self) -> None:
        """切到本页时刷新一次，保证显示的是最新设置。"""
        self.cancel_capture()
        self.settings = self.app.settings.copy()
        self._sync()
        self.lbl_status.configure(text="")
