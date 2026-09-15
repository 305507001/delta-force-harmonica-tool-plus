@echo off
cd /d "%~dp0"
title 口琴工坊 - 核心自检
chcp 65001 >nul

echo 核心自检不涉及任何游戏操作，可以放心运行。
echo.

python -X utf8 "口琴工坊.py" --selftest
echo.
pause
