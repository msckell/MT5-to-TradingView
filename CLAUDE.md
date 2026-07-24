# CLAUDE.md — MT5 to TradingView

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

- **Python 3.10+** — `MetaTrader5`, `pytz`, `pyperclip` (ver `requirements.txt`).
- **`config.json`** (gitignored — cada máquina tiene el suyo, copiado de
  `config.example.json`). Nunca commitear `config.json` real ni el `sltp_log.csv`.
- **MQL5** — `SLTPLogger.mq5`, Expert Advisor que corre en MT5 y loguea SL/TP en vivo
  (MT5 no retiene el SL original ni el TP final de una posición cerrada/trailed).
- **Node.js** — el MCP `tradingview-mcp` (`tradesdontlie/tradingview-mcp`, no vive en
  este repo) es quien efectivamente dibuja en TradingView vía CDP.

## Cómo se corre

`Run Terminal.bat` (chequea/instala deps y corre `mt5_to_tradingview.py`) o
`python mt5_to_tradingview.py` directo. Requiere MT5 abierto y logueado, y el EA
`SLTPLogger.mq5` corriendo si se quiere el SL/TP real (si no, cae a defaults de
`config.json`).

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
