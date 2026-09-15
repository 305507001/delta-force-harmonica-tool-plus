"""
钢琴卷帘视图 —— 把音符画成方块，支持点选、拖动、增删。

坐标换算只有一套：``_x_of`` / ``_time_of`` 互为逆运算，
渲染与命中检测共用同一个函数，两个方向严格对称，
这样不会出现「看到的方块和点得到的方块差几像素」的错位。

颜色约定：
    绿色  可演奏（落在口琴音域内）
    橙色  超音域，演奏时会被折叠到最近的可用八度
    灰色  超音域且策略为「跳过」，演奏时会变成空拍
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, List, Optional, Sequence, Tuple

from kouqin.layout import Layout
from kouqin.smf import Note

__all__ = ["PianoRoll"]

# 配色
BG = "#141821"
ROW_WHITE = "#1c222e"
ROW_BLACK = "#171c26"
ROW_OUT = "#12151c"
GRID_MINOR = "#232a38"
GRID_MAJOR = "#303a4d"
RULER_BG = "#1a1f2a"
RULER_FG = "#7c8798"
KEYCOL_BG = "#1a1f2a"
KEYCOL_FG = "#9aa5b6"
NOTE_OK = "#35c98a"
NOTE_OK_SEL = "#6ff0b6"
NOTE_FOLD = "#e0954a"
NOTE_FOLD_SEL = "#ffc078"
NOTE_SKIP = "#59616f"
NOTE_SKIP_SEL = "#8d97a8"
PLAYHEAD = "#ff5f6d"
SEL_BOX = "#4d7cff"
PLAYED_FLASH = "#ffffff"

_BLACK_KEY_SEMITONES = {1, 3, 6, 8, 10}
_KEY_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

KEYCOL_W = 46
RULER_H = 22
ROW_H = 11


class PianoRoll(tk.Frame):
    """
    卷帘编辑器。

    :param on_edited: 音符被修改后的回调 ``(notes) -> None``
    """

    def __init__(self, master, layout: Layout, on_edited: Optional[Callable] = None,
                 on_select: Optional[Callable] = None, **kwargs) -> None:
        super().__init__(master, bg=BG, **kwargs)
        self.layout = layout
        self.on_edited = on_edited
        self.on_select = on_select

        self.notes: List[Note] = []
        self.selected: set = set()          # 选中的下标集合

        # 视图状态
        self.px_per_ms = 0.12
        self.view_ms = 0.0
        self.snap_ms = 100.0
        self.snap_on = True

        self.lo_pitch = 48
        self.hi_pitch = 85

        # 拖动状态
        self._drag_mode: Optional[str] = None
        self._drag_origin = (0, 0)
        self._drag_snapshot: List[Tuple[int, float, int]] = []
        self._box: Optional[int] = None
        self._box_start = (0, 0)
        self._playhead_ms: Optional[float] = None
        self._flash_until: float = 0.0

        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, cursor="arrow")
        self.hbar = tk.Scrollbar(self, orient=tk.HORIZONTAL, command=self._on_scroll,
                                 troughcolor="#1a1f2a", bg="#2b3342",
                                 activebackground="#3c4658", width=12)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.hbar.pack(side=tk.BOTTOM, fill=tk.X)

        self._bind_events()

    # ------------------------------------------------------------------ #
    # 数据
    # ------------------------------------------------------------------ #

    def set_notes(self, notes: Sequence[Note], keep_view: bool = False) -> None:
        """载入音符并自动调整视图范围。"""
        self.notes = [n.copy() for n in notes]
        self.selected.clear()
        self._playhead_ms = None
        if not keep_view:
            self._fit_view()
        self._notify_select()
        self._redraw()

    def current_notes(self) -> List[Note]:
        return list(self.notes)

    def _fit_view(self) -> None:
        """把视图调整到刚好装下所有音符。"""
        if not self.notes:
            self.lo_pitch, self.hi_pitch = 48, 85
            self.view_ms = 0.0
            return
        pitches = [n.pitch for n in self.notes]
        self.lo_pitch = max(0, min(pitches) - 4)
        self.hi_pitch = min(127, max(pitches) + 4)

        total = max(n.end for n in self.notes)
        width = max(200, self.canvas.winfo_width() or 900)
        self.px_per_ms = max(0.01, (width - KEYCOL_W - 24) / max(1.0, total))
        self.view_ms = 0.0

    # ------------------------------------------------------------------ #
    # 坐标换算（唯一来源）
    # ------------------------------------------------------------------ #

    def _x_of(self, ms: float) -> float:
        return KEYCOL_W + (ms - self.view_ms) * self.px_per_ms

    def _time_of(self, x: float) -> float:
        return (x - KEYCOL_W) / self.px_per_ms + self.view_ms

    def _y_of(self, pitch: int) -> float:
        return RULER_H + (self.hi_pitch - pitch) * ROW_H

    def _pitch_of(self, y: float) -> int:
        return self.hi_pitch - int((y - RULER_H) // ROW_H)

    def _view_span(self) -> Tuple[float, float]:
        w = self.canvas.winfo_width() or 900
        return self._time_of(KEYCOL_W), self._time_of(w)

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        width = c.winfo_width() or 900
        height = c.winfo_height() or 400

        # --- 行底色 ---
        for pitch in range(self.lo_pitch, self.hi_pitch + 1):
            y = self._y_of(pitch)
            if y + ROW_H < RULER_H or y > height:
                continue
            if not self.layout.can_play(pitch):
                color = ROW_OUT
            elif (pitch % 12) in _BLACK_KEY_SEMITONES:
                color = ROW_BLACK
            else:
                color = ROW_WHITE
            c.create_rectangle(KEYCOL_W, y, width, y + ROW_H, fill=color, width=0)

        # --- 纵向网格 ---
        t0, t1 = self._view_span()
        step, major = self._grid_step()
        t = (int(t0 / step)) * step
        while t <= t1:
            x = self._x_of(t)
            if x >= KEYCOL_W:
                is_major = abs(t % major) < 1e-6
                c.create_line(x, RULER_H, x, height,
                              fill=GRID_MAJOR if is_major else GRID_MINOR)
            t += step

        # --- 八度分隔线（C 音处加粗）---
        for pitch in range(self.lo_pitch, self.hi_pitch + 1):
            if pitch % 12 == 0:
                y = self._y_of(pitch) + ROW_H
                c.create_line(KEYCOL_W, y, width, y, fill=GRID_MAJOR)

        # --- 左侧音名列 ---
        c.create_rectangle(0, RULER_H, KEYCOL_W, height, fill=KEYCOL_BG, width=0)
        for pitch in range(self.lo_pitch, self.hi_pitch + 1):
            y = self._y_of(pitch)
            if y + ROW_H < RULER_H or y > height:
                continue
            if pitch % 12 == 0 or pitch == self.hi_pitch:
                c.create_text(KEYCOL_W - 6, y + ROW_H / 2, anchor="e",
                              text=f"{_KEY_NAMES[pitch % 12]}{pitch // 12 - 1}",
                              fill=KEYCOL_FG, font=("Consolas", 8))

        # --- 顶部时间标尺 ---
        c.create_rectangle(KEYCOL_W, 0, width, RULER_H, fill=RULER_BG, width=0)
        c.create_rectangle(0, 0, KEYCOL_W, RULER_H, fill=RULER_BG, width=0)
        rstep, _ = self._grid_step()
        t = (int(t0 / rstep)) * rstep
        while t <= t1:
            x = self._x_of(t)
            if x >= KEYCOL_W:
                secs = t / 1000.0
                label = f"{int(secs // 60)}:{secs % 60:04.1f}" if t >= 60000 else f"{secs:.1f}s"
                c.create_line(x, RULER_H - 6, x, RULER_H, fill=RULER_FG)
                c.create_text(x + 3, RULER_H / 2, anchor="w", text=label,
                              fill=RULER_FG, font=("Consolas", 8))
            t += rstep * 4

        # --- 音符 ---
        for idx, note in enumerate(self.notes):
            x1 = self._x_of(note.start)
            x2 = self._x_of(note.end)
            if x2 < KEYCOL_W or x1 > width:
                continue
            y = self._y_of(note.pitch)
            if y + ROW_H < RULER_H or y > height:
                continue
            playable = self.layout.can_play(note.pitch)
            selected = idx in self.selected
            if playable:
                fill = NOTE_OK_SEL if selected else NOTE_OK
            else:
                fill = NOTE_FOLD_SEL if selected else NOTE_FOLD
            if x2 - x1 < 2:
                x2 = x1 + 2
            c.create_rectangle(max(x1, KEYCOL_W - 1), y + 1,
                               min(x2, width + 4), y + ROW_H - 1,
                               fill=fill, width=1,
                               outline="#ffffff" if selected else "")

        # --- 框选 ---
        if self._box is not None:
            c.create_rectangle(*self._box_coords(), outline=SEL_BOX, dash=(4, 2), width=1)

        # --- 播放头 ---
        if self._playhead_ms is not None:
            x = self._x_of(self._playhead_ms)
            if x >= KEYCOL_W:
                c.create_line(x, RULER_H, x, height, fill=PLAYHEAD, width=2)
                c.create_polygon(x - 4, RULER_H, x + 4, RULER_H, x, RULER_H + 6,
                                 fill=PLAYHEAD, outline="")

        # --- 空状态提示 ---
        if not self.notes:
            c.create_text((KEYCOL_W + width) / 2, (RULER_H + height) / 2,
                          text="拖入或打开一个 MIDI / 简谱文件开始",
                          fill="#4a5464", font=("Microsoft YaHei UI", 11))
        self._sync_scrollbar()

    def _grid_step(self) -> Tuple[float, float]:
        """根据缩放选一个不密不疏的网格间距。"""
        target_px = 12.0
        raw = target_px / max(1e-6, self.px_per_ms)
        for step in (10, 20, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000):
            if step >= raw:
                return float(step), float(step * 4)
        return 60000.0, 240000.0

    def _sync_scrollbar(self) -> None:
        total = max((n.end for n in self.notes), default=1.0)
        first = self.view_ms / max(1.0, total * 1.15)
        last = (self.view_ms + (self.canvas.winfo_width() or 900) / self.px_per_ms) / max(1.0, total * 1.15)
        self.hbar.set(max(0.0, first), min(1.0, last))

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #

    def _bind_events(self) -> None:
        c = self.canvas
        c.bind("<Button-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Double-Button-1>", self._on_double)
        c.bind("<Motion>", self._on_motion)
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Control-MouseWheel>", self._on_zoom)
        c.bind("<Shift-MouseWheel>", self._on_hscroll)
        c.bind("<Configure>", lambda e: self._redraw())
        c.bind("<Delete>", lambda e: self.delete_selected())
        c.bind("<BackSpace>", lambda e: self.delete_selected())
        c.focus_set()

    def _hit_test(self, x: float, y: float) -> Optional[int]:
        """返回命中的音符下标（后画的优先，即取最后一个匹配的）。"""
        if x < KEYCOL_W or y < RULER_H:
            return None
        hit = None
        for idx, note in enumerate(self.notes):
            x1, x2 = self._x_of(note.start), self._x_of(note.end)
            if x2 - x1 < 2:
                x2 = x1 + 2
            ny = self._y_of(note.pitch)
            if x1 <= x <= x2 and ny <= y <= ny + ROW_H:
                hit = idx
        return hit

    def _on_motion(self, event) -> None:
        pitch = self._pitch_of(event.y)
        ms = self._time_of(event.x)
        try:
            combo = self.layout.resolve(pitch)
            hint = combo.describe() if combo else "超音域"
        except Exception:
            hint = "-"
        self.canvas.configure(cursor="hand2" if self._hit_test(event.x, event.y) is not None else "arrow")
        if hasattr(self, "_on_hover") and self._on_hover:
            self._on_hover(ms, pitch, hint)

    def _on_press(self, event) -> None:
        self.canvas.focus_set()
        ctrl = bool(event.state & 0x0004)
        idx = self._hit_test(event.x, event.y)

        if idx is None:
            if not ctrl:
                self.selected.clear()
                self._notify_select()
            self._drag_mode = "box"
            self._box_start = (event.x, event.y)
            self._box = None
            self._redraw()
            return

        if ctrl:
            self.selected.symmetric_difference_update({idx})
        elif idx not in self.selected:
            self.selected = {idx}
        self._notify_select()

        self._drag_mode = "move"
        self._drag_origin = (event.x, event.y)
        self._drag_snapshot = [
            (i, self.notes[i].start, self.notes[i].pitch) for i in sorted(self.selected)
        ]
        self._redraw()

    def _on_drag(self, event) -> None:
        if self._drag_mode == "move" and self._drag_snapshot:
            dx = event.x - self._drag_origin[0]
            dy = event.y - self._drag_origin[1]
            delta_ms = dx / self.px_per_ms
            delta_pitch = int(round(-dy / ROW_H))
            for idx, start0, pitch0 in self._drag_snapshot:
                new_start = start0 + delta_ms
                if self.snap_on and self.snap_ms > 0:
                    new_start = round(new_start / self.snap_ms) * self.snap_ms
                self.notes[idx].start = max(0.0, new_start)
                self.notes[idx].pitch = max(0, min(127, pitch0 + delta_pitch))
            self._redraw()
        elif self._drag_mode == "box":
            self._box = (self._box_start, (event.x, event.y))
            self._redraw()

    def _box_coords(self) -> Tuple[float, float, float, float]:
        (x1, y1), (x2, y2) = self._box
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)

    def _on_release(self, event) -> None:
        mode, self._drag_mode = self._drag_mode, None
        if mode == "box" and self._box is not None:
            bx1, by1, bx2, by2 = self._box_coords()
            if abs(bx2 - bx1) > 3 or abs(by2 - by1) > 3:
                t1, t2 = self._time_of(bx1), self._time_of(bx2)
                p1, p2 = self._pitch_of(by2), self._pitch_of(by1)
                for idx, note in enumerate(self.notes):
                    if t1 - 1 <= note.start <= t2 and p1 <= note.pitch <= p2:
                        self.selected.add(idx)
            self._box = None
        elif mode == "move":
            changed = [
                (i, s, p) for i, s, p in self._drag_snapshot
                if abs(self.notes[i].start - s) > 1e-6 or self.notes[i].pitch != p
            ]
            if changed and self.on_edited:
                self.on_edited(self.notes)
        self._drag_snapshot = []
        self._notify_select()
        self._redraw()

    def _on_double(self, event) -> None:
        """双击空白处添加一个音，双击音符删除它。"""
        idx = self._hit_test(event.x, event.y)
        if idx is not None:
            del self.notes[idx]
            self.selected = {i for i in self.selected if i != idx}
            self.selected = {i - 1 if i > idx else i for i in self.selected}
        else:
            pitch = self._pitch_of(event.y)
            if not (self.lo_pitch <= pitch <= 127):
                return
            start = self._time_of(event.x)
            if self.snap_on and self.snap_ms > 0:
                start = round(start / self.snap_ms) * self.snap_ms
            start = max(0.0, start)
            self.notes.append(Note(start, max(120.0, self.snap_ms), pitch))
            self.notes.sort(key=lambda n: (n.start, n.pitch))
        if self.on_edited:
            self.on_edited(self.notes)
        self._redraw()

    def _on_wheel(self, event) -> None:
        """滚轮上下平移音高视图（保持在 0~127 之间）。"""
        step = 3 if event.delta > 0 else -3
        span = self.hi_pitch - self.lo_pitch
        new_lo = self.lo_pitch + step
        new_hi = self.hi_pitch + step
        if new_lo < 0 or new_hi > 127:
            return
        self.lo_pitch, self.hi_pitch = new_lo, new_hi
        self._redraw()

    def _on_zoom(self, event) -> None:
        """Ctrl+滚轮缩放时间轴，以鼠标位置为锚点。"""
        factor = 1.25 if event.delta > 0 else 0.8
        anchor_ms = self._time_of(event.x)
        self.px_per_ms = max(0.005, min(8.0, self.px_per_ms * factor))
        self.view_ms = anchor_ms - (event.x - KEYCOL_W) / self.px_per_ms
        self.view_ms = max(0.0, self.view_ms)
        self._redraw()

    def _on_hscroll(self, event) -> None:
        """Shift+滚轮左右平移。"""
        self.view_ms = max(0.0, self.view_ms - event.delta * 0.5 / self.px_per_ms)
        self._redraw()

    def _on_scroll(self, *args) -> None:
        total = max((n.end for n in self.notes), default=1.0) * 1.15
        self.view_ms = max(0.0, float(args[0]) * total if args[0] == "moveto"
                           else self.view_ms + float(args[1]) * total * 0.1)
        self._redraw()

    # ------------------------------------------------------------------ #
    # 编辑操作
    # ------------------------------------------------------------------ #

    def delete_selected(self) -> None:
        if not self.selected:
            return
        self.notes = [n for i, n in enumerate(self.notes) if i not in self.selected]
        self.selected.clear()
        if self.on_edited:
            self.on_edited(self.notes)
        self._notify_select()
        self._redraw()

    def nudge_selected(self, delta_ms: float = 0.0, delta_pitch: int = 0) -> None:
        """微调选中音符（方向键用）。"""
        if not self.selected:
            return
        for idx in self.selected:
            note = self.notes[idx]
            note.start = max(0.0, note.start + delta_ms)
            note.pitch = max(0, min(127, note.pitch + delta_pitch))
        if self.on_edited:
            self.on_edited(self.notes)
        self._redraw()

    def set_snap(self, enabled: bool, snap_ms: float = 100.0) -> None:
        self.snap_on = enabled
        self.snap_ms = snap_ms

    # ------------------------------------------------------------------ #
    # 播放指示
    # ------------------------------------------------------------------ #

    def set_playhead(self, ms: Optional[float]) -> None:
        self._playhead_ms = ms
        self._redraw()

    def follow_playhead(self, ms: float) -> None:
        """让播放头保持在可见区域内（超出右边时自动翻页）。"""
        self._playhead_ms = ms
        width = self.canvas.winfo_width() or 900
        x = self._x_of(ms)
        if x > width - 40 or x < KEYCOL_W:
            self.view_ms = max(0.0, ms - (width - KEYCOL_W) * 0.25 / self.px_per_ms)
        self._redraw()

    def _notify_select(self) -> None:
        if self.on_select:
            self.on_select(sorted(self.selected))
