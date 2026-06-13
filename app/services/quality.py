"""Periodic data-quality monitor.

Looks back over recent ticks and flags:
- STALE_TICK: no tick in the last `stale_minutes` for a company that traded today
- NO_RECENT_RUN: no collector run finished in the last `recency_minutes`
- INDEX_MISSING: no DSE index snapshot in the last 30 minutes (during market hours only)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import func, select

from ..config import get_settings
from ..db import session_scope
from ..models import (
    CollectionRun,
    Company,
    DataQualityLog,
    MarketSnapshot,
    PriceTick,
)


def _is_market_hours(now: datetime | None = None) -> bool:
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    # Translate UTC -> approximate Dhaka time (UTC+6, no DST).
    dhaka = now + timedelta(hours=6)
    if dhaka.weekday() in (4, 5):  # Fri/Sat = DSE weekend
        return False
    minute = dhaka.hour * 60 + dhaka.minute
    open_m = settings.market_open_hour * 60 + settings.market_open_minute
    close_m = settings.market_close_hour * 60 + settings.market_close_minute
    return open_m <= minute <= close_m


def run_quality_check(stale_minutes: int = 15, recency_minutes: int = 10) -> dict:
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=stale_minutes)
    recency_before = now - timedelta(minutes=recency_minutes)
    today = now.date().isoformat()
    in_hours = _is_market_hours(now)

    flagged_stale = 0
    flagged_no_run = 0
    flagged_index = 0

    with session_scope() as s:
        if in_hours:
            # Companies with a tick today but nothing recent.
            stmt = (
                select(
                    PriceTick.company_id,
                    func.max(PriceTick.captured_at).label("last_at"),
                )
                .where(func.date(PriceTick.captured_at) == today)
                .group_by(PriceTick.company_id)
                .having(func.max(PriceTick.captured_at) < stale_before)
            )
            for company_id, last_at in s.execute(stmt).all():
                company = s.get(Company, company_id)
                if not company:
                    continue
                s.add(
                    DataQualityLog(
                        collector="quality_monitor",
                        symbol=company.symbol,
                        severity="warn",
                        code="STALE_TICK",
                        detail=f"last tick {last_at.isoformat()} (>{stale_minutes}m ago)",
                    )
                )
                flagged_stale += 1

        # Any successful collector run recently?
        recent_runs = s.execute(
            select(func.count(CollectionRun.id)).where(
                CollectionRun.status == "ok",
                CollectionRun.finished_at >= recency_before,
            )
        ).scalar_one()
        if in_hours and recent_runs == 0:
            s.add(
                DataQualityLog(
                    collector="quality_monitor",
                    severity="error",
                    code="NO_RECENT_RUN",
                    detail=f"no successful run in last {recency_minutes}m during market hours",
                )
            )
            flagged_no_run += 1

        # Index snapshot freshness.
        if in_hours:
            recent_snap = s.execute(
                select(func.count(MarketSnapshot.id)).where(
                    MarketSnapshot.captured_at >= now - timedelta(minutes=30)
                )
            ).scalar_one()
            if recent_snap == 0:
                s.add(
                    DataQualityLog(
                        collector="quality_monitor",
                        severity="warn",
                        code="INDEX_MISSING",
                        detail="no index snapshot in last 30m",
                    )
                )
                flagged_index += 1

    logger.info(
        f"quality_check: stale={flagged_stale} no_run={flagged_no_run} "
        f"index={flagged_index} (market_hours={in_hours})"
    )
    return {
        "market_hours": in_hours,
        "stale_symbols": flagged_stale,
        "no_recent_run": flagged_no_run,
        "index_missing": flagged_index,
    }
