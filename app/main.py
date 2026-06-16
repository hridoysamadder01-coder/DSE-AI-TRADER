from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from loguru import logger

from . import __version__
from .config import get_settings
from .db import init_db
from .logging_setup import setup_logging
from .routes import (
    audit, chart, explain, intel, market, portfolio,
    quality, research, scan, udf, watchlist,
)
from .services.scheduler import build_scheduler


_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    logger.info(f"booting DSE AI Trader OS v{__version__} env={settings.app_env}")
    init_db()
    # Seed DSE sector mapping (idempotent). Required for sector-grouped views.
    try:
        from .data.sectors_seed import run_seed_on_startup
        run_seed_on_startup()
    except Exception as e:
        logger.warning(f"sector seed failed: {e}")
    # Roll today's intraday ticks into price_daily so the 1D chart has a bar.
    try:
        from .services.eod import run_eod_rollup
        run_eod_rollup()
    except Exception as e:
        logger.warning(f"EOD rollup at boot failed: {e}")
    # Backfill index history (DSEX/DSES/DS30) in the background so the chart has
    # ~20 years of data even on an ephemeral disk. Idempotent: skips if already
    # populated. Runs off-thread so it never delays readiness / health checks.
    try:
        import threading

        from .collectors.index_history import (
            backfill_index_history,
            ensure_index_companies,
        )

        ensure_index_companies()

        def _boot_index_backfill() -> None:
            try:
                backfill_index_history(duration_months=240, only_if_sparse=True)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"index backfill (startup) failed: {e}")

        threading.Thread(target=_boot_index_backfill, daemon=True).start()
    except Exception as e:
        logger.warning(f"index backfill bootstrap failed: {e}")
    global _scheduler
    if settings.app_env != "test":
        _scheduler = build_scheduler()
        _scheduler.start()
        logger.info("scheduler started")
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler stopped")


app = FastAPI(
    title="DSE AI Trader OS",
    version=__version__,
    description="Bangladesh AI-Native Market Intelligence OS — Phase 1 Data Foundation",
    lifespan=lifespan,
)

app.include_router(market.router)
app.include_router(quality.router)
app.include_router(scan.router)
app.include_router(intel.router)
app.include_router(chart.router)
app.include_router(udf.router)
app.include_router(research.router)
app.include_router(portfolio.router)
app.include_router(watchlist.router)
app.include_router(audit.router)
app.include_router(explain.router)


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__}


_TEMPLATES = Path(__file__).parent / "templates"


@app.get("/", response_class=HTMLResponse)
def terminal() -> HTMLResponse:
    """User-facing Bloomberg-style trader terminal."""
    return HTMLResponse((_TEMPLATES / "terminal.html").read_text(encoding="utf-8"))


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard() -> HTMLResponse:
    """Internal data-operations dashboard (Phase 1 monitoring)."""
    return HTMLResponse((_TEMPLATES / "admin.html").read_text(encoding="utf-8"))
