"""Heuristic intelligence engine — evidence-bounded.

All scores ship with `factors` (the evidence that produced them) and a
`confidence` band. No probability above 85% without ≥3 corroborating
signals. Health/Valuation/Risk return None until Phase 3 fundamentals.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..models import Company, PriceDaily, PriceTick, Sector


DEFAULT_CIRCUIT_PCT = 10.0  # DSE typical daily band — varies 2-10% by price tier


@dataclass
class IntelScore:
    value: Optional[float]
    label: str
    confidence: str  # low | medium | high
    factors: list[str]
    formula: str = ""
    inputs: dict | None = None

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "label": self.label,
            "confidence": self.confidence,
            "factors": self.factors,
            "formula": self.formula,
            "inputs": self.inputs or {},
        }


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _company(db: Session, symbol: str) -> tuple[Company | None, str | None]:
    row = db.execute(
        select(Company, Sector.name)
        .join(Sector, Sector.id == Company.sector_id, isouter=True)
        .where(Company.symbol == symbol.upper())
    ).first()
    if not row:
        return None, None
    return row[0], row[1]


def _recent_ticks(db: Session, company_id: int, hours: int = 24) -> list[PriceTick]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return (
        db.execute(
            select(PriceTick)
            .where(PriceTick.company_id == company_id)
            .where(PriceTick.captured_at >= cutoff)
            .order_by(PriceTick.captured_at)
        )
        .scalars()
        .all()
    )


def _recent_daily(db: Session, company_id: int, days: int = 60) -> list[PriceDaily]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    return (
        db.execute(
            select(PriceDaily)
            .where(PriceDaily.company_id == company_id)
            .where(PriceDaily.trade_date >= cutoff)
            .order_by(PriceDaily.trade_date)
        )
        .scalars()
        .all()
    )


# -------------------- individual signal detectors --------------------

MOMENTUM_FORMULA = "score = 50 + (intraday_Δ% / ±10% circuit band) × 50"


def _momentum(ticks: list[PriceTick]) -> IntelScore:
    if len(ticks) < 2:
        return IntelScore(None, "n/a", "low", ["not enough ticks"], MOMENTUM_FORMULA)
    last = ticks[-1]
    if last.ltp is None or last.ycp is None or last.ycp <= 0:
        return IntelScore(None, "n/a", "low", ["missing price"], MOMENTUM_FORMULA)
    change_pct = (last.ltp - last.ycp) / last.ycp * 100
    value = _clamp(50 + (change_pct / DEFAULT_CIRCUIT_PCT) * 50, 0, 100)
    label = ("strong up" if value >= 75 else "up" if value >= 55
             else "strong down" if value <= 25 else "down" if value <= 45 else "neutral")
    conf = "high" if len(ticks) >= 30 else "medium" if len(ticks) >= 8 else "low"
    return IntelScore(
        round(value, 1), label, conf,
        [
            f"intraday change {change_pct:+.2f}%",
            f"normalized vs ±{DEFAULT_CIRCUIT_PCT:.0f}% circuit band",
            f"tick sample {len(ticks)} → {conf} confidence",
        ],
        MOMENTUM_FORMULA,
        {"ltp": last.ltp, "ycp": last.ycp, "change_pct": round(change_pct, 4), "ticks": len(ticks)},
    )


VOL_FORMULA = "ratio = today_cumulative_volume / median(prior N daily volumes)"


def _volume_anomaly(ticks: list[PriceTick], daily: list[PriceDaily]) -> IntelScore:
    if not ticks or ticks[-1].volume is None:
        return IntelScore(None, "n/a", "low", ["no volume data"], VOL_FORMULA)
    today_vol = ticks[-1].volume  # cumulative intraday
    prior_vols = [d.volume for d in daily[:-1] if d.volume]
    if len(prior_vols) < 3:
        return IntelScore(
            None, "warming up", "low",
            [f"need ≥3 prior daily closes; have {len(prior_vols)}"],
            VOL_FORMULA,
            {"today_vol": today_vol, "prior_days": len(prior_vols)},
        )
    baseline = median(prior_vols)
    if baseline <= 0:
        return IntelScore(None, "n/a", "low", ["zero baseline"], VOL_FORMULA)
    ratio = today_vol / baseline
    label = ("extreme surge" if ratio >= 4 else "surge" if ratio >= 2
             else "above avg" if ratio >= 1.3 else "below avg" if ratio <= 0.7 else "normal")
    conf = "high" if len(prior_vols) >= 10 else "medium"
    return IntelScore(
        round(ratio, 2), label, conf,
        [
            f"today vol {today_vol:,}",
            f"prior-day median {int(baseline):,} over {len(prior_vols)} days",
            f"ratio {ratio:.2f}×",
        ],
        VOL_FORMULA,
        {"today_vol": today_vol, "prior_median": int(baseline), "ratio": round(ratio, 4)},
    )


SM_FORMULA = (
    "CLV_t = ((C-L)-(H-C)) / (H-L); "
    "score = 50 + Σ(CLV_t × Δvol_t) / Σ|CLV_t × Δvol_t| × 50"
)


def _smart_money(ticks: list[PriceTick]) -> IntelScore:
    """A/D via Williams CLV × volume-delta."""
    if len(ticks) < 3:
        return IntelScore(None, "n/a", "low", ["need ≥3 ticks"], SM_FORMULA)
    total_pos = total_neg = 0.0
    contributing = 0
    prev_vol = None
    for t in ticks:
        h, l, c, v = t.high, t.low, t.ltp, t.volume
        if None in (h, l, c, v) or h <= l:
            if v is not None:
                prev_vol = v
            continue
        clv = ((c - l) - (h - c)) / (h - l)
        delta_vol = max(0, v - prev_vol) if prev_vol is not None else v
        prev_vol = v
        if delta_vol <= 0:
            continue
        contribution = clv * delta_vol
        if contribution > 0:
            total_pos += contribution
        else:
            total_neg += -contribution
        contributing += 1
    if contributing < 2:
        return IntelScore(None, "n/a", "low", ["insufficient tick variability"], SM_FORMULA)
    if total_pos + total_neg == 0:
        return IntelScore(50.0, "neutral", "low", ["zero CLV across ticks"], SM_FORMULA)
    net = total_pos - total_neg
    magnitude = total_pos + total_neg
    score = _clamp(50 + (net / magnitude) * 50, 0, 100)
    label = ("strong accumulation" if score >= 75
             else "accumulation" if score >= 60
             else "strong distribution" if score <= 25
             else "distribution" if score <= 40 else "balanced")
    conf = "high" if contributing >= 20 else "medium" if contributing >= 8 else "low"
    return IntelScore(
        round(score, 1), label, conf,
        [
            f"buying pressure (CLV·Δvol summed positive) {int(total_pos):,}",
            f"selling pressure (negative half) {int(total_neg):,}",
            f"contributing ticks {contributing}",
        ],
        SM_FORMULA,
        {"buying": int(total_pos), "selling": int(total_neg), "ticks": contributing},
    )


def _repeated_directional(ticks: list[PriceTick]) -> dict:
    """Count longest consecutive run of higher / lower ticks (LTP)."""
    if len(ticks) < 2:
        return {"buying_run": 0, "selling_run": 0, "evidence": ["need ≥2 ticks"]}
    longest_up = cur_up = longest_dn = cur_dn = 0
    for i in range(1, len(ticks)):
        a, b = ticks[i-1].ltp, ticks[i].ltp
        if a is None or b is None:
            cur_up = cur_dn = 0
            continue
        if b > a:
            cur_up += 1; cur_dn = 0
            longest_up = max(longest_up, cur_up)
        elif b < a:
            cur_dn += 1; cur_up = 0
            longest_dn = max(longest_dn, cur_dn)
        else:
            cur_up = cur_dn = 0
    evidence = []
    if longest_up >= 3: evidence.append(f"repeated buying detected — {longest_up} consecutive up-ticks")
    if longest_dn >= 3: evidence.append(f"repeated selling detected — {longest_dn} consecutive down-ticks")
    return {"buying_run": longest_up, "selling_run": longest_dn, "evidence": evidence}


def _absorption(ticks: list[PriceTick]) -> dict:
    """Absorption: price range tight while cumulative volume rises sharply."""
    if len(ticks) < 5:
        return {"detected": False, "evidence": ["need ≥5 ticks"]}
    last = ticks[-1]
    if last.high is None or last.low is None or last.ltp is None or last.ltp <= 0:
        return {"detected": False, "evidence": ["missing price"]}
    range_pct = (last.high - last.low) / last.ltp * 100
    # Volume delta across last 3 ticks
    vols = [t.volume for t in ticks[-4:] if t.volume is not None]
    if len(vols) < 2:
        return {"detected": False, "evidence": ["missing volume"]}
    vol_delta = max(0, vols[-1] - vols[0])
    # Compare against avg per-tick delta
    deltas = [max(0, vols[i] - vols[i-1]) for i in range(1, len(vols))]
    avg = sum(deltas) / len(deltas) if deltas else 0
    detected = range_pct < 1.5 and vol_delta > 3 * avg and avg > 0
    return {
        "detected": detected,
        "range_pct": round(range_pct, 2),
        "volume_delta": vol_delta,
        "evidence": (
            [f"range {range_pct:.2f}% (<1.5%) + 3× avg volume influx" ] if detected
            else [f"range {range_pct:.2f}%, vol pattern unremarkable"]
        ),
    }


def _breakout(ticks: list[PriceTick], daily: list[PriceDaily]) -> dict:
    """Breakout: LTP > recent N-day high by ≥0.5% with above-avg volume."""
    if not ticks or ticks[-1].ltp is None:
        return {"detected": False, "evidence": ["no price"]}
    ltp = ticks[-1].ltp
    look = [d.high for d in daily[-10:-1] if d.high]  # last 9 prior days
    if len(look) < 3:
        return {"detected": False, "evidence": [f"need ≥3 prior daily highs; have {len(look)}"]}
    n_high = max(look)
    vol = ticks[-1].volume
    prior_vols = [d.volume for d in daily[-10:-1] if d.volume]
    vol_ok = vol is not None and prior_vols and vol > 1.3 * median(prior_vols)
    breach = ltp > n_high * 1.005
    detected = breach and vol_ok
    return {
        "detected": detected,
        "level": round(n_high, 2),
        "evidence": (
            [f"LTP {ltp} > {len(look)}-day high {n_high} (+{(ltp/n_high-1)*100:.1f}%)",
             f"volume {vol:,} > 1.3× prior median"] if detected
            else [f"LTP {ltp} vs {len(look)}-day high {n_high} — no breakout"]
        ),
    }


def _pivot_levels(daily: list[PriceDaily], lookback: int = 30) -> dict:
    """Support / resistance from pivot highs and lows in the daily series."""
    if len(daily) < 5:
        return {"support": [], "resistance": [], "evidence": [f"need ≥5 daily bars; have {len(daily)}"]}
    window = daily[-lookback:] if len(daily) >= lookback else daily
    highs = [d.high for d in window if d.high]
    lows = [d.low for d in window if d.low]
    if not highs or not lows:
        return {"support": [], "resistance": [], "evidence": ["missing high/low"]}

    last_close = window[-1].close or window[-1].ycp
    if last_close is None:
        return {"support": [], "resistance": [], "evidence": ["missing close"]}

    res = sorted({round(h, 2) for h in highs if h >= last_close}, reverse=False)[:3]
    sup = sorted({round(l, 2) for l in lows  if l <= last_close}, reverse=True)[:3]
    return {
        "support": sup,
        "resistance": res,
        "evidence": [f"derived from {len(window)} daily bars"],
    }


def _circuit(
    ticks: list[PriceTick],
    vol_score: IntelScore,
    smart_score: IntelScore,
    repeated: dict,
) -> dict:
    """Circuit reach probability — evidence-bounded.

    Pure price-position only contributes up to 40% of the probability.
    Volume confirmation, accumulation/distribution, and repeated directional
    pressure each contribute additional 15-pt blocks. Without confirming
    evidence, probabilities never exceed 40% even at the circuit band edge.
    """
    if not ticks or ticks[-1].ltp is None or ticks[-1].ycp is None or ticks[-1].ycp <= 0:
        return {
            "upper_pct": None, "lower_pct": None, "continuation_pct": None,
            "confidence": "low", "factors": ["no price data"],
        }
    last = ticks[-1]
    change_pct = (last.ltp - last.ycp) / last.ycp * 100
    upper_progress = _clamp(max(0, change_pct) / DEFAULT_CIRCUIT_PCT, 0, 1)
    lower_progress = _clamp(max(0, -change_pct) / DEFAULT_CIRCUIT_PCT, 0, 1)

    # ---- Evidence for upper move ----
    upper_evidence = 0.0
    upper_factors = [f"intraday change {change_pct:+.2f}%"]
    if vol_score.value is not None and vol_score.value >= 2.0:
        upper_evidence += 0.18
        upper_factors.append(f"volume {vol_score.value}× prior median (surge)")
    elif vol_score.value is not None and vol_score.value >= 1.3:
        upper_evidence += 0.08
        upper_factors.append(f"volume {vol_score.value}× prior median (above avg)")
    if smart_score.value is not None and smart_score.value >= 65:
        upper_evidence += 0.15
        upper_factors.append(f"smart-money accumulation ({smart_score.value})")
    if repeated["buying_run"] >= 3:
        upper_evidence += 0.12
        upper_factors.append(f"repeated buying — {repeated['buying_run']} up-ticks in a row")

    # ---- Evidence for lower move ----
    lower_evidence = 0.0
    lower_factors = [f"intraday change {change_pct:+.2f}%"]
    if vol_score.value is not None and vol_score.value >= 2.0:
        lower_evidence += 0.18
        lower_factors.append(f"volume {vol_score.value}× prior median (surge)")
    elif vol_score.value is not None and vol_score.value >= 1.3:
        lower_evidence += 0.08
    if smart_score.value is not None and smart_score.value <= 35:
        lower_evidence += 0.15
        lower_factors.append(f"smart-money distribution ({smart_score.value})")
    if repeated["selling_run"] >= 3:
        lower_evidence += 0.12
        lower_factors.append(f"repeated selling — {repeated['selling_run']} down-ticks in a row")

    upper_prob = _clamp(upper_progress * 0.4 + upper_evidence, 0, 0.85)
    lower_prob = _clamp(lower_progress * 0.4 + lower_evidence, 0, 0.85)

    # Continuation: if already at circuit, will it hold tomorrow?
    # Without multi-day persistence data, only emit when current intraday is
    # >85% of circuit AND we have evidence; otherwise None.
    continuation = None
    cont_factors = []
    if upper_progress >= 0.85 and upper_evidence >= 0.20:
        # crude: 35% base + half the evidence boost
        continuation = _clamp(0.35 + upper_evidence * 0.5, 0.2, 0.65)
        cont_factors.append("at upper band with confirmation — continuation moderately likely")
    elif lower_progress >= 0.85 and lower_evidence >= 0.20:
        continuation = _clamp(0.35 + lower_evidence * 0.5, 0.2, 0.65)
        cont_factors.append("at lower band with confirmation — continuation moderately likely")

    # Confidence: count corroborating factors
    confirming = 0
    if vol_score.value is not None and vol_score.value >= 1.3: confirming += 1
    if smart_score.value is not None and (smart_score.value >= 65 or smart_score.value <= 35): confirming += 1
    if repeated["buying_run"] >= 3 or repeated["selling_run"] >= 3: confirming += 1
    if len(ticks) >= 20: confirming += 1
    conf = "high" if confirming >= 3 else "medium" if confirming >= 1 else "low"

    return {
        "upper_pct": round(upper_prob * 100, 1),
        "lower_pct": round(lower_prob * 100, 1),
        "continuation_pct": round(continuation * 100, 1) if continuation else None,
        "continuation_factors": cont_factors,
        "confidence": conf,
        "factors": (upper_factors if upper_progress > lower_progress else lower_factors),
        "evidence_score": round(max(upper_evidence, lower_evidence), 2),
        "formula": (
            "prob = min(85%, price_progress × 0.4 + Σ evidence_weights); "
            "evidence: vol≥2× +0.18, vol≥1.3× +0.08, smart_acc≥65 +0.15, repeated_run≥3 +0.12"
        ),
        "inputs": {
            "upper_progress": round(upper_progress, 3),
            "lower_progress": round(lower_progress, 3),
            "upper_evidence": round(upper_evidence, 3),
            "lower_evidence": round(lower_evidence, 3),
            "confirming_factors": confirming,
        },
    }


# -------------------- main entry --------------------

def compute_intel(db: Session, symbol: str) -> dict:
    company, sector_name = _company(db, symbol)
    if not company:
        return {"error": "symbol not found", "symbol": symbol.upper()}

    ticks = _recent_ticks(db, company.id, hours=24)
    daily = _recent_daily(db, company.id, days=60)
    latest = ticks[-1] if ticks else None

    momentum = _momentum(ticks)
    volume   = _volume_anomaly(ticks, daily)
    smart    = _smart_money(ticks)
    repeated = _repeated_directional(ticks)
    absorb   = _absorption(ticks)
    breakout = _breakout(ticks, daily)
    levels   = _pivot_levels(daily)
    circuit  = _circuit(ticks, volume, smart, repeated)

    return {
        "symbol": company.symbol,
        "exchange": company.exchange,
        "sector": sector_name,
        "as_of": latest.captured_at if latest else None,
        "price": {
            "ltp":  latest.ltp if latest else None,
            "open": latest.open if latest else None,
            "high": latest.high if latest else None,
            "low":  latest.low if latest else None,
            "ycp":  latest.ycp if latest else None,
            "change": latest.change if latest else None,
            "change_pct": latest.change_pct if latest else None,
            "volume": latest.volume if latest else None,
            "trades": latest.trades if latest else None,
            "value_bdt": latest.value_bdt if latest else None,
        },
        "scores": {
            "momentum":       momentum.to_dict(),
            "volume_anomaly": volume.to_dict(),
            "smart_money":    smart.to_dict(),
        },
        "signals": {
            "repeated":  repeated,
            "absorption": absorb,
            "breakout":   breakout,
        },
        "levels": levels,
        "circuit": circuit,
        "fundamentals_pending": {
            "health":    "Phase 3 — needs EPS/NAV/Dividend feed",
            "valuation": "Phase 3 — needs P/E, P/B, sector comps",
            "risk":      "Phase 3 — needs beta, debt ratios, liquidity",
        },
        "tick_count_24h": len(ticks),
        "daily_count_60d": len(daily),
    }


# -------------------- chart overlays --------------------

def compute_overlays(db: Session, symbol: str) -> dict:
    """Produce overlay primitives for the chart:
    - horizontal_levels: support/resistance lines with labels
    - zones: accumulation/distribution rectangles (price band, time band)
    - markers: breakout candle markers, AI notes
    """
    company, _ = _company(db, symbol)
    if not company:
        return {"error": "symbol not found"}
    intel = compute_intel(db, symbol)
    ticks = _recent_ticks(db, company.id, hours=24)
    last_ts = ticks[-1].captured_at.timestamp() if ticks else None
    first_ts = ticks[0].captured_at.timestamp() if ticks else None

    horizontals = []
    for lvl in intel.get("levels", {}).get("resistance", []):
        horizontals.append({"price": lvl, "color": "#ff5560", "label": f"R {lvl}"})
    for lvl in intel.get("levels", {}).get("support", []):
        horizontals.append({"price": lvl, "color": "#2fd49a", "label": f"S {lvl}"})

    zones = []
    sm = intel.get("scores", {}).get("smart_money", {})
    if sm.get("value") is not None and ticks:
        last = ticks[-1]
        if last.low and last.high and first_ts and last_ts:
            if sm["value"] >= 60:
                zones.append({
                    "kind": "accumulation",
                    "from": first_ts, "to": last_ts,
                    "low": last.low, "high": last.low + (last.high - last.low) * 0.35,
                    "color": "rgba(47, 212, 154, 0.10)",
                    "label": sm.get("label", "accumulation"),
                })
            elif sm["value"] <= 40:
                zones.append({
                    "kind": "distribution",
                    "from": first_ts, "to": last_ts,
                    "low": last.low + (last.high - last.low) * 0.65, "high": last.high,
                    "color": "rgba(255, 85, 96, 0.10)",
                    "label": sm.get("label", "distribution"),
                })

    markers = []
    bk = intel.get("signals", {}).get("breakout", {})
    if bk.get("detected") and last_ts:
        markers.append({
            "time": int(last_ts), "position": "aboveBar",
            "color": "#f7a300", "shape": "arrowUp",
            "text": f"BREAKOUT {bk.get('level')}",
        })
    absorb = intel.get("signals", {}).get("absorption", {})
    if absorb.get("detected") and last_ts:
        markers.append({
            "time": int(last_ts), "position": "belowBar",
            "color": "#a78bfa", "shape": "circle",
            "text": "ABSORB",
        })
    rep = intel.get("signals", {}).get("repeated", {})
    if rep.get("buying_run", 0) >= 3 and last_ts:
        markers.append({
            "time": int(last_ts), "position": "belowBar",
            "color": "#2fd49a", "shape": "arrowUp",
            "text": f"BUY×{rep['buying_run']}",
        })
    if rep.get("selling_run", 0) >= 3 and last_ts:
        markers.append({
            "time": int(last_ts), "position": "aboveBar",
            "color": "#ff5560", "shape": "arrowDown",
            "text": f"SELL×{rep['selling_run']}",
        })

    notes = []
    circ = intel.get("circuit", {})
    if circ.get("upper_pct") and circ["upper_pct"] >= 40:
        notes.append(f"Upper-circuit reach {circ['upper_pct']}% (conf {circ['confidence']})")
    if circ.get("lower_pct") and circ["lower_pct"] >= 40:
        notes.append(f"Lower-circuit reach {circ['lower_pct']}% (conf {circ['confidence']})")
    if circ.get("continuation_pct"):
        notes.append(f"Continuation prob {circ['continuation_pct']}%")

    return {
        "symbol": symbol.upper(),
        "horizontals": horizontals,
        "zones": zones,
        "markers": markers,
        "notes": notes,
    }
