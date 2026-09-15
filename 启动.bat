@echo off
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

echo.
echo  ==============================================================
echo   口琴工坊 - 仅供个人学习、技术研究与娱乐使用
echo   严禁在任何在线对局中使用，可能违反游戏用户协议并导致封号
echo   一切后果由使用者自行承担，作者与贡献者不承担任何责任
echo   完整条款见 DISCLAIMER.md 与 LICENSE
echo  ==============================================================
echo.

rem 优先用 pythonw 启动，不留黑窗口
where pythonw >nul 2>nul
if errorlevel 1 (
    python "口琴工坊.py" %*
) else (
    start "" pythonw "口琴工坊.py" %*
)
