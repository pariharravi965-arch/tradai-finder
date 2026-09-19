"""
Automatic multi-factor interaction discovery (2–4 factors).

Controls combinatorial explosion via:
  - binary/categorical factor whitelist
  - min sample per combination
  - max pairs/triples scanned
  - effect-size + p-value prefilter before FDR
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd
from loguru import logger

from .engine import _forward_return, _simple_pvalue


DEFAULT_FACTOR_POOL = [
    "bos_up", "bos_dn", "choch_up", "choch_dn",
    "sweep_low", "sweep_high", "fvg_bull", "fvg_bear",
    "equal_high", "equal_low", "mtf_align", "mtf_conflict",
    "sig_h1_struct_bull", "sig_h3_sweep_reversal", "sig_h5_mtf_align",
    "sig_h6_deriv_long_build", "no_trade_candidate",
    "funding_extreme_pos", "funding_extreme_neg", "oi_shock",
    "btc_shock", "chop_flag",
]


@dataclass
class InteractionHit:
    factors: tuple[str, ...]
    n: int
    mean_fwd: float
    hit_rate: float
    effect_vs_base: float
    p_value: float | None
    raw_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "factors": list(self.factors),
            "n": self.n,
            "mean_fwd": self.mean_fwd,
            "hit_rate": self.hit_rate,
            "effect_vs_base": self.effect_vs_base,
            "p_value": self.p_value,
            "raw_score": self.raw_score,
        }


def _binary_mask(df: pd.DataFrame, col: str) -> pd.Series:
    s = df[col]
    if s.dtype == bool:
        return s.fillna(False)
    if s.dtype == object:
        return s.fillna("").astype(str).isin(["1", "True", "true", "BULLISH", "LONG_BUILD"])
    return s.fillna(0).astype(float) != 0


def scan_interactions(
    df: pd.DataFrame,
    factor_pool: list[str] | None = None,
    max_order: int = 3,
    horizon: int = 3,
    min_sample: int = 25,
    max_combos: int = 2000,
    close_col: str = "close",
) -> list[dict]:
    """Enumerate AND-combinations of binary factors; rank by effect × √n."""
    if df is None or df.empty or close_col not in df.columns:
        return []
    pool = [f for f in (factor_pool or DEFAULT_FACTOR_POOL) if f in df.columns]
    if len(pool) < 2:
        logger.warning("Interaction scan: factor pool too small")
        return []

    fwd = _forward_return(df[close_col], horizon)
    base_mean = float(fwd.dropna().mean()) if fwd.notna().any() else 0.0
    masks = {f: _binary_mask(df, f) for f in pool}

    hits: list[InteractionHit] = []
    counted = 0
    for order in range(2, max_order + 1):
        for combo in itertools.combinations(pool, order):
            counted += 1
            if counted > max_combos:
                logger.info(f"Interaction scan capped at {max_combos} combos")
                break
            m = masks[combo[0]]
            for f in combo[1:]:
                m = m & masks[f]
            valid = m & fwd.notna()
            n = int(valid.sum())
            if n < min_sample:
                continue
            sub = fwd[valid]
            mean_r = float(sub.mean())
            hit = float((sub > 0).mean())
            effect = mean_r - base_mean
            p = _simple_pvalue(sub.values.astype(float))
            if abs(effect) < 1e-6:
                continue
            score = abs(effect) * np.sqrt(n)
            hits.append(InteractionHit(combo, n, mean_r, hit, effect, p, float(score)))
        if counted > max_combos:
            break

    hits.sort(key=lambda h: h.raw_score, reverse=True)
    return [h.to_dict() for h in hits[:100]]
