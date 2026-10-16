#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在线曲库联网检查：真的去读一次公开曲库的目录，再随机下载几首解码，
核对解出来的音符数 / 时长和目录里标的是否一致。

只读，不上传任何东西；不会往系统发按键。需要能上网。

用法：
    python tools/online_check.py            # 默认抽 5 首
    python tools/online_check.py 20
"""

from __future__ import annotations

import os
import random
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from kouqin.online import DEFAULT_BASE, OnlineError, OnlineLibrary


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"在线曲库：{DEFAULT_BASE}")
    with tempfile.TemporaryDirectory() as cache:
        library = OnlineLibrary(DEFAULT_BASE, cache)
        try:
            entries = library.catalog(force=True)
        except OnlineError as exc:
            print(f"  [失败] 读目录 — {exc}")
            return 1
        print(f"  [通过] 读目录 — {len(entries)} 首，最新《{entries[0].title}》（{entries[0].updated_text}）")
        bad = 0
        for entry in random.sample(entries, min(count, len(entries))):
            try:
                _info, snapshot = library.score(entry.id)
            except OnlineError as exc:
                print(f"  [失败] 《{entry.title}》 — {exc}")
                bad += 1
                continue
            end = max((n.end for n in snapshot.notes), default=0.0)
            same = len(snapshot.notes) == entry.note_count and abs(end - entry.duration_ms) <= 2
            mark = "通过" if same else "失败"
            bad += 0 if same else 1
            print(f"  [{mark}] 《{entry.title}》 — 音数 {len(snapshot.notes)}/{entry.note_count}，"
                  f"时长 {end / 1000:.1f}/{entry.duration_ms / 1000:.1f} 秒")
    print("结果：" + ("全部通过 ✔" if not bad else f"{bad} 项失败"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
