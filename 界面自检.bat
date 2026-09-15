@echo off
cd /d "%~dp0"
title 界面自检（不会真的按键）
chcp 65001 >nul

echo 正在自检界面各个页面，请勿关闭窗口...
echo 本测试使用假注入器，不会向系统发送任何按键。
echo.
python -X utf8 "tools\gui_smoke.py"
echo.
pause
