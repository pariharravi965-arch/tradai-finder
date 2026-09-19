"""Automatic hypothesis / candidate signal generation from feature states.

Still research candidates — not proof of edge.
"""
from __future__ import annotations

import pandas as pd


def generate_hypotheses(df: pd.DataFrame) -> pd.DataFrame:
    """Expand signal set beyond simple_candidate_signals."""
    out = df.copy()
    out["sig_h1_struct_bull"] = 0
    out["sig_h2_bos_retest"] = 0
    out["sig_h3_sweep_reversal"] = 0
    out["sig_h4_fvg_cont"] = 0
    out["sig_h5_mtf_align"] = 0
    out["sig_h6_deriv_long_build"] = 0
    out["sig_short_h1"] = 0
    out["sig_short_h3"] = 0
    out["hyp_reason"] = ""

    # H1: deep structure bull + not extended + volume
    if "deep_bias" in out.columns:
        m = (
            (out["deep_bias"] == "BULLISH")
            & (out.get("chop_flag", 0) == 0)
            & (out.get("rel_volume", 1) > 0.9)
            & (out.get("rsi", 50) < 70)
        )
        out.loc[m, "sig_h1_struct_bull"] = 1
        out.loc[m, "hyp_reason"] = "H1_STRUCT_BULL"

    # H2: BOS up then pullback (prior bar BOS, current not new high chase)
    if "bos_up" in out.columns:
        m = (out["bos_up"].shift(1) == 1) & (out["close"] < out["close"].shift(1)) & (out.get("deep_bias", "") == "BULLISH")
        out.loc[m.fillna(False), "sig_h2_bos_retest"] = 1
        out.loc[m.fillna(False), "hyp_reason"] = out.loc[m.fillna(False), "hyp_reason"].replace("", "H2_BOS_RETEST")

    # H3: sweep low → reclaim (bullish reversal)
    if "sweep_low" in out.columns:
        m = (out["sweep_low"] == 1) & (out["close"] > out["open"])
        out.loc[m, "sig_h3_sweep_reversal"] = 1
        out.loc[m, "hyp_reason"] = "H3_SWEEP_REV"
        m2 = (out.get("sweep_high", 0) == 1) & (out["close"] < out["open"])
        out.loc[m2, "sig_short_h3"] = 1

    # H4: bullish FVG + structure
    if "fvg_bull" in out.columns:
        m = (out["fvg_bull"] == 1) & (out.get("deep_bias", "") == "BULLISH")
        out.loc[m, "sig_h4_fvg_cont"] = 1

    # H5: MTF align
    if "mtf_align" in out.columns:
        m = (out["mtf_align"] == 1) & (out.get("ltf_bias", "") == "BULLISH")
        out.loc[m, "sig_h5_mtf_align"] = 1
        m2 = (out["mtf_align"] == 1) & (out.get("ltf_bias", "") == "BEARISH")
        out.loc[m2, "sig_short_h1"] = 1

    # H6: derivatives long build
    if "deriv_regime" in out.columns:
        m = out["deriv_regime"] == "LONG_BUILD"
        out.loc[m, "sig_h6_deriv_long_build"] = 1

    # Aggregate long/short for backtest compatibility
    long_cols = [c for c in out.columns if c.startswith("sig_h") and "short" not in c]
    short_cols = [c for c in out.columns if c.startswith("sig_short")]
    out["signal_long"] = (out[long_cols].sum(axis=1) > 0).astype(int) if long_cols else 0
    out["signal_short"] = (out[short_cols].sum(axis=1) > 0).astype(int) if short_cols else out.get("signal_short", 0)

    # No-trade discovery candidates
    out["no_trade_candidate"] = 0
    if "mtf_conflict" in out.columns:
        out.loc[out["mtf_conflict"] == 1, "no_trade_candidate"] = 1
    if "chop_flag" in out.columns:
        out.loc[out["chop_flag"] == 1, "no_trade_candidate"] = 1
    if "vol_regime" in out.columns:
        out.loc[out["vol_regime"] == "EXTREME_VOL", "no_trade_candidate"] = 1
    if "funding_extreme_pos" in out.columns and "deep_bias" in out.columns:
        # long into extreme positive funding → often crowded
        out.loc[(out["funding_extreme_pos"] == 1) & (out["deep_bias"] == "BULLISH"), "no_trade_candidate"] = 1

    return out
