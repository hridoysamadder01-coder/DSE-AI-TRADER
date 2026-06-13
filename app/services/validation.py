"""Tick validation.

Returns a list of (severity, code) tuples. `error` rejects the row;
`warn` and `info` are logged but the row is still written.
"""
from __future__ import annotations

from ..schemas import TickIn

Issue = tuple[str, str]


# Sanity ranges for Bangladesh equity prices. DSE has stocks from ~Tk 4 to several
# thousand BDT; anything outside this almost always means a scrape error.
MIN_PRICE = 1.0
MAX_PRICE = 50_000.0
# Daily DSE circuit limits are ~10% for most bands; 25% gives margin without
# flagging real moves on low-cap stocks.
MAX_CHANGE_PCT = 25.0


def validate_tick(t: TickIn) -> list[Issue]:
    issues: list[Issue] = []

    if not t.symbol or not t.symbol.strip():
        issues.append(("error", "MISSING_SYMBOL"))
        return issues

    if t.ltp is None and t.ycp is None:
        issues.append(("error", "MISSING_PRICE"))
        return issues

    for field in ("ltp", "open", "high", "low", "ycp"):
        v = getattr(t, field, None)
        if v is None:
            continue
        if v <= 0:
            issues.append(("error", f"NONPOS_{field.upper()}"))
        elif v < MIN_PRICE:
            issues.append(("warn", f"LOW_{field.upper()}"))
        elif v > MAX_PRICE:
            issues.append(("error", f"HIGH_{field.upper()}"))

    if t.high is not None and t.low is not None and t.high < t.low:
        issues.append(("error", "HIGH_LT_LOW"))

    if t.change_pct is not None and abs(t.change_pct) > MAX_CHANGE_PCT:
        issues.append(("warn", "EXTREME_CHANGE_PCT"))

    if t.volume is not None and t.volume < 0:
        issues.append(("error", "NEG_VOLUME"))

    if t.trades is not None and t.trades < 0:
        issues.append(("error", "NEG_TRADES"))

    return issues
