"""Derivatives intelligence from Delta FUNDING: / OI: series + price.

Classifies 4 OI-price regimes, funding extremes, 3-way states.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def enrich_derivatives(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    has_oi = "oi" in out.columns and out["oi"].notna().any()
    has_fund = "funding_rate" in out.columns and out["funding_rate"].notna().any()

    # OI-price regimes (already partially in merge_oi_features)
    if has_oi:
        oi_chg = out["oi"].pct_change()
        px_chg = out["close"].pct_change()
        out["oi_pct_change"] = oi_chg
        out["oi_z"] = (out["oi"] - out["oi"].rolling(48, min_periods=10).mean()) / out["oi"].rolling(48, min_periods=10).std().replace(0, np.nan)
        out["oi_shock"] = (out["oi_z"].abs() > 2).astype(int)
        # 4-way
        out["deriv_regime"] = "UNKNOWN"
        out.loc[(px_chg > 0) & (oi_chg > 0), "deriv_regime"] = "LONG_BUILD"
        out.loc[(px_chg > 0) & (oi_chg < 0), "deriv_regime"] = "SHORT_COVER"
        out.loc[(px_chg < 0) & (oi_chg > 0), "deriv_regime"] = "SHORT_BUILD"
        out.loc[(px_chg < 0) & (oi_chg < 0), "deriv_regime"] = "LONG_UNWIND"
        # OI divergence: price new high, OI not
        roll_px = out["close"].rolling(20).max()
        roll_oi = out["oi"].rolling(20).max()
        out["oi_bear_div"] = ((out["close"] >= roll_px * 0.998) & (out["oi"] < roll_oi * 0.98)).astype(int)
        out["oi_bull_div"] = ((out["close"] <= out["close"].rolling(20).min() * 1.002) & (out["oi"] < roll_oi * 0.98)).astype(int)
    else:
        out["deriv_regime"] = "DATA_MISSING"
        out["oi_shock"] = 0
        out["oi_bear_div"] = 0
        out["oi_bull_div"] = 0

    if has_fund:
        fr = out["funding_rate"]
        out["funding_z"] = (fr - fr.rolling(48, min_periods=5).mean()) / fr.rolling(48, min_periods=5).std().replace(0, np.nan)
        out["funding_extreme_pos"] = (out["funding_z"] > 2).astype(int)
        out["funding_extreme_neg"] = (out["funding_z"] < -2).astype(int)
        out["funding_accel"] = fr.diff()
        # 3-way: funding + price + oi
        if has_oi:
            out["f_oi_px_state"] = (
                out["deriv_regime"].astype(str)
                + "|"
                + np.where(out["funding_extreme_pos"] == 1, "F+", np.where(out["funding_extreme_neg"] == 1, "F-", "F0"))
            )
        else:
            out["f_oi_px_state"] = "FUND_ONLY"
    else:
        out["funding_extreme_pos"] = 0
        out["funding_extreme_neg"] = 0
        out["f_oi_px_state"] = "DATA_MISSING"

    # Basis — Delta public may not give spot; mark as missing unless column exists
    if "basis" not in out.columns:
        out["basis"] = np.nan
        out["basis_status"] = "DATA_MISSING"
    return out
