"""Portfolio intelligence — health/concentration/sector exposure."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Company, PriceTick, Sector
from ..services.intel import compute_intel

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class Position(BaseModel):
    symbol: str
    quantity: float
    average_price: float


class PortfolioRequest(BaseModel):
    positions: List[Position]


def _latest_ltp(db: Session, symbol: str) -> Optional[float]:
    company = db.execute(select(Company).where(Company.symbol == symbol.upper())).scalar_one_or_none()
    if not company:
        return None
    tick = db.execute(
        select(PriceTick).where(PriceTick.company_id == company.id)
        .order_by(desc(PriceTick.captured_at)).limit(1)
    ).scalar_one_or_none()
    return tick.ltp if tick else None


def _sector_of(db: Session, symbol: str) -> Optional[str]:
    row = db.execute(
        select(Sector.name).join(Company, Company.sector_id == Sector.id)
        .where(Company.symbol == symbol.upper())
    ).scalar_one_or_none()
    return row


@router.post("/analyze")
def analyze(body: PortfolioRequest, db: Session = Depends(get_db)):
    positions = body.positions
    if not positions:
        return {"positions": [], "warnings": ["empty portfolio"]}

    enriched = []
    total_invested = 0.0
    total_market = 0.0
    sector_buckets: dict[str, float] = {}
    symbol_buckets: dict[str, float] = {}

    for pos in positions:
        ltp = _latest_ltp(db, pos.symbol) or pos.average_price
        sector = _sector_of(db, pos.symbol) or "Uncategorized"
        invested = pos.quantity * pos.average_price
        market = pos.quantity * ltp
        pnl = market - invested
        pnl_pct = (pnl / invested * 100) if invested > 0 else 0
        total_invested += invested
        total_market += market
        sector_buckets[sector] = sector_buckets.get(sector, 0) + market
        symbol_buckets[pos.symbol.upper()] = symbol_buckets.get(pos.symbol.upper(), 0) + market
        enriched.append({
            "symbol": pos.symbol.upper(),
            "sector": sector,
            "quantity": pos.quantity,
            "average_price": pos.average_price,
            "ltp": ltp,
            "invested": round(invested, 2),
            "market_value": round(market, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        })

    # Concentration
    sector_exposure = [
        {"sector": k, "value": round(v, 2), "pct": round(v / total_market * 100, 2) if total_market else 0}
        for k, v in sorted(sector_buckets.items(), key=lambda kv: -kv[1])
    ]
    symbol_exposure = [
        {"symbol": k, "value": round(v, 2), "pct": round(v / total_market * 100, 2) if total_market else 0}
        for k, v in sorted(symbol_buckets.items(), key=lambda kv: -kv[1])
    ]

    # Health score: penalize concentration + low diversification + heavy losers
    warnings = []
    health = 100.0
    if symbol_exposure and symbol_exposure[0]["pct"] > 40:
        warnings.append(f"Single-symbol overexposure: {symbol_exposure[0]['symbol']} = {symbol_exposure[0]['pct']}%")
        health -= 25
    elif symbol_exposure and symbol_exposure[0]["pct"] > 25:
        warnings.append(f"Concentration: {symbol_exposure[0]['symbol']} = {symbol_exposure[0]['pct']}%")
        health -= 10
    if sector_exposure and sector_exposure[0]["pct"] > 60:
        warnings.append(f"Sector overexposure: {sector_exposure[0]['sector']} = {sector_exposure[0]['pct']}%")
        health -= 20
    if len(positions) < 5:
        warnings.append(f"Low diversification — {len(positions)} position(s)")
        health -= 10
    pnl_total_pct = ((total_market - total_invested) / total_invested * 100) if total_invested else 0
    if pnl_total_pct < -10:
        warnings.append(f"Portfolio drawdown {pnl_total_pct:.1f}%")
        health -= 15
    losers = [e for e in enriched if e["pnl_pct"] < -10]
    if len(losers) >= 3:
        warnings.append(f"{len(losers)} positions down >10%")
        health -= 10

    health = max(0, min(100, round(health, 1)))

    # Concentration risk score (Herfindahl-like over symbol shares)
    if total_market > 0:
        hhi = sum((v / total_market) ** 2 for v in symbol_buckets.values()) * 10000
    else:
        hhi = 0
    concentration_label = (
        "extreme" if hhi > 2500 else "high" if hhi > 1500 else "moderate" if hhi > 1000 else "low"
    )

    return {
        "totals": {
            "invested": round(total_invested, 2),
            "market_value": round(total_market, 2),
            "pnl": round(total_market - total_invested, 2),
            "pnl_pct": round(pnl_total_pct, 2),
            "position_count": len(positions),
        },
        "health_score": health,
        "concentration": {"hhi": round(hhi, 0), "label": concentration_label},
        "sector_exposure": sector_exposure,
        "symbol_exposure": symbol_exposure[:10],
        "positions": enriched,
        "warnings": warnings,
    }
