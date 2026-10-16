#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《口琴工坊》启动入口。

两种用法：

  图形界面（默认）
      python 口琴工坊.py
      python 口琴工坊.py 某首歌.mid          直接带谱启动

  命令行（不想开界面，或想写进批处理）
      python 口琴工坊.py --tracks 某首歌.mid         列出所有音轨
      python 口琴工坊.py --play 某首歌.mid           直接演奏
      python 口琴工坊.py --play 歌.mid --speed 0.8 --countdown 3
      python 口琴工坊.py --keytable                 导出全音域键位表
      python 口琴工坊.py --jitpu 谱.txt            用简谱文本演奏
      python 口琴工坊.py --selftest                 跑一遍自检

运行环境：Windows 10/11 + Python 3.9 以上，无需任何第三方库。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# 让脚本可以从任意目录启动
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from kouqin import (
    TIMING_PRESETS,
    AppSettings,
    FoldMode,
    GlobalHotkeys,
    Layout,
    LIBRARY_DIR,
    MidiError,
    Player,
    Tune,
    TuneLibrary,
    compile_notes,
    export_keytable_csv,
    export_keytable_md,
    notes_to_tune,
    parse_jianpu,
    pitch_name,
    read_midi,
)

BANNER = r"""
   ___                    _          _____           __ _
  / _ \ _   _  __ _  __ _(_)_ __    / ____|         / _(_)
 | | | | | | |/ _` |/ _` | | '_ \  | |  __  ___    | |_ _  ___  _ __
 | |_| | |_| | (_| | (_| | | | | | | | |_ |/ _ \   |  _| |/ _ \| '__|
  \__\_\\__,_|\__,_|\__,_|_|_| |_| | |__| | (_) |  | | | | (_) | |
                                    \_____|\___/    |_| |_|\___/|_|

        《口琴工坊》 —— 三角洲行动 · 口琴自动演奏工具

  ! 仅供个人学习、技术研究与娱乐使用。严禁在任何在线对局中使用。
  ! 使用自动化输入可能违反游戏用户协议并导致账号封禁，后果由使用者自负。
  ! 完整条款见 DISCLAIMER.md 与 LICENSE。
"""


# ---------------------------------------------------------------------- #
# 子命令实现
# ---------------------------------------------------------------------- #


def cmd_tracks(path: str) -> int:
    """列出音轨，并标出推荐的主旋律轨。"""
    song = read_midi(path)
    suggested = song.pick_melody()
    print(f"\n文件：{os.path.basename(path)}")
    print(f"格式：format {song.format}，{len(song.tracks)} 条音轨，时长 {song.length()/1000:.1f}s\n")
    print(f"{'序号':<6}{'音轨名':<28}{'音符':>6}{'音域':>16}  说明")
    print("-" * 78)
    for track in song.tracks:
        if not track.notes:
            continue
        lo, hi = track.span()
        span = f"{pitch_name(lo)} ~ {pitch_name(hi)}"
        tag = "  <== 推荐主旋律" if track is suggested else ""
        if track.is_drum:
            tag = "  (鼓轨，不可用于口琴)"
        print(f"{track.index:<6}{track.label():<28}{len(track.notes):>6}{span:>16}{tag}")
    print()
    return 0


def _library() -> TuneLibrary:
    """拿到曲库（空的话先把内置曲目铺进去）。"""
    library = TuneLibrary(LIBRARY_DIR)
    if not library.names():
        library.install_presets()
    return library


def cmd_tunes(args) -> int:
    """列出本地曲库。"""
    library = _library()
    names = library.names()
    print(f"\n曲库目录：{LIBRARY_DIR}\n")
    if not names:
        print("曲库是空的。")
        return 0
    print(f"{'曲名':<22}{'小节':>6}{'音符':>6}{'时长':>10}{'速度':>8}  说明")
    print("-" * 76)
    for name in names:
        tune = library.load(name)
        if tune is None:
            continue
        seconds = tune.duration_ms() / 1000.0
        print(f"{name:<22}{tune.bar_count:>6}{tune.note_count():>6}"
              f"{seconds:>9.0f}s{tune.bpm:>8.0f}  {tune.beats_per_bar} 拍/小节")
    print()
    print("演奏其中一首：python 口琴工坊.py --play-tune 曲名")
    return 0


def cmd_play_tune(args) -> int:
    """演奏曲库里的某首曲子。"""
    library = _library()
    name = args.play_tune
    tune = library.load(name)
    if tune is None:
        # 没找到就试试模糊匹配
        matches = [n for n in library.names() if name in n]
        if len(matches) == 1:
            tune = library.load(matches[0])
        elif matches:
            print(f"曲库里匹配到多首：{'、'.join(matches)}")
            return 2
        else:
            print(f"曲库里没有《{name}》。可用：{'、'.join(library.names()) or '（空）'}")
            return 2

    settings = AppSettings.load()
    layout = settings.to_layout()
    notes = tune.to_notes()
    if not notes:
        print(f"《{tune.name}》里还没有填任何音。")
        return 2

    events, report = compile_notes(
        notes, layout, settings.timing_preset(),
        speed=settings.speed, transpose=args.transpose, fold=FoldMode.FOLD,
        legato=settings.legato and not args.no_legato,
    )
    if not events:
        print("编译后没有事件，检查键位设置。")
        return 2

    print(BANNER)
    print(f"曲目      {tune.name}（{tune.bar_count} 小节，{tune.note_count()} 个音）")
    print(f"音符      {report.summary()}")
    print(f"时长      {report.duration_text()}　速度 {tune.bpm:.0f} 拍/分")
    print(f"键位      {layout.summary()}")
    print()
    print("热键      F6 开始/暂停    F8 停止")
    print()

    player = Player()
    player.load(events)
    hotkeys = GlobalHotkeys({"f6": player.toggle, "f8": player.stop})
    hotkeys.start()

    countdown = args.countdown if args.countdown is not None else settings.countdown
    if countdown > 0:
        print(f"{countdown:g} 秒后开始，请切到游戏窗口…")
    player.play(countdown_ms=countdown * 1000.0)

    try:
        while player.is_active:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n收到中断，停止演奏。")
    finally:
        player.stop()
        hotkeys.shutdown()

    print(f"\n完成。注入统计：{player.sender.stats.summary()}")
    return 0


def _pick_track(song, index: int = -1):
    """按序号或自动推荐挑一条轨。"""
    candidates = song.melody_candidates()
    if not candidates:
        return None
    if index >= 0:
        for track in song.tracks:
            if track.index == index and track.notes and not track.is_drum:
                return track
        print(f"警告：音轨 {index} 不可用，改用推荐轨。")
    return song.pick_melody()


def cmd_play(args) -> int:
    """命令行直接演奏。"""
    layout = Layout()

    if args.jianpu:
        with open(args.jianpu, "r", encoding="utf-8") as fp:
            score = parse_jianpu(fp.read(), bpm=args.bpm)
        notes = score.notes
        title = os.path.basename(args.jianpu)
        if score.warnings:
            for warning in score.warnings[:5]:
                print("提醒：" + warning)
    else:
        song = read_midi(args.play)
        track = _pick_track(song, args.track)
        if track is None:
            print("错误：文件里没有可用于口琴的音轨。")
            return 2
        notes = track.notes
        title = f"{os.path.basename(args.play)} / {track.label()}"

    if not notes:
        print("错误：没有可演奏的音符。")
        return 2

    events, report = compile_notes(
        notes, layout, TIMING_PRESETS[args.timing],
        speed=args.speed, transpose=args.transpose, fold=args.fold,
        legato=not args.no_legato,
    )
    if not events:
        print("错误：编译后没有事件。")
        return 2

    print(BANNER)
    print(f"曲目      {title}")
    print(f"音符      {report.summary()}")
    print(f"时长      {report.duration_text()}（倍速 {args.speed}，移调 {args.transpose:+d}）")
    print(f"节奏档    {TIMING_PRESETS[args.timing].label} — {TIMING_PRESETS[args.timing].describe()}")
    print(f"音域      {pitch_name(layout.lowest)} ~ {pitch_name(layout.highest)}")
    print()
    print("热键      F6 开始/暂停    F8 停止（或 Ctrl+C）")
    print()

    player = Player()
    player.load(events)

    hotkeys = GlobalHotkeys({
        "f6": player.toggle,
        "f8": player.stop,
    })
    hotkeys.start()

    countdown = args.countdown if args.countdown is not None else 3.0
    if countdown > 0:
        print(f"{countdown:g} 秒后开始，请切到游戏窗口…")
    player.play(countdown_ms=countdown * 1000.0)

    try:
        while player.is_active:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n收到中断，停止演奏。")
    finally:
        player.stop()
        hotkeys.shutdown()

    stats = player.sender.stats
    print(f"\n完成。注入统计：{stats.summary()}")
    if stats.failed:
        print("有注入失败，建议以管理员身份运行，或把节奏档换成 safe。")
    return 0


def cmd_keytable(args) -> int:
    """导出全音域键位对照表。"""
    layout = Layout()
    csv_path = args.keytable if args.keytable != "-" else "口琴键位表.csv"
    md_path = os.path.splitext(csv_path)[0] + ".md"
    rows = export_keytable_csv(layout, csv_path)
    export_keytable_md(layout, md_path)

    print(f"音域：{pitch_name(layout.lowest)} ~ {pitch_name(layout.highest)}，共 {rows} 个可演奏音高")
    print(f"已导出：{csv_path}\n        {md_path}\n")
    print(f"{'音名':<8}{'键位':<12}按键方案")
    print("-" * 44)
    for pitch, name, tokens, desc in layout.table_rows():
        if pitch % 12 == 0 or not layout.can_play(pitch - 1):
            print(f"{name:<8}{tokens:<12}{desc}")
    return 0


def cmd_selftest(args) -> int:
    """不碰游戏、不碰界面，把核心链路跑一遍，确认各模块正常。"""
    print(BANNER)
    ok = True

    def check(label: str, fn):
        nonlocal ok
        try:
            detail = fn()
            print(f"  [通过] {label}" + (f" — {detail}" if detail else ""))
        except Exception as exc:
            ok = False
            print(f"  [失败] {label} — {type(exc).__name__}: {exc}")

    print("核心自检\n" + "-" * 60)

    layout = Layout()
    check("键位表构建", lambda: f"{len(layout.playable_pitches())} 个可奏音高，"
                                f"{pitch_name(layout.lowest)} ~ {pitch_name(layout.highest)}")
    check("锚点校验 C4=Z", lambda: _assert(layout.lookup(60).key == "z", layout.lookup(60).describe()))
    check("锚点校验 C5=逗号", lambda: _assert(layout.lookup(72).key == ",", layout.lookup(72).describe()))
    check("锚点校验 C#4=中键+Z", lambda: _assert(
        layout.lookup(61).key == "z" and layout.lookup(61).mods == ("middle",),
        layout.lookup(61).describe()))
    check("锚点校验 C6=右键+逗号", lambda: _assert(
        layout.lookup(84).mods == ("right",) and layout.lookup(84).key == ",",
        layout.lookup(84).describe()))
    check("超高音折叠", lambda: f"MIDI 100 -> {pitch_name(layout.fold_pitch(100))} "
                                f"({layout.resolve(100).describe()})")
    check("超低音折叠", lambda: f"MIDI 20 -> {pitch_name(layout.fold_pitch(20))} "
                                f"({layout.resolve(20).describe()})")

    def parse_demo():
        # 1 2 3 5 6/ 5/ 3 共 7 个音，末尾的 - 是延长线不产生新音
        score = parse_jianpu("T=100\n1 2 3 5 | 6/ 5/ 3 - |")
        assert len(score.notes) == 7, f"期望 7 个音，得到 {len(score.notes)}"
        assert score.bpm == 100, f"速度应为 100，得到 {score.bpm}"
        # 延长线应作用在最后一个音上：四分音符 600ms + 600ms = 1200ms
        assert score.notes[-1].duration == 1200.0, f"延长后时长错误：{score.notes[-1].duration}"
        # 八分音符应为 300ms
        assert score.notes[4].duration == 300.0, f"八分音符时长错误：{score.notes[4].duration}"
        return f"{len(score.notes)} 个音，{score.bpm:.0f} BPM，延长线与时值正确"
    check("简谱解析", parse_demo)

    def compile_demo():
        score = parse_jianpu("1 2 3 4 5 6 7 1'")
        events, report = compile_notes(score.notes, layout, TIMING_PRESETS["normal"])
        assert events, "事件为空"
        # 事件必须单调不减
        times = [e.t for e in events]
        assert times == sorted(times), "事件时间未排序"
        # 第一个音必须是裸键
        assert report.played == 8, f"期望 8 个可奏音，得到 {report.played}"
        return f"{report.summary()}，{report.event_count} 个事件，时长 {report.duration_text()}"
    check("编译与槽位排布", compile_demo)

    def monophonic_check():
        """单声部约束：任意时刻不得有两个音键同时按下。"""
        score = parse_jianpu("1 2 3 4 5 6 7 1' 2' 3'")
        events, _ = compile_notes(score.notes, layout, TIMING_PRESETS["tight"])
        held = set()
        for event in events:
            if event.kind == "kd":
                if held:
                    raise AssertionError(f"时刻 {event.t:.1f}ms 有两个键同时按住：{held}")
                held.add(event.arg)
            elif event.kind == "ku":
                held.discard(event.arg)
        return "无重叠按键"
    check("单声部约束", monophonic_check)

    def mod_state_check():
        """修饰键不能自相矛盾（左右键同时按住）。"""
        score = parse_jianpu("1 1, 1'' 1 1, 1''")
        events, _ = compile_notes(score.notes, layout, TIMING_PRESETS["normal"])
        down = set()
        for event in events:
            if event.kind == "md":
                down.add(event.arg)
                if {"left", "right"} <= down:
                    raise AssertionError("同一时刻同时按住了左右键")
            elif event.kind == "mu":
                down.discard(event.arg)
        if down:
            raise AssertionError(f"结束时仍有修饰键没松开：{down}")
        return "修饰键差分切换正确"
    check("修饰键状态机", mod_state_check)

    def roundtrip():
        """MIDI 写出再读回，音符数和音高应保持一致。"""
        import tempfile
        from kouqin.smf import Song, Track, write_midi
        from kouqin.smf import read_midi as _read
        score = parse_jianpu("1 2 3 4 5")
        song = Song()
        song.tracks.append(Track(index=0, name="t", notes=score.notes))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rt.mid")
            write_midi(song, path)
            back = _read(path)
        got = [n.pitch for t in back.tracks for n in t.notes]
        assert got == [60, 62, 64, 65, 67], f"往返后音高不一致：{got}"
        return "5 个音往返一致"
    check("MIDI 写出/读回", roundtrip)

    def sender_check():
        from kouqin.sender import SCANCODES, Sender
        assert len(SCANCODES) >= 8
        Sender()          # 只构造，不发按键
        return f"{len(SCANCODES)} 个扫描码已就绪"
    check("输入注入模块", sender_check)

    # ---- 下面是面向"点格子"这一层的检查 ----

    def tune_model():
        from kouqin.tune import Cell, HOLD, REST, Tune
        tune = Tune(name="t", bpm=120, beats_per_bar=4,
                    cells=[Cell(0, 0), HOLD, REST, Cell(4, 0), Cell(4, 0), HOLD, HOLD, REST])
        notes = tune.to_notes()
        assert len(notes) == 3, f"期望 3 个音（延长 + 同音吹两下），得到 {len(notes)}"
        assert abs(notes[0].duration - 1000) < 0.01, f"延长后时长错误：{notes[0].duration}"
        assert abs(notes[1].start - 1500) < 0.01 and abs(notes[1].duration - 500) < 0.01, \
            f"同音第一下错误：{notes[1]}"
        assert abs(notes[2].start - 2000) < 0.01 and abs(notes[2].duration - 1500) < 0.01, \
            f"同音第二下错误：{notes[2]}"
        assert tune.note_count() == 3 and tune.bar_count == 2
        orphan = Tune(name="o", bpm=120, cells=[HOLD, REST, HOLD, Cell(1, 0)]).to_notes()
        assert len(orphan) == 1, "前面没有音可接的延长应当当休止"
        return f"{len(notes)} 个音：延长 {notes[0].duration:.0f}ms，同音连吹两下"
    check("格子谱转音符", tune_model)

    def library_roundtrip():
        import tempfile
        from kouqin.tune import PRESET_TUNES, TuneLibrary
        with tempfile.TemporaryDirectory() as tmp:
            lib = TuneLibrary(tmp)
            count = lib.install_presets()
            assert count == len(PRESET_TUNES), f"内置曲目数量不符：{count}"
            names = lib.names()
            assert len(names) == len(PRESET_TUNES), f"曲库应列出 {len(PRESET_TUNES)} 首，得到 {len(names)}"
            tune = lib.load(names[0])
            assert tune is not None and not tune.is_empty(), "读回的曲子是空的"
            tune.bpm = 111.0
            lib.save(tune)
            again = lib.load(tune.name)
            assert again is not None and abs(again.bpm - 111.0) < 0.01, "改动没有落盘"
            assert lib.delete(tune.name), "删除失败"
        return f"{count} 首内置曲目，增删改查都正常"
    check("曲库读写", library_roundtrip)

    def preset_sanity():
        from kouqin.tune import PRESET_TUNES
        bad = []
        for tune in PRESET_TUNES:
            if tune.is_empty():
                bad.append(f"{tune.name}(空)")
                continue
            if len(tune.cells) % tune.beats_per_bar != 0:
                bad.append(f"{tune.name}(不是整小节)")
            off_range = [c for c in tune.cells if c.is_note and not (48 <= c.pitch <= 85)]
            if off_range:
                bad.append(f"{tune.name}(超音域 {len(off_range)} 个)")
        assert not bad, "内置曲目有问题：" + "、".join(bad)
        return f"{len(PRESET_TUNES)} 首都是整小节且在音域内"
    check("内置曲目体检", preset_sanity)

    def quantize_check():
        from kouqin.smf import Note as MidiNote
        notes = [MidiNote(0, 500, 60), MidiNote(500, 500, 62), MidiNote(1000, 1000, 64),
                 MidiNote(2000, 500, 64)]                 # 最后两个 E 是连着吹的两下
        tune = notes_to_tune(notes, "量化测试", bpm=120, beats_per_bar=4)
        back = tune.to_notes()
        assert tune.note_count() == 4, f"期望 4 个音，得到 {tune.note_count()}"
        assert [n.pitch for n in back] == [60, 62, 64, 64], f"音高不对：{[n.pitch for n in back]}"
        assert abs(back[2].duration - 1000) < 0.01, f"长音没还原：{back[2].duration}"
        return f"{tune.bar_count} 小节，{tune.note_count()} 个音，长音和同音重复都还原了"
    check("MIDI 量化成格子谱", quantize_check)

    def custom_keymap():
        from kouqin.layout import DEFAULT_MOD_ASSIGN
        layout2 = Layout(keys=("a", "s", "d", "f", "g", "h", "j", "k"))
        assert layout2.lookup(60).key == "a", "自定义键位没生效"
        assert layout2.lookup(72).key == "k", "高音 do 应对应第 8 个键"
        # 关掉"升半音"这一档后，变化音应当不再可演奏
        layout3 = Layout(mod_assign={**DEFAULT_MOD_ASSIGN, "sharp": None})
        assert not layout3.can_play(61), "关掉升半音后 C#4 不该还能吹"
        return (f"自定义键位 {layout2.lookup(60).describe()}；"
                f"关掉升半音后音域变为 {pitch_name(layout3.lowest)} ~ {pitch_name(layout3.highest)}")
    check("自定义键位", custom_keymap)

    def settings_roundtrip():
        import tempfile
        from kouqin.settings import AppSettings
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            settings = AppSettings()
            settings.keys = list("asdfghjk")
            settings.countdown = 5.0
            settings.timing = "safe"
            settings.guide_wait = True
            settings.overlay_scale = 1.25
            settings.overlay_x, settings.overlay_y = -1600, 420      # 主屏左边的副屏
            assert settings.save(path), "写配置文件失败"
            back = AppSettings.load(path)
            assert back.keys == list("asdfghjk"), "键位没存住"
            assert abs(back.countdown - 5.0) < 0.01, "倒计时没存住"
            assert back.timing == "safe", "节奏档没存住"
            assert back.guide_wait and abs(back.overlay_scale - 1.25) < 0.01, "跟弹选项没存住"
            assert (back.overlay_x, back.overlay_y) == (-1600, 420), "悬浮窗位置没存住"
            assert back.copy().keys is not back.keys, "copy() 不能和原对象共用列表"
            assert not back.validate_keymap(), "校验器误报冲突"
            # 故意造一个冲突，校验器应当抓到
            back.keys[1] = back.keys[0]
            assert back.validate_keymap(), "键位冲突没被检出"
        return "存取正常，冲突校验有效"
    check("设置存取", settings_roundtrip)

    def legacy_format():
        from kouqin.tune import Tune
        legacy = {"version": 1, "name": "旧谱", "bpm": 120, "beats_per_bar": 4,
                  "cells": [[0, 0], [0, 0], None, [4, 0], [4, 0], [4, 0], None, None]}
        tune = Tune.from_dict(legacy)
        notes = tune.to_notes()
        assert [(n.start, n.duration, n.pitch) for n in notes] == [(0.0, 1000.0, 60), (1500.0, 1500.0, 67)], \
            f"旧版曲谱应保持「连续同音 = 长音」的听感：{notes}"
        assert tune.to_dict()["version"] == 2, "重新保存应写成新格式"
        return "旧版曲谱读进来听感不变，存回去是新格式"
    check("旧版曲谱兼容", legacy_format)

    def preset_upgrade():
        import json
        import tempfile
        from kouqin.tune import PRESET_TUNES, TuneLibrary, _LEGACY_PRESETS
        with tempfile.TemporaryDirectory() as tmp:
            lib = TuneLibrary(tmp)
            for name in ("小星星", "欢乐颂"):
                old = _LEGACY_PRESETS[name]
                cells = []
                for cell in old.cells:                      # 还原成旧版文件：延长写回成重复的音
                    cells.append(cells[-1] if cell.is_hold else cell)
                data = {"version": 1, "name": name, "bpm": old.bpm, "beats_per_bar": old.beats_per_bar,
                        "cells": [None if c.is_rest else [c.step, c.octave] for c in cells]}
                if name == "欢乐颂":
                    data["cells"][0] = [6, 0]               # 用户改过的旧副本
                with open(lib.path_of(name), "w", encoding="utf-8") as fp:
                    json.dump(data, fp, ensure_ascii=False)
            replaced = lib.upgrade_presets()
            assert replaced == ["小星星"], f"只该换掉没改过的那首：{replaced}"
            fresh = next(t for t in PRESET_TUNES if t.name == "小星星")
            assert len(lib.load("小星星").to_notes()) == len(fresh.to_notes()), "没换成新版"
            assert lib.load("欢乐颂").cells[0].step == 6, "用户改过的曲子被动了"
        return "没改过的旧版内置曲目换成新版，改过的不动"
    check("内置曲目升级", preset_upgrade)

    def legato_transitions():
        timing = TIMING_PRESETS["normal"]
        score = parse_jianpu("T=120\n1 2 2 2'")         # 换键 / 同键连按 / 换修饰键
        events, _ = compile_notes(score.notes, layout, timing, legato=True)
        downs = [e for e in events if e.kind == "kd"]
        ups = [e for e in events if e.kind == "ku"]
        gaps = [round(downs[i + 1].t - ups[i].t, 1) for i in range(len(ups) - 1)]
        assert gaps[0] == 0.0, f"换键不该留空隙：{gaps}"
        assert abs(gaps[1] - timing.gap) < 0.05, f"同键连按要留 {timing.gap:.1f}ms：{gaps}"
        assert abs(gaps[2] - timing.gap) < 0.05, f"同键（X）换修饰键也要真的松开：{gaps}"
        events, _ = compile_notes(score.notes, layout, timing, legato=False)
        downs = [e for e in events if e.kind == "kd"]
        ups = [e for e in events if e.kind == "ku"]
        apart = [round(downs[i + 1].t - ups[i].t, 1) for i in range(len(ups) - 1)]
        assert all(abs(g - timing.gap) < 0.05 for g in apart), f"关掉连奏后每两个音都要留空：{apart}"
        return f"连奏 {gaps} ms，关掉连奏 {apart} ms"
    check("音符衔接", legato_transitions)

    # ---- 跟弹 ----

    def slots_match_events():
        """悬浮窗提示用的槽位，必须和自动演奏真正按下的时刻、按键完全一致。"""
        score = parse_jianpu("1 2 3 1' 5, #4 5")
        events, report = compile_notes(score.notes, layout, TIMING_PRESETS["normal"])
        downs = [e for e in events if e.kind == "kd"]
        assert len(downs) == len(report.slots), f"{len(downs)} 个按键事件 / {len(report.slots)} 个槽位"
        for event, slot in zip(downs, report.slots):
            assert abs(event.t - slot.start) < 1e-6 and event.arg == slot.combo.key, \
                f"槽位与事件对不上：{event} / {slot}"
        return f"{len(report.slots)} 个音，提示时间与自动演奏一致"
    check("跟弹提示与自动演奏同一套时间", slots_match_events)

    def judge_check():
        from kouqin.practice import Judge, Mark
        score = parse_jianpu("T=120\n1 2' 5, #4")
        _, report = compile_notes(score.notes, layout, TIMING_PRESETS["normal"])
        s = report.slots
        judge = Judge(s)
        verdict = judge.press(s[0].combo.key, s[0].combo.mods, s[0].start + 30)
        assert verdict.kind == Mark.PERFECT, f"准点按下应为完美：{verdict}"
        verdict = judge.press(s[1].combo.key, (), s[1].start)
        assert verdict.kind == "wrong" and "鼠标" in verdict.text, f"漏按鼠标键应提示：{verdict}"
        verdict = judge.press(s[1].combo.key, s[1].combo.mods, s[1].start + 120)
        assert verdict.kind == Mark.GOOD, f"按错后改对、稍晚，应为不错：{verdict}"
        judge.sweep(s[2].start + 500)
        assert judge.marks[2] == Mark.MISS, "过了判定窗口还没按应判漏"
        assert judge.press("q", (), s[3].start - 2000) is None, "离哪个音都远的按键应忽略"
        judge.finish()
        assert (judge.hits, judge.missed) == (2, 2), f"统计不对：{judge.summary()}"
        wait = Judge(s, lenient=True)
        assert wait.press(s[0].combo.key, s[0].combo.mods, s[0].start + 150).kind == Mark.PERFECT, \
            "等待模式下按对就算完美"
        return judge.summary()
    check("跟弹判定", judge_check)

    def difficulty_check():
        from kouqin.practice import difficulty
        from kouqin.tune import PRESET_TUNES
        stars = []
        for tune in PRESET_TUNES:
            _, report = compile_notes(tune.to_notes(), layout, TIMING_PRESETS["normal"])
            value, _hint = difficulty(report.slots)
            assert 1 <= value <= 5, f"{tune.name} 难度越界：{value}"
            stars.append(value)
        fast = parse_jianpu("T=200\n1/ 1'/ 5,/ #4/ 2'/ 6,/ 3/ 7,/ " * 4).notes
        _, report = compile_notes(fast, layout, TIMING_PRESETS["normal"])
        hard, _ = difficulty(report.slots)
        assert hard > max(stars), f"快速跳八度的片段应该比内置曲目难：{hard} vs {max(stars)}"
        return f"内置曲目 {min(stars)}~{max(stars)} 星，快速跳八度片段 {hard} 星"
    check("跟弹难度估计", difficulty_check)

    print("-" * 60)
    print("自检结果：" + ("全部通过 ✔" if ok else "存在失败项 ✘"))
    return 0 if ok else 1


def _assert(condition: bool, detail: str) -> str:
    if not condition:
        raise AssertionError(f"断言失败（实际：{detail}）")
    return detail


# ---------------------------------------------------------------------- #
# 参数解析
# ---------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="口琴工坊",
        description="三角洲行动 · 口琴自动演奏工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="不带参数运行即打开图形界面。",
    )
    parser.add_argument("file", nargs="?", help="要载入的 MIDI 文件（图形界面模式下）")

    parser.add_argument("--play", metavar="MIDI", help="命令行模式：直接演奏这个 MIDI")
    parser.add_argument("--jianpu", metavar="TXT", help="命令行模式：演奏简谱文本文件")
    parser.add_argument("--bpm", type=float, default=90.0, help="简谱默认速度（默认 90）")
    parser.add_argument("--tracks", metavar="MIDI", help="列出 MIDI 里的所有音轨")
    parser.add_argument("--tunes", action="store_true", help="列出本地曲库里的曲子")
    parser.add_argument("--play-tune", metavar="曲名", help="演奏本地曲库里的某首曲子")
    parser.add_argument("--track", type=int, default=-1, help="指定音轨序号（默认自动推荐）")
    parser.add_argument("--speed", type=float, default=1.0, help="播放倍速（默认 1.0）")
    parser.add_argument("--transpose", type=int, default=0, help="移调半音数（默认 0）")
    parser.add_argument("--countdown", type=float, default=None,
                        help="开始前倒计时秒数（不填则用设置里的值）")
    parser.add_argument("--timing", choices=list(TIMING_PRESETS), default="normal",
                        help="节奏档：safe 稳健 / normal 标准 / tight 灵敏")
    parser.add_argument("--fold", choices=[FoldMode.FOLD, FoldMode.SKIP], default=FoldMode.FOLD,
                        help="超音域处理：fold 折叠（默认）/ skip 跳过")
    parser.add_argument("--no-legato", action="store_true",
                        help="每两个音之间都留一点空隙（游戏里漏音时试试）")
    parser.add_argument("--keytable", nargs="?", const="-", metavar="OUT.csv",
                        help="导出全音域键位表")
    parser.add_argument("--selftest", action="store_true", help="运行核心自检")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.selftest:
        return cmd_selftest(args)
    if args.tunes:
        return cmd_tunes(args)
    if args.play_tune:
        try:
            return cmd_play_tune(args)
        except MidiError as exc:
            print(f"错误：{exc}")
            return 2
    if args.tracks:
        return cmd_tracks(args.tracks)
    if args.keytable:
        return cmd_keytable(args)
    if args.play or args.jianpu:
        try:
            return cmd_play(args)
        except MidiError as exc:
            print(f"错误：{exc}")
            return 2

    # 默认：图形界面
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("当前 Python 没有内置 tkinter，无法启动图形界面。")
        print("可以用命令行模式：python 口琴工坊.py --play 歌曲.mid")
        return 1

    from gui.main_window import MainWindow

    window = MainWindow()
    if args.file:
        window.open_midi_file(args.file)
    window.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
