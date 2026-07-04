@echo off
chcp 65001 >nul

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python first.
    pause
    exit /b
)

python -c "import pyperclip" >nul 2>&1 || pip install pyperclip >nul 2>&1
python -c "import MetaTrader5" >nul 2>&1 || pip install MetaTrader5 >nul 2>&1
python -c "import pytz" >nul 2>&1 || pip install pytz >nul 2>&1

cd /d "%~dp0"
rem Launch windowless (pythonw) and detached (start) so no empty console lingers.
start "" pythonw gui.py
