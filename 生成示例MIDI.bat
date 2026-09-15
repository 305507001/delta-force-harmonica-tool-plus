@echo off
cd /d "%~dp0"
title Éú³ÉÊ¾Àý MIDI
chcp 65001 >nul

python -X utf8 "tools\make_demos.py"
echo.
pause
