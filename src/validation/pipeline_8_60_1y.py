"""
Leakage-proof validation: 8D discovery → 60D unseen → 1Y OOS.

CRITICAL RULE:
  - Discovery metrics computed ONLY on 8D window.
  - 60D and 1Y must NOT influence feature selection or thresholds from 8D.
  - Same fixed hypothesis from 8D is evaluated on later windows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

import pandas as pd
from loguru import logger


@dataclass
class SplitWindows:
    discovery: pd.DataFrame   # 8D
    validation: pd.DataFrame  # 60D
    robustness: pd.DataFrame  # 1Y (or max available)


def split_by_time(
    df: pd.DataFrame,
    discovery_days: int = 8,
    validation_days: int = 60,
    robustness_days: int = 365,
    time_col: str = "open_time",
) -> SplitWindows:
    """Last robustness_days end at max time; validation before that; discovery before that."""
    if df is None or df.empty:
        return SplitWindows(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    d = df.sort_values(time_col).reset_index(drop=True)
    t_max = int(d[time_col].iloc[-1])
    ms_day = 86_400_000
    t_rob_start = t_max - robustness_days * ms_day
    t_val_start = t_rob_start - validation_days * ms_day
    t_disc_start = t_val_start - discovery_days * ms_day

    disc = d[(d[time_col] >= t_disc_start) & (d[time_col] < t_val_start)]
    val = d[(d[time_col] >= t_val_start) & (d[time_col] < t_rob_start)]
    rob = d[d[time_col] >= t_rob_start]
    # If not enough history, shrink gracefully and FLAG
    if len(disc) < 10:
        logger.warning("Discovery window thin — insufficient history")
    return SplitWindows(disc, val, rob)


def evaluate_fixed_hypothesis(
    windows: SplitWindows,
    signal_col: str,
    metric_fn: Callable[[pd.DataFrame], dict],
) -> dict[str, Any]:
    """Apply same signal definition to each window; never re-optimize on val/rob."""
    out: dict[str, Any] = {"signal_col": signal_col, "leakage_guard": "fixed_hypothesis_from_discovery_only"}
    for name, part in [("discovery_8d", windows.discovery), ("validation_60d", windows.validation), ("robustness_1y", windows.robustness)]:
        if part is None or part.empty or signal_col not in part.columns:
            out[name] = {"status": "NO_DATA", "n": 0}
            continue
        m = metric_fn(part)
        m["n_bars"] = len(part)
        m["n_signals"] = int(part[signal_col].fillna(0).astype(bool).sum()) if signal_col in part.columns else 0
        out[name] = m
    # Classification
    d_exp = out.get("discovery_8d", {}).get("expectancy")
    v_exp = out.get("validation_60d", {}).get("expectancy")
    r_exp = out.get("robustness_1y", {}).get("expectancy")
    classification = "INSUFFICIENT_DATA"
    if d_exp is not None and v_exp is not None:
        if d_exp > 0 and v_exp > 0 and (r_exp is None or r_exp > 0):
            classification = "ROBUST_CANDIDATE"
        elif d_exp > 0 and (v_exp is None or v_exp <= 0):
            classification = "REJECTED_OOS"
        elif d_exp > 0 and v_exp > 0 and r_exp is not None and r_exp <= 0:
            classification = "UNSTABLE_LONG_TERM"
        else:
            classification = "REJECTED"
    out["classification"] = classification
    return out
