"""DSE historical OHLCV collector.

Source: https://www.dsebd.org/day_end_archive.php?startDate=…&endDate=…&archive=data&inst={SYM}

The archive page returns a multi-year history table:
    #, date, trading code, ltp*, high, low, openp*, closep*, ycp, trade, value (mn), volume

We parse rows and upsert into `price_daily` (idempotent by `uq_daily_company_date`).
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone

from bs4 import BeautifulSoup
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope
from ..models import CollectionRun, Company, PriceDaily, Sector
from .dse import _to_float, _to_int
from .http import fetch_html


ARCHIVE_PATH = "/day_end_archive.php"


def _parse_archive_html(html: str, symbol: str) -> list[dict]:
    """Return list of dicts: {trade_date, ltp, high, low, open, close, ycp, trades, value_bdt, volume}."""
    soup = BeautifulSoup(html, "lxml")
    target = None
    for t in soup.find_all("table"):
        headers = " ".join(th.get_text(strip=True).lower() for th in t.find_all("th"))
        if "date" in headers and "closep" in headers:
            target = t
            break
    if target is None:
        return []

    headers = [th.get_text(strip=True).lower() for th in target.find_all("th")]
    idx = {h: i for i, h in enumerate(headers)}

    def col(*names) -> int | None:
        for n in names:
            if n in idx:
                return idx[n]
            for h, i in idx.items():
                if n in h:
                    return i
        return None

    i_date = col("date")
    i_ltp = col("ltp")
    i_high = col("high")
    i_low = col("low")
    i_open = col("openp", "open")
    i_close = col("closep", "close")
    i_ycp = col("ycp")
    i_trade = col("trade")
    i_value = col("value (mn)", "value")
    i_volume = col("volume")

    out: list[dict] = []
    for tr in target.find_all("tr")[1:]:  # skip header
        tds = tr.find_all("td")
        if len(tds) < 6 or i_date is None:
            continue
        date_str = tds[i_date].get_text(strip=True)
        try:
            trade_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        def cell(i: int | None) -> str | None:
            return tds[i].get_text(strip=True) if (i is not None and i < len(tds)) else None

        value_mn = _to_float(cell(i_value))
        out.append({
            "trade_date": trade_date,
            "ltp":  _to_float(cell(i_ltp)),
            "high": _to_float(cell(i_high)),
            "low":  _to_float(cell(i_low)),
            "open": _to_float(cell(i_open)),
            "close": _to_float(cell(i_close)),
            "ycp":  _to_float(cell(i_ycp)),
            "trades": _to_int(cell(i_trade)),
            "value_bdt": value_mn * 1_000_000 if value_mn is not None else None,
            "volume": _to_int(cell(i_volume)),
        })
    return out


def fetch_symbol_history(symbol: str, start: date, end: date) -> list[dict]:
    settings = get_settings()
    url = (
        f"{settings.dse_base_url}{ARCHIVE_PATH}"
        f"?startDate={start.isoformat()}&endDate={end.isoformat()}"
        f"&archive=data&inst={symbol}"
    )
    html = fetch_html(url, timeout=30)
    return _parse_archive_html(html, symbol)


def _upsert_daily(s: Session, company_id: int, rows: list[dict]) -> tuple[int, int]:
    """Returns (inserted, updated)."""
    inserted = updated = 0
    if not rows:
        return 0, 0
    # De-dupe by trade_date — DSE occasionally lists corrections/duplicates;
    # keep the first occurrence (the page is ordered newest→oldest).
    seen: set = set()
    deduped: list[dict] = []
    for r in rows:
        if r["trade_date"] in seen:
            continue
        seen.add(r["trade_date"])
        deduped.append(r)
    rows = deduped
    dates = [r["trade_date"] for r in rows]
    existing = s.execute(
        select(PriceDaily).where(
            PriceDaily.company_id == company_id,
            PriceDaily.trade_date.in_(dates),
        )
    ).scalars().all()
    by_date = {pd.trade_date: pd for pd in existing}

    for r in rows:
        # Skip rows with no usable price
        if r["close"] is None and r["ltp"] is None:
            continue
        close = r["close"] if r["close"] is not None else r["ltp"]
        existing_row = by_date.get(r["trade_date"])
        if existing_row:
            # Only overwrite if source is from this collector (don't trample today's
            # intraday rollup if the archive has incomplete data for today).
            if existing_row.source == "dse_history":
                existing_row.open = r["open"]
                existing_row.high = r["high"]
                existing_row.low = r["low"]
                existing_row.close = close
                existing_row.ycp = r["ycp"]
                existing_row.volume = r["volume"]
                existing_row.trades = r["trades"]
                existing_row.value_bdt = r["value_bdt"]
                updated += 1
        else:
            s.add(PriceDaily(
                company_id=company_id,
                trade_date=r["trade_date"],
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=close,
                ycp=r["ycp"],
                volume=r["volume"],
                trades=r["trades"],
                value_bdt=r["value_bdt"],
                source="dse_history",
            ))
            inserted += 1
    return inserted, updated


def backfill_all(
    days_back: int = 720,
    rate_limit_seconds: float = 0.4,
    symbols: list[str] | None = None,
    skip_existing: bool = True,
) -> dict:
    """One-shot backfill across every DSE company.

    Args:
        days_back: how far back to request (default ~2 years).
        rate_limit_seconds: polite delay between symbol requests.
        symbols: subset to backfill (None = all DSE companies).
        skip_existing: if a symbol already has ≥days_back × 0.5 daily rows, skip it (idempotent re-runs).
    """
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days_back)

    started_at = datetime.now(timezone.utc)

    with session_scope() as s:
        run = CollectionRun(collector="dse_history_backfill", status="running")
        s.add(run); s.flush()
        run_id = run.id

        # Pick targets
        q = select(Company).where(Company.exchange == "DSE")
        if symbols:
            q = q.where(Company.symbol.in_([x.upper() for x in symbols]))
        companies = s.execute(q.order_by(Company.symbol)).scalars().all()
        company_ids = [(c.id, c.symbol) for c in companies]

    total_inserted = total_updated = total_skipped = total_failed = 0
    processed = 0

    for cid, sym in company_ids:
        # Idempotency check
        if skip_existing:
            with session_scope() as s:
                count = s.execute(
                    select(PriceDaily).where(PriceDaily.company_id == cid)
                ).scalars().all()
                if len(count) >= int(days_back * 0.5):
                    total_skipped += 1
                    processed += 1
                    if processed % 25 == 0:
                        logger.info(
                            f"backfill progress: {processed}/{len(company_ids)} "
                            f"(inserted={total_inserted} updated={total_updated} "
                            f"skipped={total_skipped} failed={total_failed})"
                        )
                    continue

        try:
            rows = fetch_symbol_history(sym, start_date, end_date)
            with session_scope() as s:
                ins, upd = _upsert_daily(s, cid, rows)
                total_inserted += ins
                total_updated += upd
            logger.debug(f"  {sym}: {len(rows)} rows scraped, +{ins} inserted, ~{upd} updated")
        except Exception as e:
            total_failed += 1
            logger.warning(f"  {sym}: FAILED — {e}")

        processed += 1
        if processed % 25 == 0:
            logger.info(
                f"backfill progress: {processed}/{len(company_ids)} "
                f"(inserted={total_inserted} updated={total_updated} "
                f"skipped={total_skipped} failed={total_failed})"
            )
        time.sleep(rate_limit_seconds)

    duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    with session_scope() as s:
        run = s.get(CollectionRun, run_id)
        if run:
            run.status = "ok"
            run.finished_at = datetime.now(timezone.utc)
            run.duration_ms = duration_ms
            run.rows_in = len(company_ids)
            run.rows_written = total_inserted + total_updated
            run.rows_rejected = total_failed
            run.message = (
                f"inserted={total_inserted} updated={total_updated} "
                f"skipped={total_skipped} failed={total_failed} "
                f"duration={duration_ms}ms"
            )

    summary = {
        "symbols_processed": len(company_ids),
        "inserted": total_inserted,
        "updated": total_updated,
        "skipped_already_full": total_skipped,
        "failed": total_failed,
        "duration_seconds": round(duration_ms / 1000, 1),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    logger.info(f"backfill complete: {summary}")
    return summary
