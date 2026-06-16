"""Chart data: aggregated OHLCV candles.

- For resolution_min < 240 (i.e. 1m..1H): aggregate intraday PriceTick rows.
- For resolution_min >= 240 (4H, 1D): read directly from PriceDaily (EOD rollup)
  and synthesize bars. This gives a real chart even when the user has only a
  handful of intraday ticks today.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..collectors.index_history import is_index_symbol
from ..db import get_db
from ..models import Company, MarketSnapshot, PriceDaily, PriceTick

router = APIRouter(prefix="/api/chart", tags=["chart"])


def _daily_bars(db: Session, company_id: int, days: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    rows = (
        db.execute(
            select(PriceDaily)
            .where(PriceDaily.company_id == company_id)
            .where(PriceDaily.trade_date >= cutoff)
            .order_by(PriceDaily.trade_date)
        )
        .scalars()
        .all()
    )
    out = []
    for r in rows:
        ts = int(datetime(r.trade_date.year, r.trade_date.month, r.trade_date.day,
                          tzinfo=timezone.utc).timestamp())
        out.append({
            "time": ts,
            "open": r.open if r.open is not None else r.ycp,
            "high": r.high if r.high is not None else r.close,
            "low":  r.low  if r.low  is not None else r.close,
            "close": r.close if r.close is not None else r.ycp,
            "volume": r.volume or 0,
            "value_bdt": r.value_bdt or 0.0,
        })
    return out


def _intraday_bars(db: Session, company_id: int, hours: int, resolution_min: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    ticks = (
        db.execute(
            select(PriceTick)
            .where(PriceTick.company_id == company_id)
            .where(PriceTick.captured_at >= cutoff)
            .order_by(PriceTick.captured_at)
        )
        .scalars()
        .all()
    )
    if not ticks:
        return []
    bucket_sec = resolution_min * 60
    bars: dict[int, dict] = {}
    last_cum_vol: int | None = None
    bucket_start_cum_vol: dict[int, int] = {}
    for t in ticks:
        if t.ltp is None:
            continue
        ts = int(t.captured_at.replace(tzinfo=timezone.utc).timestamp()) \
            if t.captured_at.tzinfo is None else int(t.captured_at.timestamp())
        bkey = ts - (ts % bucket_sec)
        if bkey not in bars:
            bars[bkey] = {"time": bkey, "open": t.ltp, "high": t.ltp,
                          "low": t.ltp, "close": t.ltp, "volume": 0, "value_bdt": 0.0}
            bucket_start_cum_vol[bkey] = last_cum_vol if last_cum_vol is not None else (t.volume or 0)
        b = bars[bkey]
        b["high"] = max(b["high"], t.ltp)
        b["low"]  = min(b["low"], t.ltp)
        b["close"] = t.ltp
        if t.volume is not None:
            b["volume"] = max(0, t.volume - bucket_start_cum_vol[bkey])
            last_cum_vol = t.volume
        if t.value_bdt is not None:
            b["value_bdt"] = t.value_bdt
    return [bars[k] for k in sorted(bars)]


def _index_intraday_bars(
    db: Session, index_name: str, hours: int, resolution_min: int
) -> list[dict]:
    """Aggregate market_snapshots (one value per poll) into OHLC buckets.

    Indices have no per-tick volume, so bars carry price only. History only
    accumulates going forward (from the 5-min index poller)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        db.execute(
            select(MarketSnapshot)
            .where(MarketSnapshot.index_name == index_name)
            .where(MarketSnapshot.captured_at >= cutoff)
            .order_by(MarketSnapshot.captured_at)
        )
        .scalars()
        .all()
    )
    bucket_sec = resolution_min * 60
    bars: dict[int, dict] = {}
    for r in rows:
        if r.value is None:
            continue
        ts = int(
            r.captured_at.replace(tzinfo=timezone.utc).timestamp()
            if r.captured_at.tzinfo is None
            else r.captured_at.timestamp()
        )
        bkey = ts - (ts % bucket_sec)
        b = bars.get(bkey)
        if b is None:
            bars[bkey] = {
                "time": bkey, "open": r.value, "high": r.value,
                "low": r.value, "close": r.value, "volume": 0, "value_bdt": 0.0,
            }
        else:
            b["high"] = max(b["high"], r.value)
            b["low"] = min(b["low"], r.value)
            b["close"] = r.value
    return [bars[k] for k in sorted(bars)]


def _append_live_index_bar(db: Session, index_name: str, bars: list[dict]) -> list[dict]:
    """Make today's daily index bar move live off the latest snapshot value."""
    snap = db.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.index_name == index_name)
        .order_by(desc(MarketSnapshot.captured_at))
        .limit(1)
    ).scalar_one_or_none()
    if snap is None or snap.value is None:
        return bars
    cap = snap.captured_at
    cap = cap.replace(tzinfo=timezone.utc) if cap.tzinfo is None else cap
    day_ts = int(datetime(cap.year, cap.month, cap.day, tzinfo=timezone.utc).timestamp())
    v = snap.value
    if bars and bars[-1]["time"] >= day_ts:
        last = bars[-1]
        last["high"] = max(last["high"], v)
        last["low"] = min(last["low"], v)
        last["close"] = v
    else:
        prev_close = bars[-1]["close"] if bars else v
        bars.append({
            "time": day_ts, "open": prev_close,
            "high": max(prev_close, v), "low": min(prev_close, v),
            "close": v, "volume": 0, "value_bdt": 0.0,
        })
    return bars


def _lazy_backfill_stock(db: Session, company: Company, days: int = 1825) -> None:
    """On first chart view of a DSE stock with no stored history, scrape its
    full daily OHLCV from day_end_archive and persist it. Cheap & one-off: after
    the first load price_daily is populated so this won't re-trigger."""
    if company.exchange != "DSE":
        return
    from ..collectors.dse_history import _upsert_daily, fetch_symbol_history

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    rows = fetch_symbol_history(company.symbol, start, end)
    if rows:
        _upsert_daily(db, company.id, rows)
        db.flush()


@router.get("/{symbol}/candles")
def candles(
    symbol: str,
    resolution_min: int = Query(5, ge=1, le=1440),
    hours: int = Query(24, ge=1, le=200000),     # up to ~22 years (index history)
    db: Session = Depends(get_db),
):
    company = db.execute(
        select(Company).where(Company.symbol == symbol.upper())
    ).scalar_one_or_none()
    if not company:
        raise HTTPException(404, "symbol not found")

    sym = company.symbol
    is_index = is_index_symbol(sym)

    if resolution_min >= 240:
        # Long timeframes: daily OHLCV. For an index that's the backfilled
        # history; append a live-moving bar for today off the latest snapshot.
        days = max(7, hours // 24)
        bars = _daily_bars(db, company.id, days)
        if is_index:
            bars = _append_live_index_bar(db, sym, bars)
        elif len(bars) < 30:
            # Stock with no history yet — fetch it on demand, then re-read.
            try:
                _lazy_backfill_stock(db, company)
                bars = _daily_bars(db, company.id, days)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"lazy backfill {sym} failed: {e}")
        source = "price_daily"
    elif is_index:
        bars = _index_intraday_bars(db, sym, hours, resolution_min)
        source = "market_snapshots"
    else:
        bars = _intraday_bars(db, company.id, hours, resolution_min)
        source = "price_ticks"

    return {
        "symbol": company.symbol,
        "exchange": company.exchange,
        "resolution_min": resolution_min,
        "is_index": is_index,
        "source": source,
        "bars": bars,
    }
