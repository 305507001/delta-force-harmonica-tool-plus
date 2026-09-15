@echo off
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

echo.
echo  ==============================================================
echo   口琴工坊 - 仅供个人学习、技术研究与娱乐使用
echo   严禁在任何在线对局中使用，可能违反游戏用户协议并导致封号
echo   一切后果由使用者自行承担，作者与贡献者不承担任何责任
echo   完整条款见 DISCLAIMER.md 与 LICENSE
echo  ==============================================================
echo.

python -X utf8 "口琴工坊.py" --play "%~1" --countdown 3 --timing normal
echo.
pause
