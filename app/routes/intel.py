from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Company, PriceTick, Sector
from ..services.intel import compute_intel, compute_overlays

router = APIRouter(prefix="/api/intel", tags=["intel"])


@router.get("/{symbol}")
def intel(symbol: str, db: Session = Depends(get_db)):
    out = compute_intel(db, symbol)
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


@router.get("/{symbol}/overlays")
def overlays(symbol: str, db: Session = Depends(get_db)):
    out = compute_overlays(db, symbol)
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


@router.get("/scan/smart_money")
def smart_money_scan(
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Compute smart-money score for every symbol with a recent tick and return
    top accumulation and top distribution candidates.

    Note: this iterates per-symbol — fine for ~800 BD symbols. We cap by ticks
    in the last 6h to avoid unnecessary work.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
    symbols = db.execute(
        select(Company.symbol)
        .join(PriceTick, PriceTick.company_id == Company.id)
        .where(PriceTick.captured_at >= cutoff)
        .distinct()
    ).scalars().all()
    rows = []
    for s in symbols:
        d = compute_intel(db, s)
        if "error" in d:
            continue
        score = d["scores"]["smart_money"]["value"]
        if score is None:
            continue
        rows.append(
            {
                "symbol": s,
                "exchange": d["exchange"],
                "ltp": d["price"]["ltp"],
                "change_pct": d["price"]["change_pct"],
                "smart_money": score,
                "label": d["scores"]["smart_money"]["label"],
                "volume_anomaly": d["scores"]["volume_anomaly"]["value"],
            }
        )
    accumulation = sorted(rows, key=lambda r: r["smart_money"], reverse=True)[:limit]
    distribution = sorted(rows, key=lambda r: r["smart_money"])[:limit]
    return {
        "scanned": len(rows),
        "accumulation": accumulation,
        "distribution": distribution,
    }


@router.get("/scan/circuit")
def circuit_scan(
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Symbols closest to upper / lower circuit."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
    symbols = db.execute(
        select(Company.symbol)
        .join(PriceTick, PriceTick.company_id == Company.id)
        .where(PriceTick.captured_at >= cutoff)
        .distinct()
    ).scalars().all()
    rows = []
    for s in symbols:
        d = compute_intel(db, s)
        if "error" in d:
            continue
        c = d["circuit"]
        if c["upper_pct"] is None:
            continue
        rows.append(
            {
                "symbol": s,
                "ltp": d["price"]["ltp"],
                "change_pct": d["price"]["change_pct"],
                "upper_pct": c["upper_pct"],
                "lower_pct": c["lower_pct"],
                "confidence": c["confidence"],
            }
        )
    upper = sorted(rows, key=lambda r: r["upper_pct"], reverse=True)[:limit]
    lower = sorted(rows, key=lambda r: r["lower_pct"], reverse=True)[:limit]
    return {"scanned": len(rows), "upper": upper, "lower": lower}
