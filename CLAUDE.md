# CLAUDE.md — MT5 to TradingView (Trade.LINK)

> Doc de arranque de este repo. Cualquier sesión de IA que trabaje acá lee esto PRIMERO.

## Qué es

Script que lee los trades cerrados de XAU/USD desde MetaTrader 5, arma un prompt
estructurado y lo copia al clipboard. El trader lo pega en Claude Code (con el MCP
`tradingview-mcp` activo) y este dibuja las posiciones (entry/SL/TP/exit) en el chart
de TradingView. No es un robot — no abre, modifica ni cierra órdenes; es una
herramienta de review post-trade.

Es **este** el repo en uso hoy. Vive en GitHub: `msckell/MT5-to-TradingView`
(público). El detalle completo de uso está en `README.md` — este archivo es para
orientar a un agente de IA que va a tocar el código, no para explicar el producto.

## Relación con el resto del ecosistema del trader

- **No tiene relación con Obsidian Terminal.** Obsidian Terminal (el journal de
  trading, en `Desktop\Obsidian Terminal`) es un sistema totalmente aparte con su
  propio pipeline de importación de MT5 (`journal-app/scripts/mt5_export.py`,
  location-independent, vive en OneDrive). Ninguno de los dos depende del otro. No
  asumir hardcodeos cruzados entre carpetas.
- **`MT5 A EXCEL` está muerto.** Era el pipeline viejo (MT5 → Excel Journal vía
  xlwings). Reemplazado por Obsidian Terminal. Vive archivado en
  `../Archivo (obsoleto)/MT5 A EXCEL` solo como referencia histórica — no se toca ni
  se reactiva.
- **`../Archivo (obsoleto)/`** (carpeta hermana, un nivel arriba) tiene versiones
  superadas de este mismo proyecto: la versión en español original, un clon duplicado
  viejo del repo, y un experimento (`direct.py`, conexión directa al MCP sin pasar por
  clipboard — no probado en vivo). Sirven de contexto histórico si hace falta
  reconstruir una decisión, pero no son código activo.

## Stack

- **Python 3.10+** — `MetaTrader5`, `pytz`, `pyperclip`, `PySide6-Essentials` (ver
  `requirements.txt`). Qt lo necesita solo `app/gui_qt.py`; el engine y la GUI vieja
  de Tkinter corren sin él.
- **`config.json`** (gitignored — cada máquina tiene el suyo, copiado de
  `config.example.json`). Nunca commitear `config.json` real ni el `sltp_log.csv`.
- **MQL5** — `mql5/SLTPLogger.mq5`, Expert Advisor que corre en MT5 y loguea SL/TP en
  vivo (MT5 no retiene el SL original ni el TP final de una posición cerrada/trailed).
- **PowerShell** — `tools/install.ps1`: instala deps, crea `config.json` si falta y
  genera el acceso directo `MT5 to TradingView.lnk` (gitignored: tiene paths de la
  máquina) apuntando a `pythonw app/gui_qt.py`, con `assets/icon.ico` como ícono.
- **Node.js** — el MCP `tradingview-mcp` (`tradesdontlie/tradingview-mcp`, no vive en
  este repo) es quien efectivamente dibuja en TradingView vía CDP.

## Estructura

```
.
├─ MT5 to TradingView (Trade.LINK).lnk   ← launcher (lo crea tools/install.ps1, gitignored)
├─ app/gui_qt.py             app de escritorio (Qt) — entry point principal
├─ app/gui.py                GUI vieja de Tkinter — fallback sin dependencias
├─ app/mt5_to_tradingview.py el engine (además corre como menú de consola)
├─ mql5/SLTPLogger.mq5
├─ tools/install.ps1 · tools/make_logo.py
├─ docs/design/              el design canvas del que sale la GUI Qt
├─ assets/                   icon.ico + logo-wide.png / logo-icon.png
└─ config.example.json · requirements.txt · README.md · LICENSE
```

El engine resuelve `config.json` desde la raíz del proyecto (`PROJECT_ROOT`), no desde
`app/`. Si se mueven archivos, revisar `SCRIPT_DIR` / `PROJECT_ROOT` en
`app/mt5_to_tradingview.py` y `ASSETS` / `ICON_PATH` en `app/gui_qt.py` y `app/gui.py`.

## Cómo se corre

Doble clic en el acceso directo **MT5 to TradingView (Trade.LINK)** (o
`pythonw app/gui_qt.py`). La app Qt es el ejecutor principal: elegís semana + rango de
días, botón **Generate & copy prompt**, y el prompt queda en el clipboard. Requiere MT5
abierto y logueado, y el EA `SLTPLogger.mq5` corriendo si se quiere el SL/TP real (si
no, cae a defaults de `config.json`).

A diferencia de la GUI vieja, la Qt **carga la semana al elegirla** (no al generar): los
segmentos de rango filtran en memoria, sin volver a pegarle a MT5.

Fallbacks: `pythonw app/gui.py` (la GUI vieja de Tkinter, sin dependencias más allá de
Python) y `python app/mt5_to_tradingview.py` (menú de consola). Los dos se mantienen a
propósito — no borrarlos sin que el trader lo pida.

**Cuidado con la desincronización GUI ↔ engine.** El split de días vive en TRES lados:
`show_menu()` en el engine, `SCOPE_BATCHES` / `_scope_filter()` en `app/gui.py`, y
`BATCH_SCOPES` / `DAY_SCOPES` en `app/gui_qt.py`. Si se cambia uno, cambiar los tres (ya
pasó una vez: la GUI quedó en Mon+Tue / Wed+Thu+Fri cuando el engine ya usaba
Mon+Tue+Wed / Thu+Fri).

**La GUI Qt no toca MT5 en el hilo principal.** Todo pasa por `EngineWorker` en su
propio `QThread` y vuelve por señales; `engine.log` se reapunta a una señal para que
todo el pipeline caiga en el drawer de log. Si agregás una llamada a MT5, va como slot
del worker, no en el handler del botón.

## Reglas duras

- **Nunca hardcodear paths ni credenciales de una máquina puntual en el código** — es
  repo público. Todo lo que varíe por usuario va en `config.json` (gitignored),
  siguiendo `config.example.json`.
- **Solo XAU/USD.** El fallback de SL/TP está calibrado a la escala de precio del oro;
  no confiar en eso para otros símbolos aunque el script técnicamente corra.
- **El script Python nunca se conecta a TradingView directamente** — es un bridge de
  datos vía clipboard hacia Claude Code + MCP. No introducir una conexión directa acá
  sin que sea explícitamente pedido (ese camino ya se exploró en
  `../Archivo (obsoleto)/Experimentos` y no se terminó de validar).

## Git — commits y push automáticos

Este repo usa **auto-commit + auto-push**: después de modificar cualquier script acá
(`.py`, `.mq5`, `config.example.json`, etc.), hacer `git add`, commitear con un mensaje
descriptivo del cambio, y pushear a `origin/main` sin pedir confirmación adicional.
Excepción: si el cambio toca lógica de riesgo (SL/TP, validación de trades) de forma
no trivial, mencionárselo al trader en el mismo turno aunque ya se haya pusheado.

## Idioma

Código, README y mensajes de commit van en **inglés** (repo público). La conversación
con el trader sobre este repo es en español.
