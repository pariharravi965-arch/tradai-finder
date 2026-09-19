"""Market structure: swing highs/lows, HH/HL/LH/LL, simple BOS/CHOCH proxies.

All labels are causal (confirmed only after enough bars).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def detect_swings(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    """Mark swing high / swing low with confirmation lag = lookback."""
    out = df.copy()
    high = out["high"].values
    low = out["low"].values
    n = len(out)
    swing_high = np.zeros(n, dtype=int)
    swing_low = np.zeros(n, dtype=int)

    for i in range(lookback, n - lookback):
        # Confirmed at i+lookback, but we place label at confirmation bar for causality
        window_h = high[i - lookback : i + lookback + 1]
        window_l = low[i - lookback : i + lookback + 1]
        if high[i] == np.max(window_h):
            confirm_idx = i + lookback
            if confirm_idx < n:
                swing_high[confirm_idx] = 1
        if low[i] == np.min(window_l):
            confirm_idx = i + lookback
            if confirm_idx < n:
                swing_low[confirm_idx] = 1

    out["swing_high"] = swing_high
    out["swing_low"] = swing_low
    return out


def add_structure_features(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    out = detect_swings(df, lookback=lookback)

    # Last confirmed swing levels (forward-filled from confirmation points)
    out["last_swing_high"] = np.where(out["swing_high"] == 1, out["high"], np.nan)
    out["last_swing_low"] = np.where(out["swing_low"] == 1, out["low"], np.nan)
    out["last_swing_high"] = out["last_swing_high"].ffill()
    out["last_swing_low"] = out["last_swing_low"].ffill()

    # Structure state via successive swings (simplified)
    sh_price = out.loc[out["swing_high"] == 1, "high"]
    sl_price = out.loc[out["swing_low"] == 1, "low"]

    out["structure_bias"] = "UNKNOWN"
    # Rolling comparison of last two swing highs / lows
    sh_vals = out["last_swing_high"]
    sl_vals = out["last_swing_low"]
    prev_sh = sh_vals.shift(lookback * 2)
    prev_sl = sl_vals.shift(lookback * 2)

    bull = (sh_vals > prev_sh) & (sl_vals > prev_sl)
    bear = (sh_vals < prev_sh) & (sl_vals < prev_sl)
    out.loc[bull, "structure_bias"] = "BULLISH"
    out.loc[bear, "structure_bias"] = "BEARISH"
    out.loc[~(bull | bear) & sh_vals.notna(), "structure_bias"] = "RANGE_OR_TRANSITION"

    # Simple BOS proxy: close beyond last opposite swing
    out["bos_bull"] = ((out["close"] > out["last_swing_high"]) & (out["close"].shift(1) <= out["last_swing_high"].shift(1))).astype(int)
    out["bos_bear"] = ((out["close"] < out["last_swing_low"]) & (out["close"].shift(1) >= out["last_swing_low"].shift(1))).astype(int)

    # Distance to structure
    out["dist_to_swing_high_atr"] = (out["last_swing_high"] - out["close"]) / out.get("atr", 1)
    out["dist_to_swing_low_atr"] = (out["close"] - out["last_swing_low"]) / out.get("atr", 1)
    return out
