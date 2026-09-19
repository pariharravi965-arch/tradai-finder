"""
Knowledge discovery engine — factor testing with sample + significance filters.

Not a strategy optimizer. Generates hypotheses, scores stability, rejects weak claims.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd
from loguru import logger

from ..registry.store import RegistryStore


@dataclass
class FactorTestResult:
    factor: str
    direction: str
    n: int
    hit_rate: float
    mean_forward_ret: float
    effect_size: float
    p_value: float | None
    status: str  # DISCOVERED | REJECTED | UNSTABLE | LOW_SAMPLE
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "direction": self.direction,
            "n": self.n,
            "hit_rate": self.hit_rate,
            "mean_forward_ret": self.mean_forward_ret,
            "effect_size": self.effect_size,
            "p_value": self.p_value,
            "status": self.status,
            "notes": self.notes,
        }


def _forward_return(close: pd.Series, horizon: int = 3) -> pd.Series:
    return close.shift(-horizon) / close - 1.0


def _simple_pvalue(x: np.ndarray) -> float | None:
    """Two-sided t-test vs 0 without scipy if needed."""
    x = x[~np.isnan(x)]
    if len(x) < 10:
        return None
    m = x.mean()
    s = x.std(ddof=1)
    if s == 0:
        return None
    t = m / (s / np.sqrt(len(x)))
    # rough normal approximation
    from math import erfc, sqrt
    p = erfc(abs(t) / sqrt(2))
    return float(min(1.0, max(0.0, p)))


def test_binary_factor(
    df: pd.DataFrame,
    factor_col: str,
    horizon: int = 3,
    min_sample: int = 30,
    close_col: str = "close",
) -> FactorTestResult:
    if factor_col not in df.columns or close_col not in df.columns:
        return FactorTestResult(factor_col, "NA", 0, 0, 0, 0, None, "REJECTED", "missing column")
    fwd = _forward_return(df[close_col], horizon)
    mask = df[factor_col].fillna(0).astype(bool)
    # only rows where we can observe forward return (exclude last horizon bars)
    valid = mask & fwd.notna()
    # CRITICAL: for discovery on 8D only — caller must pass 8D slice already
    sub = fwd[valid]
    n = int(sub.shape[0])
    if n < min_sample:
        return FactorTestResult(factor_col, "LONG_BIAS", n, 0, 0, 0, None, "LOW_SAMPLE", f"n<{min_sample}")
    hit = float((sub > 0).mean())
    mean_r = float(sub.mean())
    # effect size vs overall mean
    overall = float(fwd.dropna().mean()) if fwd.notna().any() else 0.0
    effect = mean_r - overall
    p = _simple_pvalue(sub.values.astype(float))
    status = "DISCOVERED"
    if p is not None and p > 0.1:
        status = "REJECTED"
        notes = "weak p"
    elif abs(effect) < 1e-5:
        status = "REJECTED"
        notes = "negligible effect"
    else:
        notes = "candidate — needs 60D validation"
    return FactorTestResult(factor_col, "CONDITIONAL", n, hit, mean_r, effect, p, status, notes)


def discover_from_dataframe(
    df: pd.DataFrame,
    factor_cols: list[str],
    horizon: int = 3,
    min_sample: int = 30,
    registry: Optional[RegistryStore] = None,
    coin: str = "",
    timeframe: str = "",
    training_period: str = "8d",
) -> list[dict]:
    results = []
    for col in factor_cols:
        if col not in df.columns:
            continue
        # only binary-ish factors
        u = df[col].dropna().unique()
        if len(u) > 10:
            continue
        r = test_binary_factor(df, col, horizon=horizon, min_sample=min_sample)
        d = r.to_dict()
        d["coin"] = coin
        d["timeframe"] = timeframe
        results.append(d)
        if registry and r.status == "DISCOVERED":
            registry.register_hypothesis(
                features=[col],
                conditions={col: 1},
                timeframe=timeframe,
                coin=coin,
                sample_size=r.n,
                direction=r.direction,
                expected_effect=None,
                observed_effect=r.mean_forward_ret,
                confidence=1.0 - (r.p_value or 0.5),
                p_value=r.p_value,
                effect_size=r.effect_size,
                stability=None,
                training_period=training_period,
                validation_period=None,
                status="DISCOVERED",
            )
    return results


def no_trade_factor_scan(df: pd.DataFrame, min_sample: int = 20) -> list[dict]:
    """Find conditions where forward |return| is small or hit rate ~50% with high vol — avoid."""
    candidates = []
    for col in ["mtf_conflict", "chop_flag", "no_trade_candidate", "vol_regime"]:
        if col not in df.columns:
            continue
        if col == "vol_regime":
            mask = df[col].astype(str) == "EXTREME_VOL"
        else:
            mask = df[col].fillna(0).astype(bool)
        if "close" not in df.columns:
            continue
        fwd = _forward_return(df["close"], 3)
        sub = fwd[mask & fwd.notna()]
        if len(sub) < min_sample:
            continue
        candidates.append({
            "condition": col,
            "n": len(sub),
            "mean_abs_ret": float(sub.abs().mean()),
            "hit_rate": float((sub > 0).mean()),
            "suggestion": "NO_TRADE_CANDIDATE" if float(sub.abs().mean()) < float(fwd.dropna().abs().mean()) * 0.7 else "REVIEW",
        })
    return candidates
