@echo off
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

echo.
echo  ==============================================================
echo   口琴工坊 - 仅供个人学习、技术研究与娱乐使用
echo   严禁在任何在线对局中使用，可能违反游戏用户协议并导致封号
echo   一切后果由使用者自行承担，作者与贡献者不承担任何责任
echo   完整条款见 DISCLAIMER.md 与 LICENSE
echo  ==============================================================
echo.

rem 拖拽演奏同样需要管理员权限
net session >nul 2>&1
if errorlevel 1 (
    echo 正在申请管理员权限...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%1' -Verb RunAs"
    exit /b
)

python -X utf8 "口琴工坊.py" --play "%~1" --countdown 3
echo.
pause
