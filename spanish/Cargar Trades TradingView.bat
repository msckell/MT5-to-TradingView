@echo off
chcp 65001 >nul
title MT5 a TradingView - XAU/USD

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado. Instala Python primero.
    pause
    exit /b
)

python -c "import pyperclip" >nul 2>&1
if errorlevel 1 (
    echo Instalando pyperclip...
    pip install pyperclip
)

python -c "import MetaTrader5" >nul 2>&1
if errorlevel 1 (
    echo Instalando MetaTrader5...
    pip install MetaTrader5
)

python -c "import pytz" >nul 2>&1
if errorlevel 1 (
    echo Instalando pytz...
    pip install pytz
)

cd /d "%~dp0"
python mt5_to_tradingview.py
pause
