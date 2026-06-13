from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CollectionRun, DataQualityLog, PriceTick
from ..schemas import QualityIssueOut, RunOut
from ..services.quality import run_quality_check

router = APIRouter(prefix="/api/quality", tags=["quality"])


@router.get("/runs", response_model=list[RunOut])
def list_runs(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    rows = (
        db.execute(
            select(CollectionRun).order_by(desc(CollectionRun.started_at)).limit(limit)
        )
        .scalars()
        .all()
    )
    return rows


@router.get("/issues", response_model=list[QualityIssueOut])
def list_issues(
    hours: int = Query(24, ge=1, le=168),
    severity: str | None = Query(None, pattern="^(info|warn|error)$"),
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = select(DataQualityLog).where(DataQualityLog.created_at >= cutoff)
    if severity:
        stmt = stmt.where(DataQualityLog.severity == severity)
    stmt = stmt.order_by(desc(DataQualityLog.created_at)).limit(500)
    return db.execute(stmt).scalars().all()


@router.get("/report")
def report(db: Session = Depends(get_db)):
    last_run = db.execute(
        select(CollectionRun).order_by(desc(CollectionRun.started_at)).limit(1)
    ).scalar_one_or_none()
    last_ok_run = db.execute(
        select(CollectionRun)
        .where(CollectionRun.status == "ok")
        .order_by(desc(CollectionRun.finished_at))
        .limit(1)
    ).scalar_one_or_none()
    cutoff_24 = datetime.now(timezone.utc) - timedelta(hours=24)
    issues_24 = db.execute(
        select(DataQualityLog.severity, func.count(DataQualityLog.id))
        .where(DataQualityLog.created_at >= cutoff_24)
        .group_by(DataQualityLog.severity)
    ).all()
    last_tick_at = db.execute(select(func.max(PriceTick.captured_at))).scalar_one()
    tick_count_24 = db.execute(
        select(func.count(PriceTick.id)).where(PriceTick.captured_at >= cutoff_24)
    ).scalar_one()
    return {
        "last_run": RunOut.model_validate(last_run).model_dump() if last_run else None,
        "last_ok_run": RunOut.model_validate(last_ok_run).model_dump() if last_ok_run else None,
        "issues_24h": {sev: cnt for sev, cnt in issues_24},
        "ticks_24h": tick_count_24,
        "last_tick_at": last_tick_at,
    }


@router.post("/check")
def trigger_check():
    return run_quality_check()
