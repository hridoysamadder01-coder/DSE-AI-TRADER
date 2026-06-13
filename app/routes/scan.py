"""Market scanner endpoints powered by real DSE/CSE ticks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, aliased

from ..db import get_db
from ..models import Company, PriceTick, Sector

router = APIRouter(prefix="/api/scan", tags=["scan"])


def _latest_tick_per_company(db: Session, exchange: str | None):
    """Return rows of (PriceTick, Company, sector_name) for the latest tick of
    every company in the last 6 hours, optionally filtered by exchange."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=6)

    latest = (
        select(
            PriceTick.company_id,
            func.max(PriceTick.captured_at).label("last_at"),
        )
        .where(PriceTick.captured_at >= cutoff)
        .group_by(PriceTick.company_id)
        .subquery()
    )

    stmt = (
        select(PriceTick, Company, Sector.name)
        .join(
            latest,
            (PriceTick.company_id == latest.c.company_id)
            & (PriceTick.captured_at == latest.c.last_at),
        )
        .join(Company, Company.id == PriceTick.company_id)
        .join(Sector, Sector.id == Company.sector_id, isouter=True)
    )
    if exchange:
        stmt = stmt.where(Company.exchange == exchange)
    return db.execute(stmt).all()


def _row(t: PriceTick, c: Company, sector: str | None) -> dict:
    return {
        "symbol": c.symbol,
        "exchange": c.exchange,
        "sector": sector,
        "ltp": t.ltp,
        "ycp": t.ycp,
        "change": t.change,
        "change_pct": t.change_pct,
        "volume": t.volume,
        "value_bdt": t.value_bdt,
        "trades": t.trades,
        "captured_at": t.captured_at,
    }


@router.get("/movers")
def movers(
    exchange: str | None = Query(None, pattern="^(DSE|CSE)$"),
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Top gainers, top losers, and most active by value."""
    rows = [_row(t, c, s) for t, c, s in _latest_tick_per_company(db, exchange)]
    with_change = [r for r in rows if r["change_pct"] is not None]
    by_value = [r for r in rows if r["value_bdt"] is not None]
    by_volume = [r for r in rows if r["volume"] is not None]

    gainers = sorted(with_change, key=lambda r: r["change_pct"], reverse=True)[:limit]
    losers = sorted(with_change, key=lambda r: r["change_pct"])[:limit]
    actives_value = sorted(by_value, key=lambda r: r["value_bdt"], reverse=True)[:limit]
    actives_volume = sorted(by_volume, key=lambda r: r["volume"], reverse=True)[:limit]

    return {
        "as_of": rows[0]["captured_at"] if rows else None,
        "total_symbols": len(rows),
        "gainers": gainers,
        "losers": losers,
        "actives_value": actives_value,
        "actives_volume": actives_volume,
    }


@router.get("/heatmap")
def heatmap(
    exchange: str | None = Query(None, pattern="^(DSE|CSE)$"),
    db: Session = Depends(get_db),
):
    """All latest LTPs with % change — for the heatmap grid."""
    rows = [_row(t, c, s) for t, c, s in _latest_tick_per_company(db, exchange)]
    # Group by sector for the visual layout.
    by_sector: dict[str, list[dict]] = {}
    for r in rows:
        key = r["sector"] or "Uncategorized"
        by_sector.setdefault(key, []).append(r)
    # Sort each sector by absolute value traded (size of tile).
    for k in by_sector:
        by_sector[k].sort(
            key=lambda r: r["value_bdt"] or 0, reverse=True
        )
    return {
        "as_of": rows[0]["captured_at"] if rows else None,
        "total_symbols": len(rows),
        "sectors": [
            {"name": name, "symbols": syms} for name, syms in sorted(by_sector.items())
        ],
    }


@router.get("/sector_browser")
def sector_browser(db: Session = Depends(get_db)):
    """Stocks Now / Amar Stocks-style sector browser.

    Returns each sector with its constituent stocks (real-time LTP + Δ%),
    sorted by latest turnover. Click drill-down on the frontend.
    """
    rows = [_row(t, c, s) for t, c, s in _latest_tick_per_company(db, None)]
    by_sector: dict[str, list[dict]] = {}
    for r in rows:
        by_sector.setdefault(r["sector"] or "Uncategorized", []).append(r)
    out = []
    for name, stocks in sorted(by_sector.items()):
        stocks.sort(key=lambda r: r["value_bdt"] or 0, reverse=True)
        pos = sum(1 for s in stocks if (s["change_pct"] or 0) > 0)
        neg = sum(1 for s in stocks if (s["change_pct"] or 0) < 0)
        flat = len(stocks) - pos - neg
        valid = [s["change_pct"] for s in stocks if s["change_pct"] is not None]
        avg = round(sum(valid) / len(valid), 3) if valid else None
        turnover = sum(s["value_bdt"] or 0 for s in stocks)
        out.append({
            "sector": name,
            "count": len(stocks),
            "winners": pos, "losers": neg, "flat": flat,
            "avg_change_pct": avg,
            "turnover_bdt": round(turnover, 2),
            "stocks": stocks,
        })
    out.sort(key=lambda s: s["turnover_bdt"], reverse=True)
    return {"as_of": rows[0]["captured_at"] if rows else None, "sectors": out}


@router.get("/sectors")
def sector_rotation(db: Session = Depends(get_db)):
    """Average % change per sector — proxy for sector rotation."""
    rows = [_row(t, c, s) for t, c, s in _latest_tick_per_company(db, None)]
    buckets: dict[str, list[float]] = {}
    values: dict[str, float] = {}
    counts: dict[str, int] = {}
    for r in rows:
        key = r["sector"] or "Uncategorized"
        if r["change_pct"] is not None:
            buckets.setdefault(key, []).append(r["change_pct"])
        values[key] = values.get(key, 0.0) + (r["value_bdt"] or 0.0)
        counts[key] = counts.get(key, 0) + 1
    out = []
    for name, pcts in buckets.items():
        if not pcts:
            continue
        out.append(
            {
                "sector": name,
                "avg_change_pct": round(sum(pcts) / len(pcts), 3),
                "winners": sum(1 for p in pcts if p > 0),
                "losers": sum(1 for p in pcts if p < 0),
                "symbol_count": counts.get(name, len(pcts)),
                "turnover_bdt": round(values.get(name, 0.0), 2),
            }
        )
    out.sort(key=lambda r: r["avg_change_pct"], reverse=True)
    return {"sectors": out}
