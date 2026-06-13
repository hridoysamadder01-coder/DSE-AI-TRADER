from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import (
    CollectionRun,
    Company,
    DataQualityLog,
    MarketSnapshot,
    PriceTick,
    Sector,
)
from ..schemas import IndexSnapshotIn, TickIn
from ..services.validation import validate_tick


class CollectorResult:
    __slots__ = ("rows_in", "rows_written", "rows_rejected", "issues")

    def __init__(self) -> None:
        self.rows_in = 0
        self.rows_written = 0
        self.rows_rejected = 0
        self.issues: list[tuple[str, str | None, str, str, str | None]] = []
        # (severity, symbol, code, collector, detail)


class BaseCollector(ABC):
    """Base class for any data collector.

    Subclasses implement `fetch()` which returns an iterable of TickIn (or
    IndexSnapshotIn for index collectors). Persisting + run tracking + quality
    logging is centralized here.
    """

    name: str = "base"

    @abstractmethod
    def fetch(self) -> tuple[list[TickIn], list[IndexSnapshotIn]]:
        """Return (ticks, index_snapshots). Either list may be empty."""

    def run(self) -> CollectorResult:
        result = CollectorResult()
        t0 = time.perf_counter()
        run_id: int | None = None

        with session_scope() as s:
            run = CollectionRun(collector=self.name, status="running")
            s.add(run)
            s.flush()
            run_id = run.id

        try:
            ticks, index_snaps = self.fetch()
            result.rows_in = len(ticks) + len(index_snaps)
            with session_scope() as s:
                if ticks:
                    self._persist_ticks(s, ticks, result)
                if index_snaps:
                    self._persist_snapshots(s, index_snaps, result)
                self._persist_issues(s, result.issues)

            duration_ms = int((time.perf_counter() - t0) * 1000)
            with session_scope() as s:
                run = s.get(CollectionRun, run_id)
                if run:
                    run.status = "ok"
                    run.finished_at = datetime.now(timezone.utc)
                    run.duration_ms = duration_ms
                    run.rows_in = result.rows_in
                    run.rows_written = result.rows_written
                    run.rows_rejected = result.rows_rejected
            logger.info(
                f"[{self.name}] ok in={result.rows_in} "
                f"written={result.rows_written} rejected={result.rows_rejected} "
                f"({duration_ms} ms)"
            )
        except Exception as e:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            logger.exception(f"[{self.name}] failed: {e}")
            with session_scope() as s:
                run = s.get(CollectionRun, run_id) if run_id else None
                if run:
                    run.status = "failed"
                    run.finished_at = datetime.now(timezone.utc)
                    run.duration_ms = duration_ms
                    run.message = str(e)[:1000]
                s.add(
                    DataQualityLog(
                        collector=self.name,
                        severity="error",
                        code="COLLECTOR_FAILED",
                        detail=str(e)[:2000],
                    )
                )
        return result

    # ---------- persistence helpers ----------

    def _get_or_create_sector(self, s: Session, name: str | None) -> Sector | None:
        if not name:
            return None
        name = name.strip()
        if not name:
            return None
        row = s.execute(select(Sector).where(Sector.name == name)).scalar_one_or_none()
        if row:
            return row
        row = Sector(name=name)
        s.add(row)
        s.flush()
        return row

    def _get_or_create_company(
        self, s: Session, symbol: str, name: str | None, exchange: str, sector_name: str | None
    ) -> Company:
        symbol = symbol.strip().upper()
        row = s.execute(select(Company).where(Company.symbol == symbol)).scalar_one_or_none()
        sector = self._get_or_create_sector(s, sector_name)
        if row:
            if name and row.name != name:
                row.name = name
            if sector and row.sector_id != sector.id:
                row.sector_id = sector.id
            return row
        row = Company(
            symbol=symbol,
            name=name,
            exchange=exchange,
            sector_id=sector.id if sector else None,
        )
        s.add(row)
        s.flush()
        return row

    def _persist_ticks(
        self, s: Session, ticks: list[TickIn], result: CollectorResult
    ) -> None:
        for t in ticks:
            issues = validate_tick(t)
            if any(sev == "error" for sev, _ in issues):
                for sev, code in issues:
                    result.issues.append((sev, t.symbol, code, self.name, None))
                result.rows_rejected += 1
                continue
            for sev, code in issues:
                result.issues.append((sev, t.symbol, code, self.name, None))

            company = self._get_or_create_company(
                s, t.symbol, t.name, t.exchange, t.sector
            )
            s.add(
                PriceTick(
                    company_id=company.id,
                    captured_at=t.captured_at,
                    ltp=t.ltp,
                    open=t.open,
                    high=t.high,
                    low=t.low,
                    ycp=t.ycp,
                    change=t.change,
                    change_pct=t.change_pct,
                    trades=t.trades,
                    volume=t.volume,
                    value_bdt=t.value_bdt,
                    source=t.source,
                )
            )
            result.rows_written += 1

    def _persist_snapshots(
        self, s: Session, snaps: list[IndexSnapshotIn], result: CollectorResult
    ) -> None:
        for snap in snaps:
            s.add(
                MarketSnapshot(
                    captured_at=snap.captured_at,
                    exchange=snap.exchange,
                    index_name=snap.index_name,
                    value=snap.value,
                    change=snap.change,
                    change_pct=snap.change_pct,
                    total_trades=snap.total_trades,
                    total_volume=snap.total_volume,
                    total_value_bdt=snap.total_value_bdt,
                    source=snap.source,
                )
            )
            result.rows_written += 1

    def _persist_issues(
        self,
        s: Session,
        issues: list[tuple[str, str | None, str, str, str | None]],
    ) -> None:
        for severity, symbol, code, collector, detail in issues:
            s.add(
                DataQualityLog(
                    collector=collector,
                    symbol=symbol,
                    severity=severity,
                    code=code,
                    detail=detail,
                )
            )
