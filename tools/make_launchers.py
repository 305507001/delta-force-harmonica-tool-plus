#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 Windows 启动脚本。

只保留 3 个，够用就行：

    启动.bat          ★ 日常就点这个（自动申请管理员权限）
    诊断启动.bat      出问题时用，保留控制台能看到报错
    拖拽演奏.bat      把 .mid 文件拖到它图标上，直接开始吹

其余用途（自检、生成示例）都并到命令行里了，不占用桌面空间：
    python 口琴工坊.py --selftest          核心自检
    python tools/gui_smoke.py              界面自检
    python tools/make_demos.py             生成示例 MIDI

为什么不直接手写 .bat 文件：
    cmd.exe 按系统 ANSI（简体中文下是 GBK）解析批处理，
    而编辑器默认存成 UTF-8，中文路径会变成乱码导致"系统找不到指定的文件"。
    所以这里统一用 Python 以 GBK + CRLF 落盘，保证双击就能用。

为什么脚本里不再有 chcp 65001（修改版改动）：
    cmd.exe 是一行一行读批处理的，每读一行都按"当前代码页"解码。
    在 GBK 文件中途切到 65001（UTF-8），后面的中文行——包括 "口琴工坊.py"
    这个文件名——就会被当成 UTF-8 解码成乱码。Python 3.6+ 往控制台输出中文
    走的是 Unicode 接口，本来就不需要 chcp 65001。
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENTRY = "口琴工坊.py"

#: 启动时打印的免责提示
NOTICE = """echo.
echo  ==============================================================
echo   口琴工坊 - 仅供个人学习、技术研究与娱乐使用
echo   严禁在任何在线对局中使用，可能违反游戏用户协议并导致封号
echo   一切后果由使用者自行承担，作者与贡献者不承担任何责任
echo   完整条款见 DISCLAIMER.md 与 LICENSE
echo  ==============================================================
echo."""

LAUNCHERS = {

    # ---------------------------------------------------------------- #
    # 主入口：自动提权，一步到位。小白只需要认这一个文件。
    # ---------------------------------------------------------------- #
    "启动.bat": f"""@echo off
cd /d "%~dp0"
title 口琴工坊

rem ---- 1. 检查 Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [错误] 没有找到 Python。
    echo   请先安装 Python 3.9 以上版本，安装时记得勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)

rem ---- 2. 检查管理员权限，没有就自动申请 ----
rem 游戏对模拟按键有权限校验，不是管理员的话游戏里会完全没反应
net session >nul 2>&1
if errorlevel 1 (
    echo 正在申请管理员权限（游戏需要）...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

{NOTICE}

rem ---- 3. 启动程序（优先用 pythonw，不留黑窗口）----
where pythonw >nul 2>nul
if errorlevel 1 (
    python "{ENTRY}" %*
) else (
    start "" pythonw "{ENTRY}" %*
)
""",

    # ---------------------------------------------------------------- #
    # 排错用：保留控制台，任何异常都看得见
    # ---------------------------------------------------------------- #
    "诊断启动.bat": f"""@echo off
cd /d "%~dp0"
title 口琴工坊 - 诊断模式

echo ============================================
echo   诊断模式（保留控制台，出错能看到原因）
echo   如果正常启动没反应，就用这个看看报什么错
echo ============================================
echo.

{NOTICE}

python -X utf8 "{ENTRY}" %*
echo.
echo 程序已退出（返回码 %errorlevel%）
echo 把上面的报错内容截图发出来即可定位问题
pause
""",

    # ---------------------------------------------------------------- #
    # 便利功能：把 MIDI 拖上来直接吹，不用开界面
    # ---------------------------------------------------------------- #
    "拖拽演奏.bat": f"""@echo off
cd /d "%~dp0"
title 拖拽演奏

if "%~1"=="" (
    echo.
    echo   用法：把 .mid 文件拖到本文件图标上，就会自动开始演奏
    echo.
    echo   如果想在界面里选曲、编辑曲子，请双击「启动.bat」
    echo.
    pause
    exit /b 0
)

{NOTICE}

rem 拖拽演奏同样需要管理员权限
net session >nul 2>&1
if errorlevel 1 (
    echo 正在申请管理员权限...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%1' -Verb RunAs"
    exit /b
)

python -X utf8 "{ENTRY}" --play "%~1" --countdown 3
echo.
pause
""",
}

#: 早期版本生成过、现在不再需要的启动脚本
OBSOLETE = (
    "以管理员启动.bat",
    "自检.bat",
    "界面自检.bat",
    "生成示例MIDI.bat",
)


def main() -> int:
    print("生成启动脚本 →", _ROOT)

    for name, content in LAUNCHERS.items():
        path = os.path.join(_ROOT, name)
        # GBK + CRLF 是 cmd.exe 最舒服的组合
        with open(path, "w", encoding="gbk", errors="replace", newline="\r\n") as fp:
            fp.write(content)
        print(f"  ✔ {name}")

    # 清掉已经合并掉的旧脚本，免得桌面上堆一堆图标
    for name in OBSOLETE:
        path = os.path.join(_ROOT, name)
        if os.path.exists(path):
            try:
                os.remove(path)
                print(f"  ✘ {name}（已合并，删除）")
            except OSError as exc:
                print(f"  ! {name} 删除失败：{exc}")

    print(f"\n完成，只保留 {len(LAUNCHERS)} 个启动脚本。"
          f"日常双击「启动.bat」即可（会自动申请管理员权限）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
