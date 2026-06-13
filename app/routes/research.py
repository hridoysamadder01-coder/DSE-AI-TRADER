"""Research endpoint — executive summary derived from intel signals."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.intel import compute_intel

router = APIRouter(prefix="/api/research", tags=["research"])


def _executive_summary(intel: dict) -> str:
    p = intel["price"]
    parts = [f"{intel['symbol']} trades at {p['ltp']} BDT"]
    if p["change_pct"] is not None:
        direction = "up" if p["change_pct"] > 0 else "down" if p["change_pct"] < 0 else "flat"
        parts.append(f"{direction} {abs(p['change_pct']):.2f}% on the session")
    if p["volume"] is not None:
        parts.append(f"on {p['volume']:,} shares")
    if p["value_bdt"] is not None:
        parts.append(f"(turnover ~{int(p['value_bdt']/1e6):,}M BDT)")
    summary = ", ".join(parts[:2]) + " " + " ".join(parts[2:]) + "."

    sm = intel["scores"]["smart_money"]
    if sm["value"] is not None and sm["confidence"] != "low":
        summary += f" Tick-level CLV × Δvol implies {sm['label']} ({sm['value']}/100, {sm['confidence']} confidence)."

    vol = intel["scores"]["volume_anomaly"]
    if vol["value"] is not None:
        summary += f" Today's volume runs {vol['value']}× the prior-day median ({vol['label']})."

    c = intel["circuit"]
    if c.get("upper_pct") and c["upper_pct"] >= 35:
        summary += f" Upper-circuit reach probability is {c['upper_pct']}% ({c['confidence']} confidence)."
    elif c.get("lower_pct") and c["lower_pct"] >= 35:
        summary += f" Lower-circuit reach probability is {c['lower_pct']}% ({c['confidence']} confidence)."

    return summary


def _strengths_weaknesses(intel: dict) -> tuple[list[str], list[str]]:
    strengths, weaknesses = [], []
    sm = intel["scores"]["smart_money"]
    vol = intel["scores"]["volume_anomaly"]
    mom = intel["scores"]["momentum"]
    sig = intel.get("signals", {})

    if sm["value"] is not None:
        if sm["value"] >= 60:
            strengths.append(f"Smart money accumulating ({sm['value']}/100, {sm['confidence']})")
        elif sm["value"] <= 40:
            weaknesses.append(f"Smart money distributing ({sm['value']}/100, {sm['confidence']})")

    if vol["value"] is not None:
        if vol["value"] >= 2:
            strengths.append(f"Volume surge {vol['value']}× normal — strong participation")
        elif vol["value"] <= 0.7:
            weaknesses.append(f"Volume thin {vol['value']}× normal — low conviction")

    if mom["value"] is not None:
        if mom["value"] >= 70:
            strengths.append(f"Momentum {mom['value']}/100 — strongly upward")
        elif mom["value"] <= 30:
            weaknesses.append(f"Momentum {mom['value']}/100 — strongly downward")

    if sig.get("breakout", {}).get("detected"):
        bk = sig["breakout"]
        strengths.append(f"Breakout above {bk.get('level')} on confirming volume")

    if sig.get("absorption", {}).get("detected"):
        strengths.append("Absorption pattern — large supply being absorbed without price drop")

    rep = sig.get("repeated", {})
    if rep.get("buying_run", 0) >= 3:
        strengths.append(f"Repeated buying — {rep['buying_run']} consecutive up-ticks")
    if rep.get("selling_run", 0) >= 3:
        weaknesses.append(f"Repeated selling — {rep['selling_run']} consecutive down-ticks")

    return strengths, weaknesses


def _ai_conclusion(intel: dict, strengths: list[str], weaknesses: list[str]) -> str:
    s = len(strengths); w = len(weaknesses)
    sm = intel["scores"]["smart_money"].get("value")
    mom = intel["scores"]["momentum"].get("value")
    if s == 0 and w == 0:
        return ("Neutral — not enough tick density or daily history to form a confident view. "
                "Re-run after more intraday data accumulates.")
    if s > w and (sm is None or sm >= 50) and (mom is None or mom >= 50):
        return f"Lean bullish — {s} confirming strength signal(s) vs {w} weakness signal(s). Watch volume for confirmation."
    if w > s and (sm is None or sm <= 50) and (mom is None or mom <= 50):
        return f"Lean bearish — {w} weakness signal(s) vs {s} strength signal(s). Risk-reward unfavourable."
    return f"Mixed — {s} strength vs {w} weakness signals. Wait for clearer setup."


@router.get("/{symbol}")
def research(symbol: str, db: Session = Depends(get_db)):
    intel = compute_intel(db, symbol)
    if "error" in intel:
        raise HTTPException(404, intel["error"])
    strengths, weaknesses = _strengths_weaknesses(intel)
    return {
        "symbol": intel["symbol"],
        "exchange": intel["exchange"],
        "sector": intel["sector"],
        "executive_summary": _executive_summary(intel),
        "strengths": strengths,
        "weaknesses": weaknesses,
        "ai_conclusion": _ai_conclusion(intel, strengths, weaknesses),
        "scores": intel["scores"],
        "circuit": intel["circuit"],
        "signals": intel["signals"],
        "levels": intel["levels"],
        "fundamentals_pending": intel["fundamentals_pending"],
        "filings_url": f"https://www.dsebd.org/displayCompany.php?name={intel['symbol']}",
        "news_url": "https://www.dsebd.org/recent_market_information.php",
    }
