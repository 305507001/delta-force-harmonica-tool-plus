"""
导出器：把编译好的事件流输出成别的程序吃得下的格式。

三种出口：

* ``导出 CSV``     通用按键表，任何宏工具都能读（时间, 动作, 目标）
* ``导出 Lua``     罗技 G HUB 脚本，不想跑本程序时可以直接烧进鼠标
* ``导出键位表``   全音域对照表，方便对着练
"""

from __future__ import annotations

import csv
import json
from typing import Dict, Iterable, List, Sequence

from .engine import Event, EV_KEY_DOWN, EV_KEY_UP, EV_MOUSE_DOWN, EV_MOUSE_UP
from .layout import Layout, MouseMod

__all__ = ["export_csv", "export_lua", "export_keytable_csv", "export_keytable_md", "export_json"]

_EVENT_LABEL = {
    EV_KEY_DOWN: "key_down",
    EV_KEY_UP: "key_up",
    EV_MOUSE_DOWN: "mouse_down",
    EV_MOUSE_UP: "mouse_up",
}

_MOUSE_LABEL = {MouseMod.LEFT: "left", MouseMod.RIGHT: "right", MouseMod.MIDDLE: "middle"}

#: G HUB 脚本里的按键名
_LUA_KEY_NAME = {
    "z": "z", "x": "x", "c": "c", "v": "v",
    "b": "b", "n": "n", "m": "m", ",": "comma", ".": "period",
}


def export_csv(events: Sequence[Event], path: str) -> int:
    """
    导出通用按键表。返回写出的行数。

    列： ``offset_ms, action, target, pitch``
    其中 ``offset_ms`` 是相对演奏开始的毫秒偏移，单调不减。
    """
    with open(path, "w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(["offset_ms", "action", "target", "pitch"])
        for event in events:
            writer.writerow([
                f"{event.t:.3f}",
                _EVENT_LABEL.get(event.kind, event.kind),
                event.arg,
                event.pitch if event.pitch >= 0 else "",
            ])
    return len(events)


def export_lua(events: Sequence[Event], path: str, macro_button: int = 5) -> int:
    """
    导出罗技 G HUB 脚本。

    :param macro_button: 触发播放的鼠标侧键编号（默认 G5，即第 5 键）
    :returns: 写出的事件条数

    脚本在 G HUB 里以「脚本」形式加载，按提示键即可自动演奏。
    注意 G HUB 脚本运行在鼠标固件侧，不受本程序是否启动影响。
    """
    lines: List[str] = []
    lines.append("-- 由《口琴工坊》自动生成，勿手工修改")
    lines.append("-- 用法：G HUB -> 游戏与应用程序 -> 编写脚本 -> 粘贴 -> 保存")
    lines.append(f"-- 触发键：鼠标第 {macro_button} 键")
    lines.append("")
    lines.append("local SEQ = {")
    for event in events:
        target = event.arg
        if event.kind in (EV_KEY_DOWN, EV_KEY_UP):
            target = _LUA_KEY_NAME.get(event.arg, event.arg)
            token = f'"{target}"'
        else:
            token = str({"left": 1, "right": 2, "middle": 3}[target])
        lines.append(f'  {{{event.t:.0f}, "{_EVENT_LABEL[event.kind]}", {token}}},')
    lines.append("}")
    lines.append("")
    lines.append(f"local TRIGGER = {macro_button}")
    lines.append("")
    lines.append("function OnEvent(event, arg)")
    lines.append('  if event == "PROFILE_ACTIVATED" then')
    lines.append("    EnablePrimaryMouseButtonEvents(true)")
    lines.append("  end")
    lines.append('  if event == "MOUSE_BUTTON_PRESSED" and arg == TRIGGER then')
    lines.append("    local prev = 0")
    lines.append("    for i = 1, #SEQ do")
    lines.append("      local e = SEQ[i]")
    lines.append("      local wait = e[1] - prev")
    lines.append("      if wait > 0 then Sleep(wait) end")
    lines.append("      prev = e[1]")
    lines.append('      if e[2] == "key_down" then PressKey(e[3])')
    lines.append('      elseif e[2] == "key_up" then ReleaseKey(e[3])')
    lines.append('      elseif e[2] == "mouse_down" then PressMouseButton(e[3])')
    lines.append('      elseif e[2] == "mouse_up" then ReleaseMouseButton(e[3])')
    lines.append("      end")
    lines.append("    end")
    lines.append("  end")
    lines.append("end")

    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines))
    return len(events)


def export_keytable_csv(layout: Layout, path: str) -> int:
    """导出全音域键位对照表（CSV）。"""
    rows = layout.table_rows()
    with open(path, "w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(["MIDI", "音名", "紧凑键位", "按键方案"])
        writer.writerows(rows)
    return len(rows)


def export_keytable_md(layout: Layout, path: str) -> int:
    """导出全音域键位对照表（Markdown 表格），适合贴文档。"""
    rows = layout.table_rows()
    lines = ["| MIDI | 音名 | 键位 | 按键方案 |", "| --- | --- | --- | --- |"]
    for pitch, name, tokens, desc in rows:
        lines.append(f"| {pitch} | {name} | `{tokens}` | {desc} |")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines))
    return len(rows)


def export_json(events: Sequence[Event], path: str, extra: Dict = None) -> int:
    """导出结构化 JSON，便于二次开发或喂给别的脚本。"""
    payload = {
        "version": 1,
        "generator": "kouqin-studio",
        "meta": extra or {},
        "events": [
            {"t": round(e.t, 3), "kind": e.kind, "target": e.arg, "pitch": e.pitch}
            for e in events
        ],
    }
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    return len(events)
