"""
MT5 -> TradingView (clipboard bridge).

Lee trades cerrados de XAUUSD desde MetaTrader 5, arma un prompt estructurado
para Claude Code (que tiene el MCP de TradingView activo) y lo copia al
clipboard. El usuario solo pega con Ctrl+V en Claude Code y este dibuja las
posiciones automaticamente.

Uso:
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
# Dependencias externas (con mensajes claros si faltan)
# ----------------------------------------------------------------------
try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] MetaTrader5 no instalado. Ejecuta: pip install MetaTrader5")
    sys.exit(1)

try:
    import pytz
except ImportError:
    print("[ERROR] pytz no instalado. Ejecuta: pip install pytz")
    sys.exit(1)


# ----------------------------------------------------------------------
# Constantes
# ----------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
FALLBACK_PROMPT_PATH = SCRIPT_DIR / "prompt_clipboard.txt"

UTC_TZ = pytz.UTC
SECONDS_PER_HOUR = 3600
TICK_RECENT_THRESHOLD_SEC = 600  # 10 min: si el ultimo tick es mas viejo, mercado cerrado
DEFAULT_SERVER_OFFSET_FALLBACK = 3
WEEK_FETCH_BUFFER_DAYS = 3
WEEKDAY_NAMES = ["lunes", "martes", "miercoles", "jueves", "viernes"]


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
        log("WARN", f"sltp_log.csv no encontrado en {csv_path} — se usaran defaults")
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
                # SL original = el PRIMER sl DISTINTO DE CERO. Un 0 = "todavia sin SL"
                # (fila de apertura antes de fijar el stop) -> se ignora, si no taparia
                # el SL original real.
                if sl_val != 0.0 and (c["first_sl_ts"] is None or ts_ms < c["first_sl_ts"]):
                    c["first_sl"] = sl_val
                    c["first_sl_ts"] = ts_ms
                # TP final = el ULTIMO tp DISTINTO DE CERO.
                if tp_val != 0.0 and (c["last_tp_ts"] is None or ts_ms > c["last_tp_ts"]):
                    c["last_tp"] = tp_val
                    c["last_tp_ts"] = ts_ms
                rows_read += 1

        result = {
            pid: {"first_sl": v["first_sl"], "last_tp": v["last_tp"]}
            for pid, v in cache.items()
        }
        log("OK", f"sltp_log.csv cargado: {len(result)} posiciones ({rows_read} filas, {rows_skipped} saltadas)")
        return result
    except Exception as e:
        log("WARN", f"Error leyendo sltp_log.csv: {e} — se usaran defaults")
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
        log("WARN", "No hay sltp_log.csv para limpiar.")
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
        log("ERROR", f"No se pudo leer CSV para limpieza: {e}")
        return -1, -1

    # Atomic replace with retry
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".sltp_log_", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tf:
            tf.writelines(kept_lines)
    except Exception as e:
        log("ERROR", f"No se pudo escribir tempfile: {e}")
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
            log("ERROR", "EA mantiene el archivo bloqueado. Reintentar en unos segundos.")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return -1, -1
        except Exception as e:
            log("ERROR", f"Fallo el reemplazo atomico: {e}")
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
        log("WARN", f"No hay archivo en {csv_path}. Nada para limpiar.")
        input("Presiona Enter para volver al menu...")
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
        log("ERROR", f"No se pudo inspeccionar CSV: {e}")
        input("Presiona Enter para volver al menu...")
        return

    to_keep = total - to_delete

    print()
    print("=" * 55)
    print("LIMPIEZA DE CACHE SL/TP")
    print("=" * 55)
    print(f"Archivo: {csv_path}")
    print(f"Cutoff:  lunes {monday_start.strftime('%d/%m/%Y')} 00:00 ({user_tz.zone})")
    print(f"Total de filas:   {total}")
    print(f"A conservar:      {to_keep} (semana actual y posteriores)")
    print(f"A borrar:         {to_delete} (anteriores a la semana actual)")
    print()
    if to_delete == 0:
        log("INFO", "No hay nada para borrar. Volviendo al menu.")
        input("Presiona Enter para continuar...")
        return

    try:
        confirm = input("Confirmas la limpieza? (s/n): ").strip().lower()
    except EOFError:
        return
    if confirm not in {"s", "si", "sí", "y", "yes"}:
        log("INFO", "Limpieza cancelada.")
        return

    kept, deleted = clean_old_sltp_cache(csv_path, user_tz, server_offset_h)
    if kept < 0:
        log("ERROR", "Limpieza fallo. Verifica que el EA no este bloqueando el archivo.")
    else:
        log("OK", f"Limpieza completa: {kept} filas conservadas, {deleted} filas borradas.")
    input("Presiona Enter para volver al menu...")


# ----------------------------------------------------------------------
# Logging simple (sin emojis, compatible con consola .bat)
# ----------------------------------------------------------------------
def log(level: str, msg: str) -> None:
    """Imprime mensaje con prefijo [OK] / [INFO] / [WARN] / [ERROR]."""
    print(f"[{level}] {msg}")


# ----------------------------------------------------------------------
# Carga de configuracion
# ----------------------------------------------------------------------
def load_config() -> dict[str, Any]:
    """Lee y valida config.json del mismo directorio del script."""
    if not CONFIG_PATH.exists():
        log("ERROR", f"No se encontro config.json en {CONFIG_PATH}")
        sys.exit(1)
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        log("ERROR", f"config.json invalido: {e}")
        sys.exit(1)


# ----------------------------------------------------------------------
# Conexion MT5
# ----------------------------------------------------------------------
def connect_mt5() -> bool:
    """Inicializa MT5 y verifica que haya cuenta logueada."""
    if not mt5.initialize():
        err = mt5.last_error()
        log("ERROR", f"No se pudo inicializar MT5: {err}. Abri MetaTrader 5 y logueate.")
        return False
    info = mt5.account_info()
    if info is None:
        log("ERROR", "MT5 no esta logueado en una cuenta. Logueate y reintentalo.")
        return False
    log("OK", f"Conectado a MT5 - cuenta {info.login} ({info.server})")
    return True


# ----------------------------------------------------------------------
# Deteccion de zona horaria del servidor MT5
# ----------------------------------------------------------------------
def _last_sunday_of_month(year: int, month: int) -> date:
    """Devuelve el ultimo domingo del mes/year dado."""
    last_day = monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != 6:  # 6 = domingo
        d -= timedelta(days=1)
    return d


def _heuristic_server_offset(now_utc: datetime) -> int:
    """Heuristica europea (EET/EEST): verano (ult dom mar - ult dom oct) -> GMT+3, invierno -> GMT+2.

    ICMarkets y la mayoria de brokers MT5 europeos usan EET:
      Verano (DST activo): UTC+3
      Invierno:            UTC+2
    La version anterior estaba INVERTIDA.
    """
    year = now_utc.year
    last_sun_mar = _last_sunday_of_month(year, 3)
    last_sun_oct = _last_sunday_of_month(year, 10)
    today_utc = now_utc.date()
    if last_sun_mar <= today_utc < last_sun_oct:
        return 3  # verano EET+DST = GMT+3
    return 2  # invierno EET = GMT+2


def _dynamic_offset_from_tick(symbol: str) -> Optional[int]:
    """Candidato de offset GMT calculado desde el ultimo tick en vivo.

    Compara tick.time_msc (hora del servidor codificada como UTC) contra el reloj
    del PC. Devuelve el offset en horas, o None si no hay tick fresco/valido.

    ADVERTENCIA: depende del reloj del PC. Si el reloj esta corrido, el valor
    estara corrido y el chequeo de frescura (que usa el mismo reloj) NO lo detecta.
    Por eso detect_server_offset lo usa solo como cross-check contra la heuristica
    de calendario, nunca como unica fuente.
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
    """Determina el offset GMT del servidor MT5 de forma robusta y auto-verificable.

    Jerarquia:
      1. server_offset_override en config (si no es None): gana incondicionalmente.
      2. Heuristica de calendario EET/EEST: ancla conocida-buena para ICMarkets.
      3. Cross-check con el tick en vivo. Si coincide con la heuristica -> alta
         confianza. Si DISCREPA -> se avisa con WARN y se usa la heuristica, porque
         el tick depende del reloj del PC y puede mentir sin detectarlo.
    """
    override = config.get("server_offset_override")
    if override is not None:
        log("INFO", f"Offset FORZADO por config (server_offset_override): GMT+{override}")
        return int(override)

    now_utc = datetime.now(UTC_TZ)
    try:
        heuristic = _heuristic_server_offset(now_utc)
    except Exception as e:
        log("WARN", f"Heuristica fallo ({e}). Default GMT+{DEFAULT_SERVER_OFFSET_FALLBACK}")
        heuristic = DEFAULT_SERVER_OFFSET_FALLBACK

    dynamic = _dynamic_offset_from_tick(symbol)

    if dynamic is None:
        log("INFO", f"Offset por calendario (sin tick en vivo): GMT+{heuristic}")
        return heuristic

    if dynamic == heuristic:
        log("INFO", f"Offset confirmado GMT+{heuristic} (calendario y tick coinciden)")
        return heuristic

    log(
        "WARN",
        f"DISCREPANCIA de offset: tick en vivo dice GMT+{dynamic} pero calendario "
        f"dice GMT+{heuristic}. Uso calendario GMT+{heuristic}. Si tu broker NO es "
        f"EET/EEST o el reloj del PC esta corrido, revisa la hora del PC o setea "
        f'"server_offset_override" en config.json.',
    )
    return heuristic


# ----------------------------------------------------------------------
# Conversion de timestamps
# ----------------------------------------------------------------------
def server_msc_to_utc_ms(deal_time_msc: int, server_offset_h: int) -> int:
    """deal.time_msc (server-local-as-UTC, ms) -> Unix ms UTC reales.

    MT5 entrega ms desde epoch, pero el valor representa la hora del servidor
    codificada como si fuera UTC. Para obtener UTC real hay que restar el
    offset del servidor.
    """
    return int(deal_time_msc) - server_offset_h * SECONDS_PER_HOUR * 1000


def utc_ms_to_user_tz_dt(unix_ms_utc: int, user_tz: pytz.BaseTzInfo) -> datetime:
    """Convierte Unix ms UTC a datetime en la zona horaria del usuario."""
    return datetime.fromtimestamp(unix_ms_utc / 1000.0, tz=UTC_TZ).astimezone(user_tz)


def _deal_time_msc(deal: Any) -> int:
    """Lee deal.time_msc con fallback a deal.time*1000 si no esta disponible."""
    msc = getattr(deal, "time_msc", 0) or 0
    if msc:
        return int(msc)
    return int(getattr(deal, "time", 0)) * 1000


# ----------------------------------------------------------------------
# Rango semanal habil
# ----------------------------------------------------------------------
def get_business_week_range(user_tz: pytz.BaseTzInfo, weeks_back: int = 0) -> tuple[datetime, datetime]:
    """Devuelve (lunes 00:00, viernes 23:59:59) de la semana habil relevante.

    Si hoy es sab/dom: semana habil ANTERIOR.
    Si hoy es lun-vie: semana actual (de lunes hasta viernes 23:59:59).
    """
    now_local = datetime.now(user_tz)
    weekday = now_local.weekday()  # 0 = lunes, 6 = domingo

    if weekday >= 5:  # sab(5) o dom(6)
        # retroceder hasta el viernes anterior, luego al lunes de esa semana
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
# Fetch de deals
# ----------------------------------------------------------------------
def fetch_deals(symbol: str, monday_start: datetime, friday_end: datetime) -> list[Any]:
    """Descarga deals de MT5 con buffer generoso. Filtramos despues por timezone exacto."""
    fetch_from = (monday_start - timedelta(days=WEEK_FETCH_BUFFER_DAYS)).astimezone(UTC_TZ).replace(tzinfo=None)
    fetch_to = (friday_end + timedelta(days=WEEK_FETCH_BUFFER_DAYS)).astimezone(UTC_TZ).replace(tzinfo=None)

    deals = mt5.history_deals_get(fetch_from, fetch_to)
    if deals is None:
        err = mt5.last_error()
        log("WARN", f"history_deals_get devolvio None: {err}")
        return []

    deals = [d for d in deals if d.symbol == symbol]
    log("OK", f"{len(deals)} deals descargados de MT5")
    return deals


# ----------------------------------------------------------------------
# Emparejamiento de deals -> trades
# ----------------------------------------------------------------------
def pair_deals_into_trades(deals: list[Any]) -> list[dict[str, Any]]:
    """Agrupa deals por position_id y arma trades con entry/exit/profit_neto.

    Ignora posiciones aun abiertas (grupos con < 2 deals) y deals sin position_id.
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

    log("OK", f"{len(trades)} trades cerrados emparejados")
    return trades


# ----------------------------------------------------------------------
# Logica SL/TP
# ----------------------------------------------------------------------
def _safe_get_price_attr(deal: Any, name: str) -> float:
    """Lee deal.<name> tolerando ausencias o tipos raros (TradeDeal sin sl/tp en algunas versiones)."""
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
    """Devuelve (sl_price_final, fue_calculado, motivo)."""
    # Priority 1: EA cache — FIRST SL (original risk setup)
    if cached_sl != 0.0:
        valid = (is_long and cached_sl < entry_price) or \
                (not is_long and cached_sl > entry_price)
        if valid:
            return cached_sl, False, ""

    distance = config["default_sl_points"] * config["point_size"]
    if raw_sl == 0.0:
        return (entry_price - distance if is_long else entry_price + distance, True, "no configurado")
    if is_long and raw_sl >= entry_price:
        return entry_price - distance, True, "trailing"
    if (not is_long) and raw_sl <= entry_price:
        return entry_price + distance, True, "trailing"
    return raw_sl, False, ""


def _resolve_tp(
    raw_tp: float, entry_price: float, is_long: bool, config: dict[str, Any], cached_tp: float = 0.0
) -> tuple[float, bool, str]:
    """Devuelve (tp_price_final, fue_calculado, motivo)."""
    # Priority 1: EA cache — LAST TP (final target after modifications)
    if cached_tp != 0.0:
        valid = (is_long and cached_tp > entry_price) or \
                (not is_long and cached_tp < entry_price)
        if valid:
            return cached_tp, False, ""

    distance = config["default_tp_points"] * config["point_size"]
    if raw_tp == 0.0:
        return (entry_price + distance if is_long else entry_price - distance, True, "no configurado")
    if is_long and raw_tp <= entry_price:
        return entry_price + distance, True, "invalido"
    if (not is_long) and raw_tp >= entry_price:
        return entry_price - distance, True, "invalido"
    return raw_tp, False, ""


# ----------------------------------------------------------------------
# Construccion de records (trade_dicts con todos los datos finales)
# ----------------------------------------------------------------------
def build_trade_records(
    trades_raw: list[dict[str, Any]],
    server_offset: int,
    user_tz: pytz.BaseTzInfo,
    config: dict[str, Any],
    sltp_cache: dict = None,
) -> list[dict[str, Any]]:
    """Construye los registros finales con timestamps locales, SL/TP resueltos y exit lines."""
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

            # ROJA = el PRIMER stop loss (riesgo fijo del trade). No se mueve nunca al
            # precio de salida. Solo si NO habia SL real (se defaulteo) usamos la salida
            # real como linea de riesgo.
            if sl_calc:  # sl_calc True solo cuando _resolve_sl tuvo que fabricar un default
                sl_price = exit_price
                sl_calc = False
                sl_reason = "exit price (sin SL)"

            # TP fallback: solo cuando NO habia TP real Y el trade fue ganador:
            #   - ganancia mayor al umbral -> tratamos el cierre real COMO el TP.
            #   - ganancia menor al umbral -> dejamos el TP default como referencia;
            #     la linea violeta marca donde cerro de verdad.
            is_win = (is_long and exit_price > entry_price) or (
                (not is_long) and exit_price < entry_price
            )
            if tp_calc and is_win:
                profit_points = abs(exit_price - entry_price) / config["point_size"]
                threshold = config.get("tp_as_close_threshold_points", 200)
                if profit_points > threshold:
                    tp_price = exit_price
                    tp_calc = False
                    tp_reason = "exit price (ganancia sin TP)"

            # VIOLETA = donde cerro REALMENTE el trade, siempre que ese precio no sea ni
            # el stop loss ni el take profit (cierre anticipado/manual, trailing, o un
            # fill con slippage pasado el SL). Da igual arriba o abajo de la entrada.
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
            log("WARN", f"Trade con datos corruptos ignorado (position_id={raw.get('position_id')}): {e}")
            continue

    return records


def assign_indices_and_log(records: list[dict[str, Any]], config: dict[str, Any]) -> None:
    """Ordena records por hora de entrada, asigna idx 1..N y emite logs por trade."""
    records.sort(key=lambda r: r["entry_dt"])
    for i, r in enumerate(records, start=1):
        r["idx"] = i

        # Diagnostico SL: distingue "sin SL en el log del EA" (roja en la salida, sin
        # violeta) de un default fabricado, para que un grafico raro sea explicable.
        if r["sl_reason"] == "exit price (sin SL)":
            log(
                "WARN",
                f"Trade #{i}: sin SL original en el log del EA -> ROJA dibujada en el "
                f"precio de salida (sin violeta). Verifica que el EA estaba corriendo.",
            )
        elif r["sl_calculated"]:
            log(
                "WARN",
                f"Trade #{i}: SL invalido ({r['sl_reason']}) -> default {config['default_sl_points']}pts aplicado",
            )

        if r["tp_reason"] == "exit price (ganancia sin TP)":
            log(
                "INFO",
                f"Trade #{i}: sin TP, ganancia pasado el umbral -> VERDE dibujada en el "
                f"cierre (tratada como TP).",
            )
        elif r["tp_calculated"]:
            log(
                "WARN",
                f"Trade #{i}: TP invalido ({r['tp_reason']}) -> default {config['default_tp_points']}pts aplicado",
            )
        if r["entry_dt"].date() != r["exit_dt"].date():
            log(
                "INFO",
                f"Trade #{i}: cruza medianoche (entrada {r['entry_dt'].strftime('%d/%m %H:%M')} "
                f"-> salida {r['exit_dt'].strftime('%d/%m %H:%M')})",
            )


# ----------------------------------------------------------------------
# Filtrado por semana
# ----------------------------------------------------------------------
def filter_to_business_week(
    records: list[dict[str, Any]], monday_start: datetime, friday_end: datetime
) -> list[dict[str, Any]]:
    """Mantiene solo trades cuya entrada cae dentro del rango de la semana habil."""
    return [r for r in records if monday_start <= r["entry_dt"] <= friday_end]


# ----------------------------------------------------------------------
# Menu interactivo
# ----------------------------------------------------------------------
def show_menu(
    week_records: list[dict[str, Any]],
    monday_start: datetime,
    weeks_back: int = 0,
) -> Optional[tuple[str, list[dict[str, Any]]] | str]:
    """Muestra el menu y devuelve (label, records_filtrados), 'HISTORICAL', 'CLEAN_CACHE', o None."""
    days: list[tuple[date, list[dict[str, Any]]]] = []
    for i in range(5):
        day_date = monday_start.date() + timedelta(days=i)
        day_records = [r for r in week_records if r["entry_dt"].date() == day_date]
        days.append((day_date, day_records))

    monday_str = days[0][0].strftime("%d/%m")
    friday_str = days[4][0].strftime("%d/%m")
    week_label = "Semana actual" if weeks_back == 0 else f"Hace {weeks_back} semana(s)"

    print()
    print("=" * 55)
    print("MT5 a TradingView | XAU/USD")
    print(f"Semana: lunes {monday_str} al viernes {friday_str}  [{week_label}]")
    print("=" * 55)
    lun_mar_recs = [r for r in week_records if r["entry_dt"].date() in (days[0][0], days[1][0])]
    mie_vie_recs = [r for r in week_records if r["entry_dt"].date() in (days[2][0], days[3][0], days[4][0])]

    print("Subida de semana completa:")
    print(f"[6] Lun+Mar ({days[0][0].strftime('%d/%m')}–{days[1][0].strftime('%d/%m')})           ({len(lun_mar_recs)} trades)")
    print(f"[7] Mie+Jue+Vie ({days[2][0].strftime('%d/%m')}–{days[4][0].strftime('%d/%m')})       ({len(mie_vie_recs)} trades)")
    print("·  " * 12)

    day_names = ["Lunes    ", "Martes   ", "Miercoles", "Jueves   ", "Viernes  "]
    for i in range(5):
        day_date, day_recs = days[i]
        print(f"[{i + 1}] {day_names[i]} {day_date.strftime('%d/%m')}                ({len(day_recs)} trades)")
    print("[9] Historico (semanas anteriores)")
    print("[C] Limpiar cache SL/TP (borra anteriores a la semana actual)")
    print("[X] Salir")

    while True:
        try:
            choice = input("Tu eleccion: ").strip()
        except EOFError:
            return None

        if choice in ("X", "x"):
            return None
        if choice in {"1", "2", "3", "4", "5"}:
            day_idx = int(choice) - 1
            day_date, day_records = days[day_idx]
            label = f"Solo {WEEKDAY_NAMES[day_idx]} {day_date.strftime('%d/%m')}"
            return (label, day_records)
        if choice == "6":
            label = f"Lun+Mar ({days[0][0].strftime('%d/%m')}–{days[1][0].strftime('%d/%m')})"
            return (label, lun_mar_recs)
        if choice == "7":
            label = f"Mie+Jue+Vie ({days[2][0].strftime('%d/%m')}–{days[4][0].strftime('%d/%m')})"
            return (label, mie_vie_recs)
        if choice == "9":
            return "HISTORICAL"
        if choice in ("C", "c"):
            return "CLEAN_CACHE"
        print("Opcion invalida. Intenta de nuevo.")


def show_historical_menu(user_tz: pytz.BaseTzInfo) -> Optional[int]:
    """Muestra las ultimas 12 semanas habiles como lista numerada.

    Devuelve weeks_back (1-12) si el usuario selecciona una semana,
    o None si elige volver/salir.
    """
    print()
    print("=" * 55)
    print("HISTORICO — Selecciona una semana")
    print("=" * 55)

    options = []
    for i in range(1, 13):
        mon, fri = get_business_week_range(user_tz, weeks_back=i)
        label = f"[{i:>2}] Semana {mon.strftime('%d/%m/%y')} — {fri.strftime('%d/%m/%y')}"
        print(label)
        options.append(i)

    print("[ 0] Volver al menu principal")
    print()

    while True:
        try:
            choice = input("Tu eleccion: ").strip()
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
        print("Opcion invalida. Ingresa un numero del 0 al 12.")


# ----------------------------------------------------------------------
# Resumen informativo
# ----------------------------------------------------------------------
def show_summary(label: str, filtered: list[dict[str, Any]], monday_start: datetime) -> None:
    """Imprime el resumen sin pedir confirmacion."""
    print()
    print("=" * 36)
    print("RESUMEN DE TRADES A CARGAR")
    print("=" * 36)
    print(f"Rango: {label}")

    by_date: dict[date, list[dict[str, Any]]] = {}
    for r in filtered:
        by_date.setdefault(r["entry_dt"].date(), []).append(r)

    day_labels = ["Lunes    ", "Martes   ", "Miercoles", "Jueves   ", "Viernes  "]
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
    print(f"SL/TP defaults aplicados: {sl_tp_defaults} trades")
    print(f"Exit lines a dibujar: {exit_lines} trades")
    print("Preparando prompt para Claude Code...")


def show_timestamps_detail(filtered: list[dict[str, Any]]) -> None:
    """Imprime cada trade con su entrada/salida en GMT-3, UTC y Unix segundos UTC.

    Sirve para verificar manualmente que la conversion de zona horaria
    es correcta antes de mandar el prompt al MCP. El MCP de TradingView
    espera segundos, no milisegundos.
    """
    print()
    print("=" * 92)
    print("TIMESTAMPS DETALLADOS (verificacion antes de dibujar)")
    print("=" * 92)
    print(f"{'#':>3} {'TIPO':5}  {'ENTRADA GMT-3':22}  {'ENTRADA UTC':22}  {'unix s':>11}")
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
    print(f"{'#':>3} {'TIPO':5}  {'SALIDA  GMT-3':22}  {'SALIDA  UTC':22}  {'unix s':>11}")
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
# Construccion del prompt para Claude Code
# ----------------------------------------------------------------------
def _round2(x: float) -> float:
    return round(float(x), 2)


def build_prompt(filtered: list[dict[str, Any]], config: dict[str, Any]) -> str:
    """Arma el prompt completo con instrucciones + JSON, listo para pegar en Claude Code."""
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

    sl_color = config["colors"]["sl_zone"]
    tp_color = config["colors"]["tp_zone"]
    exit_color = config["colors"]["exit_line"]
    exit_dur_candles = int(config["exit_line_duration_candles"])
    tf_minutes = int(config["timeframe"])
    exit_dur_s = exit_dur_candles * tf_minutes * 60
    sym = config["symbol_tradingview"]
    tf = config["timeframe"]

    entry_color = "#000000"   # negro para todas las entradas (long y short)

    prompt = (
        "Dibuja las siguientes posiciones en TradingView usando el MCP.\n"
        "NO modifiques ni borres nada que ya este en el grafico.\n"
        "SOLO agrega las nuevas lineas. No toques nada existente.\n"
        "\n"
        "CONFIGURACION:\n"
        f"- Simbolo: {sym}\n"
        f"- Timeframe: {tf} minuto\n"
        "- Los timestamps en el JSON ya estan en Unix SEGUNDOS UTC (10 digitos).\n"
        "  El MCP de TradingView (draw_shape) usa segundos. NO milisegundos.\n"
        "  Pasalos tal cual; TradingView los convierte a la zona del usuario.\n"
        "\n"
        "ANTES DE DIBUJAR:\n"
        "1. Ejecutar tv_health_check. Si falla, detener y reportar error.\n"
        f"2. Cambiar a {sym} si el simbolo activo es diferente.\n"
        f"3. Cambiar a timeframe {tf} minuto si es diferente.\n"
        "4. Confirmar en consola: \"Verificacion OK. Procediendo a dibujar X trades.\"\n"
        "\n"
        "HERRAMIENTA: mcp__tradingview-mcp__draw_shape\n"
        "Parametros: shape, point, point2, overrides (JSON-string).\n"
        "  - shape: \"trend_line\"\n"
        "  - point:  { time: <unix_segundos>, price: <number> }\n"
        "  - point2: { time: <unix_segundos>, price: <number> }\n"
        "  - overrides: string JSON con linecolor / linewidth / linestyle\n"
        "    (linestyle: 0 = solid, 2 = dashed)\n"
        "\n"
        "INSTRUCCIONES DE DIBUJO:\n"
        "Para CADA trade del JSON, dibuja 3 lineas (4 si exit_line=true).\n"
        "Cada linea es HORIZONTAL: ambos puntos tienen el MISMO precio,\n"
        "lo que cambia es el tiempo (entrada -> salida) para que la linea\n"
        "tenga extension temporal.\n"
        "\n"
        "ORDEN DE DIBUJO (IMPORTANTE):\n"
        "Dibuja SECUENCIALMENTE, un trade (o un dia) a la vez.\n"
        "Espera la respuesta de cada tanda antes de mandar la siguiente.\n"
        "NUNCA mandes todas las lineas en paralelo de una sola vez:\n"
        "TradingView descarta dibujos en silencio si recibe demasiadas\n"
        "ordenes simultaneas (probado: ~16 a la vez falla, <=6 por tanda anda bien).\n"
        "draw_shape devuelve 'success' aunque la linea NO haya quedado dibujada,\n"
        "asi que NO confies en el success: dibuja en tandas chicas para que entren todas.\n"
        "\n"
        "1. LINEA DE ENTRADA (horizontal en precio_entrada):\n"
        "   - point:  { time: ts_entrada_s_utc, price: precio_entrada }\n"
        "   - point2: { time: ts_salida_s_utc,  price: precio_entrada }\n"
        f"   - linecolor: \"{entry_color}\" (negro, igual para Long y Short)\n"
        "   - linewidth: 3 ; linestyle: 0 (solid)\n"
        "\n"
        "2. LINEA DE STOP LOSS (horizontal en precio_sl):\n"
        "   precio_sl es el stop loss ORIGINAL que pusiste (riesgo fijo del trade).\n"
        "   Si nunca hubo SL, precio_sl es el precio de salida real.\n"
        "   - point:  { time: ts_entrada_s_utc, price: precio_sl }\n"
        "   - point2: { time: ts_salida_s_utc,  price: precio_sl }\n"
        "   - linecolor: \"#FF0000\" (rojo) ; linewidth: 3 ; linestyle: 0 (solid)\n"
        "\n"
        "3. LINEA DE TAKE PROFIT (horizontal en precio_tp):\n"
        "   - point:  { time: ts_entrada_s_utc, price: precio_tp }\n"
        "   - point2: { time: ts_salida_s_utc,  price: precio_tp }\n"
        f"   - linecolor: \"{tp_color}\" (verde) ; linewidth: 3 ; linestyle: 0 (solid)\n"
        "\n"
        "4. LINEA DE EXIT/TRAILING (solo si exit_line=true):\n"
        "   Marca donde cerro REALMENTE el trade cuando eso no fue ni el SL ni el TP\n"
        "   (cierre anticipado/manual, trailing, o un fill con slippage). Puede estar\n"
        "   ARRIBA o ABAJO de la entrada — dibujala donde este exit_line_precio.\n"
        "   - point:  { time: ts_salida_s_utc,           price: exit_line_precio }\n"
        f"   - point2: {{ time: ts_salida_s_utc + {exit_dur_s}, price: exit_line_precio }}\n"
        f"   - linecolor: \"{exit_color}\" (morado) ; linewidth: 2 ; linestyle: 0 (solid)\n"
        "\n"
        "NO borrar nada existente. NO modificar nada existente. SOLO agregar.\n"
        "Si una linea falla: loguearla y continuar con la siguiente.\n"
        "No detener el proceso por un error individual.\n"
        "\n"
        "DATOS:\n"
        f"{json_str}\n"
        "\n"
        "AL TERMINAR, mostrar:\n"
        "Total dibujados: X de X trades (Y de Z lineas)\n"
        "- Profit total semana: $XX.XX\n"
        "- Exit lines dibujadas: X\n"
    )
    return prompt


# ----------------------------------------------------------------------
# Clipboard (con fallback a archivo + notepad)
# ----------------------------------------------------------------------
def copy_to_clipboard(text: str) -> bool:
    """Copia el prompt al clipboard. Si falla, lo guarda en archivo y lo abre con notepad."""
    try:
        import pyperclip  # import diferido: el .bat lo instala si falta
        pyperclip.copy(text)
        return True
    except ImportError:
        log("WARN", "pyperclip no instalado")
    except Exception as e:
        log("WARN", f"pyperclip fallo: {e}")

    try:
        FALLBACK_PROMPT_PATH.write_text(text, encoding="utf-8")
        log("INFO", f"Prompt guardado en {FALLBACK_PROMPT_PATH}")
        try:
            os.startfile(str(FALLBACK_PROMPT_PATH))  # type: ignore[attr-defined]
        except Exception as e:
            log("WARN", f"No pude abrir notepad automaticamente: {e}")
        return False
    except Exception as e:
        log("ERROR", f"No pude guardar fallback: {e}")
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
            log("WARN", f"No se pudo seleccionar simbolo {symbol} en Market Watch")

        server_offset = detect_server_offset(symbol, config)
        log("INFO", f"Conversion aplicada: GMT+{server_offset} -> GMT-3")

        # ─ Initial fetch for current week
        monday_start, friday_end = get_business_week_range(user_tz, weeks_back=0)

        deals = fetch_deals(symbol, monday_start, friday_end)
        if not deals:
            log("INFO", "No hay deals de XAUUSD en el rango. Salgo.")
            return 0

        trades_raw = pair_deals_into_trades(deals)
        if not trades_raw:
            log("INFO", "No hay trades cerrados en el rango. Salgo.")
            return 0

        sltp_cache = load_sltp_cache(config.get("sltp_log_path", ""))
        records = build_trade_records(trades_raw, server_offset, user_tz, config,
                                      sltp_cache=sltp_cache)
        week_records = filter_to_business_week(records, monday_start, friday_end)
        if not week_records:
            log("INFO", "No hay trades en la semana habil seleccionada. Salgo.")
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
                    log("INFO", "No hay deals para esa semana. Volviendo a semana actual.")
                    weeks_back = 0
                    monday_start, friday_end = get_business_week_range(user_tz, weeks_back=0)
                    deals = fetch_deals(symbol, monday_start, friday_end)
                    if not deals:
                        log("INFO", "No hay deals disponibles. Salgo.")
                        return 0
                trades_raw = pair_deals_into_trades(deals)
                records = build_trade_records(trades_raw, server_offset, user_tz, config,
                                              sltp_cache=sltp_cache)
                week_records = filter_to_business_week(records, monday_start, friday_end)
                assign_indices_and_log(week_records, config)

            result = show_menu(week_records, monday_start, weeks_back=weeks_back)

            if result is None:
                log("INFO", "Salida solicitada por el usuario.")
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
            log("INFO", f"No hay trades para: {label}")
            return 0

        show_summary(label, filtered, monday_start)
        show_timestamps_detail(filtered)

        prompt = build_prompt(filtered, config)
        clipboard_ok = copy_to_clipboard(prompt)

        n_trades = len(filtered)
        n_exit_lines = sum(1 for r in filtered if r["exit_line"])
        log("OK", f"Prompt generado: {n_trades} trades, {n_exit_lines} exit lines")

        if clipboard_ok:
            log("OK", f"Prompt copiado al clipboard ({len(prompt):,} caracteres)")
            log("INFO", "Pega en Claude Code con Ctrl+V y presiona Enter")
        else:
            log(
                "INFO",
                "El prompt se abrio en notepad. Selecciona todo (Ctrl+A), copia (Ctrl+C) "
                "y pega en Claude Code.",
            )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        log("INFO", "Interrumpido por el usuario.")
        try:
            mt5.shutdown()
        except Exception:
            pass
        sys.exit(0)
    except Exception as e:
        log("ERROR", f"Error inesperado: {e}")
        import traceback
        traceback.print_exc()
        try:
            mt5.shutdown()
        except Exception:
            pass
        sys.exit(1)
