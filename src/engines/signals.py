"""Candidate signal generation from validated features.

These are RESEARCH CANDIDATES — not guaranteed strategies.
All signals are causal (use only info available at bar open/close rules defined).
"""
from __future__ import annotations

import pandas as pd


def simple_candidate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Generate a few hypothesis signals for discovery/testing.

    Entry is marked on bar t; execution assumed next bar open (no lookahead).
    """
    out = df.copy()
    out["signal_long"] = 0
    out["signal_short"] = 0
    out["signal_reason"] = ""

    # Hypothesis 1: Structure bullish + RSI not overbought + rel volume > 1
    if all(c in out.columns for c in ["structure_bias", "rsi", "rel_volume"]):
        long_mask = (
            (out["structure_bias"] == "BULLISH")
            & (out["rsi"] < 65)
            & (out["rsi"] > 40)
            & (out["rel_volume"] > 1.0)
            & (out.get("chop_flag", 0) == 0)
        )
        out.loc[long_mask, "signal_long"] = 1
        out.loc[long_mask, "signal_reason"] = "STRUCT_BULL_RSI_VOL"

    # Hypothesis 2: BOS bull + not extreme vol
    if "bos_bull" in out.columns:
        m = (out["bos_bull"] == 1) & (out.get("vol_regime", "") != "EXTREME_VOL")
        out.loc[m, "signal_long"] = 1
        out.loc[m, "signal_reason"] = out.loc[m, "signal_reason"].replace("", "BOS_BULL")

    # Hypothesis 3: Structure bearish + RSI not oversold
    if all(c in out.columns for c in ["structure_bias", "rsi"]):
        short_mask = (
            (out["structure_bias"] == "BEARISH")
            & (out["rsi"] > 35)
            & (out["rsi"] < 60)
            & (out.get("chop_flag", 0) == 0)
        )
        out.loc[short_mask, "signal_short"] = 1
        out.loc[short_mask, "signal_reason"] = "STRUCT_BEAR_RSI"

    # Hypothesis 4: MACD conflict — for research (often negative edge)
    if "macd_conflict" in out.columns:
        out["macd_conflict_signal"] = out["macd_conflict"]

    # No-trade flag research
    out["no_trade_candidate"] = 0
    if "chop_flag" in out.columns:
        out.loc[out["chop_flag"] == 1, "no_trade_candidate"] = 1
    if "vol_regime" in out.columns:
        out.loc[out["vol_regime"] == "EXTREME_VOL", "no_trade_candidate"] = 1

    return out
