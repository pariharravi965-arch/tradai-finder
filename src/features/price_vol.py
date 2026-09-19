"""Price, returns, volume features — all causal."""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_price_volume_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    vol_ma = cfg.get("volume_ma_period", 20)

    out["typical_price"] = (out["high"] + out["low"] + out["close"]) / 3.0
    out["ret_1"] = out["close"].pct_change(1)
    out["ret_3"] = out["close"].pct_change(3)
    out["ret_5"] = out["close"].pct_change(5)
    out["log_ret_1"] = np.log(out["close"] / out["close"].shift(1))

    out["body"] = (out["close"] - out["open"]).abs()
    out["range"] = out["high"] - out["low"]
    out["upper_wick"] = out["high"] - out[["open", "close"]].max(axis=1)
    out["lower_wick"] = out[["open", "close"]].min(axis=1) - out["low"]
    out["body_ratio"] = out["body"] / out["range"].replace(0, np.nan)
    out["close_loc"] = (out["close"] - out["low"]) / out["range"].replace(0, np.nan)

    out["volume_ma"] = out["volume"].rolling(vol_ma, min_periods=1).mean()
    out["rel_volume"] = out["volume"] / out["volume_ma"].replace(0, np.nan)
    out["volume_z"] = (
        (out["volume"] - out["volume"].rolling(vol_ma).mean())
        / out["volume"].rolling(vol_ma).std().replace(0, np.nan)
    )
    if "taker_buy_base" in out.columns:
        out["taker_buy_ratio"] = out["taker_buy_base"] / out["volume"].replace(0, np.nan)
    else:
        out["taker_buy_ratio"] = np.nan

    # Momentum acceleration (causal)
    out["ret_accel"] = out["ret_1"] - out["ret_1"].shift(1)
    return out
