"""Momentum features: RSI, MACD, ROC — lagged where needed for causality."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def add_momentum_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    rsi_p = cfg.get("rsi_period", 14)
    fast = cfg.get("macd_fast", 12)
    slow = cfg.get("macd_slow", 26)
    sig = cfg.get("macd_signal", 9)

    out["rsi"] = _rsi(out["close"], rsi_p)
    out["rsi_slope"] = out["rsi"] - out["rsi"].shift(3)

    ema_fast = _ema(out["close"], fast)
    ema_slow = _ema(out["close"], slow)
    out["macd"] = ema_fast - ema_slow
    out["macd_signal"] = _ema(out["macd"], sig)
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    out["macd_hist_slope"] = out["macd_hist"] - out["macd_hist"].shift(1)

    # MACD_CONFLICT research flag: price direction vs MACD hist direction
    price_up = out["close"] > out["close"].shift(1)
    macd_up = out["macd_hist"] > out["macd_hist"].shift(1)
    out["macd_conflict"] = (price_up != macd_up).astype(int)

    out["roc_5"] = out["close"].pct_change(5) * 100
    out["roc_10"] = out["close"].pct_change(10) * 100

    # Stochastic
    low_n = out["low"].rolling(14).min()
    high_n = out["high"].rolling(14).max()
    out["stoch_k"] = 100 * (out["close"] - low_n) / (high_n - low_n).replace(0, np.nan)
    out["stoch_d"] = out["stoch_k"].rolling(3).mean()
    return out
