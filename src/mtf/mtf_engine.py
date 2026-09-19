"""Multi-timeframe reasoning — HTF bias, LTF entry, conflict matrix."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def align_htf_to_ltf(ltf: pd.DataFrame, htf: pd.DataFrame, htf_cols: list[str]) -> pd.DataFrame:
    """As-of merge HTF features onto LTF timeline (no lookahead)."""
    if ltf.empty or htf.empty:
        return ltf
    h = htf[["open_time"] + [c for c in htf_cols if c in htf.columns]].copy()
    h = h.sort_values("open_time")
    out = pd.merge_asof(
        ltf.sort_values("open_time"),
        h,
        on="open_time",
        direction="backward",
        suffixes=("", "_htf"),
    )
    return out


def add_mtf_context(
    ltf: pd.DataFrame,
    htf: Optional[pd.DataFrame],
    htf_label: str = "1h",
) -> pd.DataFrame:
    out = ltf.copy()
    if htf is None or htf.empty:
        out["htf_bias"] = "UNKNOWN"
        out["mtf_align"] = 0
        out["mtf_conflict"] = 0
        out["mtf_status"] = "NO_HTF"
        return out

    cols = [c for c in ["deep_bias", "structure_bias", "trend_regime", "market_regime", "rsi", "macd_hist"] if c in htf.columns]
    if not cols:
        out["htf_bias"] = "UNKNOWN"
        out["mtf_status"] = "NO_HTF_COLS"
        return out

    merged = align_htf_to_ltf(out, htf, cols)
    # HTF bias
    if "deep_bias" in cols:
        merged["htf_bias"] = merged["deep_bias"]
    elif "structure_bias" in cols:
        merged["htf_bias"] = merged["structure_bias"]
    elif "trend_regime" in cols:
        merged["htf_bias"] = merged["trend_regime"].map(
            {
                "STRONG_BULL": "BULLISH",
                "WEAK_BULL": "BULLISH",
                "STRONG_BEAR": "BEARISH",
                "WEAK_BEAR": "BEARISH",
                "SIDEWAYS": "NONE",
                "CHOP": "NONE",
            }
        ).fillna("UNKNOWN")
    else:
        merged["htf_bias"] = "UNKNOWN"

    # LTF bias
    ltf_bias = merged.get("deep_bias", merged.get("structure_bias", pd.Series(["NONE"] * len(merged))))
    merged["ltf_bias"] = ltf_bias

    align = (
        ((merged["htf_bias"] == "BULLISH") & (merged["ltf_bias"] == "BULLISH"))
        | ((merged["htf_bias"] == "BEARISH") & (merged["ltf_bias"] == "BEARISH"))
    )
    conflict = (
        ((merged["htf_bias"] == "BULLISH") & (merged["ltf_bias"] == "BEARISH"))
        | ((merged["htf_bias"] == "BEARISH") & (merged["ltf_bias"] == "BULLISH"))
    )
    merged["mtf_align"] = align.astype(int)
    merged["mtf_conflict"] = conflict.astype(int)
    merged["mtf_status"] = "OK"
    merged["htf_tf"] = htf_label
    return merged
