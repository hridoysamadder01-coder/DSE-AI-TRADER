# DSE AI Trader OS

Bangladesh AI-Native Market Intelligence Operating System.

> **Status:** Phase 1 — Data Foundation. Reliable DSE/CSE collection, validation,
> quality monitoring, and a quality dashboard. No AI features yet — by design.

---

## What this is

A Python service that:

- Polls DSE and CSE every 2 minutes during trading hours (Asia/Dhaka,
  Sun–Thu, 10:00–14:30).
- Captures per-symbol intraday ticks (LTP, high, low, ycp, volume, value,
  trades) and DSE index snapshots (DSEX, DS30, DSES).
- Validates every tick before persisting (range, sign, high/low sanity,
  extreme % change).
- Rolls intraday ticks into a daily OHLCV table after market close.
- Tracks every collector run and every quality issue in the database.
- Exposes a FastAPI JSON API and a live quality dashboard at `/`.

No AI logic yet. The next phases (Market Intelligence, Smart Money,
Circuit Intelligence, etc.) build *on top* of this data layer.

---

## Quick start

### 1. Install Python 3.11+

If `python` isn't on your PATH yet (Windows often has a Microsoft Store
stub), install the real interpreter from <https://www.python.org/downloads/>.

### 2. Create a virtualenv and install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

(macOS / Linux: `source .venv/bin/activate`.)

### 3. Configure

```powershell
copy .env.example .env
```

The defaults work out of the box — SQLite under `./data/market.db`,
server on `127.0.0.1:8000`.

### 4. Run

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000/> for the data-quality dashboard.

---

## Endpoints

| Method | Path | What |
| ------ | ---- | ---- |
| GET    | `/`                              | Quality dashboard (HTML) |
| GET    | `/health`                        | Liveness probe |
| GET    | `/api/companies`                 | List companies (`exchange`, `q`, `limit`) |
| GET    | `/api/prices/{symbol}/ticks`     | Intraday ticks (`hours`, default 8) |
| GET    | `/api/prices/{symbol}/daily`     | EOD OHLCV (`days`, default 180) |
| GET    | `/api/market/snapshot`           | Latest index values + last tick time |
| GET    | `/api/quality/report`            | One-shot summary of pipeline health |
| GET    | `/api/quality/runs`              | Recent collector runs |
| GET    | `/api/quality/issues`            | Recent quality issues (`hours`, `severity`) |
| POST   | `/api/quality/check`             | Trigger quality monitor on-demand |
| POST   | `/api/admin/collect/{dse,cse,dse_index}` | Trigger a collector immediately |

---

## Data model

- `companies`, `sectors` — symbol registry.
- `price_ticks` — intraday snapshots (immutable, append-only).
- `price_daily` — one row per (company, trade_date) — produced by the EOD
  rollup, idempotent.
- `market_snapshots` — index-level rows (DSEX, DS30, DSES).
- `collection_runs` — every collector invocation (status, timing, counts).
- `data_quality_log` — every validation rejection / anomaly.

Schema is Postgres-ready. For production, set
`DATABASE_URL=postgresql+psycopg://user:pw@host/db` and
`pip install -e ".[postgres]"`.

---

## Scheduled jobs (Asia/Dhaka)

| Job | Cadence | Gated to market hours |
| --- | ------- | --------------------- |
| `dse_intraday`  | every 2 min | yes |
| `cse_intraday`  | every 2 min | yes |
| `dse_index`     | every 5 min | yes |
| `eod_rollup`    | 16:30 Sun–Thu | n/a |
| `quality_check` | every 15 min | always runs |

---

## Tests

```powershell
pip install -e ".[dev]"
pytest -q
```

The tests don't hit the network — they exercise the parser and validator
against inline HTML and synthetic ticks.

---

## Project layout

```
app/
  main.py              FastAPI app + lifespan (scheduler boot)
  config.py            Settings (pydantic-settings, .env)
  db.py                SQLAlchemy engine + session
  models.py            ORM tables
  schemas.py           Pydantic DTOs
  logging_setup.py     Loguru config
  collectors/
    http.py            tenacity-wrapped GET
    base.py            Run tracking + persistence + quality logging
    dse.py             dsebd.org latest share price parser
    cse.py             cse.com.bd latest share price parser
    dse_index.py       DSEX / DS30 / DSES snapshot parser
  services/
    validation.py      Per-tick checks
    eod.py             Intraday → daily rollup
    quality.py         Stale-tick / no-run / index-missing monitor
    scheduler.py       APScheduler wiring + market-hours gating
  routes/
    market.py          /api/companies, /api/prices/*, /api/market/snapshot
    quality.py         /api/quality/{runs,issues,report,check}
  templates/
    dashboard.html     /
tests/
  test_dse_parser.py
  test_validation.py
```

---

## Phase 1 done — what's next

This data foundation is the prerequisite for every later phase:

- **Phase 2 — Market Intelligence**: scanner (gainers/losers/volume-surge),
  sector engine, circuit probability, smart-money score.
- **Phase 3 — Company Research**: financial fundamentals, health/valuation
  scores, SWOT.
- **Phase 4+** — watchlists, portfolio OS, news intelligence, Obsidian
  brain, knowledge graph, voice, institutional terminal.

Every later phase reads from the same `price_ticks` / `price_daily` /
`market_snapshots` tables built here.
