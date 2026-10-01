@echo off
cd /d "%~dp0"
title Momentum-Bot (Simulation)
python momentum_bot.py run --mode paper
pause
