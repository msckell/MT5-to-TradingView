# assets

Brand assets for the app. All of them are generated from vector geometry, so
they stay sharp at any size — nothing here is an upscaled bitmap.

| File | What it is |
|---|---|
| `icon.ico` | App icon. Seven frames (16–256 px), each **drawn at its own size** rather than downscaled from one raster. Used for the launcher shortcut and the app window. |
| `logo-icon.png` | The icon at 1024 px, transparent corners. |
| `logo-wide.png` | The wide four-node mark at 1608 px, transparent background. For the app header and any doc that needs the full lockup. |

## Regenerating

`tools/make_logo.py` holds the geometry and redraws everything:

```bash
python tools/make_logo.py          # icon.ico
python tools/make_logo.py --png    # icon.ico + both PNGs
```

It needs Pillow, which is a dev-time dependency only — the app itself does not
import it. After regenerating the icon, re-run `tools/install.ps1` so the
launcher shortcut picks up the new one.
