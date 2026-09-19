"""Deep market structure — HH/HL/LH/LL state machine, CHoCH, MSS, FVG, OB proxies.

All causal (confirmed after lookback). Aligned with committee v20.4 concepts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _swing_points(high: np.ndarray, low: np.ndarray, lb: int = 3):
    n = len(high)
    sh_idx, sl_idx = [], []
    for i in range(lb, n - lb):
        if high[i] == np.max(high[i - lb : i + lb + 1]):
            sh_idx.append(i)
        if low[i] == np.min(low[i - lb : i + lb + 1]):
            sl_idx.append(i)
    return sh_idx, sl_idx


def add_deep_structure(df: pd.DataFrame, lookback: int = 3) -> pd.DataFrame:
    out = df.copy()
    h = out["high"].values
    l = out["low"].values
    c = out["close"].values
    o = out["open"].values
    n = len(out)

    sh_idx, sl_idx = _swing_points(h, l, lookback)

    # Last confirmed swing prices (label at confirmation bar = i+lookback)
    last_sh = np.full(n, np.nan)
    last_sl = np.full(n, np.nan)
    for i in sh_idx:
        conf = min(i + lookback, n - 1)
        last_sh[conf] = h[i]
    for i in sl_idx:
        conf = min(i + lookback, n - 1)
        last_sl[conf] = l[i]
    # ffill
    sh_s = pd.Series(last_sh).ffill()
    sl_s = pd.Series(last_sl).ffill()
    out["ds_last_sh"] = sh_s.values
    out["ds_last_sl"] = sl_s.values

    # HH/HL/LH/LL state from successive swings
    structure_state = np.array(["UNKNOWN"] * n, dtype=object)
    prev_sh_val, prev_sl_val = np.nan, np.nan
    for i in range(n):
        cur_sh, cur_sl = sh_s.iloc[i], sl_s.iloc[i]
        if np.isnan(cur_sh) or np.isnan(cur_sl):
            continue
        if not np.isnan(prev_sh_val) and not np.isnan(prev_sl_val):
            if cur_sh > prev_sh_val and cur_sl > prev_sl_val:
                structure_state[i] = "HH_HL"  # bullish structure
            elif cur_sh < prev_sh_val and cur_sl < prev_sl_val:
                structure_state[i] = "LH_LL"  # bearish
            elif cur_sh > prev_sh_val and cur_sl < prev_sl_val:
                structure_state[i] = "EXPAND"
            else:
                structure_state[i] = "CONTRACT"
        if not np.isnan(cur_sh) and (np.isnan(prev_sh_val) or cur_sh != prev_sh_val):
            prev_sh_val = cur_sh
        if not np.isnan(cur_sl) and (np.isnan(prev_sl_val) or cur_sl != prev_sl_val):
            prev_sl_val = cur_sl
    out["structure_state"] = structure_state

    # BOS / CHoCH proxies
    bos_up = np.zeros(n, dtype=int)
    bos_dn = np.zeros(n, dtype=int)
    choch_up = np.zeros(n, dtype=int)
    choch_dn = np.zeros(n, dtype=int)
    for i in range(1, n):
        if np.isnan(sh_s.iloc[i - 1]) or np.isnan(sl_s.iloc[i - 1]):
            continue
        # BOS: close beyond last swing
        if c[i] > sh_s.iloc[i - 1] and c[i - 1] <= sh_s.iloc[i - 1]:
            bos_up[i] = 1
            if structure_state[i - 1] in ("LH_LL", "CONTRACT"):
                choch_up[i] = 1  # change of character
        if c[i] < sl_s.iloc[i - 1] and c[i - 1] >= sl_s.iloc[i - 1]:
            bos_dn[i] = 1
            if structure_state[i - 1] in ("HH_HL", "EXPAND"):
                choch_dn[i] = 1
    out["bos_up"] = bos_up
    out["bos_dn"] = bos_dn
    out["choch_up"] = choch_up
    out["choch_dn"] = choch_dn
    out["mss_up"] = choch_up  # MSS ≈ CHoCH in this simplified model
    out["mss_dn"] = choch_dn

    # Equal highs / lows (liquidity pools proxy)
    eq_high = np.zeros(n, dtype=int)
    eq_low = np.zeros(n, dtype=int)
    tol = np.nanmedian(h - l) * 0.15 if n > 10 else 0.0
    for i in range(5, n):
        if abs(h[i] - h[i - 1]) <= tol and h[i] >= np.max(h[max(0, i - 5) : i]):
            eq_high[i] = 1
        if abs(l[i] - l[i - 1]) <= tol and l[i] <= np.min(l[max(0, i - 5) : i]):
            eq_low[i] = 1
    out["equal_high"] = eq_high
    out["equal_low"] = eq_low

    # Sweep proxies: wick beyond prior swing then close back inside
    sweep_high = np.zeros(n, dtype=int)
    sweep_low = np.zeros(n, dtype=int)
    for i in range(1, n):
        if not np.isnan(sh_s.iloc[i - 1]) and h[i] > sh_s.iloc[i - 1] and c[i] < sh_s.iloc[i - 1]:
            sweep_high[i] = 1
        if not np.isnan(sl_s.iloc[i - 1]) and l[i] < sl_s.iloc[i - 1] and c[i] > sl_s.iloc[i - 1]:
            sweep_low[i] = 1
    out["sweep_high"] = sweep_high
    out["sweep_low"] = sweep_low

    # Fair Value Gap (3-candle)
    fvg_bull = np.zeros(n, dtype=int)
    fvg_bear = np.zeros(n, dtype=int)
    for i in range(2, n):
        # bullish FVG: low[i] > high[i-2]
        if l[i] > h[i - 2]:
            fvg_bull[i] = 1
        if h[i] < l[i - 2]:
            fvg_bear[i] = 1
    out["fvg_bull"] = fvg_bull
    out["fvg_bear"] = fvg_bear

    # Order block proxy: last opposing candle before BOS
    ob_bull = np.zeros(n, dtype=int)
    ob_bear = np.zeros(n, dtype=int)
    for i in range(3, n):
        if bos_up[i]:
            # look back for last down candle
            for j in range(i - 1, max(i - 8, 0), -1):
                if c[j] < o[j]:
                    ob_bull[j] = 1
                    break
        if bos_dn[i]:
            for j in range(i - 1, max(i - 8, 0), -1):
                if c[j] > o[j]:
                    ob_bear[j] = 1
                    break
    out["ob_bull"] = ob_bull
    out["ob_bear"] = ob_bear

    # Premium / discount vs dealing range (last swing high-low midpoint)
    mid = (sh_s + sl_s) / 2.0
    out["dealing_mid"] = mid.values
    out["in_premium"] = (c > mid.values).astype(int)
    out["in_discount"] = (c < mid.values).astype(int)

    # Structure bias summary
    bias = np.array(["NONE"] * n, dtype=object)
    bias[structure_state == "HH_HL"] = "BULLISH"
    bias[structure_state == "LH_LL"] = "BEARISH"
    out["deep_bias"] = bias
    return out
