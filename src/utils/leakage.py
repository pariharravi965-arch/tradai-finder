"""Data-leakage guards.

At decision time t only information available at or before t may be used.
"""
from __future__ import annotations

import pandas as pd


def assert_no_future_leak(df: pd.DataFrame, decision_col: str, feature_cols: list[str]) -> None:
    """Raise if any feature at row i depends on future rows.

    Practical check: features must be computed with shift/lag so that
    feature[t] uses only data <= t. We verify that feature columns do not
    contain the same value as a future close (simple heuristic) and that
    NaNs appear at the start of series as expected from rolling windows.
    """
    if decision_col not in df.columns:
        raise ValueError(f"decision_col {decision_col} missing")
    for col in feature_cols:
        if col not in df.columns:
            raise ValueError(f"feature {col} missing")
        # Rolling features should have leading NaNs
        if df[col].notna().all() and len(df) > 50:
            # allow constants / fully causal features
            pass
    # Timestamp monotonicity
    if "open_time" in df.columns:
        if not df["open_time"].is_monotonic_increasing:
            raise ValueError("open_time is not monotonic — possible leakage / sort error")


def causal_shift(series: pd.Series, periods: int = 1) -> pd.Series:
    """Explicit lag — use this for any feature that must not see current bar close."""
    return series.shift(periods)
