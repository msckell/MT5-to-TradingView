@echo off
chcp 65001 >nul
title MT5 to TradingView - XAU/USD

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python first.
    pause
    exit /b
)

python -c "import pyperclip" >nul 2>&1
if errorlevel 1 (
    echo Installing pyperclip...
    pip install pyperclip
)

python -c "import MetaTrader5" >nul 2>&1
if errorlevel 1 (
    echo Installing MetaTrader5...
    pip install MetaTrader5
)

python -c "import pytz" >nul 2>&1
if errorlevel 1 (
    echo Installing pytz...
    pip install pytz
)

cd /d "%~dp0"
python mt5_to_tradingview.py
pause
