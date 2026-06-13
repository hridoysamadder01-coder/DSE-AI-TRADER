"""Watchlist intelligence — generate auto-alerts for monitored symbols."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.intel import compute_intel

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class AlertsRequest(BaseModel):
    symbols: List[str]


@router.post("/alerts")
def alerts(body: AlertsRequest, db: Session = Depends(get_db)):
    """Run intel on each symbol and emit triggered alerts.

    Alert categories:
    - VOLUME_SURGE: today vol ≥2× prior median
    - MOMENTUM_UP / MOMENTUM_DOWN: momentum score ≥75 / ≤25
    - SMART_ACCUMULATION / SMART_DISTRIBUTION: smart-money ≥70 / ≤30
    - CIRCUIT_RISK_UPPER / CIRCUIT_RISK_LOWER: circuit reach ≥50% with confirmation
    - BREAKOUT: breakout detected
    - REPEATED_BUYING / REPEATED_SELLING: ≥4 consecutive directional ticks
    - ABSORPTION: absorption pattern
    """
    out = []
    now = datetime.now(timezone.utc).isoformat()
    for sym in body.symbols:
        d = compute_intel(db, sym)
        if "error" in d:
            continue
        sig = d.get("signals", {})
        sc = d.get("scores", {})
        c = d.get("circuit", {})

        def push(kind, severity, message, evidence):
            out.append({
                "symbol": d["symbol"], "kind": kind, "severity": severity,
                "message": message, "evidence": evidence, "ts": now,
            })

        if sc.get("volume_anomaly", {}).get("value") and sc["volume_anomaly"]["value"] >= 2:
            push("VOLUME_SURGE", "info",
                 f"Volume {sc['volume_anomaly']['value']}× prior median",
                 sc["volume_anomaly"]["factors"])
        if sc.get("momentum", {}).get("value") is not None:
            mv = sc["momentum"]["value"]
            if mv >= 75: push("MOMENTUM_UP", "info", f"Momentum {mv}/100", sc["momentum"]["factors"])
            elif mv <= 25: push("MOMENTUM_DOWN", "warn", f"Momentum {mv}/100", sc["momentum"]["factors"])
        if sc.get("smart_money", {}).get("value") is not None:
            sv = sc["smart_money"]["value"]
            if sv >= 70: push("SMART_ACCUMULATION", "info",
                              f"Accumulation {sv}/100", sc["smart_money"]["factors"])
            elif sv <= 30: push("SMART_DISTRIBUTION", "warn",
                                f"Distribution {sv}/100", sc["smart_money"]["factors"])
        if c.get("upper_pct") and c["upper_pct"] >= 50 and c["confidence"] != "low":
            push("CIRCUIT_RISK_UPPER", "warn",
                 f"Upper-circuit reach {c['upper_pct']}% ({c['confidence']} conf)",
                 c.get("factors", []))
        if c.get("lower_pct") and c["lower_pct"] >= 50 and c["confidence"] != "low":
            push("CIRCUIT_RISK_LOWER", "warn",
                 f"Lower-circuit reach {c['lower_pct']}% ({c['confidence']} conf)",
                 c.get("factors", []))
        if sig.get("breakout", {}).get("detected"):
            push("BREAKOUT", "info",
                 f"Breakout above {sig['breakout'].get('level')}",
                 sig["breakout"].get("evidence", []))
        rep = sig.get("repeated", {})
        if rep.get("buying_run", 0) >= 4:
            push("REPEATED_BUYING", "info",
                 f"{rep['buying_run']} up-ticks in a row", rep.get("evidence", []))
        if rep.get("selling_run", 0) >= 4:
            push("REPEATED_SELLING", "warn",
                 f"{rep['selling_run']} down-ticks in a row", rep.get("evidence", []))
        if sig.get("absorption", {}).get("detected"):
            push("ABSORPTION", "info", "Absorption pattern detected",
                 sig["absorption"].get("evidence", []))

    # Order: error > warn > info
    order = {"error": 0, "warn": 1, "info": 2}
    out.sort(key=lambda a: (order.get(a["severity"], 3), a["symbol"]))
    return {"count": len(out), "alerts": out}
