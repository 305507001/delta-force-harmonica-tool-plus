@echo off
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

echo.
echo  ==============================================================
echo   口琴工坊 - 仅供个人学习、技术研究与娱乐使用
echo   严禁在任何在线对局中使用，可能违反游戏用户协议并导致封号
echo   一切后果由使用者自行承担，作者与贡献者不承担任何责任
echo   完整条款见 DISCLAIMER.md 与 LICENSE
echo  ==============================================================
echo.

echo  已获得管理员权限，正在启动...
echo.
python "口琴工坊.py" %*
if errorlevel 1 pause
