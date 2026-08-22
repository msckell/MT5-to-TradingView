# assets

Drop the app icon here as `icon.ico` (multi-size .ico: 16/32/48/256 px).

It is picked up in two places:

- `tools/install.ps1` sets it as the icon of the "MT5 to TradingView" shortcut.
- `app/gui.py` uses it as the window / taskbar icon.

Both degrade gracefully if the file is absent. After adding or replacing the
icon, re-run `tools/install.ps1` so the shortcut picks up the change.
