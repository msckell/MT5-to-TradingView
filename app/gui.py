"""
MT5 -> TradingView — desktop app (main entry point).

A Tkinter front-end over the same engine as the console fallback
(mt5_to_tradingview.py). No extra dependencies — Tkinter ships with Python.
Pick a week and a day range, click Generate, and the drawing prompt is built
and copied to your clipboard, ready to paste into Claude Code.

Run:
    pythonw app/gui.py        (or double-click the "MT5 to TradingView" shortcut)
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
ICON_PATH = PROJECT_ROOT / "assets" / "icon.ico"


def _enable_dpi_awareness() -> None:
    """Ask Windows to render the app at native DPI (no blurry upscaling)."""
    try:
        from ctypes import windll

        try:
            windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
        except Exception:  # noqa: BLE001 — pre-8.1 fallback
            windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 — not Windows, or no ctypes
        pass


def _set_app_id() -> None:
    """Own our taskbar entry so it shows the app icon, not pythonw's."""
    try:
        from ctypes import windll

        windll.shell32.SetCurrentProcessExplicitAppUserModelID("MT5.TradingView.Bridge")
    except Exception:  # noqa: BLE001 — not Windows, or no ctypes
        pass


def _require_dependencies() -> None:
    """Fail with a visible dialog instead of dying silently under pythonw."""
    missing = [m for m in ("MetaTrader5", "pytz") if importlib.util.find_spec(m) is None]
    if not missing:
        return
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "MT5 -> TradingView",
        "Missing Python packages: " + ", ".join(missing) + "\n\n"
        "Run tools/install.ps1 (right-click -> Run with PowerShell), "
        "or install them manually:\n\n    pip install -r requirements.txt",
    )
    sys.exit(1)


_enable_dpi_awareness()
_set_app_id()
_require_dependencies()

import pytz  # noqa: E402 — imported after the dependency check

import mt5_to_tradingview as engine  # noqa: E402 — reuse the whole pipeline

# ── dark palette (keeps the terminal aura) ───────────────────────────────────
BG = "#0d1117"
PANEL = "#161b22"
BORDER = "#21262d"
FG = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#238636"
GREEN = "#2ea043"
RED = "#da3633"
AMBER = "#d29922"
MONO = ("Cascadia Mono", 9)

SCOPE_BATCHES = ("Mon + Tue + Wed", "Thu + Fri")
SCOPE_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")


class MT5GUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.connected = False
        self.server_offset = 0
        self.config: dict | None = None
        self.user_tz = None
        self.symbol = ""
        self._week_map: dict[str, int] = {}
        self._flash_job: str | None = None

        # Route the engine's logging into our status pane (engine calls the
        # module-global `log`, so reassigning it captures the whole pipeline).
        engine.log = self._engine_log

        root.title("MT5 -> TradingView")
        root.configure(bg=BG)
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        root.geometry(f"{int(760 * self.scale)}x{int(720 * self.scale)}")
        root.minsize(int(640 * self.scale), int(600 * self.scale))
        if ICON_PATH.exists():
            try:
                root.iconbitmap(default=str(ICON_PATH))
            except Exception:  # noqa: BLE001 — a bad/locked .ico shouldn't block the app
                pass

        self._init_style()
        self._build_ui()
        self._center()
        root.bind("<Return>", lambda _e: self.on_generate())
        root.bind("<Escape>", lambda _e: self.on_close())
        self._boot()

    # ── style ────────────────────────────────────────────────────────────────
    def _init_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Dark.TCombobox", fieldbackground=PANEL, background=PANEL,
                        foreground=FG, arrowcolor=MUTED, bordercolor=BORDER,
                        lightcolor=PANEL, darkcolor=PANEL,
                        selectbackground=PANEL, selectforeground=FG)
        style.map("Dark.TCombobox", fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", FG)], selectbackground=[("readonly", PANEL)])
        style.configure("Dark.Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                        bordercolor=BORDER, borderwidth=0, rowheight=int(22 * self.scale),
                        font=("Segoe UI", 9))
        style.configure("Dark.Treeview.Heading", background=BORDER, foreground=MUTED,
                        relief="flat", font=("Segoe UI Semibold", 9))
        style.map("Dark.Treeview.Heading", background=[("active", BORDER)])
        style.map("Dark.Treeview", background=[("selected", "#1f6feb")],
                  foreground=[("selected", "white")])
        style.configure("Dark.Vertical.TScrollbar", background=BORDER, troughcolor=BG,
                        bordercolor=BG, arrowcolor=MUTED)

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(header, text="MT5  →  TradingView", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 15)).pack(side="left")

        pill = tk.Frame(header, bg=PANEL, padx=8, pady=3)
        pill.pack(side="right")
        self.pill_dot = tk.Label(pill, text="●", bg=PANEL, fg=MUTED, font=("Segoe UI", 9))
        self.pill_dot.pack(side="left")
        self.pill_text = tk.Label(pill, text="Starting…", bg=PANEL, fg=MUTED,
                                  font=("Segoe UI", 9), padx=4)
        self.pill_text.pack(side="left")

        tk.Label(self.root, text="Build the weekly drawing prompt and copy it to the clipboard.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 12))

        controls = tk.Frame(self.root, bg=BG)
        controls.pack(fill="x", padx=16)
        tk.Label(controls, text="Week", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        self.week_var = tk.StringVar()
        self.week_combo = ttk.Combobox(controls, textvariable=self.week_var, state="readonly",
                                       width=36, style="Dark.TCombobox")
        self.week_combo.grid(row=1, column=0, sticky="w", pady=(2, 8))
        # Range — grouped radios (batch upload on top, single days below).
        range_col = tk.Frame(controls, bg=BG)
        range_col.grid(row=0, column=1, rowspan=2, sticky="nw", padx=(18, 0))
        self.scope_var = tk.StringVar(value=SCOPE_BATCHES[0])

        def _mk_radio(parent, text):
            return tk.Radiobutton(
                parent, text=text, value=text, variable=self.scope_var,
                bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                activeforeground=FG, font=("Segoe UI", 9), anchor="w",
                highlightthickness=0, bd=0, cursor="hand2",
            )

        wk = tk.LabelFrame(range_col, text=" Whole week upload ", bg=BG, fg=MUTED,
                           font=("Segoe UI", 8), labelanchor="nw", bd=1, relief="groove")
        wk.pack(anchor="w", fill="x")
        for _t in SCOPE_BATCHES:
            _mk_radio(wk, _t).pack(anchor="w", padx=6, pady=1)

        tk.Label(range_col, text="·  " * 10, bg=BG, fg=MUTED,
                 font=("Segoe UI", 7)).pack(anchor="w", pady=(3, 3))

        _days = tk.Frame(range_col, bg=BG)
        _days.pack(anchor="w")
        for _t in SCOPE_DAYS:
            _mk_radio(_days, _t).pack(anchor="w", padx=6, pady=1)

        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", padx=16, pady=(4, 8))
        self.gen_btn = tk.Button(btns, text="Generate & copy prompt", command=self.on_generate,
                                 bg=ACCENT, fg="white", relief="flat",
                                 font=("Segoe UI Semibold", 10), padx=14, pady=8,
                                 activebackground=GREEN, activeforeground="white", cursor="hand2")
        self.gen_btn.pack(side="left")
        tk.Button(btns, text="Clean SL/TP cache", command=self.on_clean, bg=PANEL, fg=FG,
                  relief="flat", font=("Segoe UI", 9), padx=12, pady=8,
                  activebackground=BORDER, activeforeground=FG,
                  cursor="hand2").pack(side="left", padx=(8, 0))
        tk.Button(btns, text="Reconnect", command=self._connect, bg=PANEL, fg=MUTED,
                  relief="flat", font=("Segoe UI", 9), padx=12, pady=8,
                  activebackground=BORDER, activeforeground=FG,
                  cursor="hand2").pack(side="right")

        # ── results table ────────────────────────────────────────────────────
        table_head = tk.Frame(self.root, bg=BG)
        table_head.pack(fill="x", padx=16, pady=(6, 2))
        tk.Label(table_head, text="Trades", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        self.summary = tk.Label(table_head, text="—", bg=BG, fg=MUTED,
                                font=("Segoe UI Semibold", 9))
        self.summary.pack(side="right")

        table_wrap = tk.Frame(self.root, bg=BORDER, bd=0)
        table_wrap.pack(fill="both", expand=True, padx=16)
        cols = ("idx", "day", "side", "entry", "exit", "pnl")
        self.tree = ttk.Treeview(table_wrap, columns=cols, show="headings",
                                 style="Dark.Treeview", height=8, selectmode="browse")
        headings = (("idx", "#", 40), ("day", "Day", 150), ("side", "Side", 60),
                    ("entry", "Entry", 90), ("exit", "Exit", 90), ("pnl", "P&L", 90))
        for key, label, width in headings:
            self.tree.heading(key, text=label)
            anchor = "w" if key == "day" else ("center" if key in ("idx", "side") else "e")
            self.tree.column(key, width=int(width * self.scale), anchor=anchor,
                             stretch=(key == "day"))
        vsb = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview,
                            style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        vsb.pack(side="right", fill="y", pady=1)
        self.tree.tag_configure("win", foreground=GREEN)
        self.tree.tag_configure("loss", foreground=RED)

        # ── log ──────────────────────────────────────────────────────────────
        tk.Label(self.root, text="Log", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(10, 2))
        self.status = scrolledtext.ScrolledText(self.root, bg=PANEL, fg=FG, insertbackground=FG,
                                                relief="flat", font=MONO, height=8, wrap="word",
                                                borderwidth=0)
        self.status.pack(fill="both", padx=16, pady=(0, 6))
        self.status.configure(state="disabled")
        for tag, color in (("OK", GREEN), ("ERROR", RED), ("WARN", AMBER), ("INFO", MUTED)):
            self.status.tag_config(tag, foreground=color)

        self.footer = tk.Label(self.root, text="Starting…", bg=BG, fg=MUTED,
                               anchor="w", font=("Segoe UI", 9))
        self.footer.pack(fill="x", padx=16, pady=(0, 12))

    def _center(self) -> None:
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = max(0, (self.root.winfo_screenheight() - h) // 2 - int(20 * self.scale))
        self.root.geometry(f"+{x}+{y}")

    # ── logging / status ──────────────────────────────────────────────────────
    def _engine_log(self, level: str, msg: str) -> None:
        self._append(f"[{level}] {msg}", level)

    def _append(self, text: str, tag: str | None = None) -> None:
        self.status.configure(state="normal")
        self.status.insert("end", text + "\n", (tag,) if tag else ())
        self.status.see("end")
        self.status.configure(state="disabled")
        self.root.update_idletasks()

    def _set_footer(self, text: str, ok: bool | None = None) -> None:
        color = GREEN if ok else (RED if ok is False else MUTED)
        self.footer.configure(text=text, fg=color)
        self.root.update_idletasks()

    def _set_pill(self, text: str, ok: bool | None = None) -> None:
        color = GREEN if ok else (RED if ok is False else AMBER)
        self.pill_dot.configure(fg=color)
        self.pill_text.configure(text=text, fg=FG if ok else MUTED)
        self.root.update_idletasks()

    def _flash_button(self, text: str, color: str) -> None:
        """Briefly confirm an action on the main button, then restore it."""
        if self._flash_job is not None:
            self.root.after_cancel(self._flash_job)
        self.gen_btn.configure(text=text, bg=color)

        def _restore() -> None:
            self._flash_job = None
            self.gen_btn.configure(text="Generate & copy prompt", bg=ACCENT)

        self._flash_job = self.root.after(1400, _restore)

    # ── boot / connect ────────────────────────────────────────────────────────
    def _boot(self) -> None:
        try:
            self.config = engine.load_config()
            self.user_tz = pytz.timezone(self.config["user_timezone"])
            self.symbol = self.config["symbol_mt5"]
        except SystemExit:
            self._set_pill("No config", ok=False)
            self._set_footer("config.json missing or invalid — see the log.", ok=False)
            self.gen_btn.configure(state="disabled")
            return
        self._refresh_week_options()
        self.root.after(150, self._connect)

    def _refresh_week_options(self) -> None:
        labels: list[str] = []
        self._week_map = {}
        for wb in range(0, 13):
            mon, fri = engine.get_business_week_range(self.user_tz, weeks_back=wb)
            tag = "Current week" if wb == 0 else f"{wb} week(s) ago"
            label = f"{tag}  ·  {mon.strftime('%d/%m')}–{fri.strftime('%d/%m/%y')}"
            labels.append(label)
            self._week_map[label] = wb
        self.week_combo.configure(values=labels)
        self.week_combo.current(0)

    def _connect(self) -> None:
        self._set_pill("Connecting…", ok=None)
        self._set_footer("Connecting to MetaTrader 5…")
        if not engine.connect_mt5():
            self.connected = False
            self._set_pill("MT5 offline", ok=False)
            self._set_footer("MT5 not connected — open MetaTrader 5, log in, then Reconnect.",
                             ok=False)
            return
        try:
            engine.mt5.symbol_select(self.symbol, True)
            self.server_offset = engine.detect_server_offset(self.symbol, self.config)
        except Exception as exc:  # noqa: BLE001
            self._append(f"[WARN] offset detection: {exc}", "WARN")
            self.server_offset = 0
        self.connected = True
        self._set_pill(f"MT5 · {self.symbol}", ok=True)
        self._set_footer("Connected. Pick a week and generate.", ok=True)

    # ── actions ───────────────────────────────────────────────────────────────
    def _scope_filter(self, week_records: list, monday_start) -> tuple[list, str]:
        scope = self.scope_var.get()
        d = [monday_start.date() + timedelta(days=i) for i in range(5)]
        day_map = {
            "Monday": {d[0]}, "Tuesday": {d[1]}, "Wednesday": {d[2]},
            "Thursday": {d[3]}, "Friday": {d[4]},
            "Mon + Tue + Wed": {d[0], d[1], d[2]}, "Thu + Fri": {d[3], d[4]},
        }
        wanted = day_map[scope]
        return [r for r in week_records if r["entry_dt"].date() in wanted], scope

    def _fill_table(self, records: list) -> None:
        self.tree.delete(*self.tree.get_children())
        for r in records:
            pnl = r["profit"]
            self.tree.insert(
                "", "end",
                values=(
                    r.get("idx", ""),
                    r["entry_dt"].strftime("%a %d/%m %H:%M"),
                    r["tipo"],
                    f"{r['entry_price']:.2f}",
                    f"{r['exit_price']:.2f}",
                    f"{pnl:+.2f}",
                ),
                tags=("win" if pnl >= 0 else "loss",),
            )

    def on_generate(self) -> None:
        if not self.connected:
            self._set_footer("Not connected to MT5.", ok=False)
            return
        self.gen_btn.configure(state="disabled")
        try:
            wb = self._week_map[self.week_var.get()]
            monday, friday = engine.get_business_week_range(self.user_tz, weeks_back=wb)
            deals = engine.fetch_deals(self.symbol, monday, friday)
            if not deals:
                self._set_footer("No deals in that range.", ok=False)
                return
            trades_raw = engine.pair_deals_into_trades(deals)
            if not trades_raw:
                self._set_footer("No closed trades in that range.", ok=False)
                return
            cache = engine.load_sltp_cache(self.config.get("sltp_log_path", ""))
            records = engine.build_trade_records(trades_raw, self.server_offset,
                                                 self.user_tz, self.config, sltp_cache=cache)
            week_records = engine.filter_to_business_week(records, monday, friday)
            engine.assign_indices_and_log(week_records, self.config)
            filtered, scope = self._scope_filter(week_records, monday)
            if not filtered:
                self._fill_table([])
                self.summary.configure(text="—", fg=MUTED)
                self._set_footer(f"No trades for: {scope}", ok=False)
                return
            total = sum(r["profit"] for r in filtered)
            exit_lines = sum(1 for r in filtered if r["exit_line"])
            self._fill_table(filtered)
            self.summary.configure(text=f"{scope}  ·  {len(filtered)} trades  ·  ${total:+.2f}",
                                   fg=GREEN if total >= 0 else RED)
            self._append(f"[OK] {scope}: {len(filtered)} trades  ·  ${total:+.2f}"
                         f"  ·  {exit_lines} exit lines", "OK")
            prompt = engine.build_prompt(filtered, self.config)
            copied = engine.copy_to_clipboard(prompt)
            if copied:
                self._flash_button("Copied ✓", GREEN)
                self._set_footer(f"Prompt copied ({len(prompt):,} chars) — "
                                 f"paste into Claude Code (Ctrl+V).", ok=True)
            else:
                self._set_footer("Clipboard unavailable — prompt saved to file (see log).",
                                 ok=None)
        except Exception as exc:  # noqa: BLE001
            self._append(f"[ERROR] {exc}", "ERROR")
            self._set_footer("Something failed — see the log.", ok=False)
        finally:
            self.gen_btn.configure(state="normal")

    def on_clean(self) -> None:
        if not self.connected:
            self._set_footer("Not connected to MT5.", ok=False)
            return
        path = self.config.get("sltp_log_path", "")
        kept, deleted = engine.clean_old_sltp_cache(path, self.user_tz, self.server_offset)
        if kept < 0:
            self._set_footer("Cache cleanup failed — see the log.", ok=False)
        else:
            self._append(f"[OK] Cache cleaned: {kept} kept, {deleted} deleted.", "OK")
            self._set_footer(f"Cache cleaned: {kept} kept, {deleted} deleted.", ok=True)

    def on_close(self) -> None:
        try:
            engine.mt5.shutdown()
        except Exception:  # noqa: BLE001
            pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = MT5GUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
