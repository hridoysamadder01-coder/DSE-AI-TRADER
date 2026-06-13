from datetime import datetime, timezone

from app.schemas import TickIn
from app.services.validation import validate_tick


def mk(**kw) -> TickIn:
    base = dict(
        symbol="GP",
        exchange="DSE",
        captured_at=datetime.now(timezone.utc),
        ltp=320.0,
        source="dsebd_latest",
    )
    base.update(kw)
    return TickIn(**base)


def test_ok_tick_has_no_errors():
    issues = validate_tick(mk())
    assert all(sev != "error" for sev, _ in issues)


def test_missing_price_rejected():
    issues = validate_tick(mk(ltp=None, ycp=None))
    assert ("error", "MISSING_PRICE") in issues


def test_high_lt_low_rejected():
    issues = validate_tick(mk(high=100, low=200))
    assert ("error", "HIGH_LT_LOW") in issues


def test_nonpositive_price_rejected():
    issues = validate_tick(mk(ltp=-1.0))
    assert any(code == "NONPOS_LTP" for sev, code in issues if sev == "error")


def test_extreme_change_warned():
    issues = validate_tick(mk(ltp=400, ycp=300, change_pct=33.3))
    assert ("warn", "EXTREME_CHANGE_PCT") in issues
