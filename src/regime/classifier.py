"""Market regime classification — multiple independent methods, causal."""
from __future__ import annotations

import numpy as np
import pandas as pd


def classify_trend(df: pd.DataFrame, lookback: int = 50) -> pd.Series:
    """Return trend label series using slope + structure bias."""
    close = df["close"]
    ma = close.rolling(lookback, min_periods=max(10, lookback // 2)).mean()
    slope = (ma - ma.shift(lookback // 2)) / ma.shift(lookback // 2).replace(0, np.nan)
    atr_pct = df.get("atr_pct", pd.Series(1.0, index=df.index))

    labels = pd.Series("SIDEWAYS", index=df.index, dtype=object)
    strong = atr_pct > atr_pct.rolling(100, min_periods=20).quantile(0.5)

    labels.loc[(slope > 0.02) & strong] = "STRONG_BULL"
    labels.loc[(slope > 0.005) & ~strong] = "WEAK_BULL"
    labels.loc[(slope < -0.02) & strong] = "STRONG_BEAR"
    labels.loc[(slope < -0.005) & ~strong] = "WEAK_BEAR"
    labels.loc[slope.abs() <= 0.005] = "SIDEWAYS"
    return labels


def add_regime_labels(df: pd.DataFrame, lookback: int = 50) -> pd.DataFrame:
    out = df.copy()
    out["trend_regime"] = classify_trend(out, lookback=lookback)

    # Chop proxy: high noise, low directional efficiency
    ret = out["close"].pct_change()
    efficiency = ret.rolling(lookback).sum().abs() / (ret.abs().rolling(lookback).sum().replace(0, np.nan))
    out["efficiency_ratio"] = efficiency
    out["chop_flag"] = (efficiency < 0.3).astype(int)

    # Combined regime
    out["market_regime"] = out["trend_regime"]
    out.loc[out["chop_flag"] == 1, "market_regime"] = "CHOP"
    if "vol_regime" in out.columns:
        out["regime_combo"] = out["market_regime"].astype(str) + "_" + out["vol_regime"].astype(str)
    else:
        out["regime_combo"] = out["market_regime"]
    return out
