"""DSE index history + pseudo-company backfill.

Source: ``/php_graph/monthly_graph_index.php?type={dseX|dseS|ds30}&duration={months}``
which embeds a Dygraph CSV of daily index closing values (the homepage marquee
loads the live value separately via AJAX; this page is the long history). With
``duration=240`` it returns ~20 years of daily closes.

We materialize each index as a pseudo-``Company`` (symbol ``DSEX`` / ``DSES`` /
``DS30`` under a dedicated ``Index`` sector) and store its daily closes in
``price_daily``. The existing chart / search / indicator stack then treats an
index exactly like a stock — no special plumbing needed downstream. Indices are
deliberately NOT written to ``price_ticks`` so they never pollute scanners.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope
from ..models import CollectionRun, Company, PriceDaily, Sector
from .http import fetch_html

GRAPH_PATH = "/php_graph/monthly_graph_index.php"
INDEX_SECTOR = "Index"

# pseudo-symbol -> (graph `type` query param, display name)
INDEX_DEFS: dict[str, tuple[str, str]] = {
    "DSEX": ("dseX", "DSE Broad Index"),
    "DSES": ("dseS", "DSE Shariah Index"),
    "DS30": ("ds30", "DSE 30 Index"),
}
INDEX_SYMBOLS = set(INDEX_DEFS)

_ROW_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*,\s*(-?\d+(?:\.\d+)?)")


def is_index_symbol(symbol: str | None) -> bool:
    return bool(symbol) and symbol.upper() in INDEX_SYMBOLS


def fetch_index_series(graph_type: str, duration_months: int) -> list[tuple[date, float]]:
    """Return ascending [(date, close), ...] parsed from the dygraph CSV blob."""
    settings = get_settings()
    url = f"{settings.dse_base_url}{GRAPH_PATH}?type={graph_type}&duration={duration_months}"
    html = fetch_html(url, timeout=45)
    by_date: dict[date, float] = {}
    for m in _ROW_RE.finditer(html):
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            v = float(m.group(2))
        except ValueError:
            continue
        if v > 0:
            by_date[d] = v  # last wins on dup dates
    return sorted(by_date.items())


def _get_or_create_index_company(s: Session, symbol: str, name: str) -> Company:
    row = s.execute(select(Company).where(Company.symbol == symbol)).scalar_one_or_none()
    if row:
        if row.name != name:
            row.name = name
        return row
    sector = s.execute(
        select(Sector).where(Sector.name == INDEX_SECTOR)
    ).scalar_one_or_none()
    if sector is None:
        sector = Sector(name=INDEX_SECTOR)
        s.add(sector)
        s.flush()
    row = Company(symbol=symbol, name=name, exchange="DSE", sector_id=sector.id)
    s.add(row)
    s.flush()
    return row


def ensure_index_companies() -> None:
    """Create the DSEX/DSES/DS30 pseudo-companies if absent (cheap, idempotent)."""
    with session_scope() as s:
        for symbol, (_gtype, name) in INDEX_DEFS.items():
            _get_or_create_index_company(s, symbol, name)


def backfill_index_history(
    duration_months: int = 240, only_if_sparse: bool = False
) -> dict:
    """Populate price_daily for every index from the graph CSV.

    Daily index bars carry only a close, so we synthesize a clean OHLC where the
    body spans yesterday's close -> today's close (green up / red down) with no
    fabricated wicks. ``only_if_sparse`` skips an index that already has a full
    history (used on startup so warm restarts don't re-scrape).
    """
    started = datetime.now(timezone.utc)
    with session_scope() as s:
        run = CollectionRun(collector="index_history_backfill", status="running")
        s.add(run)
        s.flush()
        run_id = run.id

    summary: dict[str, dict] = {}
    total_ins = total_upd = 0

    for symbol, (gtype, name) in INDEX_DEFS.items():
        try:
            with session_scope() as s:
                company = _get_or_create_index_company(s, symbol, name)
                cid = company.id
                if only_if_sparse:
                    have = s.execute(
                        select(func.count(PriceDaily.id)).where(
                            PriceDaily.company_id == cid
                        )
                    ).scalar_one()
                    if have and have > 200:
                        summary[symbol] = {"skipped_have": have}
                        continue

            series = fetch_index_series(gtype, duration_months)
            ins = upd = 0
            with session_scope() as s:
                existing = {
                    pd.trade_date: pd
                    for pd in s.execute(
                        select(PriceDaily).where(PriceDaily.company_id == cid)
                    ).scalars().all()
                }
                prev = None
                for d, v in series:
                    ycp = prev
                    open_ = ycp if ycp is not None else v
                    high = max(open_, v)
                    low = min(open_, v)
                    row = existing.get(d)
                    if row:
                        if row.source == "dse_index_history":
                            row.open, row.high, row.low = open_, high, low
                            row.close, row.ycp = v, ycp
                            upd += 1
                    else:
                        s.add(
                            PriceDaily(
                                company_id=cid,
                                trade_date=d,
                                open=open_,
                                high=high,
                                low=low,
                                close=v,
                                ycp=ycp,
                                volume=None,
                                trades=None,
                                value_bdt=None,
                                source="dse_index_history",
                            )
                        )
                        ins += 1
                    prev = v
            total_ins += ins
            total_upd += upd
            summary[symbol] = {"points": len(series), "inserted": ins, "updated": upd}
            logger.info(f"index backfill {symbol}: {len(series)} pts +{ins} ~{upd}")
        except Exception as e:  # noqa: BLE001
            summary[symbol] = {"error": str(e)[:200]}
            logger.warning(f"index backfill {symbol} failed: {e}")

    with session_scope() as s:
        run = s.get(CollectionRun, run_id)
        if run:
            run.status = "ok"
            run.finished_at = datetime.now(timezone.utc)
            run.rows_written = total_ins + total_upd
            run.duration_ms = int(
                (datetime.now(timezone.utc) - started).total_seconds() * 1000
            )
            run.message = str(summary)[:1000]

    return summary
