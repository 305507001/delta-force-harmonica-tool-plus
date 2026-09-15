#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
展示页一致性检查。

「功能展示.html」和「键位自测台.html」里都有一份用 JavaScript 重写的键位算法
（为了让网页不依赖后端也能实时算按键方案）。
JS 和 Python 两份实现必须给出完全一样的结果，否则网页上展示的键位就是错的。

这个脚本把 HTML 里的 JS 抽出来交给 node 跑一遍，再和 Python 端的
``Layout.table_rows()`` 逐行比对。

依赖：node（没装会直接跳过并提示，不算失败）。

用法：
    python tools/html_crosscheck.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from kouqin.layout import Layout

#: 需要检查的网页，以及各自「键位算法段」的起止标记
TARGETS = [
    ("功能展示.html", "const SCALE=", "/* ================= 曲库"),
    ("键位自测台.html", "const SCALE =", "/* ---------------- 状态与渲染"),
]

OUTPUT_JS = """
const out=[];
Object.keys(TABLE.map).map(Number).sort((a,b)=>a-b).forEach(p=>{
  out.push(pitchName(p)+'|'+tokens(TABLE.map[p])+'|'+describe(TABLE.map[p]));
});
console.log(out.join('\\n'));
"""


def normalise(text: str) -> str:
    return text.replace(" ", "").replace("\t", "").strip()


def syntax_check(file_name: str) -> bool:
    """把整个 <script> 抽出来交给 node 做语法检查。"""
    path = os.path.join(_ROOT, file_name)
    try:
        with open(path, "r", encoding="utf-8") as fp:
            html = fp.read()
    except OSError as exc:
        print(f"  [失败] {file_name} — {exc}")
        return False

    found = re.findall(r"<script>(.*?)</script>", html, re.S)
    if not found:
        print(f"  [失败] {file_name} — 找不到 <script> 段")
        return False

    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "syntax.js")
        with open(script, "w", encoding="utf-8") as fp:
            fp.write("\n".join(found))
        result = subprocess.run(["node", "--check", script],
                                capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        first = (result.stderr or "").strip().splitlines()
        print(f"  [失败] {file_name} — JS 语法错误：{first[0] if first else '未知'}")
        return False

    total = sum(len(x) for x in found)
    print(f"  [通过] {file_name} — JS 语法正确（{total} 字符）")
    return True


def extract(file_name: str, start_mark: str, end_mark: str) -> str:
    path = os.path.join(_ROOT, file_name)
    with open(path, "r", encoding="utf-8") as fp:
        html = fp.read()
    if start_mark not in html or end_mark not in html:
        raise ValueError(f"找不到标记段（{start_mark} / {end_mark}）")
    return html[html.index(start_mark):html.index(end_mark)]


def check(file_name: str, start_mark: str, end_mark: str, expected: list) -> bool:
    try:
        js = extract(file_name, start_mark, end_mark)
    except (OSError, ValueError) as exc:
        print(f"  [失败] {file_name} — {exc}")
        return False

    # 两个网页的函数名不完全一样（自测台叫 comboTokens/comboPlan），补上别名
    js += """
if (typeof tokens === 'undefined' && typeof comboTokens !== 'undefined') { var tokens = comboTokens; }
if (typeof describe === 'undefined' && typeof comboPlan !== 'undefined') { var describe = comboPlan; }
"""
    js += OUTPUT_JS

    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "probe.js")
        with open(script, "w", encoding="utf-8") as fp:
            fp.write(js)
        result = subprocess.run(["node", script], capture_output=True,
                                text=True, encoding="utf-8", cwd=_ROOT)
    if result.returncode != 0:
        err = (result.stderr or "").strip().splitlines()
        print(f"  [失败] {file_name} — node 执行出错：{err[-1] if err else '未知'}")
        return False

    got = [normalise(line) for line in (result.stdout or "").strip().splitlines() if line.strip()]
    if got == expected:
        print(f"  [通过] {file_name} — {len(got)} 个音高，与 Python 端完全一致")
        return True

    print(f"  [失败] {file_name} — JS {len(got)} 行 / Python {len(expected)} 行")
    shown = 0
    for i in range(max(len(got), len(expected))):
        a = got[i] if i < len(got) else "<缺>"
        b = expected[i] if i < len(expected) else "<多余>"
        if a != b:
            print(f"         第 {i} 行  JS={a}   Python={b}")
            shown += 1
            if shown >= 8:
                print("         …（其余差异省略）")
                break
    return False


def check_tune_data() -> bool:
    """
    对比展示页里抄的曲库数据与 Python 端的 PRESET_TUNES。

    展示页为了不依赖后端，把 8 首曲子用简谱字符串写死在 JS 里。
    手抄的东西最容易出错（错一个小节听上去就不对了），所以这里逐首核对
    拍数、音的个数、速度和小节数。
    """
    from kouqin.tune import PRESET_TUNES

    try:
        js = extract("功能展示.html", "const SCALE=", "/* ---- 纯数据段到此结束")
    except (OSError, ValueError) as exc:
        print(f"  [失败] 曲库数据 — 取不到数据段：{exc}")
        return False

    js += """
TUNES.forEach(t=>console.log(t.n+'|'+t.cells.length+'|'+t.notes.length+'|'+t.p+'|'+t.bars));
"""

    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "tunes.js")
        with open(script, "w", encoding="utf-8") as fp:
            fp.write(js)
        result = subprocess.run(["node", script], capture_output=True,
                                text=True, encoding="utf-8", cwd=_ROOT)
    if result.returncode != 0:
        first = (result.stderr or "").strip().splitlines()
        print(f"  [失败] 曲库数据 — node 出错：{first[-1] if first else '未知'}")
        return False

    got = {}
    for line in (result.stdout or "").strip().splitlines():
        parts = line.split("|")
        if len(parts) == 5:
            got[parts[0]] = (int(parts[1]), int(parts[2]), float(parts[3]), int(parts[4]))

    ok = True
    checked = 0
    for tune in PRESET_TUNES:
        expect = (len(tune.cells), len(tune.to_notes()), float(tune.bpm), tune.bar_count)
        actual = got.get(tune.name)
        checked += 1
        if actual is None:
            print(f"  [失败] 曲库数据 — 展示页缺《{tune.name}》")
            ok = False
        elif actual != expect:
            print(f"  [失败] 曲库数据 — 《{tune.name}》"
                  f"展示页(拍{actual[0]} 音{actual[1]} 速{actual[2]:g} 节{actual[3]}) "
                  f"≠ Python(拍{expect[0]} 音{expect[1]} 速{expect[2]:g} 节{expect[3]})")
            ok = False

    if ok:
        print(f"  [通过] 曲库数据 — {checked} 首曲子的拍数/音数/速度全部对得上")
    return ok


def main() -> int:
    if shutil.which("node") is None:
        print("没有找到 node，跳过网页一致性检查（这项不是必须的）。")
        return 0

    print("网页检查：JS 语法 + 键位算法与 Python 端一致性")
    print("-" * 62)

    print("语法检查")
    ok = True
    for file_name, _, _ in TARGETS:
        ok = syntax_check(file_name) and ok

    print("\n键位算法一致性")
    layout = Layout()
    expected = [normalise(f"{name}|{tk}|{de}") for _, name, tk, de in layout.table_rows()]
    for target in TARGETS:
        ok = check(*target, expected) and ok

    print("\n曲库数据一致性")
    ok = check_tune_data() and ok

    print("-" * 62)
    print("结果：" + ("全部一致 ✔" if ok else "存在不一致 ✘"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
