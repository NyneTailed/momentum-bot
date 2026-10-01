@echo off
cd /d "%~dp0"
python backtest.py %*
pause
