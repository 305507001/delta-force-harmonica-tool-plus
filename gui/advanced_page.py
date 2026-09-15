"""
进阶页 —— 给愿意折腾的人准备的。

收在这里的东西：
    * 导入现成的 MIDI 文件（.mid / .midi / .kar / .rmi），自动挑主旋律轨
    * 用简谱文本写谱
    * 钢琴卷帘可视化改谱（拖动改音高和时间、双击加音、Delete 删音）
    * 导出通用按键表 / 罗技 G HUB 脚本 / JSON
    * 一键把 MIDI 旋律量化成格子谱，存进曲库后就能像普通曲子那样点播

默认不加载，点到「进阶」标签才创建，免得拖慢启动、也别吓着新手。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Dict, List, Optional

from kouqin.engine import FoldMode
from kouqin.exporter import (
    export_csv,
    export_json,
    export_keytable_csv,
    export_keytable_md,
    export_lua,
)
from kouqin.jianpu import JIANPU_TEMPLATE, parse_jianpu
from kouqin.smf import MidiError, Note, Song, Track, pitch_name, read_midi, write_midi
from kouqin.tune import notes_to_tune

from .jianpu_editor import JianpuEditor
from .pianoroll import PianoRoll
from .theme import Palette, make_button, make_combobox, make_label, make_spinbox

#: 超音域策略在界面上的显示名
_FOLD_CHOICES = {
    "折叠到最近八度": FoldMode.FOLD,
    "超界就不吹": FoldMode.SKIP,
}


class AdvancedPage(tk.Frame):
    """MIDI 导入 + 钢琴卷帘。"""

    def __init__(self, master, app) -> None:
        super().__init__(master, bg=Palette["bg"])
        self.app = app
        self.song: Optional[Song] = None
        self.track: Optional[Track] = None
        self.notes: List[Note] = []
        self.source_name = ""
        self._track_map: Dict[str, Track] = {}

        self.var_track = tk.StringVar()
        self.var_speed = tk.DoubleVar(value=app.settings.speed)
        self.var_transpose = tk.IntVar(value=0)
        self.var_fold = tk.StringVar(value=list(_FOLD_CHOICES)[0])
        self.var_snap = tk.BooleanVar(value=True)

        self._build()

    # ================================================================== #
    # 界面
    # ================================================================== #

    def _build(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build_toolbar()
        self._build_roll()
        self._build_footer()

    def _build_toolbar(self) -> None:
        bar = tk.Frame(self, bg=Palette["panel"], height=46)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)

        make_button(bar, "打开 MIDI", self.open_midi, kind="accent").pack(side=tk.LEFT, padx=(10, 4), pady=8)
        make_button(bar, "简谱写谱", self.open_jianpu).pack(side=tk.LEFT, padx=4, pady=8)

        tk.Frame(bar, bg=Palette["border"], width=1).pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=9)

        make_label(bar, "音轨").pack(side=tk.LEFT, padx=(0, 4))
        self.cmb_track = make_combobox(bar, [], self.var_track, width=22)
        self.cmb_track.pack(side=tk.LEFT, pady=8)
        self.cmb_track.bind("<<ComboboxSelected>>", lambda e: self._on_track_change())

        tk.Frame(bar, bg=Palette["border"], width=1).pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=9)

        make_label(bar, "倍速").pack(side=tk.LEFT, padx=(0, 4))
        make_spinbox(bar, self.var_speed, 0.25, 4.0, 0.05, 5).pack(side=tk.LEFT, pady=8)

        make_label(bar, "移调").pack(side=tk.LEFT, padx=(10, 4))
        make_spinbox(bar, self.var_transpose, -24, 24, 1, 4).pack(side=tk.LEFT, pady=8)
        make_button(bar, "自动贴合", self.auto_fit, kind="ghost").pack(side=tk.LEFT, padx=6, pady=8)

        make_label(bar, "超音域").pack(side=tk.LEFT, padx=(10, 4))
        combo = make_combobox(bar, list(_FOLD_CHOICES), self.var_fold, width=14)
        combo.pack(side=tk.LEFT, pady=8)
        combo.bind("<<ComboboxSelected>>", lambda e: self._update_stats())

        tk.Checkbutton(
            bar, text="0.1s 吸附", variable=self.var_snap,
            command=lambda: self.roll.set_snap(self.var_snap.get()),
            bg=Palette["panel"], fg=Palette["text_dim"], selectcolor=Palette["entry_bg"],
            activebackground=Palette["panel"], activeforeground=Palette["text"],
            font=("Microsoft YaHei UI", 9), bd=0, highlightthickness=0,
        ).pack(side=tk.LEFT, padx=(12, 0))

    def _build_roll(self) -> None:
        self.roll = PianoRoll(self, self.app.layout,
                              on_edited=self._on_edited,
                              on_select=self._on_select)
        self.roll.grid(row=1, column=0, sticky="nsew")

    def _build_footer(self) -> None:
        bar = tk.Frame(self, bg=Palette["panel_alt"])
        bar.grid(row=2, column=0, sticky="ew")

        make_button(bar, "试听这一段", self.toggle_preview, kind="ghost").pack(side=tk.LEFT, padx=(10, 4), pady=8)
        self.lbl_stats = make_label(bar, "还没有载入乐谱", bg=Palette["panel_alt"], dim=True)
        self.lbl_stats.pack(side=tk.LEFT, padx=12)

        make_button(bar, "▶  演奏这一段", self.play_current, kind="accent").pack(side=tk.RIGHT, padx=(4, 10), pady=8)
        make_button(bar, "存进曲库", self.save_to_library, kind="ghost").pack(side=tk.RIGHT, padx=4, pady=8)

        export = tk.Menubutton(
            bar, text="导出 ▾", bg=Palette["panel_alt"], fg=Palette["text"],
            activebackground=Palette["border"], activeforeground=Palette["text"],
            relief=tk.FLAT, bd=0, padx=12, pady=4, cursor="hand2",
            font=("Microsoft YaHei UI", 9), highlightthickness=1,
            highlightbackground=Palette["border"],
        )
        menu = tk.Menu(export, tearoff=0, bg=Palette["panel_alt"], fg=Palette["text"],
                       activebackground=Palette["accent_dark"], activeforeground="#ffffff",
                       bd=0, font=("Microsoft YaHei UI", 9))
        menu.add_command(label="通用按键表 (.csv)", command=lambda: self._export("csv"))
        menu.add_command(label="罗技 G HUB 脚本 (.lua)", command=lambda: self._export("lua"))
        menu.add_command(label="结构化数据 (.json)", command=lambda: self._export("json"))
        menu.add_separator()
        menu.add_command(label="当前谱面存成 MIDI", command=lambda: self._export("midi"))
        menu.add_separator()
        menu.add_command(label="全音域键位表 (.csv)", command=lambda: self._export("keys"))
        menu.add_command(label="全音域键位表 (.md)", command=lambda: self._export("keysmd"))
        export.configure(menu=menu)
        export.pack(side=tk.RIGHT, padx=4, pady=8)

    # ================================================================== #
    # 载入
    # ================================================================== #

    def open_midi(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 MIDI 文件",
            filetypes=[("MIDI 文件", "*.mid *.midi *.kar *.rmi"), ("所有文件", "*.*")],
            parent=self.app,
        )
        if path:
            self.load_midi(path)

    def load_midi(self, path: str) -> None:
        try:
            song = read_midi(path)
        except (MidiError, OSError) as exc:
            messagebox.showerror("读取失败", f"这个文件读不了：\n{exc}", parent=self.app)
            return

        self.song = song
        self.source_name = os.path.basename(path)
        self._track_map.clear()
        choices: List[str] = []
        for track in song.tracks:
            if not track.notes:
                continue
            self._track_map[track.label()] = track
            choices.append(track.label())

        self.cmb_track.configure(values=choices)
        if not choices:
            self.app.log("这个 MIDI 里没有任何音符")
            return

        suggested = song.pick_melody()
        picked = suggested.label() if suggested and suggested.label() in choices else choices[0]
        self.var_track.set(picked)
        self._on_track_change()
        self.app.log(f"已载入 {self.source_name}：{len(song.tracks)} 条音轨，"
                     f"时长 {song.length()/1000:.1f}s")

    def open_jianpu(self) -> None:
        JianpuEditor(self.app, initial=JIANPU_TEMPLATE, on_apply=self._apply_jianpu)

    def _apply_jianpu(self, text: str) -> None:
        score = parse_jianpu(text)
        if not score.notes:
            messagebox.showwarning("简谱", "没有解析出任何音符，检查一下写法。", parent=self.app)
            return
        self.song = None
        self.source_name = "简谱"
        self._track_map = {"简谱": Track(index=0, name="简谱", channel=0, notes=score.notes)}
        self.cmb_track.configure(values=["简谱"])
        self.var_track.set("简谱")
        self.track = self._track_map["简谱"]
        self.notes = [n.copy() for n in score.notes]
        self.roll.set_notes(self.notes)
        self.roll.set_snap(self.var_snap.get())
        self._update_stats()
        for warning in score.warnings[:5]:
            self.app.log("谱面提醒：" + warning)
        self.app.log(f"简谱解析完成：{len(score.notes)} 个音，{score.bpm:.0f} BPM")

    def on_show(self) -> None:
        """切到本页时同步一下键位（用户在设置页可能改过）。"""
        self.roll.layout = self.app.layout
        self.roll._redraw()

    # ================================================================== #
    # 音轨与统计
    # ================================================================== #

    def _on_track_change(self) -> None:
        track = self._track_map.get(self.var_track.get())
        if track is None:
            return
        self.track = track
        self.notes = [n.copy() for n in track.notes]
        self.roll.set_notes(self.notes)
        self.roll.set_snap(self.var_snap.get())
        self._update_stats()
        lo, hi = track.span()
        self.app.log(f"选中音轨「{track.label()}」：{len(track.notes)} 个音，"
                     f"音域 {pitch_name(lo)} ~ {pitch_name(hi)}")

    def _on_edited(self, notes: List[Note]) -> None:
        self.notes = notes
        self._update_stats()

    def _on_select(self, indices: List[int]) -> None:
        if not indices:
            return
        note = self.notes[indices[0]]
        combo = self.app.layout.resolve(note.pitch)
        hint = combo.describe() if combo else "超音域"
        self.app.lbl_status.configure(
            text=f"选中 {len(indices)} 个音　{pitch_name(note.pitch)} @ {note.start/1000:.2f}s　按键 {hint}")

    def _update_stats(self) -> None:
        if not self.notes:
            self.lbl_stats.configure(text="还没有载入乐谱")
            return
        inrange = sum(1 for n in self.notes if self.app.layout.can_play(n.pitch))
        total = max(n.end for n in self.notes) / 1000.0
        self.lbl_stats.configure(
            text=f"{len(self.notes)} 个音，其中 {inrange} 个在口琴音域内　|　时长 {total:.1f}s"
                 f"　|　键位：{self.app.layout.key_hint(0)}…{self.app.layout.key_hint(7)}"
        )

    def auto_fit(self) -> None:
        if not self.notes:
            return
        shift = self.app.layout.transpose_for_fit([n.pitch for n in self.notes])
        self.var_transpose.set(shift)
        self.app.log(f"自动贴合：整曲移调 {shift:+d} 半音")

    # ================================================================== #
    # 播放 / 试听
    # ================================================================== #

    def play_current(self) -> None:
        if not self.notes:
            self.app.log("还没有载入乐谱")
            return
        self.app.play_notes(
            self.notes,
            name=self.source_name or "进阶模式",
            transpose=int(self.var_transpose.get() or 0),
            fold=_FOLD_CHOICES.get(self.var_fold.get(), FoldMode.FOLD),
            speed=float(self.var_speed.get() or 1.0),
        )

    def toggle_preview(self) -> None:
        if not self.notes:
            return
        if self.app.preview.play_notes(self.notes):
            self.app.log("试听中（本地合成器，不发送按键）")
        else:
            self.app.log("试听失败：系统没有可用的 MIDI 输出设备")

    def save_to_library(self) -> None:
        """把当前旋律量化成格子谱存进曲库。"""
        if not self.notes:
            self.app.log("还没有载入乐谱")
            return

        from tkinter import simpledialog
        default = os.path.splitext(self.source_name)[0] or "来自MIDI"
        name = simpledialog.askstring("存进曲库", "给这首曲子起个名字：",
                                      initialvalue=default, parent=self.app)
        if not name:
            return

        bpm = 90.0
        tunes = notes_to_tune(self.notes, name.strip(), bpm=bpm,
                              beats_per_bar=self.app.settings.beats_per_bar)
        if tunes.is_empty():
            self.app.log("量化后没有任何音符，存不了")
            return

        base = name.strip()
        index = 2
        while self.app.library.exists(tunes.name):
            tunes.name = f"{base} ({index})"
            index += 1

        try:
            self.app.library.save(tunes)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.app)
            return

        self.app.refresh_library()
        self.app.log(f"已把《{tunes.name}》量化成格子谱存进曲库"
                     f"（{tunes.bar_count} 小节，可在「曲库」页直接点播）")

    # ================================================================== #
    # 导出
    # ================================================================== #

    def _export(self, kind: str) -> None:
        from kouqin.engine import compile_notes

        base = os.path.splitext(self.source_name)[0] or "kouqin"

        if kind in ("keys", "keysmd"):
            ext, pattern = (".csv", "CSV 文件") if kind == "keys" else (".md", "Markdown 文件")
            path = filedialog.asksaveasfilename(
                title="导出键位表", initialfile=f"口琴键位表{ext}",
                filetypes=[(pattern, f"*{ext}")], parent=self.app)
            if not path:
                return
            count = (export_keytable_csv if kind == "keys" else export_keytable_md)(self.app.layout, path)
            self.app.log(f"已导出 {count} 个音高的键位对照表")
            return

        if not self.notes:
            self.app.log("还没有载入乐谱")
            return

        if kind == "midi":
            path = filedialog.asksaveasfilename(
                title="保存 MIDI", initialfile=f"{base}_修改版.mid",
                filetypes=[("MIDI 文件", "*.mid")], parent=self.app)
            if not path:
                return
            song = Song()
            song.tracks.append(Track(index=0, name=base, notes=[n.copy() for n in self.notes]))
            write_midi(song, path)
            self.app.log(f"已导出 MIDI：{os.path.basename(path)}")
            return

        events, _ = compile_notes(
            self.notes, self.app.layout, self.app.settings.timing_preset(),
            speed=float(self.var_speed.get() or 1.0),
            transpose=int(self.var_transpose.get() or 0),
            fold=_FOLD_CHOICES.get(self.var_fold.get(), FoldMode.FOLD),
        )
        if not events:
            self.app.log("编译后没有事件可导出")
            return

        specs = {
            "csv": (f"{base}_按键表.csv", "CSV 文件", "*.csv"),
            "lua": (f"{base}_罗技脚本.lua", "Lua 脚本", "*.lua"),
            "json": (f"{base}_事件流.json", "JSON 文件", "*.json"),
        }
        initial, pattern, ext = specs[kind]
        path = filedialog.asksaveasfilename(title="导出", initialfile=initial,
                                            filetypes=[(pattern, ext)], parent=self.app)
        if not path:
            return

        if kind == "csv":
            count = export_csv(events, path)
            self.app.log(f"已导出 {count} 条按键事件")
        elif kind == "lua":
            count = export_lua(events, path)
            self.app.log(f"已导出 G HUB 脚本（{count} 条事件），触发键为鼠标第 5 键")
        else:
            count = export_json(events, path, {
                "source": self.source_name,
                "speed": float(self.var_speed.get() or 1.0),
                "transpose": int(self.var_transpose.get() or 0),
            })
            self.app.log(f"已导出 {count} 条事件")
