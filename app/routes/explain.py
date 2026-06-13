"""Market Explainer — plain-language reasoning over real intel signals.

No LLM. The "AI" here is deterministic prose generated from numeric evidence:
every sentence cites an actual measured value, so reasoning is fully reproducible.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.intel import compute_intel

router = APIRouter(prefix="/api/explain", tags=["explain"])


QUESTIONS = {"rising", "falling", "volume", "smart_money", "circuit", "summary"}


def _explain(intel: dict, q: str) -> dict:
    price = intel["price"]; sc = intel["scores"]; c = intel["circuit"]
    sig = intel["signals"]; lv = intel["levels"]
    mom, vol, sm = sc["momentum"], sc["volume_anomaly"], sc["smart_money"]
    lines: list[str] = []

    def cite(text: str, conf: str | None = None):
        lines.append(text if not conf else f"{text} ({conf} confidence).")

    if q == "rising":
        if price["change_pct"] is None or price["change_pct"] <= 0:
            return {"explanation": f"{intel['symbol']} is not currently rising.", "lines": [], "confidence": "n/a"}
        cite(f"{intel['symbol']} is up {price['change_pct']:+.2f}% intraday at {price['ltp']} BDT.")
        if vol["value"] is not None and vol["value"] >= 1.3:
            cite(f"Participation is real: today's volume is {vol['value']}× the {vol['inputs'].get('prior_median'):,}-share prior-day median.")
        elif vol["value"] is not None:
            cite(f"Volume is light ({vol['value']}× normal) — the move is not yet confirmed by participation.")
        if sm["value"] is not None and sm["value"] >= 60:
            cite(f"Tick-level CLV × Δvolume shows {sm['label']} ({sm['value']}/100) — buyers are stepping into intraday lows.")
        if sig.get("breakout", {}).get("detected"):
            cite(f"Price broke above the {sig['breakout']['level']} N-day high on confirming volume.")
        rep = sig.get("repeated", {})
        if rep.get("buying_run", 0) >= 3:
            cite(f"Repeated buying detected — {rep['buying_run']} consecutive up-ticks.")
    elif q == "falling":
        if price["change_pct"] is None or price["change_pct"] >= 0:
            return {"explanation": f"{intel['symbol']} is not currently falling.", "lines": [], "confidence": "n/a"}
        cite(f"{intel['symbol']} is down {price['change_pct']:+.2f}% intraday at {price['ltp']} BDT.")
        if sm["value"] is not None and sm["value"] <= 40:
            cite(f"Tick-level CLV × Δvolume shows {sm['label']} ({sm['value']}/100) — sellers are pressing intraday highs.")
        rep = sig.get("repeated", {})
        if rep.get("selling_run", 0) >= 3:
            cite(f"Repeated selling — {rep['selling_run']} consecutive down-ticks.")
        if vol["value"] is not None:
            if vol["value"] >= 1.5:
                cite(f"Volume is heavy ({vol['value']}× normal) — distribution likely, not noise.")
            elif vol["value"] <= 0.7:
                cite(f"Volume is thin ({vol['value']}× normal) — could be drift rather than conviction.")
    elif q == "volume":
        if vol["value"] is None:
            cite(f"Volume baseline not yet established (need ≥3 prior trading days; have {(vol['inputs'] or {}).get('prior_days', 0)}).")
        else:
            cite(f"Today's volume is {price['volume']:,} shares vs prior-day median {vol['inputs']['prior_median']:,} = {vol['value']}× normal ({vol['label']}).")
            if vol["value"] >= 2:
                cite("Volume surge ≥2× is the textbook accumulation/distribution trigger — pair with price direction to interpret.")
    elif q == "smart_money":
        if sm["value"] is None:
            cite("Not enough variability across recent ticks to compute the CLV × volume signal.")
        else:
            inputs = sm["inputs"]
            cite(f"Buying pressure (Σ positive CLV × Δvol): {inputs['buying']:,}.")
            cite(f"Selling pressure (Σ negative CLV × Δvol): {inputs['selling']:,}.")
            cite(f"Net signal: {sm['label']} → score {sm['value']}/100 over {inputs['ticks']} contributing ticks ({sm['confidence']} confidence).")
    elif q == "circuit":
        if c["upper_pct"] is None:
            cite("Circuit probability cannot be computed — missing price data.")
        else:
            ipt = c.get("inputs", {})
            cite(f"Price progress toward upper circuit: {int(ipt.get('upper_progress', 0) * 100)}%.")
            cite(f"Price progress toward lower circuit: {int(ipt.get('lower_progress', 0) * 100)}%.")
            cite(f"Evidence weight added to upper reach: +{ipt.get('upper_evidence', 0)} (max 0.45 possible).")
            cite(f"Reach prob = price_progress × 0.4 + evidence — never above 85%.")
            cite(f"Final: upper {c['upper_pct']}%, lower {c['lower_pct']}% ({c['confidence']} confidence).")
    else:  # summary
        cite(f"{intel['symbol']}: LTP {price['ltp']} BDT, {price['change_pct']:+.2f}% on the session.")
        if mom["value"] is not None:
            cite(f"Momentum {mom['value']}/100 ({mom['label']}).")
        if sm["value"] is not None:
            cite(f"Smart-money flow {sm['value']}/100 ({sm['label']}).")
        if vol["value"] is not None:
            cite(f"Volume {vol['value']}× prior-day median ({vol['label']}).")
        if c["upper_pct"] is not None and c["upper_pct"] >= 30:
            cite(f"Upper-circuit reach {c['upper_pct']}% ({c['confidence']} confidence).")
        if c["lower_pct"] is not None and c["lower_pct"] >= 30:
            cite(f"Lower-circuit reach {c['lower_pct']}% ({c['confidence']} confidence).")
        if lv.get("resistance"):
            cite(f"Resistance levels (from 30-day pivots): {', '.join(str(x) for x in lv['resistance'])}.")
        if lv.get("support"):
            cite(f"Support levels: {', '.join(str(x) for x in lv['support'])}.")

    if not lines:
        lines = ["No notable signals yet — try again after more intraday ticks accumulate."]
    confidence = "high" if len(lines) >= 4 else "medium" if len(lines) >= 2 else "low"
    return {
        "question": q,
        "symbol": intel["symbol"],
        "explanation": " ".join(lines),
        "lines": lines,
        "confidence": confidence,
        "as_of": str(intel.get("as_of") or ""),
    }


@router.get("/{symbol}")
def explain(
    symbol: str,
    q: str = Query("summary", description=f"one of {sorted(QUESTIONS)}"),
    db: Session = Depends(get_db),
):
    if q not in QUESTIONS:
        raise HTTPException(400, f"unknown question; allowed: {sorted(QUESTIONS)}")
    intel = compute_intel(db, symbol)
    if "error" in intel:
        raise HTTPException(404, intel["error"])
    return _explain(intel, q)
