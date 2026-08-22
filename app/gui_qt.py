"""
MT5 -> TradingView (Trade.LINK) — desktop app (Qt).

The Qt front-end built from the Trade.LINK design canvas (docs/design/). It
drives the same engine as the console fallback (mt5_to_tradingview.py): pick a
week, pick a range, and the drawing prompt is built and copied to the clipboard,
ready to paste into Claude Code.

The Tkinter app (gui.py) is kept as a dependency-free fallback.

Run:
    pythonw app/gui_qt.py     (or double-click the Trade.LINK shortcut)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
ASSETS = PROJECT_ROOT / "assets"
ICON_PATH = ASSETS / "icon.ico"
LOGO_WIDE = ASSETS / "logo-wide.png"

if str(APP_DIR) not in sys.path:  # so the engine imports the same way gui.py does
    sys.path.insert(0, str(APP_DIR))


def _set_app_id() -> None:
    """Own our taskbar entry so it shows the app icon, not pythonw's."""
    try:
        from ctypes import windll

        windll.shell32.SetCurrentProcessExplicitAppUserModelID("MT5.TradingView.Bridge")
    except Exception:  # noqa: BLE001 — not Windows, or no ctypes
        pass


def _require_dependencies() -> None:
    """Fail with a visible dialog instead of dying silently under pythonw."""
    missing = [m for m in ("PySide6", "MetaTrader5", "pytz") if importlib.util.find_spec(m) is None]
    if not missing:
        return
    msg = ("Missing Python packages: " + ", ".join(missing) + "\n\n"
           "Run tools/install.ps1 (right-click -> Run with PowerShell), "
           "or install them manually:\n\n    pip install -r requirements.txt")
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("MT5 to TradingView (Trade.LINK)", msg)
    except Exception:  # noqa: BLE001 — no Tk either; last resort
        print(msg)
    sys.exit(1)


_set_app_id()
_require_dependencies()

import pytz  # noqa: E402 — imported after the dependency check

from PySide6.QtCore import (  # noqa: E402
    QByteArray, QEvent, QObject, QPoint, QSize, Qt, QThread, QTimer, Signal, Slot,
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QMenu,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

import mt5_to_tradingview as engine  # noqa: E402 — reuse the whole pipeline

# ─────────────────────────────────────────────────────────────────────────────
# Design tokens — docs/design/Tokens.dc.html. Two greens, and they never mean
# the same thing: ACTION is the button, PROFIT is money and the online dot, and
# the logo green belongs to the mark alone. Outside those, the window is grey.
# ─────────────────────────────────────────────────────────────────────────────
WINDOW = "#101010"
BAR = "#0B0B0B"
TRACK = "#151515"
CONTROL = "#1A1A1A"
CHIP = "#202020"

EDGE = "#262626"
CTRL_EDGE = "#2B2B2B"
HAIRLINE = "#1D1D1D"
DIVIDER = "#303030"
SEL_RING = "#343434"
SEL_FILL = "#242424"

T_PRIMARY = "#EDEDED"
T_BODY = "#C9C9C9"
T_SECOND = "#9A9A9A"
T_THIRD = "#767676"
T_CAPTION = "#6B6B6B"
T_DISABLED = "#4A4A4A"

ACTION = "#3F7355"
ACTION_HOVER = "#487F60"
ACTION_DOWN = "#376449"
ON_ACTION = "#F2F6F3"
PROFIT = "#4E9E6A"
LOSS = "#C0524F"
WARNING = "#B08B3E"

OFFLINE_BG = "#1B1414"
OFFLINE_EDGE = "#452B2A"
OFFLINE_FG = "#C99A97"

SANS = ["Source Sans 3", "Segoe UI", "sans-serif"]
MONO = ["Source Code Pro", "Cascadia Mono", "Consolas", "monospace"]

GUTTER = 26
SHADOW_PAD = 18  # transparent margin the drop shadow is painted into
RESIZE_EDGE = 6  # grab band for frameless resizing

# The day split lives here, in show_menu() in the engine, and in gui.py.
# Change one, change all three.
BATCH_SCOPES = (("Mon – Wed", (0, 1, 2)), ("Thu – Fri", (3, 4)))
DAY_SCOPES = (("Mon", (0,)), ("Tue", (1,)), ("Wed", (2,)), ("Thu", (3,)), ("Fri", (4,)))
LOG_COLORS = {"OK": PROFIT, "ERROR": LOSS, "WARN": WARNING, "INFO": T_THIRD}


def font(families: list[str], px: int, weight: QFont.Weight = QFont.Weight.Normal,
         spacing: float | None = None) -> QFont:
    f = QFont()
    f.setFamilies(families)
    f.setPixelSize(px)
    f.setWeight(weight)
    if spacing is not None:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


# ── SVG glyphs (the stroke colour is filled in per use) ──────────────────────
def _stroke(view_box: str, body: str, width: float = 1.3) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" fill="none" '
            f'stroke="{{c}}" stroke-width="{width}" stroke-linecap="round" '
            f'stroke-linejoin="round">{body}</svg>')


IC_MIN = _stroke("0 0 11 11", '<path d="M1 5.5 H10"/>', 1.2)
IC_MAX = _stroke("0 0 11 11", '<rect x="1.5" y="1.5" width="8" height="8"/>', 1.2)
IC_RESTORE = _stroke("0 0 11 11", '<rect x="1" y="3" width="7" height="7"/>'
                                  '<path d="M3.5 3 V1 H10 V7.5 H8"/>', 1.2)
IC_CLOSE = _stroke("0 0 11 11", '<path d="M1.5 1.5 L9.5 9.5 M9.5 1.5 L1.5 9.5"/>', 1.2)
IC_CHEVRON = _stroke("0 0 11 7", '<path d="M1 1 L5.5 5.5 L10 1"/>', 1.4)
IC_CARET_UP = _stroke("0 0 9 6", '<path d="M1 5 L4.5 1.5 L8 5"/>')
IC_CARET_DOWN = _stroke("0 0 9 6", '<path d="M1 1.5 L4.5 5 L8 1.5"/>')
IC_GENERATE = _stroke("0 0 15 15", '<path d="M3.5 11.5 L11.5 3.5"/>'
                                   '<path d="M5.5 3.5 H11.5 V9.5"/>', 1.7)
IC_REFRESH = _stroke("0 0 14 14", '<path d="M12 7 A5 5 0 1 1 10.2 3.2"/>'
                                  '<path d="M12 1.5 V4.2 H9.3"/>')
IC_TRASH = _stroke("0 0 14 14", '<path d="M2 3.5 H12"/><path d="M5.5 3.5 V2 H8.5 V3.5"/>'
                                '<path d="M3.2 3.5 L3.9 12 H10.1 L10.8 3.5"/>')
IC_CHECK = _stroke("0 0 12 12", '<path d="M2 6.4 L4.7 9 L10 3"/>', 1.4)
IC_MONITOR = _stroke("0 0 34 34", '<rect x="4" y="7" width="26" height="18" rx="2.5"/>'
                                  '<path d="M11 30 H23"/><path d="M17 25 V30"/>'
                                  '<path d="M13 13 L21 19 M21 13 L13 19"/>', 1.4)
IC_CALENDAR = _stroke("0 0 34 34", '<rect x="5" y="7" width="24" height="22" rx="3"/>'
                                   '<path d="M5 14 H29"/><path d="M11 4.5 V9"/>'
                                   '<path d="M23 4.5 V9"/><path d="M12 21.5 H22"/>', 1.4)
IC_LONG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 7 6" fill="{c}">'
           '<path d="M3.5 0 L7 6 H0 Z"/></svg>')
IC_SHORT = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 7 6" fill="{c}">'
            '<path d="M3.5 6 L0 0 H7 Z"/></svg>')


def svg_pixmap(template: str, color: str, w: int, h: int) -> QPixmap:
    """Renders a glyph at the screen pixel ratio so it stays crisp when scaled."""
    screen = QApplication.primaryScreen() if QApplication.instance() else None
    dpr = screen.devicePixelRatio() if screen else 1.0
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(template.format(c=color).encode("utf-8")))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def svg_label(template: str, color: str, w: int, h: int) -> QLabel:
    lb = QLabel()
    lb.setPixmap(svg_pixmap(template, color, w, h))
    lb.setFixedSize(w, h)
    lb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    return lb


def caption(text: str) -> QLabel:
    """The 10px mono all-caps label the design uses to title every block."""
    lb = QLabel(text.upper())
    lb.setFont(font(MONO, 10, spacing=1.2))
    lb.setStyleSheet(f"color: {T_CAPTION};")
    return lb


def hspacer() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    return w


def hline(color: str = HAIRLINE) -> QFrame:
    ln = QFrame()
    ln.setFixedHeight(1)
    ln.setStyleSheet(f"background: {color};")
    return ln


def money(value: float, with_sign: bool = True) -> str:
    """Formats a price or a P&L with a typographic minus, not a hyphen."""
    text = f"{value:+,.2f}" if with_sign else f"{value:,.2f}"
    return text.replace("-", "−")


# ─────────────────────────────────────────────────────────────────────────────
# Pieces of chrome
# ─────────────────────────────────────────────────────────────────────────────
class GlyphButton(QPushButton):
    """A flat square button holding one SVG glyph, recoloured on hover."""

    def __init__(self, glyph: str, size: QSize, color: str = T_THIRD,
                 hover_color: str = T_PRIMARY, hover_bg: str = CONTROL,
                 glyph_size: int = 10, radius: int = 0) -> None:
        super().__init__()
        self._glyph = glyph
        self._color = color
        self._hover_color = hover_color
        self._glyph_px = glyph_size
        self.setFixedSize(size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setIconSize(QSize(glyph_size, glyph_size))
        self.setIcon(QIcon(svg_pixmap(glyph, color, glyph_size, glyph_size)))
        self.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; border-radius: {radius}px; }}"
            f"QPushButton:hover {{ background: {hover_bg}; }}"
        )

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802 — Qt naming
        self.setIcon(QIcon(svg_pixmap(self._glyph, self._hover_color,
                                      self._glyph_px, self._glyph_px)))
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 — Qt naming
        self.setIcon(QIcon(svg_pixmap(self._glyph, self._color,
                                      self._glyph_px, self._glyph_px)))
        super().leaveEvent(event)


class TitleBar(QWidget):
    """Minimise / maximise / close, right-aligned in a 30px strip."""

    def __init__(self, window: QWidget) -> None:
        super().__init__()
        self._window = window
        self.setFixedHeight(30)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 4, 0)
        row.setSpacing(0)
        row.addStretch(1)

        self.btn_min = GlyphButton(IC_MIN, QSize(32, 30))
        self.btn_max = GlyphButton(IC_MAX, QSize(32, 30))
        self.btn_close = GlyphButton(IC_CLOSE, QSize(32, 30), color=T_SECOND,
                                     hover_color="#FFFFFF", hover_bg=LOSS)
        self.btn_min.clicked.connect(window.showMinimized)
        self.btn_max.clicked.connect(self._toggle_max)
        self.btn_close.clicked.connect(window.close)
        for b in (self.btn_min, self.btn_max, self.btn_close):
            row.addWidget(b)

    def _toggle_max(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.sync_max_icon()

    def sync_max_icon(self) -> None:
        glyph = IC_RESTORE if self._window.isMaximized() else IC_MAX
        self.btn_max._glyph = glyph  # noqa: SLF001 — same module, deliberate
        self.btn_max.setIcon(QIcon(svg_pixmap(glyph, T_THIRD, 10, 10)))


class StatusChip(QFrame):
    """The connection pill: a coloured dot and a short mono label."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(24)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(7)
        self.dot = QLabel()
        self.dot.setFixedSize(6, 6)
        self.text = QLabel()
        self.text.setFont(font(MONO, 11))
        row.addWidget(self.dot)
        row.addWidget(self.text)
        self.set_state(None, "Starting")

    def set_state(self, ok: bool | None, text: str) -> None:
        dot = PROFIT if ok else (LOSS if ok is False else WARNING)
        fg = T_BODY if ok is not False else OFFLINE_FG
        bg, edge = (OFFLINE_BG, OFFLINE_EDGE) if ok is False else (CONTROL, CTRL_EDGE)
        self.setStyleSheet(f"QFrame {{ background: {bg}; border: 1px solid {edge};"
                           f" border-radius: 4px; }}")
        self.dot.setStyleSheet(f"background: {dot}; border: none; border-radius: 3px;")
        self.text.setStyleSheet(f"color: {fg}; border: none;")
        self.text.setText(text)


class Segment(QPushButton):
    """One cell of a segmented control."""

    def __init__(self, text: str, width: int | None = None) -> None:
        super().__init__(text)
        self.setCheckable(True)
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(font(SANS, 13, QFont.Weight.Medium))
        if width is not None:
            self.setFixedWidth(width)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid transparent;"
            f" border-radius: 3px; color: {T_SECOND}; }}"
            f"QPushButton:hover:!checked {{ color: {T_PRIMARY}; }}"
            f"QPushButton:checked {{ background: {SEL_FILL}; border: 1px solid {SEL_RING};"
            f" color: {T_PRIMARY}; font-weight: 600; }}"
            f"QPushButton:disabled {{ color: {T_DISABLED}; }}"
        )


class SegmentGroup(QFrame):
    """An inset track holding segments; selection is exclusive across groups."""

    def __init__(self, expanding: bool = False) -> None:
        super().__init__()
        self.setStyleSheet(f"QFrame {{ background: {TRACK}; border: 1px solid {CTRL_EDGE};"
                           f" border-radius: 5px; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(3)
        self._row = row
        if expanding:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def add(self, segment: Segment) -> None:
        self._row.addWidget(segment)


class RangePicker(QWidget):
    """Batch ranges and single days — one selection shared across both tracks."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self._segments: list[tuple[Segment, str, tuple[int, ...]]] = []
        batches = SegmentGroup(expanding=True)
        for label, days in BATCH_SCOPES:
            seg = Segment(label)
            batches.add(seg)
            self._register(seg, label, days)
        days_group = SegmentGroup()
        for label, days in DAY_SCOPES:
            seg = Segment(label, width=42)
            seg.setFont(font(SANS, 12))
            days_group.add(seg)
            self._register(seg, label, days)

        # The batch track stretches, but only so far — past that the two tracks
        # stay anchored to their own edges instead of drifting apart.
        batches.setMaximumWidth(560)
        row.addWidget(batches, 1)
        row.addStretch(0)
        row.addWidget(days_group, 0)
        self._segments[0][0].setChecked(True)

    def _register(self, seg: Segment, label: str, days: tuple[int, ...]) -> None:
        self._segments.append((seg, label, days))
        seg.clicked.connect(lambda _checked=False, s=seg: self._select(s))

    def _select(self, chosen: Segment) -> None:
        for seg, _label, _days in self._segments:
            seg.setChecked(seg is chosen)
        self.changed.emit()

    def selection(self) -> tuple[str, tuple[int, ...]]:
        for seg, label, days in self._segments:
            if seg.isChecked():
                return label, days
        return self._segments[0][1], self._segments[0][2]


class WeekSelector(QFrame):
    """A 42px control row that opens the list of business weeks."""

    changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._weeks_back = 0
        self._apply_border(CTRL_EDGE)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(12)
        self.tag = QLabel("Current week")
        self.tag.setFont(font(SANS, 13, QFont.Weight.Medium))
        self.tag.setStyleSheet(f"color: {T_PRIMARY}; border: none;")
        self.dates = QLabel("—")
        self.dates.setFont(font(MONO, 12))
        self.dates.setStyleSheet(f"color: {T_SECOND}; border: none;")
        self.count = QLabel("")
        self.count.setFont(font(MONO, 11))
        self.count.setStyleSheet(f"color: {T_THIRD}; border: none;")
        row.addWidget(self.tag)
        row.addWidget(self.dates)
        row.addWidget(hspacer(), 1)
        row.addWidget(self.count)
        row.addWidget(svg_label(IC_CHEVRON, T_SECOND, 10, 6))

        self._items: list[tuple[int, str, str]] = []

    def _apply_border(self, color: str) -> None:
        self.setStyleSheet(f"QFrame {{ background: {CONTROL}; border: 1px solid {color};"
                           f" border-radius: 5px; }}")

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802 — Qt naming
        self._apply_border(SEL_RING)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 — Qt naming
        self._apply_border(CTRL_EDGE)
        super().leaveEvent(event)

    def set_weeks(self, items: list[tuple[int, str, str]]) -> None:
        self._items = items
        self._show(items[0])

    def _show(self, item: tuple[int, str, str]) -> None:
        self._weeks_back, tag, dates = item
        self.tag.setText(tag)
        self.dates.setText(dates)

    def weeks_back(self) -> int:
        return self._weeks_back

    def set_count(self, text: str) -> None:
        self.count.setText(text)

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt naming
        if event.button() != Qt.MouseButton.LeftButton or not self._items:
            return
        menu = QMenu(self)
        menu.setFont(font(SANS, 13))
        menu.setStyleSheet(
            f"QMenu {{ background: {CONTROL}; border: 1px solid {CTRL_EDGE};"
            f" border-radius: 5px; padding: 4px; color: {T_BODY}; }}"
            f"QMenu::item {{ padding: 6px 14px; border-radius: 3px; }}"
            f"QMenu::item:selected {{ background: {SEL_FILL}; color: {T_PRIMARY}; }}"
        )
        for item in self._items:
            wb, tag, dates = item
            action = menu.addAction(f"{tag}    {dates}")
            action.triggered.connect(lambda _c=False, it=item: self._pick(it))
        menu.setMinimumWidth(self.width())
        menu.exec(self.mapToGlobal(QPoint(0, self.height() + 4)))

    def _pick(self, item: tuple[int, str, str]) -> None:
        if item[0] == self._weeks_back:
            return
        self._show(item)
        self.changed.emit(self._weeks_back)


class StatTile(QWidget):
    """A caption over a value — the summary numbers on the result card."""

    def __init__(self, label: str, value_px: int, align_right: bool = False,
                 mono_weight: QFont.Weight = QFont.Weight.Normal) -> None:
        super().__init__()
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        cap = caption(label)
        self.value = QLabel("—")
        self.value.setFont(font(MONO, value_px, mono_weight))
        self.value.setStyleSheet(f"color: {T_DISABLED};")
        align = (Qt.AlignmentFlag.AlignRight if align_right else Qt.AlignmentFlag.AlignLeft)
        cap.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        # Line-height 1, bottom-aligned: that is what sits every value in the row
        # on the same line, whatever its size. Digits have no descenders to clip.
        self.value.setFixedHeight(value_px)
        self.value.setAlignment(align | Qt.AlignmentFlag.AlignBottom)
        col.addWidget(cap)
        col.addStretch(1)
        col.addWidget(self.value)
        self.setFixedHeight(46)

    def set_value(self, text: str, color: str = T_PRIMARY) -> None:
        self.value.setText(text)
        self.value.setStyleSheet(f"color: {color};")


class TradeRow(QFrame):
    """One closed trade: day, side, entry, exit, P&L."""

    COLS = (72, 96, 96, 100)  # side, entry, exit, pnl — day takes the rest

    def __init__(self, record: dict) -> None:
        super().__init__()
        self.setFixedHeight(32)
        self.setStyleSheet(f"QFrame {{ border-top: 1px solid {HAIRLINE}; }}"
                           f"QLabel {{ border: none; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        day = QWidget()
        day_row = QHBoxLayout(day)
        day_row.setContentsMargins(0, 0, 0, 0)
        day_row.setSpacing(8)
        entry_dt = record["entry_dt"]
        date_lb = QLabel(entry_dt.strftime("%a %d/%m"))
        date_lb.setFont(font(SANS, 13))
        date_lb.setStyleSheet(f"color: {T_BODY};")
        time_lb = QLabel(entry_dt.strftime("%H:%M"))
        time_lb.setFont(font(MONO, 11))
        time_lb.setStyleSheet(f"color: {T_THIRD};")
        day_row.addWidget(date_lb)
        day_row.addWidget(time_lb)
        day_row.addStretch(1)

        side = QFrame()
        side.setFixedHeight(18)
        side.setStyleSheet(f"QFrame {{ background: {CONTROL}; border-radius: 3px; }}")
        side_row = QHBoxLayout(side)
        side_row.setContentsMargins(6, 0, 6, 0)
        side_row.setSpacing(4)
        is_long = record["tipo"] == "Long"
        side_row.addWidget(svg_label(IC_LONG if is_long else IC_SHORT, T_THIRD, 7, 6))
        side_lb = QLabel("LONG" if is_long else "SHORT")
        side_lb.setFont(font(MONO, 10, spacing=0.5))
        side_lb.setStyleSheet(f"color: {T_SECOND};")
        side_row.addWidget(side_lb)
        side_cell = QWidget()
        side_cell_row = QHBoxLayout(side_cell)
        side_cell_row.setContentsMargins(0, 0, 0, 0)
        side_cell_row.addWidget(side)
        side_cell_row.addStretch(1)
        side_cell.setFixedWidth(self.COLS[0])

        pnl = record["profit"]
        row.addWidget(day, 1)
        row.addWidget(side_cell)
        row.addWidget(self._num(money(record["entry_price"], False), self.COLS[1], T_BODY))
        row.addWidget(self._num(money(record["exit_price"], False), self.COLS[2], T_BODY))
        row.addWidget(self._num(money(pnl), self.COLS[3],
                                PROFIT if pnl >= 0 else LOSS, QFont.Weight.DemiBold))

    @staticmethod
    def _num(text: str, width: int, color: str,
             weight: QFont.Weight = QFont.Weight.Normal) -> QLabel:
        lb = QLabel(text)
        lb.setFont(font(MONO, 12, weight))
        lb.setStyleSheet(f"color: {color};")
        lb.setFixedWidth(width)
        lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return lb


class EmptyState(QWidget):
    """The centred glyph-plus-copy panel used when there is nothing to show."""

    def __init__(self) -> None:
        super().__init__()
        col = QVBoxLayout(self)
        col.setContentsMargins(26, 0, 26, 0)
        col.setSpacing(12)
        col.addStretch(1)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel()
        self.title.setFont(font(SANS, 13, QFont.Weight.DemiBold))
        self.title.setStyleSheet(f"color: {T_BODY};")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body = QLabel()
        self.body.setFont(font(SANS, 12))
        self.body.setStyleSheet(f"color: {T_THIRD};")
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.setWordWrap(True)
        col.addWidget(self.icon)
        col.addWidget(self.title)
        col.addWidget(self.body)
        col.addStretch(1)

    def show_state(self, glyph: str, title: str, body: str) -> None:
        self.icon.setPixmap(svg_pixmap(glyph, T_DISABLED, 34, 34))
        self.title.setText(title)
        self.body.setText(body)


# ─────────────────────────────────────────────────────────────────────────────
# Engine worker — every MetaTrader 5 call happens on this one thread
# ─────────────────────────────────────────────────────────────────────────────
class EngineWorker(QObject):
    logged = Signal(str, str)
    connection = Signal(bool, str, str)          # ok, symbol, server
    week_loaded = Signal(int, object, object)    # weeks_back, records, monday
    cache_cleaned = Signal(bool, int, int)
    failed = Signal(str)

    def __init__(self, config: dict, user_tz) -> None:
        super().__init__()
        self.config = config
        self.user_tz = user_tz
        self.symbol = config["symbol_mt5"]
        self.server_offset = 0
        self.connected = False

    @Slot()
    def start(self) -> None:
        # The engine logs through a module-global; point it at our signal so the
        # whole pipeline ends up in the drawer instead of a dead stdout.
        engine.log = lambda level, msg: self.logged.emit(level, msg)

    @Slot()
    def connect_mt5(self) -> None:
        self.connected = False
        if not engine.connect_mt5():
            self.connection.emit(False, self.symbol, "")
            return
        server = ""
        try:
            info = engine.mt5.account_info()
            if info is not None:
                server = str(info.server)
        except Exception:  # noqa: BLE001 — cosmetic only
            pass
        try:
            engine.mt5.symbol_select(self.symbol, True)
            self.server_offset = engine.detect_server_offset(self.symbol, self.config)
        except Exception as exc:  # noqa: BLE001 — fall back to no offset
            self.logged.emit("WARN", f"offset detection: {exc}")
            self.server_offset = 0
        self.connected = True
        self.connection.emit(True, self.symbol, server)

    @Slot(int)
    def load_week(self, weeks_back: int) -> None:
        if not self.connected:
            return
        try:
            monday, friday = engine.get_business_week_range(self.user_tz, weeks_back=weeks_back)
            deals = engine.fetch_deals(self.symbol, monday, friday)
            trades_raw = engine.pair_deals_into_trades(deals) if deals else []
            if not trades_raw:
                self.week_loaded.emit(weeks_back, [], monday)
                return
            cache = engine.load_sltp_cache(self.config.get("sltp_log_path", ""))
            records = engine.build_trade_records(trades_raw, self.server_offset,
                                                 self.user_tz, self.config, sltp_cache=cache)
            week_records = engine.filter_to_business_week(records, monday, friday)
            engine.assign_indices_and_log(week_records, self.config)
            self.week_loaded.emit(weeks_back, week_records, monday)
        except Exception as exc:  # noqa: BLE001 — surface it, never die silently
            self.logged.emit("ERROR", str(exc))
            self.failed.emit(str(exc))

    @Slot()
    def clean_cache(self) -> None:
        if not self.connected:
            return
        path = self.config.get("sltp_log_path", "")
        kept, deleted = engine.clean_old_sltp_cache(path, self.user_tz, self.server_offset)
        self.cache_cleaned.emit(kept >= 0, kept, deleted)

    @Slot()
    def shutdown(self) -> None:
        try:
            engine.mt5.shutdown()
        except Exception:  # noqa: BLE001 — closing anyway
            pass


# ─────────────────────────────────────────────────────────────────────────────
# The window
# ─────────────────────────────────────────────────────────────────────────────
class MainWindow(QWidget):
    request_connect = Signal()
    request_week = Signal(int)
    request_clean = Signal()
    request_shutdown = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.config: dict | None = None
        self.user_tz = None
        self.symbol = ""
        self.connected = False
        self.records: list[dict] = []
        self.monday = None
        self._resize_edges = Qt.Edge(0)
        self._week_items: list[tuple[int, str, str]] = []

        self.setWindowTitle("MT5 to TradingView (Trade.LINK)")
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.resize(880 + SHADOW_PAD * 2, 640 + SHADOW_PAD * 2)
        self.setMinimumSize(840 + SHADOW_PAD * 2, 620 + SHADOW_PAD * 2)

        self._build_shell()
        self._boot()

    # ── shell ────────────────────────────────────────────────────────────────
    def _build_shell(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_PAD, SHADOW_PAD, SHADOW_PAD, SHADOW_PAD)
        outer.setSpacing(0)

        self.shell = QFrame()
        self.shell.setObjectName("shell")
        self.shell.setStyleSheet(
            f"#shell {{ background: {WINDOW}; border: 1px solid {EDGE}; border-radius: 8px; }}")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setXOffset(0)
        shadow.setYOffset(10)
        shadow.setColor(QColor(0, 0, 0, 204))
        self.shell.setGraphicsEffect(shadow)
        outer.addWidget(self.shell)

        col = QVBoxLayout(self.shell)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self._build_header())
        col.addWidget(self._build_steps())
        col.addWidget(self._build_result(), 1)
        col.addWidget(self._build_drawer())
        col.addWidget(self._build_status())

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("headerBar")
        header.setStyleSheet(
            f"#headerBar {{ background: {BAR}; border-bottom: 1px solid {EDGE};"
            f" border-top-left-radius: 8px; border-top-right-radius: 8px; }}")
        header.installEventFilter(self)  # drag the window by the header
        col = QVBoxLayout(header)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self.title_bar = TitleBar(self)
        col.addWidget(self.title_bar)

        brand = QWidget()
        brand.installEventFilter(self)
        row = QHBoxLayout(brand)
        row.setContentsMargins(GUTTER, 0, GUTTER, 20)
        row.setSpacing(13)

        if LOGO_WIDE.exists():
            logo = QLabel()
            pm = QPixmap(str(LOGO_WIDE))
            dpr = QApplication.primaryScreen().devicePixelRatio()
            pm = pm.scaled(int(49 * dpr), int(27 * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            logo.setPixmap(pm)
            logo.setFixedSize(49, 27)
            row.addWidget(logo)

        name_col = QVBoxLayout()
        name_col.setContentsMargins(0, 0, 0, 0)
        name_col.setSpacing(3)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(1)
        trade_lb = QLabel("Trade.")
        trade_lb.setFont(font(MONO, 16))
        trade_lb.setStyleSheet(f"color: {T_SECOND};")
        link_lb = QLabel("LINK")
        link_lb.setFont(font(SANS, 16, QFont.Weight.Bold, spacing=0.5))
        link_lb.setStyleSheet(f"color: {T_PRIMARY};")
        name_row.addWidget(trade_lb)
        name_row.addWidget(link_lb)
        name_row.addStretch(1)
        tagline = QLabel("Amplify to TradingView Dataflow")
        tagline.setFont(font(MONO, 10))
        tagline.setStyleSheet(f"color: {T_THIRD};")
        name_col.addLayout(name_row)
        name_col.addWidget(tagline)
        row.addLayout(name_col)
        row.addStretch(1)

        status_col = QVBoxLayout()
        status_col.setContentsMargins(0, 0, 0, 0)
        status_col.setSpacing(6)
        self.chip = StatusChip()
        self.account_lb = QLabel("")
        self.account_lb.setFont(font(MONO, 10))
        self.account_lb.setStyleSheet(f"color: {T_THIRD};")
        self.account_lb.setAlignment(Qt.AlignmentFlag.AlignRight)
        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.addStretch(1)
        chip_row.addWidget(self.chip)
        status_col.addLayout(chip_row)
        status_col.addWidget(self.account_lb)
        row.addLayout(status_col)

        col.addWidget(brand)
        return header

    @staticmethod
    def _step_label(number: str, text: str) -> QWidget:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        num = QLabel(number)
        num.setFont(font(MONO, 10))
        num.setStyleSheet(f"color: {T_DISABLED};")
        row.addWidget(num)
        row.addWidget(caption(text))
        row.addStretch(1)
        return wrap

    def _build_steps(self) -> QWidget:
        steps = QWidget()
        # Fixed vertically: the card below gives up height, never these controls.
        steps.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        col = QVBoxLayout(steps)
        col.setContentsMargins(GUTTER, 22, GUTTER, 20)
        col.setSpacing(16)

        week_block = QVBoxLayout()
        week_block.setSpacing(8)
        week_block.addWidget(self._step_label("01", "Pick the week"))
        self.week_selector = WeekSelector()
        self.week_selector.changed.connect(self.on_week_changed)
        week_block.addWidget(self.week_selector)
        col.addLayout(week_block)

        range_block = QVBoxLayout()
        range_block.setSpacing(8)
        range_block.addWidget(self._step_label("02", "Pick the range"))
        self.range_picker = RangePicker()
        self.range_picker.changed.connect(self.refresh_view)
        range_block.addWidget(self.range_picker)
        col.addLayout(range_block)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        # "&&" — a single & would be swallowed as a keyboard mnemonic.
        self.generate_btn = QPushButton("  Generate && copy prompt")
        self.generate_btn.setFixedHeight(46)
        self.generate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.generate_btn.setFont(font(SANS, 13, QFont.Weight.DemiBold))
        self.generate_btn.setIcon(QIcon(svg_pixmap(IC_GENERATE, ON_ACTION, 15, 15)))
        self.generate_btn.setIconSize(QSize(15, 15))
        self.generate_btn.setStyleSheet(
            f"QPushButton {{ background: {ACTION}; color: {ON_ACTION}; border: none;"
            f" border-radius: 5px; }}"
            f"QPushButton:hover {{ background: {ACTION_HOVER}; }}"
            f"QPushButton:pressed {{ background: {ACTION_DOWN}; }}"
            f"QPushButton:disabled {{ background: {TRACK}; color: {T_DISABLED};"
            f" border: 1px solid {EDGE}; }}")
        self.generate_btn.clicked.connect(self.on_generate)
        actions.addWidget(self.generate_btn, 1)

        self.reconnect_btn = self._icon_button(IC_REFRESH, "Reconnect to MetaTrader 5")
        self.reconnect_btn.clicked.connect(self.on_reconnect)
        self.clean_btn = self._icon_button(IC_TRASH, "Clean the SL/TP cache")
        self.clean_btn.clicked.connect(self.on_clean)
        actions.addWidget(self.reconnect_btn)
        actions.addWidget(self.clean_btn)
        col.addLayout(actions)
        return steps

    @staticmethod
    def _icon_button(glyph: str, tooltip: str) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(46, 46)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.setIcon(QIcon(svg_pixmap(glyph, T_SECOND, 15, 15)))
        btn.setIconSize(QSize(15, 15))
        btn.setStyleSheet(
            f"QPushButton {{ background: {CONTROL}; border: 1px solid {CTRL_EDGE};"
            f" border-radius: 5px; }}"
            f"QPushButton:hover {{ background: {CHIP}; border-color: {SEL_RING}; }}"
            f"QPushButton:disabled {{ background: {TRACK}; border-color: {EDGE}; }}")
        return btn

    def _build_result(self) -> QWidget:
        wrap = QWidget()
        wrap_row = QVBoxLayout(wrap)
        wrap_row.setContentsMargins(GUTTER, 0, GUTTER, 20)
        wrap_row.setSpacing(0)

        card = QFrame()
        card.setObjectName("card")
        card.setMinimumHeight(190)
        card.setStyleSheet(f"#card {{ background: {BAR}; border: 1px solid {EDGE};"
                           f" border-radius: 6px; }}")
        col = QVBoxLayout(card)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        stats = QWidget()
        stats_row = QHBoxLayout(stats)
        stats_row.setContentsMargins(20, 16, 20, 14)
        stats_row.setSpacing(24)
        self.net_tile = StatTile("Net result", 30, mono_weight=QFont.Weight.DemiBold)
        stats_row.addWidget(self.net_tile)
        stats_row.addStretch(1)
        self.trades_tile = StatTile("Trades", 15, align_right=True)
        self.exits_tile = StatTile("Exit lines", 15, align_right=True)
        self.range_tile = StatTile("Range", 15, align_right=True)
        for tile in (self.trades_tile, self.exits_tile, self.range_tile):
            stats_row.addWidget(tile)
        col.addWidget(stats)
        col.addWidget(hline())

        self.body_stack = QStackedWidget()
        col.addWidget(self.body_stack, 1)

        table_page = QWidget()
        table_col = QVBoxLayout(table_page)
        table_col.setContentsMargins(20, 4, 20, 0)
        table_col.setSpacing(0)

        self.table_header = QWidget()
        head_row = QHBoxLayout(self.table_header)
        head_row.setContentsMargins(0, 0, 4, 0)  # kept in step with the rows below
        head_row.setSpacing(0)
        self.table_header.setFixedHeight(26)
        day_cap = caption("Day")
        head_row.addWidget(day_cap, 1)
        for text, width in zip(("Side", "Entry", "Exit", "P&L"), TradeRow.COLS):
            cap = caption(text)
            cap.setFixedWidth(width)
            if text != "Side":
                cap.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            head_row.addWidget(cap)
        table_col.addWidget(self.table_header)

        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 4, 0)  # breathing room for the scrollbar
        self.rows_layout.setSpacing(0)
        self.rows_layout.addStretch(1)

        self.scroll = QScrollArea()
        self.scroll.setWidget(self.rows_host)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }"
            f"QScrollBar::handle:vertical {{ background: {EDGE}; border-radius: 4px;"
            " min-height: 30px; }"
            f"QScrollBar::handle:vertical:hover {{ background: {SEL_RING}; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
            " { background: transparent; }")
        table_col.addWidget(self.scroll, 1)

        self.empty_state = EmptyState()
        self.body_stack.addWidget(table_page)
        self.body_stack.addWidget(self.empty_state)

        wrap_row.addWidget(card)
        return wrap

    def _build_drawer(self) -> QWidget:
        self.drawer = QFrame()
        self.drawer.setObjectName("drawer")
        self.drawer.setStyleSheet(f"#drawer {{ background: {BAR};"
                                  f" border-top: 1px solid {CTRL_EDGE}; }}")
        col = QVBoxLayout(self.drawer)
        col.setContentsMargins(GUTTER, 9, GUTTER, 12)
        col.setSpacing(6)
        col.addWidget(caption("Log"))

        self.log_view = QScrollArea()
        self.log_view.setWidgetResizable(True)
        self.log_view.setFrameShape(QFrame.Shape.NoFrame)
        self.log_view.setFixedHeight(104)
        self.log_view.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }"
            f"QScrollBar::handle:vertical {{ background: {EDGE}; border-radius: 4px;"
            " min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
            " { background: transparent; }")
        host = QWidget()
        self.log_layout = QVBoxLayout(host)
        self.log_layout.setContentsMargins(0, 0, 10, 0)  # clear of the scrollbar
        self.log_layout.setSpacing(0)
        self.log_layout.addStretch(1)
        self.log_view.setWidget(host)
        col.addWidget(self.log_view)
        self.drawer.setVisible(False)
        return self.drawer

    def _build_status(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("statusBar")
        bar.setFixedHeight(38)
        bar.setStyleSheet(
            f"#statusBar {{ background: {BAR}; border-top: 1px solid {EDGE};"
            f" border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; }}"
            "QLabel { border: none; }")
        row = QHBoxLayout(bar)
        row.setContentsMargins(GUTTER, 0, GUTTER, 0)
        row.setSpacing(9)

        self.status_icon = QLabel()
        self.status_icon.setFixedSize(12, 12)
        self.status_text = QLabel("Starting…")
        self.status_text.setFont(font(SANS, 12))
        self.status_detail = QLabel("")
        self.status_detail.setFont(font(MONO, 11))
        self.status_detail.setStyleSheet(f"color: {T_THIRD};")
        self.status_hint = QLabel("")
        self.status_hint.setFont(font(SANS, 12))
        self.status_hint.setStyleSheet(f"color: {T_THIRD};")
        row.addWidget(self.status_icon)
        row.addWidget(self.status_text)
        row.addWidget(self.status_detail)
        row.addWidget(self.status_hint)
        row.addStretch(1)

        self.log_toggle = QPushButton("  LOG  ")
        self.log_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.log_toggle.setFont(font(MONO, 10, spacing=1.2))
        self.log_toggle.setIcon(QIcon(svg_pixmap(IC_CARET_UP, T_CAPTION, 9, 6)))
        self.log_toggle.setIconSize(QSize(9, 6))
        self.log_toggle.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.log_toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {T_CAPTION}; }}"
            f"QPushButton:hover {{ color: {T_SECOND}; }}")
        self.log_toggle.clicked.connect(self.toggle_drawer)
        row.addWidget(self.log_toggle)
        return bar

    # ── boot ─────────────────────────────────────────────────────────────────
    def _boot(self) -> None:
        engine.log = self._append_log  # until the worker takes the pipe over
        try:
            self.config = engine.load_config()
        except SystemExit:
            self.chip.set_state(False, "No config")
            self.set_status("err", "config.json missing or invalid", hint="— see the log")
            self.empty_state.show_state(IC_MONITOR, "No usable config.json",
                                        "Copy config.example.json to config.json and edit it, "
                                        "then restart the app.")
            self.body_stack.setCurrentWidget(self.empty_state)
            self._enable(self.generate_btn, IC_GENERATE, False, ON_ACTION)
            self._enable(self.clean_btn, IC_TRASH, False, T_SECOND)
            self._enable(self.reconnect_btn, IC_REFRESH, False, T_SECOND)
            self.toggle_drawer()
            return
        self.user_tz = pytz.timezone(self.config["user_timezone"])
        self.symbol = self.config["symbol_mt5"]
        self.account_lb.setText(self.symbol)
        self._populate_weeks()
        self._start_worker()

    def _populate_weeks(self) -> None:
        items: list[tuple[int, str, str]] = []
        for wb in range(0, 13):
            monday, friday = engine.get_business_week_range(self.user_tz, weeks_back=wb)
            if wb == 0:
                tag = "Current week"
            elif wb == 1:
                tag = "Last week"
            else:
                tag = f"{wb} weeks ago"
            dates = f"{monday.strftime('%d/%m')} – {friday.strftime('%d/%m/%y')}"
            items.append((wb, tag, dates))
        self._week_items = items
        self.week_selector.set_weeks(items)

    def _start_worker(self) -> None:
        self._qthread = QThread(self)
        self.worker = EngineWorker(self.config, self.user_tz)
        self.worker.moveToThread(self._qthread)
        self._qthread.started.connect(self.worker.start)
        self.worker.logged.connect(self.on_log)
        self.worker.connection.connect(self.on_connection)
        self.worker.week_loaded.connect(self.on_week_loaded)
        self.worker.cache_cleaned.connect(self.on_cache_cleaned)
        self.worker.failed.connect(self.on_failed)
        self.request_connect.connect(self.worker.connect_mt5)
        self.request_week.connect(self.worker.load_week)
        self.request_clean.connect(self.worker.clean_cache)
        self.request_shutdown.connect(self.worker.shutdown)
        self._qthread.start()
        self.empty_state.show_state(IC_MONITOR, "Connecting to MetaTrader 5…",
                                    "Reading your closed trades from the terminal.")
        self.body_stack.setCurrentWidget(self.empty_state)
        self.set_status("busy", "Connecting to MetaTrader 5…")
        self.chip.set_state(None, "Connecting")
        self._set_busy(True)
        self.request_connect.emit()

    # ── status bar & log ─────────────────────────────────────────────────────
    @staticmethod
    def _dot(color: str) -> QPixmap:
        dpr = QApplication.primaryScreen().devicePixelRatio()
        pm = QPixmap(int(12 * dpr), int(12 * dpr))
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(int(3 * dpr), int(3 * dpr), int(6 * dpr), int(6 * dpr))
        painter.end()
        pm.setDevicePixelRatio(dpr)
        return pm

    def set_status(self, kind: str, text: str, detail: str = "", hint: str = "") -> None:
        if kind == "ok":
            self.status_icon.setPixmap(svg_pixmap(IC_CHECK, T_THIRD, 12, 12))
            color = T_BODY
        else:
            dot = {"warn": WARNING, "err": LOSS, "busy": WARNING}.get(kind, T_THIRD)
            self.status_icon.setPixmap(self._dot(dot))
            color = T_SECOND if kind in ("warn", "err", "busy") else T_THIRD
        self.status_text.setStyleSheet(f"color: {color};")
        self.status_text.setText(text)
        self.status_detail.setText(detail)
        self.status_hint.setText(hint)

    @Slot(str, str)
    def on_log(self, level: str, msg: str) -> None:
        self._append_log(level, msg)

    def _append_log(self, level: str, msg: str) -> None:
        line = QLabel(f"[{level}] {msg}")
        line.setFont(font(MONO, 10))
        line.setStyleSheet(f"color: {LOG_COLORS.get(level, T_THIRD)};")
        line.setWordWrap(True)
        self.log_layout.insertWidget(self.log_layout.count() - 1, line)
        if self.drawer.isVisible():
            bar = self.log_view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def toggle_drawer(self) -> None:
        opening = not self.drawer.isVisible()
        self.drawer.setVisible(opening)
        self.log_toggle.setIcon(QIcon(svg_pixmap(
            IC_CARET_DOWN if opening else IC_CARET_UP, T_CAPTION, 9, 6)))
        # Grow the window by the drawer instead of stealing height from the card.
        if not self.isMaximized():
            delta = self.drawer.sizeHint().height()
            self.resize(self.width(), self.height() + (delta if opening else -delta))
        if opening:
            bar = self.log_view.verticalScrollBar()
            bar.setValue(bar.maximum())

    @staticmethod
    def _enable(button: QPushButton, glyph: str, enabled: bool, on_color: str) -> None:
        """Enables a button and dims its glyph with it — a bright icon on a dead
        button is the one thing the disabled state must not look like."""
        button.setEnabled(enabled)
        button.setIcon(QIcon(svg_pixmap(glyph, on_color if enabled else T_DISABLED, 15, 15)))

    def _set_busy(self, busy: bool) -> None:
        ready = not busy and self.connected
        self._enable(self.generate_btn, IC_GENERATE, ready, ON_ACTION)
        self._enable(self.clean_btn, IC_TRASH, ready, T_SECOND)
        self._enable(self.reconnect_btn, IC_REFRESH, not busy, T_SECOND)

    # ── worker callbacks ─────────────────────────────────────────────────────
    @Slot(bool, str, str)
    def on_connection(self, ok: bool, symbol: str, server: str) -> None:
        self.connected = ok
        if not ok:
            self.records = []
            self.chip.set_state(False, "MT5 offline")
            self.account_lb.setText(symbol)
            self.set_status("err", "Waiting for the terminal")
            self.empty_state.show_state(IC_MONITOR, "MetaTrader 5 isn't connected",
                                        "Open MT5 and log into your account, then hit Reconnect.")
            self.body_stack.setCurrentWidget(self.empty_state)
            self._clear_stats()
            self.week_selector.set_count("")
            self._set_busy(False)
            return
        self.chip.set_state(True, "Connected")
        self.account_lb.setText(f"{symbol} · {server}" if server else symbol)
        self.set_status("busy", "Loading the week…")
        self.request_week.emit(self.week_selector.weeks_back())

    @Slot(int, object, object)
    def on_week_loaded(self, weeks_back: int, records: list, monday) -> None:
        if weeks_back != self.week_selector.weeks_back():
            return  # a newer week was picked while this one was loading
        self.records = records or []
        self.monday = monday
        count = len(self.records)
        self.week_selector.set_count(f"{count} trade" + ("" if count == 1 else "s"))
        self._set_busy(False)
        self.refresh_view()
        if count:
            self.set_status("ok", "Week loaded", f"{count} closed trades",
                            "— pick a range and generate")
        else:
            self.set_status("warn", "Nothing closed this week")

    @Slot(bool, int, int)
    def on_cache_cleaned(self, ok: bool, kept: int, deleted: int) -> None:
        self._set_busy(False)
        if ok:
            self.set_status("ok", "SL/TP cache cleaned", f"{kept} kept · {deleted} deleted")
        else:
            self.set_status("err", "Cache cleanup failed", hint="— see the log")

    @Slot(str)
    def on_failed(self, message: str) -> None:
        self._set_busy(False)
        self.set_status("err", "Something failed", hint="— see the log")

    # ── view ─────────────────────────────────────────────────────────────────
    def _weekday_index(self, record: dict) -> int:
        return (record["entry_dt"].date() - self.monday.date()).days

    def _current_selection(self) -> tuple[str, list[dict]]:
        label, days = self.range_picker.selection()
        if not self.records or self.monday is None:
            return label, []
        return label, [r for r in self.records if self._weekday_index(r) in days]

    def _clear_stats(self) -> None:
        self.net_tile.set_value("—", T_DISABLED)
        self.trades_tile.set_value("0", T_DISABLED)
        self.exits_tile.set_value("0", T_DISABLED)
        label, _days = self.range_picker.selection()
        self.range_tile.set_value(label, T_DISABLED)

    def refresh_view(self) -> None:
        label, filtered = self._current_selection()
        self.range_tile.set_value(label)
        if not self.connected:
            return
        if not filtered:
            self._clear_stats()
            self.range_tile.set_value(label, T_DISABLED)
            self._show_empty_range(label)
            return

        net = sum(r["profit"] for r in filtered)
        exits = sum(1 for r in filtered if r["exit_line"])
        sign = "+" if net >= 0 else "−"
        self.net_tile.set_value(f"{sign}${abs(net):,.2f}", PROFIT if net >= 0 else LOSS)
        self.trades_tile.set_value(str(len(filtered)))
        self.exits_tile.set_value(str(exits))
        self._fill_rows(filtered)
        self.body_stack.setCurrentIndex(0)

    def _show_empty_range(self, label: str) -> None:
        if not self.records:
            self.empty_state.show_state(
                IC_CALENDAR, "Nothing closed this week",
                "No closed XAUUSD trades in this business week. Pick another week above.")
        else:
            names = ("Mon", "Tue", "Wed", "Thu", "Fri")
            present = sorted({self._weekday_index(r) for r in self.records})
            where = ", ".join(names[i] for i in present if 0 <= i < len(names))
            total = len(self.records)
            self.empty_state.show_state(
                IC_CALENDAR, f"No trades on {label}",
                f"This week has {total} closed trade" + ("" if total == 1 else "s")
                + f" on {where}. Switch the range or pick another week.")
        self.body_stack.setCurrentWidget(self.empty_state)

    def _fill_rows(self, records: list[dict]) -> None:
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for record in records:
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, TradeRow(record))
        self.scroll.verticalScrollBar().setValue(0)
        QTimer.singleShot(0, self._sync_header_pad)

    def _sync_header_pad(self) -> None:
        """Keeps the header captions over their columns when a scrollbar appears."""
        pad = 4 + (8 if self.scroll.verticalScrollBar().isVisible() else 0)
        self.table_header.layout().setContentsMargins(0, 0, pad, 0)

    # ── actions ──────────────────────────────────────────────────────────────
    def on_week_changed(self, weeks_back: int) -> None:
        if not self.connected:
            return
        self.records = []
        self.week_selector.set_count("")
        self._clear_stats()
        self._set_busy(True)
        self.set_status("busy", "Loading the week…")
        self.request_week.emit(weeks_back)

    def on_reconnect(self) -> None:
        self._set_busy(True)
        self.chip.set_state(None, "Connecting")
        self.set_status("busy", "Connecting to MetaTrader 5…")
        self.request_connect.emit()

    def on_clean(self) -> None:
        if not self.connected:
            return
        self._set_busy(True)
        self.set_status("busy", "Cleaning the SL/TP cache…")
        self.request_clean.emit()

    def on_generate(self) -> None:
        if not self.connected:
            self.set_status("err", "Not connected to MT5")
            return
        label, filtered = self._current_selection()
        if not filtered:
            self.set_status("warn", "Nothing to draw for this range")
            return
        try:
            prompt = engine.build_prompt(filtered, self.config)
        except Exception as exc:  # noqa: BLE001 — never take the window down
            self._append_log("ERROR", str(exc))
            self.set_status("err", "Could not build the prompt", hint="— see the log")
            return
        exits = sum(1 for r in filtered if r["exit_line"])
        self._append_log("OK", f"{label}: {len(filtered)} trades · {exits} exit line"
                               + ("" if exits == 1 else "s"))
        if engine.copy_to_clipboard(prompt):
            self.set_status("ok", "Prompt copied", f"{len(prompt):,} chars",
                            "— paste into Claude Code")
        else:
            self.set_status("warn", "Clipboard unavailable",
                            hint="— prompt saved to a file, see the log")

    # ── frameless window plumbing ────────────────────────────────────────────
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 — Qt naming
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton and not self.isMaximized():
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemMove()
                    return True
        elif event.type() == QEvent.Type.MouseButtonDblClick:
            self.title_bar._toggle_max()  # noqa: SLF001 — same module, deliberate
            return True
        return super().eventFilter(obj, event)

    def _edges_at(self, pos: QPoint) -> int:
        if self.isMaximized():
            return 0
        rect = self.shell.geometry()
        edges = 0
        if pos.x() <= rect.left() + RESIZE_EDGE:
            edges |= Qt.Edge.LeftEdge.value
        elif pos.x() >= rect.right() - RESIZE_EDGE:
            edges |= Qt.Edge.RightEdge.value
        if pos.y() <= rect.top() + RESIZE_EDGE:
            edges |= Qt.Edge.TopEdge.value
        elif pos.y() >= rect.bottom() - RESIZE_EDGE:
            edges |= Qt.Edge.BottomEdge.value
        return edges

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 — Qt naming
        edges = self._edges_at(event.position().toPoint())
        left, right = Qt.Edge.LeftEdge.value, Qt.Edge.RightEdge.value
        top, bottom = Qt.Edge.TopEdge.value, Qt.Edge.BottomEdge.value
        if edges in (left | top, right | bottom):
            cursor = Qt.CursorShape.SizeFDiagCursor
        elif edges in (right | top, left | bottom):
            cursor = Qt.CursorShape.SizeBDiagCursor
        elif edges in (left, right):
            cursor = Qt.CursorShape.SizeHorCursor
        elif edges in (top, bottom):
            cursor = Qt.CursorShape.SizeVerCursor
        else:
            cursor = Qt.CursorShape.ArrowCursor
        self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._edges_at(event.position().toPoint())
            handle = self.windowHandle()
            if edges and handle is not None:
                handle.startSystemResize(Qt.Edge(edges))
                return
        super().mousePressEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 — Qt naming
        if event.type() == QEvent.Type.WindowStateChange:
            maximized = self.isMaximized()
            pad = 0 if maximized else SHADOW_PAD
            self.layout().setContentsMargins(pad, pad, pad, pad)
            radius = 0 if maximized else 8
            self.shell.setStyleSheet(
                f"#shell {{ background: {WINDOW}; border: 1px solid {EDGE};"
                f" border-radius: {radius}px; }}")
            effect = self.shell.graphicsEffect()
            if effect is not None:
                effect.setEnabled(not maximized)
            self.title_bar.sync_max_icon()
        super().changeEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 — Qt naming
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.on_generate()
        elif event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt naming
        thread = getattr(self, "_qthread", None)
        if thread is not None:
            self.request_shutdown.emit()
            thread.quit()
            thread.wait(3000)
        super().closeEvent(event)


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("MT5 to TradingView (Trade.LINK)")
    app.setApplicationDisplayName("MT5 to TradingView (Trade.LINK)")
    app.setFont(font(SANS, 13))
    app.setStyleSheet(f"QToolTip {{ background: {CONTROL}; color: {T_BODY};"
                      f" border: 1px solid {CTRL_EDGE}; padding: 4px 7px; }}")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
