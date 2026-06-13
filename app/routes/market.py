from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..collectors.cse import CSELatestPriceCollector
from ..collectors.dse import DSELatestPriceCollector
from ..collectors.dse_index import DSEIndexSnapshotCollector
from ..db import get_db
from ..models import Company, MarketSnapshot, PriceDaily, PriceTick, Sector
from ..schemas import CompanyOut, PriceDailyOut, PriceTickOut

router = APIRouter(prefix="/api", tags=["market"])


@router.get("/companies", response_model=list[CompanyOut])
def list_companies(
    exchange: Optional[str] = Query(None, pattern="^(DSE|CSE)$"),
    q: Optional[str] = Query(None, description="symbol prefix"),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    stmt = select(Company, Sector.name).join(Sector, Company.sector_id == Sector.id, isouter=True)
    if exchange:
        stmt = stmt.where(Company.exchange == exchange)
    if q:
        stmt = stmt.where(Company.symbol.like(f"{q.upper()}%"))
    stmt = stmt.order_by(Company.symbol).limit(limit)
    rows = db.execute(stmt).all()
    return [
        CompanyOut(
            id=c.id, symbol=c.symbol, name=c.name, exchange=c.exchange, sector=sector_name
        )
        for c, sector_name in rows
    ]


@router.get("/prices/{symbol}/ticks", response_model=list[PriceTickOut])
def get_ticks(
    symbol: str,
    hours: int = Query(8, ge=1, le=72),
    db: Session = Depends(get_db),
):
    company = db.execute(
        select(Company).where(Company.symbol == symbol.upper())
    ).scalar_one_or_none()
    if not company:
        raise HTTPException(404, "symbol not found")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        db.execute(
            select(PriceTick)
            .where(PriceTick.company_id == company.id)
            .where(PriceTick.captured_at >= cutoff)
            .order_by(PriceTick.captured_at)
        )
        .scalars()
        .all()
    )
    return rows


@router.get("/prices/{symbol}/daily", response_model=list[PriceDailyOut])
def get_daily(
    symbol: str,
    days: int = Query(180, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    company = db.execute(
        select(Company).where(Company.symbol == symbol.upper())
    ).scalar_one_or_none()
    if not company:
        raise HTTPException(404, "symbol not found")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    rows = (
        db.execute(
            select(PriceDaily)
            .where(PriceDaily.company_id == company.id)
            .where(PriceDaily.trade_date >= cutoff)
            .order_by(PriceDaily.trade_date)
        )
        .scalars()
        .all()
    )
    return rows


@router.get("/market/snapshot")
def market_snapshot(db: Session = Depends(get_db)):
    """Latest index values per exchange + latest tick timestamp."""
    subq = (
        select(
            MarketSnapshot.index_name,
            func.max(MarketSnapshot.captured_at).label("last_at"),
        )
        .group_by(MarketSnapshot.index_name)
        .subquery()
    )
    rows = db.execute(
        select(MarketSnapshot)
        .join(
            subq,
            (MarketSnapshot.index_name == subq.c.index_name)
            & (MarketSnapshot.captured_at == subq.c.last_at),
        )
    ).scalars().all()

    last_tick_at = db.execute(select(func.max(PriceTick.captured_at))).scalar_one()
    return {
        "indices": [
            {
                "exchange": r.exchange,
                "index": r.index_name,
                "value": r.value,
                "change": r.change,
                "change_pct": r.change_pct,
                "captured_at": r.captured_at,
            }
            for r in rows
        ],
        "last_tick_at": last_tick_at,
    }


@router.post("/admin/collect/{which}")
def run_collector_now(which: str):
    """Trigger a collector immediately. Used for manual smoke testing."""
    runners = {
        "dse": DSELatestPriceCollector,
        "cse": CSELatestPriceCollector,
        "dse_index": DSEIndexSnapshotCollector,
    }
    cls = runners.get(which)
    if not cls:
        raise HTTPException(404, f"unknown collector '{which}'")
    res = cls().run()
    return {
        "collector": which,
        "rows_in": res.rows_in,
        "rows_written": res.rows_written,
        "rows_rejected": res.rows_rejected,
    }
