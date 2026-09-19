"""Trade post-mortem engine.

Categories are assigned only when evidence exists.
UNKNOWN is a first-class legitimate outcome.
"""
from __future__ import annotations

from collections import Counter
from typing import Sequence

import pandas as pd

from ..engines.backtest import TradeResult


def postmortem_trades(
    trades: Sequence[TradeResult],
    feature_df: pd.DataFrame | None = None,
) -> list[dict]:
    results = []
    for t in trades:
        pm = {
            "trade_id": t.trade_id,
            "direction": t.direction,
            "exit_reason": t.exit_reason,
            "net_r": t.net_r,
            "primary_reason": "UNKNOWN",
            "secondary_reason": None,
            "confidence_in_reason": 0.0,
            "evidence": [],
            "unknown_flag": True,
        }
        if t.net_r >= 0:
            pm["primary_reason"] = "WIN"
            pm["unknown_flag"] = False
            pm["confidence_in_reason"] = 1.0
            pm["evidence"].append("positive_net_r")
            results.append(pm)
            continue

        # Losing trade analysis
        if t.exit_reason == "SL":
            # Heuristic: if holding very short → possible noise / too tight
            if t.holding_bars <= 2:
                pm["primary_reason"] = "SL_TOO_TIGHT"
                pm["evidence"].append("holding_bars<=2")
                pm["confidence_in_reason"] = 0.4
                pm["unknown_flag"] = False
            else:
                pm["primary_reason"] = "WRONG_DIRECTION"
                pm["evidence"].append("hit_sl")
                pm["confidence_in_reason"] = 0.5
                pm["unknown_flag"] = False

        if t.regime in ("CHOP", "SIDEWAYS", "RANGE_OR_TRANSITION"):
            if pm["primary_reason"] == "UNKNOWN":
                pm["primary_reason"] = "CHOPPY"
            else:
                pm["secondary_reason"] = "CHOPPY"
            pm["evidence"].append(f"regime={t.regime}")
            pm["confidence_in_reason"] = max(pm["confidence_in_reason"], 0.45)
            pm["unknown_flag"] = False

        if t.vol_regime == "EXTREME_VOL":
            pm["secondary_reason"] = pm["secondary_reason"] or "VOLATILITY_SPIKE"
            pm["evidence"].append("extreme_vol")

        if "MACD" in (t.signal_reason or "").upper() or "macd" in (t.signal_reason or ""):
            pm["secondary_reason"] = "MACD_CONFLICT"
            pm["evidence"].append("macd_related_signal")

        results.append(pm)
    return results


def summarize_postmortem(pm_list: list[dict]) -> dict:
    losers = [p for p in pm_list if p.get("net_r", 0) < 0]
    winners = [p for p in pm_list if p.get("net_r", 0) >= 0]
    reasons = Counter(p["primary_reason"] for p in losers)
    total_loss = max(len(losers), 1)
    return {
        "n_trades": len(pm_list),
        "n_wins": len(winners),
        "n_losses": len(losers),
        "loss_reason_counts": dict(reasons),
        "loss_reason_pct": {k: round(v / total_loss * 100, 1) for k, v in reasons.items()},
        "unknown_loss_pct": round(reasons.get("UNKNOWN", 0) / total_loss * 100, 1),
        "wrong_direction_pct": round(reasons.get("WRONG_DIRECTION", 0) / total_loss * 100, 1),
        "sl_too_tight_pct": round(reasons.get("SL_TOO_TIGHT", 0) / total_loss * 100, 1),
    }
