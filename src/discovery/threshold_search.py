"""
Threshold discovery for continuous factors (RSI, ATR%, OI change, funding, volume).

Grid search with holdout split inside discovery window to reduce pure in-sample snooping.
Still must pass 60D fixed-threshold validation externally.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from .engine import _forward_return, _simple_pvalue


CONTINUOUS_CANDIDATES = [
    ("rsi", [30, 35, 40, 45, 50, 55, 60, 65, 70], "lt"),
    ("rsi", [30, 35, 40, 45, 50, 55, 60, 65, 70], "gt"),
    ("atr_pct", None, "percentile"),  # special
    ("rel_volume", [0.8, 1.0, 1.2, 1.5, 2.0, 3.0], "gt"),
    ("funding_rate", None, "percentile"),
    ("oi_pct_change", None, "percentile"),
]


def _percentile_thresholds(series: pd.Series, qs=(10, 20, 30, 70, 80, 90)) -> list[float]:
    s = series.dropna()
    if len(s) < 30:
        return []
    return [float(np.nanpercentile(s, q)) for q in qs]


def search_thresholds(
    df: pd.DataFrame,
    horizon: int = 3,
    min_sample: int = 30,
    holdout_frac: float = 0.3,
    close_col: str = "close",
) -> list[dict[str, Any]]:
    """For each continuous factor, try thresholds; score on train, verify direction on holdout."""
    if df is None or df.empty or close_col not in df.columns:
        return []
    n = len(df)
    split = int(n * (1 - holdout_frac))
    if split < 40:
        return []
    train, hold = df.iloc[:split], df.iloc[split:]
    fwd_all = _forward_return(df[close_col], horizon)
    results = []

    specs = [
        ("rsi", "lt", [30, 35, 40, 45, 50]),
        ("rsi", "gt", [50, 55, 60, 65, 70]),
        ("rel_volume", "gt", [1.0, 1.2, 1.5, 2.0, 2.5]),
    ]
    # percentile-based
    for col in ["atr", "funding_rate", "oi_pct_change", "dist_vwap_50_pct"]:
        if col not in df.columns:
            continue
        ths = _percentile_thresholds(df[col])
        for th in ths:
            specs.append((col, "gt", [th]))
            specs.append((col, "lt", [th]))

    seen = set()
    for col, op, thresholds in specs:
        if col not in df.columns:
            continue
        for th in thresholds:
            key = (col, op, round(float(th), 6))
            if key in seen:
                continue
            seen.add(key)
            if op == "lt":
                mask = df[col] < th
            else:
                mask = df[col] > th
            train_m = mask.iloc[:split] & fwd_all.iloc[:split].notna()
            hold_m = mask.iloc[split:] & fwd_all.iloc[split:].notna()
            n_tr, n_ho = int(train_m.sum()), int(hold_m.sum())
            if n_tr < min_sample:
                continue
            tr = fwd_all.iloc[:split][train_m]
            mean_tr = float(tr.mean())
            p_tr = _simple_pvalue(tr.values.astype(float))
            mean_ho = float(fwd_all.iloc[split:][hold_m].mean()) if n_ho >= 10 else None
            stable = mean_ho is not None and (np.sign(mean_tr) == np.sign(mean_ho) or abs(mean_ho) < 1e-6)
            results.append({
                "factor": col,
                "op": op,
                "threshold": float(th),
                "n_train": n_tr,
                "n_holdout": n_ho,
                "mean_train": mean_tr,
                "mean_holdout": mean_ho,
                "p_train": p_tr,
                "holdout_sign_stable": stable,
                "status": "CANDIDATE" if stable and mean_tr is not None else "UNSTABLE_IN_DISCOVERY",
            })
    results.sort(key=lambda r: abs(r["mean_train"]) * np.sqrt(r["n_train"]), reverse=True)
    return results[:50]
