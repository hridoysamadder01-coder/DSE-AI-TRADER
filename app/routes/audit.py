"""Data Reliability Report — what's missing, what's stale, what to fix."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    CollectionRun,
    Company,
    DataQualityLog,
    PriceDaily,
    PriceTick,
    Sector,
)

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def audit(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    h24 = now - timedelta(hours=24)
    h6 = now - timedelta(hours=6)

    companies_total = db.execute(select(func.count(Company.id))).scalar_one()
    companies_dse = db.execute(select(func.count(Company.id)).where(Company.exchange == "DSE")).scalar_one()
    companies_cse = db.execute(select(func.count(Company.id)).where(Company.exchange == "CSE")).scalar_one()
    sector_count = db.execute(select(func.count(Sector.id))).scalar_one()
    uncategorized = db.execute(
        select(func.count(Company.id)).where(Company.sector_id.is_(None))
    ).scalar_one()

    ticks_24h = db.execute(
        select(func.count(PriceTick.id)).where(PriceTick.captured_at >= h24)
    ).scalar_one()
    ticks_6h = db.execute(
        select(func.count(PriceTick.id)).where(PriceTick.captured_at >= h6)
    ).scalar_one()
    last_tick_at = db.execute(select(func.max(PriceTick.captured_at))).scalar_one()
    daily_total = db.execute(select(func.count(PriceDaily.id))).scalar_one()

    # Collector health
    runs_24h = db.execute(
        select(CollectionRun.collector, CollectionRun.status, func.count(CollectionRun.id))
        .where(CollectionRun.started_at >= h24).group_by(CollectionRun.collector, CollectionRun.status)
    ).all()
    by_collector: dict[str, dict] = {}
    for col, status, n in runs_24h:
        by_collector.setdefault(col, {"ok": 0, "failed": 0, "running": 0})[status] = n

    # Quality issues
    issues_24h = db.execute(
        select(DataQualityLog.code, func.count(DataQualityLog.id))
        .where(DataQualityLog.created_at >= h24).group_by(DataQualityLog.code)
    ).all()
    issues_map = {code: n for code, n in issues_24h}

    # Coverage gaps
    no_daily_count = companies_total - db.execute(
        select(func.count(func.distinct(PriceDaily.company_id)))
    ).scalar_one()

    findings = []
    fixes = []

    if uncategorized > 0:
        findings.append({
            "severity": "warn",
            "code": "SECTOR_MISSING",
            "detail": f"{uncategorized}/{companies_total} companies have no sector",
            "fix": "Phase 3 fundamentals collector seeds sector from DSE company-disclosure pages",
        })
    if sector_count <= 1:
        findings.append({
            "severity": "warn",
            "code": "NO_SECTORS",
            "detail": "Sector table is unseeded — heatmap and sector rotation will show 'Uncategorized'",
            "fix": "ship the company-info scraper (Phase 3 dependency)",
        })
    if not last_tick_at:
        findings.append({"severity": "error", "code": "NO_TICKS", "detail": "no ticks captured yet", "fix": "Admin → Run DSE / Run CSE"})
    else:
        age_minutes = (now - last_tick_at.replace(tzinfo=timezone.utc) if last_tick_at.tzinfo is None else now - last_tick_at).total_seconds() / 60
        if age_minutes > 30:
            findings.append({
                "severity": "warn",
                "code": "STALE_LAST_TICK",
                "detail": f"newest tick is {int(age_minutes)} minutes old",
                "fix": "scheduler runs during market hours; outside hours this is expected",
            })

    if daily_total == 0:
        findings.append({
            "severity": "warn",
            "code": "NO_DAILY",
            "detail": "no end-of-day rollup rows yet — daily history empty",
            "fix": "EOD rollup runs at 16:30 Asia/Dhaka, or trigger via /api/admin/collect manually",
        })
    elif no_daily_count > 0:
        findings.append({
            "severity": "info",
            "code": "PARTIAL_DAILY",
            "detail": f"{no_daily_count} companies have no rollup row — usually inactive listings",
            "fix": "investigate symbols with no EOD; some are mutual-funds with sparse trading",
        })

    for col, counts in by_collector.items():
        if counts.get("failed", 0) > counts.get("ok", 0):
            findings.append({
                "severity": "error",
                "code": "COLLECTOR_UNSTABLE",
                "detail": f"{col} failed {counts['failed']}× vs ok {counts.get('ok', 0)}× in last 24h",
                "fix": "check Admin → recent runs for error messages",
            })

    if issues_map.get("HIGH_LTP", 0) + issues_map.get("NONPOS_LTP", 0) > 0:
        fixes.append("validation rejected rows with non-positive or out-of-band prices — scraping looks healthy")

    # Reliability score
    score = 100
    for f in findings:
        score -= {"error": 25, "warn": 10, "info": 3}.get(f["severity"], 0)
    score = max(0, score)

    return {
        "as_of": now.isoformat(),
        "reliability_score": score,
        "label": (
            "excellent" if score >= 90
            else "good" if score >= 75
            else "fair" if score >= 50
            else "needs attention"
        ),
        "inventory": {
            "companies_total": companies_total,
            "companies_dse": companies_dse,
            "companies_cse": companies_cse,
            "sectors": sector_count,
            "uncategorized_companies": uncategorized,
            "ticks_24h": ticks_24h,
            "ticks_6h": ticks_6h,
            "daily_rows": daily_total,
            "last_tick_at": last_tick_at,
        },
        "collectors_24h": by_collector,
        "quality_issues_24h": issues_map,
        "findings": findings,
        "fixes_recommended": fixes,
    }
