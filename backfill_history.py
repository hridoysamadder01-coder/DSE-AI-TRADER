"""One-shot script to backfill historical OHLCV for every DSE company.

Run:
    .venv/Scripts/python.exe backfill_history.py
    .venv/Scripts/python.exe backfill_history.py --days 1095          # 3 years
    .venv/Scripts/python.exe backfill_history.py --symbols GP,BATBC
    .venv/Scripts/python.exe backfill_history.py --no-skip            # re-scrape even if already populated

Idempotent: re-runs skip symbols that already have ≥0.5 × `days` rows in price_daily.
"""
from __future__ import annotations

import argparse
import sys

from loguru import logger

from app.collectors.dse_history import backfill_all
from app.db import init_db
from app.logging_setup import setup_logging


def main():
    parser = argparse.ArgumentParser(description="Backfill DSE historical OHLCV")
    parser.add_argument("--days", type=int, default=720, help="days of history to request (default 720 = 2y)")
    parser.add_argument("--rate", type=float, default=0.4, help="seconds between requests (default 0.4)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="comma-separated symbols (default = all DSE companies)")
    parser.add_argument("--no-skip", action="store_true",
                        help="re-scrape symbols even if already populated")
    args = parser.parse_args()

    setup_logging()
    init_db()

    syms = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    logger.info(f"starting backfill: days={args.days} rate={args.rate}s symbols={syms or 'ALL'}")
    summary = backfill_all(
        days_back=args.days,
        rate_limit_seconds=args.rate,
        symbols=syms,
        skip_existing=not args.no_skip,
    )
    print()
    print("=" * 50)
    print("BACKFILL COMPLETE")
    print("=" * 50)
    for k, v in summary.items():
        print(f"  {k:25s} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
