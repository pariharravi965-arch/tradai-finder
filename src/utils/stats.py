"""Statistical helpers — bootstrap, effect size, degradation."""
from __future__ import annotations

import numpy as np
from typing import Sequence


def bootstrap_ci(
    values: Sequence[float],
    n_boot: int = 2000,
    alpha: float = 0.05,
    statistic: str = "mean",
    seed: int = 42,
) -> tuple[float, float, float]:
    """Return (point_estimate, lower, upper) via percentile bootstrap."""
    arr = np.asarray(values, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) == 0:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    if statistic == "mean":
        point = float(np.mean(arr))
        boots = [float(np.mean(rng.choice(arr, size=len(arr), replace=True))) for _ in range(n_boot)]
    elif statistic == "median":
        point = float(np.median(arr))
        boots = [float(np.median(rng.choice(arr, size=len(arr), replace=True))) for _ in range(n_boot)]
    else:
        raise ValueError(f"Unknown statistic: {statistic}")
    lo = float(np.percentile(boots, 100 * alpha / 2))
    hi = float(np.percentile(boots, 100 * (1 - alpha / 2)))
    return point, lo, hi


def effect_size(a: Sequence[float], b: Sequence[float]) -> float:
    """Cohen's d (simple)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    a = a[~np.isnan(a)]
    b = b[~np.isnan(b)]
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    pooled = np.sqrt((np.var(a, ddof=1) + np.var(b, ddof=1)) / 2)
    if pooled == 0:
        return 0.0
    return float((np.mean(a) - np.mean(b)) / pooled)


def degradation(in_sample: float, out_sample: float) -> float:
    """Relative degradation % (positive = worse on OOS)."""
    if in_sample is None or np.isnan(in_sample) or in_sample == 0:
        return float("nan")
    return float((in_sample - out_sample) / abs(in_sample) * 100.0)
