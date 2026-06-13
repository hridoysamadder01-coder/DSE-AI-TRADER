"""APScheduler wiring.

Collectors are gated to DSE/CSE trading hours (Asia/Dhaka). The EOD rollup
runs once after market close. The quality monitor runs continuously.
"""
from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from ..collectors.cse import CSELatestPriceCollector
from ..collectors.dse import DSELatestPriceCollector
from ..collectors.dse_index import DSEIndexSnapshotCollector
from ..config import get_settings
from .eod import run_eod_rollup
from .quality import _is_market_hours, run_quality_check


def _safe(label: str, fn):
    def wrapper():
        try:
            fn()
        except Exception as e:
            logger.exception(f"scheduler[{label}] error: {e}")

    return wrapper


def _intraday_dse():
    if not _is_market_hours():
        return
    DSELatestPriceCollector().run()


def _intraday_cse():
    if not _is_market_hours():
        return
    CSELatestPriceCollector().run()


def _intraday_index():
    if not _is_market_hours():
        return
    DSEIndexSnapshotCollector().run()


def build_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    sched = BackgroundScheduler(timezone=settings.market_tz)

    sched.add_job(
        _safe("dse_intraday", _intraday_dse),
        CronTrigger(
            day_of_week="sun,mon,tue,wed,thu",
            hour=f"{settings.market_open_hour}-{settings.market_close_hour}",
            minute=settings.collect_intraday_cron_minute,
        ),
        id="dse_intraday",
        replace_existing=True,
    )
    sched.add_job(
        _safe("cse_intraday", _intraday_cse),
        CronTrigger(
            day_of_week="sun,mon,tue,wed,thu",
            hour=f"{settings.market_open_hour}-{settings.market_close_hour}",
            minute=settings.collect_intraday_cron_minute,
        ),
        id="cse_intraday",
        replace_existing=True,
    )
    sched.add_job(
        _safe("dse_index", _intraday_index),
        CronTrigger(
            day_of_week="sun,mon,tue,wed,thu",
            hour=f"{settings.market_open_hour}-{settings.market_close_hour}",
            minute="*/5",
        ),
        id="dse_index",
        replace_existing=True,
    )
    sched.add_job(
        _safe("eod_rollup", lambda: run_eod_rollup()),
        CronTrigger(
            day_of_week="sun,mon,tue,wed,thu",
            hour=settings.collect_eod_cron_hour,
            minute=settings.collect_eod_cron_minute,
        ),
        id="eod_rollup",
        replace_existing=True,
    )
    sched.add_job(
        _safe("quality_check", lambda: run_quality_check()),
        CronTrigger(minute=settings.quality_check_cron_minute),
        id="quality_check",
        replace_existing=True,
    )

    return sched
