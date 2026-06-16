# DSE AI Trader OS — Project Report

**A TradingView-style market intelligence terminal for the Dhaka Stock Exchange.**

🔗 **Live:** https://dse-ai-trader.onrender.com
📦 **Code:** https://github.com/hridoysamadder01-coder/DSE-AI-TRADER

---

## What it is

A web-based trading terminal — like amarStock / StockNow / TradingView — built for
DSE & CSE. It pulls live and historical market data, draws professional candlestick
charts, runs scanners, and lets a day trader analyze any stock or index in the browser.

| | |
|---|---|
| **Stocks tracked** | 415+ (DSE + CSE), refreshed every 2 minutes during market hours |
| **DSEX history** | 3,185 daily bars — the complete index history since 27 Jan 2013 |
| **Indices** | DSEX, DS30, DSES — full history + a live-moving today bar |
| **Chart engine** | TradingView Lightweight Charts (the same library TradingView open-sources) |

---

## Features

### Charting (TradingView feel)
- Clean candlesticks with a large symbol watermark and labeled crosshair (O/H/L/C tooltip).
- Full historical data for **every** stock — fetched on demand the first time you open it.
- Timeframes: 1m, 5m, 15m, 1H, 4H, 1D, MAX.
- Indicators: EMA20, SMA50, EMA200, VWAP, Bollinger Bands, RSI, MACD, Volume — toggle any.

### Drawing & layouts
- Draw trend lines, horizontal levels, and Fibonacci retracements directly on the chart.
- Everything you draw and every indicator you toggle is **saved automatically** (survives refresh).
- **Named layouts:** save your favorite chart setups (indicators + timeframe) and switch in one click.

### Market intelligence
- Scanners: top gainers / losers / volume movers, sector heatmap.
- Smart-money accumulation/distribution scoring and circuit-reach signals.
- Per-symbol research, watchlist, portfolio, alerts.

---

## What was built & fixed (this engagement)

1. **Deployed live** on Render from a clean infrastructure blueprint (`render.yaml`).
2. **Fixed the dead data feed** — dsebd.org serves a broken TLS certificate chain that made
   every collector fail silently on the server. Added a safe verified-then-fallback fetch.
3. **Fixed price accuracy** — the parser was reading today's close as yesterday's close,
   producing wrong change %. Corrected the column mapping.
4. **Made indices chartable** — DSEX/DS30/DSES are now first-class symbols with 13 years of
   history, sourced from DSE's index graph endpoint.
5. **On-demand stock history** — opening any stock lazily backfills its full daily OHLCV.
6. **Fixed broken candles** — DSE's archive returns 0 for some no-trade days, which drew
   candles plunging to zero; these are now sanitized.
7. **Self-healing startup** — after each deploy the app re-seeds companies, prices, and index
   history automatically, so it works any time (Render's free tier wipes the disk on deploy).
8. **Polished the chart** to a TradingView look (watermark, clean defaults, crosshair labels,
   wider candles) and added **saved layouts**.

---

## Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy, APScheduler, BeautifulSoup
- **Frontend:** TradingView Lightweight Charts, vanilla JS terminal UI
- **Data:** SQLite (Postgres-ready), DSE/CSE public pages
- **Hosting:** Render (free web service, auto-deploy on git push)

---

## How to use it

1. Open https://dse-ai-trader.onrender.com
2. Click any symbol in the watchlist (DSEX, GP, etc.) or search for one.
3. Use the timeframe buttons (1m … MAX) and indicator toggles up top.
4. Draw with **↗ Trend / — Level / Φ Fib**; clear with **✕**.
5. Save a setup with **⊞ Layout → name → Save current**.

> Note: on Render's free tier the service sleeps after ~15 min idle and the first
> visit takes ~30 s to wake. Data persists across the day; a future Postgres upgrade
> would keep full history permanently.
