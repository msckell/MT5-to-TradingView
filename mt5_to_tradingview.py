"""
MT5 -> TradingView (clipboard bridge).

Reads closed XAU/USD trades from MetaTrader 5, builds a structured prompt for
Claude Code (which has the TradingView MCP active) and copies it to the
clipboard. The user just pastes with Ctrl+V into Claude Code and it draws the
positions automatically.

The on-chart drawing is done by the tradingview-mcp server by tradesdontlie
(https://github.com/tradesdontlie/tradingview-mcp); this script builds the
MT5 -> prompt half of the pipeline.

Usage:
    python mt5_to_tradingview.py
"""

from __future__ import annotations

import json
import os
import sys
import time as _time
from calendar import monthrange
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Optional

# ----------------------------------------------------------------------
# External dependencies (with clear messages if missing)
# ----------------------------------------------------------------------
try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] MetaTrader5 not installed. Run: pip install MetaTrader5")
    sys.exit(1)

try:
    import pytz
except ImportError:
    print("[ERROR] pytz not installed. Run: pip install pytz")
    sys.exit(1)


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
FALLBACK_PROMPT_PATH = SCRIPT_DIR / "prompt_clipboard.txt"

UTC_TZ = pytz.UTC
SECONDS_PER_HOUR = 3600
TICK_RECENT_THRESHOLD_SEC = 600  # 10 min: si el ultimo tick es mas viejo, mercado cerrado
DEFAULT_SERVER_OFFSET_FALLBACK = 3
WEEK_FETCH_BUFFER_DAYS = 3
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


# ----------------------------------------------------------------------
# Cache SL/TP (EA-generated sltp_log.csv)
# ----------------------------------------------------------------------
def load_sltp_cache(csv_path: str) -> dict[int, dict[str, float]]:
    """
    Reads EA-generated sltp_log.csv. For each position_id keeps:
    - first_sl: SL from row with LOWEST timestamp (original SL)
    - last_tp:  TP from row with HIGHEST timestamp (final TP)

    CSV columns (no header): position_id, symbol, sl, tp, timestamp_ms
    Missing file -> empty dict + WARN log (non-fatal).
    Malformed rows -> skipped.
    """
    cache: dict[int, dict[str, float]] = {}
    path = Path(csv_path)
    if not path.exists():
        log("WARN", f"sltp_log.csv not found at {csv_path} — defaults will be used")
        return cache
    try:
        with path.open("r", encoding="utf-8") as f:
            rows_read = 0
            rows_skipped = 0
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(",")
                if len(parts) < 5:
                    rows_skipped += 1
                    continue
                try:
                    pos_id = int(parts[0].strip())
                    sl_val = float(parts[2].strip())
                    tp_val = float(parts[3].strip())
                    ts_ms  = int(parts[4].strip())
                except (ValueError, IndexError):
                    rows_skipped += 1
                    continue

                if pos_id not in cache:
                    cache[pos_id] = {
                        "first_sl": 0.0, "first_sl_ts": None,
                        "last_tp": 0.0, "last_tp_ts": None,
                    }
                c = cache[pos_id]
                # Original SL = the EARLIEST NON-ZERO sl. A 0 means "no SL set yet"
                # (e.g. the position-open row before you placed the stop) — ignore it,
                # otherwise it would mask the real original SL.
                if sl_val != 0.0 and (c["first_sl_ts"] is None or ts_ms < c["first_sl_ts"]):
                    c["first_sl"] = sl_val
                    c["first_sl_ts"] = ts_ms
                # Final TP = the LATEST NON-ZERO tp.
                if tp_val != 0.0 and (c["last_tp_ts"] is None or ts_ms > c["last_tp_ts"]):
                    c["last_tp"] = tp_val
                    c["last_tp_ts"] = ts_ms
                rows_read += 1

        result = {
            pid: {"first_sl": v["first_sl"], "last_tp": v["last_tp"]}
            for pid, v in cache.items()
        }
        log("OK", f"sltp_log.csv loaded: {len(result)} positions ({rows_read} rows, {rows_skipped} skipped)")
        return result
    except Exception as e:
        log("WARN", f"Error reading sltp_log.csv: {e} — defaults will be used")
        return {}


def clean_old_sltp_cache(
    csv_path: str,
    user_tz: pytz.BaseTzInfo,
    server_offset_h: int,
) -> tuple[int, int]:
    """
    Removes all rows in sltp_log.csv with timestamp BEFORE Monday 00:00
    of the current business week (weeks_back=0). Preserves current week.

    Uses tempfile + os.replace for atomic write. Retries up to 3 times if
    the EA briefly holds the file open during a transaction event.

    Returns (rows_kept, rows_deleted). On failure returns (-1, -1).
    """
    import tempfile
    import os
    import time as _time

    path = Path(csv_path)
    if not path.exists():
        log("WARN", "No sltp_log.csv to clean.")
        return 0, 0

    # Cutoff: Monday 00:00 of current business week (in server time ms)
    monday_start, _ = get_business_week_range(user_tz, weeks_back=0)
    monday_utc_ms = int(monday_start.timestamp() * 1000)
    # EA writes ts in server-local-as-UTC, so cutoff in same domain:
    cutoff_server_ms = monday_utc_ms + server_offset_h * 3600 * 1000

    kept_lines: list[str] = []
    deleted_count = 0

    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                raw_line = line.rstrip("\n").rstrip("\r")
                if not raw_line.strip():
                    continue
                parts = raw_line.split(",")
                if len(parts) < 5:
                    # malformed — drop it silently
                    deleted_count += 1
                    continue
                try:
                    ts_ms = int(parts[4].strip())
                except ValueError:
                    deleted_count += 1
                    continue
                if ts_ms >= cutoff_server_ms:
                    kept_lines.append(raw_line + "\n")
                else:
                    deleted_count += 1
    except Exception as e:
        log("ERROR", f"Could not read CSV for cleanup: {e}")
        return -1, -1

    # Atomic replace with retry
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".sltp_log_", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tf:
            tf.writelines(kept_lines)
    except Exception as e:
        log("ERROR", f"Could not write tempfile: {e}")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return -1, -1

    for attempt in range(3):
        try:
            os.replace(tmp_path, str(path))
            return len(kept_lines), deleted_count
        except PermissionError:
            if attempt < 2:
                _time.sleep(0.2)
                continue
            log("ERROR", "EA is holding the file locked. Retry in a few seconds.")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return -1, -1
        except Exception as e:
            log("ERROR", f"Atomic replace failed: {e}")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return -1, -1
    return -1, -1


def prompt_clean_cache(
    csv_path: str,
    user_tz: pytz.BaseTzInfo,
    server_offset_h: int,
) -> None:
    """
    Interactive flow for [C] menu option. Shows preview, asks confirmation,
    runs cleanup, reports result.
    """
    path = Path(csv_path)
    if not path.exists():
        print()
        log("WARN", f"No file at {csv_path}. Nothing to clean.")
        input("Press Enter to return to the menu...")
        return

    # Count rows that would be deleted (dry run)
    monday_start, _ = get_business_week_range(user_tz, weeks_back=0)
    monday_utc_ms = int(monday_start.timestamp() * 1000)
    cutoff_server_ms = monday_utc_ms + server_offset_h * 3600 * 1000

    total = 0
    to_delete = 0
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                raw_line = line.strip()
                if not raw_line:
                    continue
                parts = raw_line.split(",")
                if len(parts) < 5:
                    to_delete += 1
                    total += 1
                    continue
                try:
                    ts_ms = int(parts[4].strip())
                except ValueError:
                    to_delete += 1
                    total += 1
                    continue
                if ts_ms < cutoff_server_ms:
                    to_delete += 1
                total += 1
    except Exception as e:
        log("ERROR", f"Could not inspect CSV: {e}")
        input("Press Enter to return to the menu...")
        return

    to_keep = total - to_delete

    print()
    print("=" * 55)
    print("SL/TP CACHE CLEANUP")
    print("=" * 55)
    print(f"File: {csv_path}")
    print(f"Cutoff:  Monday {monday_start.strftime('%d/%m/%Y')} 00:00 ({user_tz.zone})")
    print(f"Total rows:       {total}")
    print(f"To keep:          {to_keep} (current week and later)")
    print(f"To delete:        {to_delete} (before the current week)")
    print()
    if to_delete == 0:
        log("INFO", "Nothing to delete. Returning to the menu.")
        input("Press Enter to continue...")
        return

    try:
        confirm = input("Confirm cleanup? (y/n): ").strip().lower()
    except EOFError:
        return
    if confirm not in {"s", "si", "sí", "y", "yes"}:
        log("INFO", "Cleanup canceled.")
        return

    kept, deleted = clean_old_sltp_cache(csv_path, user_tz, server_offset_h)
    if kept < 0:
        log("ERROR", "Cleanup failed. Check that the EA is not locking the file.")
    else:
        log("OK", f"Cleanup complete: {kept} rows kept, {deleted} rows deleted.")
    input("Press Enter to return to the menu...")


# ----------------------------------------------------------------------
# Simple logging (no emojis, compatible with the .bat console)
# ----------------------------------------------------------------------
def log(level: str, msg: str) -> None:
    """Prints a message with an [OK] / [INFO] / [WARN] / [ERROR] prefix."""
    print(f"[{level}] {msg}")


# ----------------------------------------------------------------------
# Config loading
# ----------------------------------------------------------------------
def load_config() -> dict[str, Any]:
    """Reads and validates config.json from the script's own directory."""
    if not CONFIG_PATH.exists():
        log("ERROR", f"config.json not found at {CONFIG_PATH}. Copy config.example.json to config.json and edit it.")
        sys.exit(1)
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        log("ERROR", f"config.json invalid: {e}")
        sys.exit(1)


# ----------------------------------------------------------------------
# MT5 connection
# ----------------------------------------------------------------------
def connect_mt5() -> bool:
    """Initializes MT5 and verifies an account is logged in."""
    if not mt5.initialize():
        err = mt5.last_error()
        log("ERROR", f"Could not initialize MT5: {err}. Open MetaTrader 5 and log in.")
        return False
    info = mt5.account_info()
    if info is None:
        log("ERROR", "MT5 is not logged into an account. Log in and try again.")
        return False
    log("OK", f"Connected to MT5 - account {info.login} ({info.server})")
    return True


# ----------------------------------------------------------------------
# MT5 server timezone detection
# ----------------------------------------------------------------------
def _last_sunday_of_month(year: int, month: int) -> date:
    """Returns the last Sunday of the given month/year."""
    last_day = monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != 6:  # 6 = Sunday
        d -= timedelta(days=1)
    return d


def _heuristic_server_offset(now_utc: datetime) -> int:
    """European heuristic (EET/EEST): summer (last Sun Mar - last Sun Oct) -> GMT+3, winter -> GMT+2.

    ICMarkets and most European MT5 brokers use EET:
      Summer (DST active): UTC+3
      Winter:              UTC+2
    Note: summer maps to the LARGER offset (+3); flipping these two silently
    shifts every drawing by one hour, so keep the DST direction straight.
    """
    year = now_utc.year
    last_sun_mar = _last_sunday_of_month(year, 3)
    last_sun_oct = _last_sunday_of_month(year, 10)
    today_utc = now_utc.date()
    if last_sun_mar <= today_utc < last_sun_oct:
        return 3  # summer EET+DST = GMT+3
    return 2  # winter EET = GMT+2


def _dynamic_offset_from_tick(symbol: str) -> Optional[int]:
    """GMT offset candidate computed from the latest live tick.

    Compares tick.time_msc (server time encoded as UTC) against the PC clock.
    Returns the offset in hours, or None if there is no fresh/valid tick.

    WARNING: depends on the PC clock. If the clock is off, the value will be off
    and the freshness check (which uses the same clock) will NOT catch it. That's
    why detect_server_offset uses it only as a cross-check against the calendar
    heuristic, never as the sole source.
    """
    now_utc_ts = _time.time()
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or tick.time_msc <= 0:
        return None

    tick_ts = tick.time_msc / 1000.0
    diff = tick_ts - now_utc_ts
    offset_candidate = round(diff / SECONDS_PER_HOUR)
    if not (-2 <= offset_candidate <= 5):
        return None

    real_tick_utc = tick_ts - offset_candidate * SECONDS_PER_HOUR
    recency = abs(real_tick_utc - now_utc_ts)
    if recency >= TICK_RECENT_THRESHOLD_SEC:
        return None
    return offset_candidate


def detect_server_offset(symbol: str, config: dict[str, Any]) -> int:
    """Determines the MT5 server's GMT offset robustly and self-verifiably.

    Hierarchy:
      1. server_offset_override in config (if not None): wins unconditionally.
      2. EET/EEST calendar heuristic: a known-good anchor for ICMarkets.
      3. Cross-check against the live tick. If it matches the heuristic -> high
         confidence. If it DISAGREES -> warn with WARN and use the heuristic, since
         the tick depends on the PC clock and can lie without detection.
    """
    override = config.get("server_offset_override")
    if override is not None:
        log("INFO", f"Offset FORCED by config (server_offset_override): GMT+{override}")
        return int(override)

    now_utc = datetime.now(UTC_TZ)
    try:
        heuristic = _heuristic_server_offset(now_utc)
    except Exception as e:
        log("WARN", f"Heuristic failed ({e}). Default GMT+{DEFAULT_SERVER_OFFSET_FALLBACK}")
        heuristic = DEFAULT_SERVER_OFFSET_FALLBACK

    dynamic = _dynamic_offset_from_tick(symbol)

    if dynamic is None:
        log("INFO", f"Offset from calendar (no live tick): GMT+{heuristic}")
        return heuristic

    if dynamic == heuristic:
        log("INFO", f"Offset confirmed GMT+{heuristic} (calendar and tick agree)")
        return heuristic

    log(
        "WARN",
        f"offset MISMATCH: live tick says GMT+{dynamic} but calendar "
        f"says GMT+{heuristic}. Using calendar GMT+{heuristic}. If your broker is NOT "
        f"EET/EEST or the PC clock is off, check the PC time or set "
        f'"server_offset_override" in config.json.',
    )
    return heuristic


# ----------------------------------------------------------------------
# Timestamp conversion
# ----------------------------------------------------------------------
def server_msc_to_utc_ms(deal_time_msc: int, server_offset_h: int) -> int:
    """deal.time_msc (server-local-as-UTC, ms) -> real Unix ms UTC.

    MT5 returns ms since epoch, but the value represents server time encoded as
    if it were UTC. To get real UTC you must subtract the server offset.
    """
    return int(deal_time_msc) - server_offset_h * SECONDS_PER_HOUR * 1000


def utc_ms_to_user_tz_dt(unix_ms_utc: int, user_tz: pytz.BaseTzInfo) -> datetime:
    """Converts Unix ms UTC to a datetime in the user's timezone."""
    return datetime.fromtimestamp(unix_ms_utc / 1000.0, tz=UTC_TZ).astimezone(user_tz)


def _deal_time_msc(deal: Any) -> int:
    """Reads deal.time_msc, falling back to deal.time*1000 if unavailable."""
    msc = getattr(deal, "time_msc", 0) or 0
    if msc:
        return int(msc)
    return int(getattr(deal, "time", 0)) * 1000


# ----------------------------------------------------------------------
# Business week range
# ----------------------------------------------------------------------
def get_business_week_range(user_tz: pytz.BaseTzInfo, weeks_back: int = 0) -> tuple[datetime, datetime]:
    """Returns (Monday 00:00, Friday 23:59:59) of the relevant business week.

    If today is Sat/Sun: the PREVIOUS business week.
    If today is Mon-Fri: the current week (Monday to Friday 23:59:59).
    """
    now_local = datetime.now(user_tz)
    weekday = now_local.weekday()  # 0 = Monday, 6 = Sunday

    if weekday >= 5:  # Sat(5) or Sun(6)
        # go back to the previous Friday, then to that week's Monday
        days_back_to_friday = weekday - 4
        last_friday_date = (now_local - timedelta(days=days_back_to_friday)).date()
        monday_date = last_friday_date - timedelta(days=4)
    else:
        monday_date = (now_local - timedelta(days=weekday)).date()

    monday_date = monday_date - timedelta(weeks=weeks_back)
    friday_date = monday_date + timedelta(days=4)

    monday_start = user_tz.localize(datetime.combine(monday_date, dtime(0, 0, 0)))
    friday_end = user_tz.localize(datetime.combine(friday_date, dtime(23, 59, 59)))
    return monday_start, friday_end


# ----------------------------------------------------------------------
# Deal fetching
# ----------------------------------------------------------------------
def fetch_deals(symbol: str, monday_start: datetime, friday_end: datetime) -> list[Any]:
    """Downloads MT5 deals with a generous buffer. We filter later by exact timezone."""
    fetch_from = (monday_start - timedelta(days=WEEK_FETCH_BUFFER_DAYS)).astimezone(UTC_TZ).replace(tzinfo=None)
    fetch_to = (friday_end + timedelta(days=WEEK_FETCH_BUFFER_DAYS)).astimezone(UTC_TZ).replace(tzinfo=None)

    deals = mt5.history_deals_get(fetch_from, fetch_to)
    if deals is None:
        err = mt5.last_error()
        log("WARN", f"history_deals_get returned None: {err}")
        return []

    deals = [d for d in deals if d.symbol == symbol]
    log("OK", f"{len(deals)} deals downloaded from MT5")
    return deals


# ----------------------------------------------------------------------
# Pairing deals -> trades
# ----------------------------------------------------------------------
def pair_deals_into_trades(deals: list[Any]) -> list[dict[str, Any]]:
    """Groups deals by position_id and builds trades with entry/exit/net_profit.

    Ignores still-open positions (groups with < 2 deals) and deals without position_id.
    """
    groups: dict[int, list[Any]] = {}
    for d in deals:
        pid = getattr(d, "position_id", 0)
        if pid == 0:
            continue
        groups.setdefault(pid, []).append(d)

    trades: list[dict[str, Any]] = []
    for pid, group in groups.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda d: (d.time_msc, d.ticket))
        entry = group[0]
        exit_d = group[-1]
        profit_net = sum((d.profit + d.commission + d.swap) for d in group)
        trades.append(
            {
                "position_id": pid,
                "entry": entry,
                "exit": exit_d,
                "all_deals": group,
                "profit_net": profit_net,
            }
        )

    log("OK", f"{len(trades)} closed trades paired")
    return trades


# ----------------------------------------------------------------------
# SL/TP logic
# ----------------------------------------------------------------------
def _safe_get_price_attr(deal: Any, name: str) -> float:
    """Reads deal.<name> tolerating missing values or odd types (TradeDeal without sl/tp in some versions)."""
    try:
        value = getattr(deal, name, None)
        if value is None and hasattr(deal, "_asdict"):
            value = deal._asdict().get(name)
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _resolve_sl(
    raw_sl: float, entry_price: float, is_long: bool, config: dict[str, Any], cached_sl: float = 0.0
) -> tuple[float, bool, str]:
    """Returns (final_sl_price, was_calculated, reason)."""
    # Priority 1: EA cache — FIRST SL (original risk setup)
    if cached_sl != 0.0:
        valid = (is_long and cached_sl < entry_price) or \
                (not is_long and cached_sl > entry_price)
        if valid:
            return cached_sl, False, ""

    distance = config["default_sl_points"] * config["point_size"]
    if raw_sl == 0.0:
        return (entry_price - distance if is_long else entry_price + distance, True, "not set")
    if is_long and raw_sl >= entry_price:
        return entry_price - distance, True, "trailing"
    if (not is_long) and raw_sl <= entry_price:
        return entry_price + distance, True, "trailing"
    return raw_sl, False, ""


def _resolve_tp(
    raw_tp: float, entry_price: float, is_long: bool, config: dict[str, Any], cached_tp: float = 0.0
) -> tuple[float, bool, str]:
    """Returns (final_tp_price, was_calculated, reason)."""
    # Priority 1: EA cache — LAST TP (final target after modifications)
    if cached_tp != 0.0:
        valid = (is_long and cached_tp > entry_price) or \
                (not is_long and cached_tp < entry_price)
        if valid:
            return cached_tp, False, ""

    distance = config["default_tp_points"] * config["point_size"]
    if raw_tp == 0.0:
        return (entry_price + distance if is_long else entry_price - distance, True, "not set")
    if is_long and raw_tp <= entry_price:
        return entry_price + distance, True, "invalid"
    if (not is_long) and raw_tp >= entry_price:
        return entry_price - distance, True, "invalid"
    return raw_tp, False, ""


# ----------------------------------------------------------------------
# Record building (trade dicts with all final data)
# ----------------------------------------------------------------------
def build_trade_records(
    trades_raw: list[dict[str, Any]],
    server_offset: int,
    user_tz: pytz.BaseTzInfo,
    config: dict[str, Any],
    sltp_cache: dict = None,
) -> list[dict[str, Any]]:
    """Builds the final records with local timestamps, resolved SL/TP and exit lines."""
    records: list[dict[str, Any]] = []

    for raw in trades_raw:
        try:
            entry = raw["entry"]
            exit_d = raw["exit"]

            entry_ms_utc = server_msc_to_utc_ms(_deal_time_msc(entry), server_offset)
            exit_ms_utc = server_msc_to_utc_ms(_deal_time_msc(exit_d), server_offset)
            entry_s_utc = entry_ms_utc // 1000
            exit_s_utc = exit_ms_utc // 1000
            # Guarantee minimum visual duration of 120s so draw_shape never receives
            # two identical timestamps (zero-width line = silent discard by TradingView).
            MIN_VISUAL_DURATION_S = 120
            visual_exit_s_utc = exit_s_utc if (exit_s_utc - entry_s_utc) >= 60 else entry_s_utc + MIN_VISUAL_DURATION_S
            entry_dt = utc_ms_to_user_tz_dt(entry_ms_utc, user_tz)
            exit_dt = utc_ms_to_user_tz_dt(exit_ms_utc, user_tz)

            is_long = entry.type == mt5.DEAL_TYPE_BUY
            tipo = "Long" if is_long else "Short"

            entry_price = float(entry.price)
            exit_price = float(exit_d.price)

            raw_sl = _safe_get_price_attr(entry, "sl")
            raw_tp = _safe_get_price_attr(entry, "tp")

            cache_entry = (sltp_cache or {}).get(raw["position_id"], {})
            cached_sl = cache_entry.get("first_sl", 0.0)   # original SL
            cached_tp = cache_entry.get("last_tp", 0.0)    # final TP

            # Fallback: if deal lacks TP (post-fill modification not exposed by MT5 Python API),
            # try to recover from order history. Only works for pre-fill TP (limit orders).
            if raw_tp == 0.0:
                try:
                    orders = mt5.history_orders_get(position=raw["position_id"])
                    if orders:
                        for o in orders:
                            o_tp = float(getattr(o, "tp", 0.0) or 0.0)
                            if o_tp != 0.0:
                                raw_tp = o_tp
                                break
                except Exception:
                    pass

            sl_price, sl_calc, sl_reason = _resolve_sl(raw_sl, entry_price, is_long, config, cached_sl)
            tp_price, tp_calc, tp_reason = _resolve_tp(raw_tp, entry_price, is_long, config, cached_tp)

            # RED = the FIRST stop loss (the trade's fixed risk). We keep it exactly
            # where it was set and NEVER move it to the exit. Only if no real SL was
            # ever set (it had to be defaulted) do we fall back to marking the actual
            # exit price as the realized risk line.
            if sl_calc:  # sl_calc is True only when _resolve_sl had to fabricate a default
                sl_price = exit_price
                sl_calc = False
                sl_reason = "exit price (no SL set)"

            # TP fallback refinement — only when NO real TP was set AND the trade won:
            #   • profit beyond the threshold -> treat the actual close AS the TP
            #     (green line sits at the close; no separate exit line needed).
            #   • profit below the threshold  -> keep the default TP as a reference
            #     target; the exit line (violet) then marks where it really closed.
            is_win = (is_long and exit_price > entry_price) or (
                (not is_long) and exit_price < entry_price
            )
            if tp_calc and is_win:
                profit_points = abs(exit_price - entry_price) / config["point_size"]
                threshold = config.get("tp_as_close_threshold_points", 200)
                if profit_points > threshold:
                    tp_price = exit_price
                    tp_calc = False
                    tp_reason = "exit price (win, no TP set)"

            # PURPLE = where the trade ACTUALLY closed, drawn whenever that price is
            # neither the stop loss nor the take profit (early/manual close, trailing,
            # or a slipped fill past the SL). Direction-agnostic: above OR below entry.
            min_diff = config["min_diff_for_exit_line"]
            has_exit_line = (
                abs(exit_price - sl_price) > min_diff
                and abs(exit_price - tp_price) > min_diff
            )

            records.append(
                {
                    "position_id": raw["position_id"],
                    "tipo": tipo,
                    "entry_dt": entry_dt,
                    "exit_dt": exit_dt,
                    "entry_ms_utc": entry_ms_utc,
                    "exit_ms_utc": exit_ms_utc,
                    "entry_s_utc": entry_s_utc,
                    "exit_s_utc": exit_s_utc,
                    "visual_exit_s_utc": visual_exit_s_utc,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "sl_price": sl_price,
                    "sl_calculated": sl_calc,
                    "sl_reason": sl_reason,
                    "tp_price": tp_price,
                    "tp_calculated": tp_calc,
                    "tp_reason": tp_reason,
                    "profit": raw["profit_net"],
                    "exit_line": has_exit_line,
                    "exit_line_precio": exit_price if has_exit_line else None,
                }
            )
        except Exception as e:
            log("WARN", f"Trade with corrupt data ignored (position_id={raw.get('position_id')}): {e}")
            continue

    return records


def assign_indices_and_log(records: list[dict[str, Any]], config: dict[str, Any]) -> None:
    """Sorts records by entry time, assigns idx 1..N and emits per-trade logs."""
    records.sort(key=lambda r: r["entry_dt"])
    for i, r in enumerate(records, start=1):
        r["idx"] = i

        # SL diagnostics: distinguish "no SL in the EA log" (red drawn at the exit,
        # no violet) from a fabricated default, so a wrong-looking chart is explainable.
        if r["sl_reason"] == "exit price (no SL set)":
            log(
                "WARN",
                f"Trade #{i}: no original SL in the EA log -> RED drawn at the exit "
                f"price (no violet). Check the EA was running when the SL was set.",
            )
        elif r["sl_calculated"]:
            log(
                "WARN",
                f"Trade #{i}: invalid SL ({r['sl_reason']}) -> default {config['default_sl_points']}pts applied",
            )

        # TP diagnostics.
        if r["tp_reason"] == "exit price (win, no TP set)":
            log(
                "INFO",
                f"Trade #{i}: no TP set, win past threshold -> GREEN drawn at the close "
                f"(treated as the TP).",
            )
        elif r["tp_calculated"]:
            log(
                "WARN",
                f"Trade #{i}: no valid TP ({r['tp_reason']}) -> default {config['default_tp_points']}pts target drawn",
            )
        if r["entry_dt"].date() != r["exit_dt"].date():
            log(
                "INFO",
                f"Trade #{i}: crosses midnight (entry {r['entry_dt'].strftime('%d/%m %H:%M')} "
                f"-> exit {r['exit_dt'].strftime('%d/%m %H:%M')})",
            )


# ----------------------------------------------------------------------
# Weekly filtering
# ----------------------------------------------------------------------
def filter_to_business_week(
    records: list[dict[str, Any]], monday_start: datetime, friday_end: datetime
) -> list[dict[str, Any]]:
    """Keeps only trades whose entry falls within the business-week range."""
    return [r for r in records if monday_start <= r["entry_dt"] <= friday_end]


# ----------------------------------------------------------------------
# Interactive menu
# ----------------------------------------------------------------------
def show_menu(
    week_records: list[dict[str, Any]],
    monday_start: datetime,
    weeks_back: int = 0,
) -> Optional[tuple[str, list[dict[str, Any]]] | str]:
    """Shows the menu and returns (label, filtered_records), 'HISTORICAL', 'CLEAN_CACHE', or None."""
    days: list[tuple[date, list[dict[str, Any]]]] = []
    for i in range(5):
        day_date = monday_start.date() + timedelta(days=i)
        day_records = [r for r in week_records if r["entry_dt"].date() == day_date]
        days.append((day_date, day_records))

    monday_str = days[0][0].strftime("%d/%m")
    friday_str = days[4][0].strftime("%d/%m")
    week_label = "Current week" if weeks_back == 0 else f"{weeks_back} week(s) ago"

    print()
    print("=" * 55)
    print("MT5 to TradingView | XAU/USD")
    print(f"Week: Monday {monday_str} to Friday {friday_str}  [{week_label}]")
    print("=" * 55)
    lun_mar_recs = [r for r in week_records if r["entry_dt"].date() in (days[0][0], days[1][0])]
    mie_vie_recs = [r for r in week_records if r["entry_dt"].date() in (days[2][0], days[3][0], days[4][0])]

    print("Whole week upload:")
    print(f"[6] Mon+Tue ({days[0][0].strftime('%d/%m')}–{days[1][0].strftime('%d/%m')})           ({len(lun_mar_recs)} trades)")
    print(f"[7] Wed+Thu+Fri ({days[2][0].strftime('%d/%m')}–{days[4][0].strftime('%d/%m')})       ({len(mie_vie_recs)} trades)")
    print("·  " * 12)

    day_names = ["Monday   ", "Tuesday  ", "Wednesday", "Thursday ", "Friday   "]
    for i in range(5):
        day_date, day_recs = days[i]
        print(f"[{i + 1}] {day_names[i]} {day_date.strftime('%d/%m')}                ({len(day_recs)} trades)")
    print("[9] History (previous weeks)")
    print("[C] Clean SL/TP cache (deletes entries before the current week)")
    print("[X] Exit")

    while True:
        try:
            choice = input("Your choice: ").strip()
        except EOFError:
            return None

        if choice in ("X", "x"):
            return None
        if choice in {"1", "2", "3", "4", "5"}:
            day_idx = int(choice) - 1
            day_date, day_records = days[day_idx]
            label = f"Only {WEEKDAY_NAMES[day_idx]} {day_date.strftime('%d/%m')}"
            return (label, day_records)
        if choice == "6":
            label = f"Mon+Tue ({days[0][0].strftime('%d/%m')}–{days[1][0].strftime('%d/%m')})"
            return (label, lun_mar_recs)
        if choice == "7":
            label = f"Wed+Thu+Fri ({days[2][0].strftime('%d/%m')}–{days[4][0].strftime('%d/%m')})"
            return (label, mie_vie_recs)
        if choice == "9":
            return "HISTORICAL"
        if choice in ("C", "c"):
            return "CLEAN_CACHE"
        print("Invalid option. Try again.")


def show_historical_menu(user_tz: pytz.BaseTzInfo) -> Optional[int]:
    """Shows the last 12 business weeks as a numbered list.

    Returns weeks_back (1-12) if the user selects a week,
    or None if they choose to go back/exit.
    """
    print()
    print("=" * 55)
    print("HISTORY — Select a week")
    print("=" * 55)

    options = []
    for i in range(1, 13):
        mon, fri = get_business_week_range(user_tz, weeks_back=i)
        label = f"[{i:>2}] Week {mon.strftime('%d/%m/%y')} — {fri.strftime('%d/%m/%y')}"
        print(label)
        options.append(i)

    print("[ 0] Back to main menu")
    print()

    while True:
        try:
            choice = input("Your choice: ").strip()
        except EOFError:
            return None
        if choice == "0":
            return None
        try:
            val = int(choice)
            if 1 <= val <= 12:
                return val
        except ValueError:
            pass
        print("Invalid option. Enter a number from 0 to 12.")


# ----------------------------------------------------------------------
# Informational summary
# ----------------------------------------------------------------------
def show_summary(label: str, filtered: list[dict[str, Any]], monday_start: datetime) -> None:
    """Prints the summary without asking for confirmation."""
    print()
    print("=" * 36)
    print("SUMMARY OF TRADES TO LOAD")
    print("=" * 36)
    print(f"Range: {label}")

    by_date: dict[date, list[dict[str, Any]]] = {}
    for r in filtered:
        by_date.setdefault(r["entry_dt"].date(), []).append(r)

    day_labels = ["Monday   ", "Tuesday  ", "Wednesday", "Thursday ", "Friday   "]
    total_profit = 0.0
    for i in range(5):
        day_date = monday_start.date() + timedelta(days=i)
        if day_date not in by_date:
            continue
        recs = by_date[day_date]
        longs = sum(1 for r in recs if r["tipo"] == "Long")
        shorts = sum(1 for r in recs if r["tipo"] == "Short")
        profit = sum(r["profit"] for r in recs)
        total_profit += profit
        print(
            f"{day_labels[i]} {day_date.strftime('%d/%m')}: {len(recs)} trades "
            f"({longs} Long, {shorts} Short) | ${profit:+.2f}"
        )

    sl_tp_defaults = sum(1 for r in filtered if r["sl_calculated"] or r["tp_calculated"])
    exit_lines = sum(1 for r in filtered if r["exit_line"])

    print(f"Total: {len(filtered)} trades | Profit: ${total_profit:+.2f}")
    print(f"SL/TP defaults applied: {sl_tp_defaults} trades")
    print(f"Exit lines to draw: {exit_lines} trades")
    print("Preparing prompt for Claude Code...")


def show_timestamps_detail(filtered: list[dict[str, Any]]) -> None:
    """Prints each trade with its entry/exit in local time, UTC and Unix UTC seconds.

    Useful to manually verify the timezone conversion is correct before sending
    the prompt to the MCP. The TradingView MCP expects seconds, not milliseconds.
    """
    print()
    print("=" * 92)
    print("DETAILED TIMESTAMPS (verification before drawing)")
    print("=" * 92)
    print(f"{'#':>3} {'TYPE':5}  {'ENTRY LOCAL':22}  {'ENTRY UTC':22}  {'unix s':>11}")
    for r in filtered:
        entry_local = r["entry_dt"].strftime("%d/%m/%Y %H:%M:%S")
        entry_utc = datetime.fromtimestamp(
            r["entry_s_utc"], tz=UTC_TZ
        ).strftime("%d/%m/%Y %H:%M:%S")
        print(
            f"{r['idx']:>3} {r['tipo']:5}  {entry_local:22}  {entry_utc:22}  "
            f"{r['entry_s_utc']:>11}"
        )
    print("-" * 92)
    print(f"{'#':>3} {'TYPE':5}  {'EXIT LOCAL':22}  {'EXIT UTC':22}  {'unix s':>11}")
    for r in filtered:
        exit_local = r["exit_dt"].strftime("%d/%m/%Y %H:%M:%S")
        exit_utc = datetime.fromtimestamp(
            r["exit_s_utc"], tz=UTC_TZ
        ).strftime("%d/%m/%Y %H:%M:%S")
        print(
            f"{r['idx']:>3} {r['tipo']:5}  {exit_local:22}  {exit_utc:22}  "
            f"{r['exit_s_utc']:>11}"
        )
    print("=" * 92)


# ----------------------------------------------------------------------
# Prompt building for Claude Code
# ----------------------------------------------------------------------
def _round2(x: float) -> float:
    return round(float(x), 2)


def build_prompt(filtered: list[dict[str, Any]], config: dict[str, Any]) -> str:
    """Builds the full prompt with instructions + JSON, ready to paste into Claude Code."""
    trades_json = []
    for r in filtered:
        trades_json.append(
            {
                "id": r["idx"],
                "tipo": r["tipo"],
                "fecha_entrada_gmt3": r["entry_dt"].strftime("%d/%m/%Y %H:%M:%S"),
                "fecha_salida_gmt3": r["exit_dt"].strftime("%d/%m/%Y %H:%M:%S"),
                "ts_entrada_s_utc": r["entry_s_utc"],
                "ts_salida_s_utc": r["visual_exit_s_utc"],
                "precio_entrada": _round2(r["entry_price"]),
                "precio_salida": _round2(r["exit_price"]),
                "precio_sl": _round2(r["sl_price"]),
                "precio_tp": _round2(r["tp_price"]),
                "sl_calculado": bool(r["sl_calculated"]),
                "tp_calculado": bool(r["tp_calculated"]),
                "profit": _round2(r["profit"]),
                "exit_line": bool(r["exit_line"]),
                "exit_line_precio": _round2(r["exit_line_precio"]) if r["exit_line"] else None,
            }
        )

    json_str = json.dumps({"trades": trades_json}, indent=2, ensure_ascii=False)

    # Visible-range hint: span every trade's time (+15% padding, min 15 min) so the
    # agent can position the chart before drawing — off-screen candles get their
    # lines silently dropped by TradingView.
    _all_ts = [r["entry_s_utc"] for r in filtered] + [r["visual_exit_s_utc"] for r in filtered]
    _rng_from, _rng_to = min(_all_ts), max(_all_ts)
    _pad = max(int((_rng_to - _rng_from) * 0.15), 900)
    view_from, view_to = _rng_from - _pad, _rng_to + _pad

    sl_color = config["colors"]["sl_zone"]
    tp_color = config["colors"]["tp_zone"]
    exit_color = config["colors"]["exit_line"]
    exit_dur_candles = int(config["exit_line_duration_candles"])
    tf_minutes = int(config["timeframe"])
    exit_dur_s = exit_dur_candles * tf_minutes * 60
    sym = config["symbol_tradingview"]
    tf = config["timeframe"]

    entry_color = "#000000"   # black for all entries (long and short)

    prompt = (
        "Draw the following positions on TradingView using the MCP.\n"
        "Do NOT modify or delete anything already on the chart.\n"
        "ONLY add the new lines. Do not touch anything existing.\n"
        "\n"
        "CONFIGURATION:\n"
        f"- Symbol: {sym}\n"
        f"- Timeframe: {tf} minute\n"
        "- Timestamps in the JSON are already Unix UTC SECONDS (10 digits).\n"
        "  The TradingView MCP (draw_shape) uses seconds. NOT milliseconds.\n"
        "  Pass them as-is; TradingView converts them to the user's timezone.\n"
        "\n"
        "BEFORE DRAWING:\n"
        "1. Run tv_health_check. If it fails, stop and report the error.\n"
        f"2. Switch to {sym} if the active symbol is different.\n"
        f"3. Switch to timeframe {tf} minute if different.\n"
        f"4. Position the chart so ALL trades are on screen BEFORE drawing: call\n"
        f"   mcp__tradingview-mcp__chart_set_visible_range to set the visible time\n"
        f"   window to about {view_from}..{view_to} (unix seconds), then auto-scale\n"
        f"   the price axis. Off-screen candles get their drawings silently dropped.\n"
        "5. Confirm in console: \"Verification OK. Proceeding to draw X trades.\"\n"
        "\n"
        "TOOL: mcp__tradingview-mcp__draw_shape\n"
        "Parameters: shape, point, point2, overrides (JSON-string).\n"
        "  - shape: \"trend_line\"\n"
        "  - point:  { time: <unix_seconds>, price: <number> }\n"
        "  - point2: { time: <unix_seconds>, price: <number> }\n"
        "  - overrides: JSON string with linecolor / linewidth / linestyle\n"
        "    (linestyle: 0 = solid, 2 = dashed)\n"
        "\n"
        "DRAWING INSTRUCTIONS:\n"
        "For EACH trade in the JSON, draw 3 lines (4 if exit_line=true).\n"
        "Each line is HORIZONTAL: both points have the SAME price,\n"
        "what changes is the time (entry -> exit) so the line\n"
        "has time extension.\n"
        "\n"
        "DRAWING ORDER (IMPORTANT):\n"
        "Draw SEQUENTIALLY, one trade (or one day) at a time.\n"
        "Wait for each batch's response before sending the next.\n"
        "NEVER send all lines in parallel at once:\n"
        "TradingView silently drops drawings if it receives too many\n"
        "simultaneous orders (tested: ~16 at once fails, <=6 per batch works well).\n"
        "draw_shape returns 'success' even when the line did NOT get drawn,\n"
        "so do NOT trust the success: draw in small batches so they all land.\n"
        "\n"
        "1. ENTRY LINE (horizontal at precio_entrada):\n"
        "   - point:  { time: ts_entrada_s_utc, price: precio_entrada }\n"
        "   - point2: { time: ts_salida_s_utc,  price: precio_entrada }\n"
        f"   - linecolor: \"{entry_color}\" (black, same for Long and Short)\n"
        "   - linewidth: 3 ; linestyle: 0 (solid)\n"
        "\n"
        "2. STOP LOSS LINE (horizontal at precio_sl):\n"
        "   precio_sl is the ORIGINAL stop loss you set (the trade's fixed risk).\n"
        "   If no SL was ever set, precio_sl is the actual exit price instead.\n"
        "   - point:  { time: ts_entrada_s_utc, price: precio_sl }\n"
        "   - point2: { time: ts_salida_s_utc,  price: precio_sl }\n"
        "   - linecolor: \"#FF0000\" (red) ; linewidth: 3 ; linestyle: 0 (solid)\n"
        "\n"
        "3. TAKE PROFIT LINE (horizontal at precio_tp):\n"
        "   - point:  { time: ts_entrada_s_utc, price: precio_tp }\n"
        "   - point2: { time: ts_salida_s_utc,  price: precio_tp }\n"
        f"   - linecolor: \"{tp_color}\" (green) ; linewidth: 3 ; linestyle: 0 (solid)\n"
        "\n"
        "4. EXIT/TRAILING LINE (only if exit_line=true):\n"
        "   Marks where the trade ACTUALLY closed when that was neither the SL nor\n"
        "   the TP (early/manual close, trailing, or a slipped fill past the SL).\n"
        "   It can sit ABOVE or BELOW the entry — draw it wherever exit_line_precio is.\n"
        "   - point:  { time: ts_salida_s_utc,           price: exit_line_precio }\n"
        f"   - point2: {{ time: ts_salida_s_utc + {exit_dur_s}, price: exit_line_precio }}\n"
        f"   - linecolor: \"{exit_color}\" (purple) ; linewidth: 2 ; linestyle: 0 (solid)\n"
        "\n"
        "Do NOT delete anything existing. Do NOT modify anything existing. ONLY add.\n"
        "If a line fails: log it and continue with the next.\n"
        "Do not stop the process over a single error.\n"
        "\n"
        "DATA:\n"
        f"{json_str}\n"
        "\n"
        "WHEN DONE, show:\n"
        "Total drawn: X of X trades (Y of Z lines)\n"
        "- Total week profit: $XX.XX\n"
        "- Exit lines drawn: X\n"
    )
    return prompt


# ----------------------------------------------------------------------
# Clipboard (with file + notepad fallback)
# ----------------------------------------------------------------------
def copy_to_clipboard(text: str) -> bool:
    """Copies the prompt to the clipboard. If it fails, saves it to a file and opens it in Notepad."""
    try:
        import pyperclip  # deferred import: the .bat installs it if missing
        pyperclip.copy(text)
        return True
    except ImportError:
        log("WARN", "pyperclip not installed")
    except Exception as e:
        log("WARN", f"pyperclip failed: {e}")

    try:
        FALLBACK_PROMPT_PATH.write_text(text, encoding="utf-8")
        log("INFO", f"Prompt saved to {FALLBACK_PROMPT_PATH}")
        try:
            os.startfile(str(FALLBACK_PROMPT_PATH))  # type: ignore[attr-defined]
        except Exception as e:
            log("WARN", f"Could not open Notepad automatically: {e}")
        return False
    except Exception as e:
        log("ERROR", f"Could not save fallback: {e}")
        return False


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main() -> int:
    config = load_config()
    user_tz = pytz.timezone(config["user_timezone"])

    if not connect_mt5():
        return 1

    try:
        symbol = config["symbol_mt5"]

        if not mt5.symbol_select(symbol, True):
            log("WARN", f"Could not select symbol {symbol} in Market Watch")

        server_offset = detect_server_offset(symbol, config)
        log("INFO", f"Conversion applied: server GMT+{server_offset} -> {user_tz.zone} (local)")

        # ─ Initial fetch for current week
        monday_start, friday_end = get_business_week_range(user_tz, weeks_back=0)

        deals = fetch_deals(symbol, monday_start, friday_end)
        if not deals:
            log("INFO", "No XAU/USD deals in range. Exiting.")
            return 0

        trades_raw = pair_deals_into_trades(deals)
        if not trades_raw:
            log("INFO", "No closed trades in range. Exiting.")
            return 0

        sltp_cache = load_sltp_cache(config.get("sltp_log_path", ""))
        records = build_trade_records(trades_raw, server_offset, user_tz, config,
                                      sltp_cache=sltp_cache)
        week_records = filter_to_business_week(records, monday_start, friday_end)
        if not week_records:
            log("INFO", "No trades in the selected business week. Exiting.")
            return 0

        assign_indices_and_log(week_records, config)

        # ─ Menu loop
        weeks_back = 0
        while True:
            # Reload cache each iteration to capture fresh EA writes
            sltp_cache = load_sltp_cache(config.get("sltp_log_path", ""))

            # Re-fetch if user picked a historical week
            if weeks_back > 0:
                monday_start, friday_end = get_business_week_range(user_tz, weeks_back=weeks_back)
                deals = fetch_deals(symbol, monday_start, friday_end)
                if not deals:
                    log("INFO", "No deals for that week. Returning to the current week.")
                    weeks_back = 0
                    monday_start, friday_end = get_business_week_range(user_tz, weeks_back=0)
                    deals = fetch_deals(symbol, monday_start, friday_end)
                    if not deals:
                        log("INFO", "No deals available. Exiting.")
                        return 0
                trades_raw = pair_deals_into_trades(deals)
                records = build_trade_records(trades_raw, server_offset, user_tz, config,
                                              sltp_cache=sltp_cache)
                week_records = filter_to_business_week(records, monday_start, friday_end)
                assign_indices_and_log(week_records, config)

            result = show_menu(week_records, monday_start, weeks_back=weeks_back)

            if result is None:
                log("INFO", "Exit requested by user.")
                return 0

            if result == "HISTORICAL":
                selected_back = show_historical_menu(user_tz)
                if selected_back is None:
                    continue
                weeks_back = selected_back
                continue

            if result == "CLEAN_CACHE":
                prompt_clean_cache(config.get("sltp_log_path", ""), user_tz, server_offset)
                continue

            label, filtered = result
            break

        if not filtered:
            log("INFO", f"No trades for: {label}")
            return 0

        show_summary(label, filtered, monday_start)
        show_timestamps_detail(filtered)

        prompt = build_prompt(filtered, config)
        clipboard_ok = copy_to_clipboard(prompt)

        n_trades = len(filtered)
        n_exit_lines = sum(1 for r in filtered if r["exit_line"])
        log("OK", f"Prompt generated: {n_trades} trades, {n_exit_lines} exit lines")

        if clipboard_ok:
            log("OK", f"Prompt copied to clipboard ({len(prompt):,} characters)")
            log("INFO", "Paste into Claude Code with Ctrl+V and press Enter")
        else:
            log(
                "INFO",
                "The prompt opened in Notepad. Select all (Ctrl+A), copy (Ctrl+C) "
                "and paste into Claude Code.",
            )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        log("INFO", "Interrupted by user.")
        try:
            mt5.shutdown()
        except Exception:
            pass
        sys.exit(0)
    except Exception as e:
        log("ERROR", f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        try:
            mt5.shutdown()
        except Exception:
            pass
        sys.exit(1)
