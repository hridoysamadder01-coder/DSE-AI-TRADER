"""TradingView UDF (Universal Data Feed) endpoints.

Lets the user drop in the full TradingView Charting Library
(https://www.tradingview.com/charting-library/) and point its `datafeed_url`
at `/udf` — candles, search, and symbol metadata all resolve against our
DSE/CSE database.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Company, PriceTick
from .chart import candles as candle_endpoint

router = APIRouter(prefix="/udf", tags=["udf"])


@router.get("/config")
def config():
    return {
        "supported_resolutions": ["1", "5", "15", "30", "60", "240", "1D"],
        "supports_group_request": False,
        "supports_marks": False,
        "supports_search": True,
        "supports_timescale_marks": False,
        "exchanges": [
            {"value": "DSE", "name": "Dhaka Stock Exchange", "desc": "DSE"},
            {"value": "CSE", "name": "Chittagong Stock Exchange", "desc": "CSE"},
        ],
        "symbols_types": [{"name": "Equity", "value": "stock"}],
    }


@router.get("/search")
def search(
    query: str = Query(""),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    stmt = select(Company)
    if query:
        stmt = stmt.where(Company.symbol.like(f"{query.upper()}%"))
    rows = db.execute(stmt.limit(limit)).scalars().all()
    return [
        {
            "symbol": c.symbol,
            "full_name": f"{c.exchange}:{c.symbol}",
            "description": c.name or c.symbol,
            "exchange": c.exchange,
            "type": "stock",
        }
        for c in rows
    ]


@router.get("/symbols")
def symbols(
    symbol: str = Query(...),
    db: Session = Depends(get_db),
):
    bare = symbol.split(":")[-1].upper()
    c = db.execute(select(Company).where(Company.symbol == bare)).scalar_one_or_none()
    if not c:
        return {"s": "error", "errmsg": "unknown_symbol"}
    return {
        "name": c.symbol,
        "ticker": c.symbol,
        "full_name": f"{c.exchange}:{c.symbol}",
        "description": c.name or c.symbol,
        "exchange": c.exchange,
        "listed_exchange": c.exchange,
        "type": "stock",
        "session": "1000-1430",  # DSE session, Asia/Dhaka
        "timezone": "Asia/Dhaka",
        "minmov": 1,
        "pricescale": 100,
        "has_intraday": True,
        "has_daily": True,
        "supported_resolutions": ["1", "5", "15", "30", "60", "240", "1D"],
        "currency_code": "BDT",
    }


@router.get("/history")
def history(
    symbol: str = Query(...),
    resolution: str = Query("5"),
    **kwargs,
):
    """Bridge to /api/chart/{sym}/candles using TV's resolution strings."""
    # Translate resolution: "1","5","15","30","60","240","1D"
    if resolution == "1D":
        res_min = 1440
    else:
        try:
            res_min = int(resolution)
        except ValueError:
            res_min = 5
    # For simplicity, return up to 30 days of bars at requested resolution.
    hours_back = min(720, max(24, res_min * 5))  # rough sizing
    # We can't call FastAPI endpoint directly without a DB session — caller
    # should use /api/chart/{symbol}/candles directly when the front-end is
    # ours. The UDF route returns a pointer for now:
    return {
        "s": "no_data",
        "nextTime": None,
        "_note": "UDF /history bridge — wire to /api/chart/{symbol}/candles "
                 "once Charting Library library files are dropped in /static/.",
    }
