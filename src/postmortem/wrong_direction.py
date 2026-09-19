"""
Wrong-direction deep engine: LONG vs SHORT vs NO-TRADE with multi-evidence snapshot.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from ..counterfactual.full_cf import simulate_path


EVIDENCE_COLS = [
    "deep_bias", "structure_state", "htf_bias", "ltf_bias", "mtf_align", "mtf_conflict",
    "rsi", "macd_hist", "rel_volume", "deriv_regime", "funding_extreme_pos", "funding_extreme_neg",
    "oi_shock", "sweep_low", "sweep_high", "bos_up", "bos_dn", "session",
    "btc_ret", "breadth_up", "rs_vs_btc", "vol_regime", "market_regime",
]


def analyze_wrong_direction(
    df: pd.DataFrame,
    entry_i: int,
    taken_direction: int,
    atr_mult: float = 1.85,
    rr: float = 2.0,
    max_bars: int = 24,
) -> dict[str, Any]:
    if entry_i >= len(df) - 2:
        return {"status": "NO_PATH"}
    row = df.iloc[entry_i]
    atr = float(row["atr"]) if "atr" in df.columns and pd.notna(row.get("atr")) else float(row["close"]) * 0.01
    sl = atr * atr_mult
    tp = sl * rr
    long_p = simulate_path(df, entry_i, 1, sl, tp, max_bars)
    short_p = simulate_path(df, entry_i, -1, sl, tp, max_bars)
    taken = long_p if taken_direction == 1 else short_p
    alt = short_p if taken_direction == 1 else long_p

    evidence = {}
    for c in EVIDENCE_COLS:
        if c in df.columns:
            v = row[c]
            evidence[c] = v.item() if hasattr(v, "item") else v

    # classify
    if taken["r"] < 0 and alt["r"] > taken["r"]:
        verdict = "OPPOSITE_WAS_BETTER"
    elif taken["r"] < 0 and alt["r"] <= 0:
        verdict = "BOTH_DIRECTIONS_BAD_NO_TRADE_BETTER"
    elif taken["r"] >= 0:
        verdict = "NOT_WRONG_DIRECTION"
    else:
        verdict = "WRONG_DIRECTION_UNCLEAR"

    reasons = []
    if evidence.get("mtf_conflict") in (1, True):
        reasons.append("HTF_LTF_CONFLICT")
    if evidence.get("deep_bias") == "BEARISH" and taken_direction == 1:
        reasons.append("STRUCTURE_AGAINST_LONG")
    if evidence.get("deep_bias") == "BULLISH" and taken_direction == -1:
        reasons.append("STRUCTURE_AGAINST_SHORT")
    if evidence.get("funding_extreme_pos") in (1, True) and taken_direction == 1:
        reasons.append("CROWDED_LONG_FUNDING")
    if evidence.get("deriv_regime") == "SHORT_BUILD" and taken_direction == 1:
        reasons.append("OI_PRICE_SHORT_BUILD")
    if evidence.get("btc_ret") is not None:
        try:
            if float(evidence["btc_ret"]) < -0.01 and taken_direction == 1:
                reasons.append("BTC_WEAK_AGAINST_LONG")
        except (TypeError, ValueError):
            pass

    return {
        "entry_i": entry_i,
        "taken_direction": taken_direction,
        "taken_path": taken,
        "opposite_path": alt,
        "no_trade_r": 0.0,
        "verdict": verdict,
        "evidence": evidence,
        "reason_tags": reasons,
        "status": "OK",
    }


def batch_wrong_direction(
    df: pd.DataFrame,
    trade_entries: list[tuple[int, int]],
) -> dict[str, Any]:
    """trade_entries: list of (entry_index, direction)."""
    reports = [analyze_wrong_direction(df, i, d) for i, d in trade_entries]
    from collections import Counter
    verdicts = Counter(r.get("verdict") for r in reports)
    tags = Counter(t for r in reports for t in r.get("reason_tags", []))
    return {
        "n": len(reports),
        "verdict_counts": dict(verdicts),
        "top_reason_tags": tags.most_common(15),
        "cases": reports[:50],
    }
