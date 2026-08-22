<#
.SYNOPSIS
    Sets up MT5 -> TradingView on this machine.

.DESCRIPTION
    1. Verifies Python is installed.
    2. Installs the Python dependencies from requirements.txt.
    3. Creates config.json from config.example.json if it is missing.
    4. Creates the "MT5 to TradingView" shortcut (project root, and optionally
       the Desktop) pointing at the GUI, with assets/icon.ico as its icon.

    Run it once after cloning, and again after dropping a new icon.ico in.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install.ps1
    powershell -ExecutionPolicy Bypass -File tools\install.ps1 -Desktop
#>
[CmdletBinding()]
param(
    # Also drop a copy of the shortcut on the Desktop.
    [switch]$Desktop,
    # Skip the pip install step.
    [switch]$SkipDeps
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$guiPath = Join-Path $root 'app\gui_qt.py'
$iconPath = Join-Path $root 'assets\icon.ico'
$linkName = 'MT5 to TradingView (Trade.LINK).lnk'
$legacyLink = 'MT5 to TradingView.lnk'

Write-Host ''
Write-Host 'MT5 -> TradingView - setup' -ForegroundColor Cyan
Write-Host ("Project: {0}" -f $root)
Write-Host ''

# -- 1. Python ----------------------------------------------------------------
$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = (Get-Command py.exe -ErrorAction SilentlyContinue).Source }
if (-not $python) {
    Write-Host 'ERROR: Python not found in PATH. Install Python 3.10+ from python.org' -ForegroundColor Red
    Write-Host '       (tick "Add python.exe to PATH" during setup), then run this script again.'
    exit 1
}
$pyVersion = & $python --version
Write-Host ("[OK] {0}  ({1})" -f $pyVersion, $python) -ForegroundColor Green

# pythonw.exe runs the GUI without a console window behind it.
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }

# -- 2. Dependencies ----------------------------------------------------------
if ($SkipDeps) {
    Write-Host '[INFO] Skipping dependency install (-SkipDeps).'
} else {
    Write-Host '[..] Installing dependencies from requirements.txt'
    & $python -m pip install --disable-pip-version-check -q -r (Join-Path $root 'requirements.txt')
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'ERROR: pip install failed. See the output above.' -ForegroundColor Red
        exit 1
    }
    Write-Host '[OK] Dependencies installed.' -ForegroundColor Green
}

# -- 3. config.json -----------------------------------------------------------
$config = Join-Path $root 'config.json'
$example = Join-Path $root 'config.example.json'
if (Test-Path $config) {
    Write-Host '[OK] config.json already present.' -ForegroundColor Green
} else {
    Copy-Item $example $config
    Write-Host '[!!] config.json created from config.example.json - edit it before first run' -ForegroundColor Yellow
    Write-Host '     (symbol, timezone, and the sltp_log_path of your MT5 Common\Files folder).'
}

# -- 4. Shortcut --------------------------------------------------------------
function New-AppShortcut([string]$Destination) {
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($Destination)
    $sc.TargetPath = $pythonw
    $sc.Arguments = '"{0}"' -f $guiPath
    $sc.WorkingDirectory = $root
    $sc.Description = 'MT5 to TradingView (Trade.LINK) - build the weekly drawing prompt'
    if (Test-Path $iconPath) { $sc.IconLocation = $iconPath }
    $sc.Save()
}

# drop the pre-rename shortcut so only one launcher is left behind
foreach ($dir in @($root, [Environment]::GetFolderPath('Desktop'))) {
    $old = Join-Path $dir $legacyLink
    if (Test-Path $old) { Remove-Item $old -Force; Write-Host ("[OK] Removed the old shortcut in " + $dir) -ForegroundColor Green }
}

$rootLink = Join-Path $root $linkName
New-AppShortcut $rootLink
if (Test-Path $iconPath) {
    Write-Host ("[OK] Shortcut created with custom icon: {0}" -f $linkName) -ForegroundColor Green
} else {
    Write-Host ("[OK] Shortcut created: {0}" -f $linkName) -ForegroundColor Green
    Write-Host '     No assets\icon.ico found - drop one in and re-run this script to apply it.' -ForegroundColor Yellow
}

if ($Desktop) {
    New-AppShortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) $linkName)
    Write-Host '[OK] Shortcut also placed on the Desktop.' -ForegroundColor Green
}

Write-Host ''
Write-Host 'Done. Open MetaTrader 5, log in, then double-click the shortcut.' -ForegroundColor Cyan
Write-Host ''
