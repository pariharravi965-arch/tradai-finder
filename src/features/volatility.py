"""Volatility engine: ATR, realized vol, regimes helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def add_volatility_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    period = cfg.get("atr_period", 14)
    look = cfg.get("volatility_lookback", 20)

    out["atr"] = _atr(out, period)
    out["atr_pct"] = out["atr"] / out["close"] * 100
    out["atr_percentile"] = out["atr"].rolling(100, min_periods=20).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
    )

    out["realized_vol"] = out["log_ret_1"].rolling(look).std() * np.sqrt(look)
    out["hl_range_pct"] = (out["high"] - out["low"]) / out["close"] * 100
    out["range_ma"] = out["hl_range_pct"].rolling(look).mean()
    out["range_expansion"] = out["hl_range_pct"] / out["range_ma"].replace(0, np.nan)

    # Vol regime labels (causal percentiles)
    p30 = out["atr_pct"].rolling(100, min_periods=20).quantile(0.30)
    p70 = out["atr_pct"].rolling(100, min_periods=20).quantile(0.70)
    p90 = out["atr_pct"].rolling(100, min_periods=20).quantile(0.90)
    out["vol_regime"] = "NORMAL"
    out.loc[out["atr_pct"] <= p30, "vol_regime"] = "LOW_VOL"
    out.loc[out["atr_pct"] >= p70, "vol_regime"] = "HIGH_VOL"
    out.loc[out["atr_pct"] >= p90, "vol_regime"] = "EXTREME_VOL"
    return out
