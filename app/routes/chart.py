"""Chart data: aggregated OHLCV candles.

- For resolution_min < 240 (i.e. 1m..1H): aggregate intraday PriceTick rows.
- For resolution_min >= 240 (4H, 1D): read directly from PriceDaily (EOD rollup)
  and synthesize bars. This gives a real chart even when the user has only a
  handful of intraday ticks today.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Company, PriceDaily, PriceTick

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


@router.get("/{symbol}/candles")
def candles(
    symbol: str,
    resolution_min: int = Query(5, ge=1, le=1440),
    hours: int = Query(24, ge=1, le=26280),     # up to 3 years
    db: Session = Depends(get_db),
):
    company = db.execute(
        select(Company).where(Company.symbol == symbol.upper())
    ).scalar_one_or_none()
    if not company:
        raise HTTPException(404, "symbol not found")

    # For long timeframes, prefer daily OHLCV (EOD rollup).
    if resolution_min >= 240:
        days = max(7, hours // 24)
        bars = _daily_bars(db, company.id, days)
        source = "price_daily"
    else:
        bars = _intraday_bars(db, company.id, hours, resolution_min)
        source = "price_ticks"

    return {
        "symbol": company.symbol,
        "exchange": company.exchange,
        "resolution_min": resolution_min,
        "source": source,
        "bars": bars,
    }
