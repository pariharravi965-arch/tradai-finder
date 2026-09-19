"""Volume profile (rolling approximation) + VWAP family — from OHLCV only."""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_vwap_features(df: pd.DataFrame, session_col: str | None = None) -> pd.DataFrame:
    out = df.copy()
    tp = (out["high"] + out["low"] + out["close"]) / 3.0
    vol = out["volume"].replace(0, np.nan)

    # Rolling VWAP (causal, expanding within lookback windows)
    for win, name in [(20, "vwap_20"), (50, "vwap_50"), (100, "vwap_100")]:
        cum_tp_v = (tp * vol).rolling(win, min_periods=1).sum()
        cum_v = vol.rolling(win, min_periods=1).sum()
        out[name] = cum_tp_v / cum_v
        out[f"dist_{name}_pct"] = (out["close"] - out[name]) / out[name] * 100

    # Session VWAP if session labels present
    if session_col and session_col in out.columns:
        # reset at session change
        groups = (out[session_col] != out[session_col].shift(1)).cumsum()
        cum_tp_v = (tp * vol).groupby(groups).cumsum()
        cum_v = vol.groupby(groups).cumsum()
        out["session_vwap"] = cum_tp_v / cum_v
        out["dist_session_vwap_pct"] = (out["close"] - out["session_vwap"]) / out["session_vwap"] * 100
    else:
        out["session_vwap"] = np.nan
        out["dist_session_vwap_pct"] = np.nan

    # VWAP bands (rolling std of close vs vwap)
    if "vwap_50" in out.columns:
        dev = out["close"] - out["vwap_50"]
        sd = dev.rolling(50, min_periods=10).std()
        out["vwap_upper_1"] = out["vwap_50"] + sd
        out["vwap_lower_1"] = out["vwap_50"] - sd
        out["vwap_band_pos"] = dev / sd.replace(0, np.nan)

    return out


def add_volume_profile_approx(df: pd.DataFrame, lookback: int = 48, bins: int = 20) -> pd.DataFrame:
    """Rolling volume profile approximation — POC / VAH / VAL proxies.

    Not a true tick VP; uses OHLC + volume distribution by price bins in window.
    """
    out = df.copy()
    n = len(out)
    poc = np.full(n, np.nan)
    vah = np.full(n, np.nan)
    val = np.full(n, np.nan)
    hvn_flag = np.zeros(n, dtype=int)
    lvn_flag = np.zeros(n, dtype=int)

    highs = out["high"].values
    lows = out["low"].values
    closes = out["close"].values
    vols = out["volume"].values

    for i in range(lookback, n):
        window_h = highs[i - lookback : i]
        window_l = lows[i - lookback : i]
        window_c = closes[i - lookback : i]
        window_v = vols[i - lookback : i]
        lo, hi = np.nanmin(window_l), np.nanmax(window_h)
        if hi <= lo or np.isnan(lo):
            continue
        edges = np.linspace(lo, hi, bins + 1)
        # assign each bar's volume to close-bin
        hist = np.zeros(bins)
        for j in range(lookback):
            idx = np.searchsorted(edges, window_c[j], side="right") - 1
            idx = int(np.clip(idx, 0, bins - 1))
            hist[idx] += window_v[j]
        if hist.sum() <= 0:
            continue
        poc_bin = int(np.argmax(hist))
        poc[i] = (edges[poc_bin] + edges[poc_bin + 1]) / 2
        # value area ~70% volume around POC
        order = np.argsort(hist)[::-1]
        cum = 0.0
        total = hist.sum()
        selected = set()
        for b in order:
            selected.add(int(b))
            cum += hist[b]
            if cum >= 0.7 * total:
                break
        sel = sorted(selected)
        val[i] = edges[sel[0]]
        vah[i] = edges[sel[-1] + 1]
        # HVN/LVN at current close
        cur_bin = int(np.clip(np.searchsorted(edges, closes[i], side="right") - 1, 0, bins - 1))
        med = np.median(hist[hist > 0]) if (hist > 0).any() else 0
        if hist[cur_bin] >= med * 1.5:
            hvn_flag[i] = 1
        if hist[cur_bin] <= med * 0.5 and med > 0:
            lvn_flag[i] = 1

    out["vp_poc"] = poc
    out["vp_vah"] = vah
    out["vp_val"] = val
    out["at_hvn"] = hvn_flag
    out["at_lvn"] = lvn_flag
    out["dist_poc_pct"] = (out["close"] - out["vp_poc"]) / out["vp_poc"] * 100
    return out
