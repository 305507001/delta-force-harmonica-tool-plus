#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 Windows 启动脚本。

为什么不直接手写 .bat 文件：
    cmd.exe 按系统 ANSI（简体中文下是 GBK）解析批处理，
    而编辑器默认存成 UTF-8，中文路径会变成乱码导致"系统找不到指定的文件"。
    所以这里统一用 Python 以 GBK + CRLF 落盘，保证双击就能用。

用法：
    python tools/make_launchers.py
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENTRY = "口琴工坊.py"

#: 每个脚本开头都会打印的免责提示（批处理里 echo 用）
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
    "启动.bat": f"""@echo off
cd /d "%~dp0"
title 口琴工坊

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [错误] 没有找到 Python。
    echo   请先安装 Python 3.9 以上版本，安装时记得勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)

{NOTICE}

rem 优先用 pythonw 启动，不留黑窗口
where pythonw >nul 2>nul
if errorlevel 1 (
    python "{ENTRY}" %*
) else (
    start "" pythonw "{ENTRY}" %*
)
""",

    # ---------------------------------------------------------------- #
    "以管理员启动.bat": f"""@echo off
cd /d "%~dp0"

rem 向游戏注入输入需要管理员权限，否则游戏里会完全没反应
net session >nul 2>&1
if errorlevel 1 (
    echo 正在申请管理员权限...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

title 口琴工坊（管理员）
chcp 65001 >nul

{NOTICE}

echo  已获得管理员权限，正在启动...
echo.
python "{ENTRY}" %*
if errorlevel 1 pause
""",

    # ---------------------------------------------------------------- #
    "诊断启动.bat": f"""@echo off
cd /d "%~dp0"
title 口琴工坊 - 诊断模式
chcp 65001 >nul

echo ============================================
echo   诊断模式：保留控制台，可以看到报错信息
echo ============================================
echo.

{NOTICE}

python -X utf8 "{ENTRY}" %*
echo.
echo 程序已退出（返回码 %errorlevel%）
pause
""",

    # ---------------------------------------------------------------- #
    "自检.bat": f"""@echo off
cd /d "%~dp0"
title 口琴工坊 - 核心自检
chcp 65001 >nul

echo 核心自检不涉及任何游戏操作，可以放心运行。
echo.

python -X utf8 "{ENTRY}" --selftest
echo.
pause
""",

    # ---------------------------------------------------------------- #
    "界面自检.bat": f"""@echo off
cd /d "%~dp0"
title 界面自检（不会真的按键）
chcp 65001 >nul

echo 正在自检界面各个页面，请勿关闭窗口...
echo 本测试使用假注入器，不会向系统发送任何按键。
echo.
python -X utf8 "tools\\gui_smoke.py"
echo.
pause
""",

    # ---------------------------------------------------------------- #
    "拖拽演奏.bat": f"""@echo off
cd /d "%~dp0"
title 拖拽演奏
chcp 65001 >nul

if "%~1"=="" (
    echo.
    echo   用法：把 .mid 文件拖到本文件图标上即可自动演奏
    echo   也可以先用「启动.bat」在界面里选曲
    echo.
    pause
    exit /b 0
)

{NOTICE}

python -X utf8 "{ENTRY}" --play "%~1" --countdown 3 --timing normal
echo.
pause
""",

    # ---------------------------------------------------------------- #
    "生成示例MIDI.bat": f"""@echo off
cd /d "%~dp0"
title 生成示例 MIDI
chcp 65001 >nul

python -X utf8 "tools\\make_demos.py"
echo.
pause
""",
}


def main() -> int:
    print("生成启动脚本 →", _ROOT)
    for name, content in LAUNCHERS.items():
        path = os.path.join(_ROOT, name)
        # GBK + CRLF 是 cmd.exe 最舒服的组合
        with open(path, "w", encoding="gbk", errors="replace", newline="\r\n") as fp:
            fp.write(content)
        print(f"  ✔ {name}")

    print(f"\n完成，共 {len(LAUNCHERS)} 个脚本。双击「以管理员启动.bat」即可运行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
