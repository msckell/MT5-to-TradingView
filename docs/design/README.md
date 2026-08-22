# design

The design canvas the Qt app (`app/gui_qt.py`) was built from — the source of truth for
its colours, type and geometry.

| File | What it is |
| --- | --- |
| `Current.dc.html` | the old Tkinter window, drawn as-is — the "before" |
| `Main.dc.html` | the app in its normal state: header, the two steps, the result card |
| `States.dc.html` | MT5 offline · no trades in range · log drawer open |
| `Tokens.dc.html` | colours, type scale and geometry — these lift straight into the code |
| `canvas.json` | how the artboards are laid out on the canvas |

These are static artboards: plain HTML meant to be *looked at*, not run. Each one
expects a `support.js` next to it that only exists inside the canvas editor, so opening
them straight in a browser renders the markup without the canvas chrome.

**The rule the palette rests on:** two greens, and they never mean the same thing. The
action green (`#3F7355`) is the button; the money green (`#4E9E6A`) is P&L and the
online dot; the logo green (`#2FDAA6`) belongs to the mark alone and never touches the
interface. Outside those three and the connection dot, the window is grey.

If you restyle the app, change it here first — then port it.
