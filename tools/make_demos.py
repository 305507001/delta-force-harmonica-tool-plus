#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
示例素材生成器。

把内置曲库里的曲子额外导出成标准 MIDI 文件，放在「示例MIDI」目录。

为什么还要导出 MIDI：
    曲库里的 `.json` 是本程序自己的格式，别的软件不认。
    导出成 `.mid` 之后，你可以丢进任何音乐软件里听、改、加伴奏，
    也可以拿去当「进阶」页的导入测试素材。

用法：
    python tools/make_demos.py
"""

from __future__ import annotations

import os
import json
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from kouqin.exporter import export_keytable_md
from kouqin.layout import Layout
from kouqin.smf import Song, Track, pitch_name, write_midi
from kouqin.tune import PRESET_TUNES

OUT_DIR = os.path.join(_ROOT, "示例MIDI")
TUNE_DIR = os.path.join(_ROOT, "示例曲谱")


def main() -> int:
    print(f"生成示例 MIDI → {OUT_DIR}")
    os.makedirs(OUT_DIR, exist_ok=True)

    layout = Layout()
    total_notes = 0

    for tune in PRESET_TUNES:
        notes = tune.to_notes()
        if not notes:
            print(f"  ✘ {tune.name}：没有音符")
            continue

        song = Song()
        song.tracks.append(Track(index=0, name=tune.name, channel=0, notes=notes))
        path = os.path.join(OUT_DIR, f"{tune.name}.mid")
        write_midi(song, path, tempo_bpm=tune.bpm)

        # 同时刷一份格子谱 JSON（曲库页「导入曲谱」用的就是这个格式）
        os.makedirs(TUNE_DIR, exist_ok=True)
        with open(os.path.join(TUNE_DIR, f"{tune.name}.json"), "w", encoding="utf-8") as fp:
            json.dump(tune.to_dict(), fp, ensure_ascii=False, indent=1)

        pitches = [n.pitch for n in notes]
        lo, hi = min(pitches), max(pitches)
        inrange = sum(1 for p in pitches if layout.can_play(p))
        total_notes += len(notes)
        flag = "" if inrange == len(pitches) else f"  ({len(pitches) - inrange} 个超音域，演奏时会折叠)"
        print(f"  ✔ {tune.name}：{len(notes)} 个音，"
              f"{pitch_name(lo)} ~ {pitch_name(hi)}，{tune.bpm:.0f} 拍/分{flag}")

    # 顺手刷一份键位对照表
    table_path = os.path.join(_ROOT, "键位对照表.md")
    rows = export_keytable_md(layout, table_path)
    print(f"\n键位对照表：{rows} 个音高 → {os.path.basename(table_path)}")
    print(f"完成，共 {len(PRESET_TUNES)} 首 / {total_notes} 个音符。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
