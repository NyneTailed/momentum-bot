@echo off
cd /d "%~dp0"
title Momentum-Bot (Bitget Demo-Konto)
python momentum_bot.py run --mode bitget_demo
pause
