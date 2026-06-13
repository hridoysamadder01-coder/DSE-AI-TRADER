"""End-of-day rollup: promote the last intraday tick of each company into price_daily."""
from __future__ import annotations

from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import func, select

from ..db import session_scope
from ..models import CollectionRun, PriceDaily, PriceTick


def run_eod_rollup(target_date: date | None = None) -> dict:
    """Compute one PriceDaily row per company from that day's ticks.

    Idempotent: rerunning overwrites the day's row.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()

    written = 0
    skipped = 0
    started_at = datetime.now(timezone.utc)

    with session_scope() as s:
        run = CollectionRun(collector="eod_rollup", status="running")
        s.add(run)
        s.flush()
        run_id = run.id

        # Gather all ticks for that calendar date.
        stmt = (
            select(
                PriceTick.company_id,
                func.min(PriceTick.captured_at).label("first_at"),
                func.max(PriceTick.captured_at).label("last_at"),
                func.max(PriceTick.high).label("high"),
                func.min(PriceTick.low).label("low"),
                func.max(PriceTick.volume).label("volume"),
                func.max(PriceTick.trades).label("trades"),
                func.max(PriceTick.value_bdt).label("value_bdt"),
            )
            .where(func.date(PriceTick.captured_at) == target_date.isoformat())
            .group_by(PriceTick.company_id)
        )
        groups = s.execute(stmt).all()

        for g in groups:
            first_tick = s.execute(
                select(PriceTick)
                .where(PriceTick.company_id == g.company_id)
                .where(PriceTick.captured_at == g.first_at)
                .limit(1)
            ).scalar_one_or_none()
            last_tick = s.execute(
                select(PriceTick)
                .where(PriceTick.company_id == g.company_id)
                .where(PriceTick.captured_at == g.last_at)
                .limit(1)
            ).scalar_one_or_none()
            if not last_tick:
                skipped += 1
                continue

            open_ = first_tick.open if first_tick and first_tick.open else (
                first_tick.ltp if first_tick else None
            )
            close = last_tick.ltp
            ycp = last_tick.ycp

            # Upsert (company_id, trade_date).
            existing = s.execute(
                select(PriceDaily)
                .where(PriceDaily.company_id == g.company_id)
                .where(PriceDaily.trade_date == target_date)
            ).scalar_one_or_none()
            if existing:
                existing.open = open_
                existing.high = g.high
                existing.low = g.low
                existing.close = close
                existing.ycp = ycp
                existing.volume = g.volume
                existing.trades = g.trades
                existing.value_bdt = g.value_bdt
                existing.source = "eod_rollup"
            else:
                s.add(
                    PriceDaily(
                        company_id=g.company_id,
                        trade_date=target_date,
                        open=open_,
                        high=g.high,
                        low=g.low,
                        close=close,
                        ycp=ycp,
                        volume=g.volume,
                        trades=g.trades,
                        value_bdt=g.value_bdt,
                        source="eod_rollup",
                    )
                )
            written += 1

        run = s.get(CollectionRun, run_id)
        if run:
            run.status = "ok"
            run.finished_at = datetime.now(timezone.utc)
            run.rows_in = written + skipped
            run.rows_written = written
            run.rows_rejected = skipped
            run.duration_ms = int(
                (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
            )

    logger.info(f"EOD rollup {target_date}: written={written} skipped={skipped}")
    return {"date": target_date.isoformat(), "written": written, "skipped": skipped}
