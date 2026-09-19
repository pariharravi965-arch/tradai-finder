"""
Multiple-testing control: Benjamini–Hochberg FDR, permutation null, simple PBO proxy.

Never treat raw significant count as truth.

SAFEGUARD: `fdr_pass=True` / not being marked `REJECTED_FDR` is NOT proof that a
hypothesis is real. It only means the result survived a multiple-testing
correction on the discovery sample. Mandatory regardless of FDR outcome:
60D OOS and 1Y OOS (real, held-out, out-of-sample evidence) before a
hypothesis may be treated as validated. FDR-pass + OOS-fail is still a
rejection (REJECTED_OOS) — FDR passing never substitutes for out-of-sample
testing.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd


def benjamini_hochberg(p_values: list[float | None], alpha: float = 0.1) -> list[bool]:
    """Return reject flags for BH procedure. None p → never reject."""
    n = len(p_values)
    if n == 0:
        return []
    indexed = [(i, p if p is not None else 1.0) for i, p in enumerate(p_values)]
    indexed.sort(key=lambda x: x[1])
    reject = [False] * n
    max_i = -1
    for rank, (i, p) in enumerate(indexed, start=1):
        if p <= alpha * rank / n:
            max_i = rank
    for rank, (i, p) in enumerate(indexed, start=1):
        if rank <= max_i:
            reject[i] = True
    return reject


def apply_fdr_to_results(results: list[dict], p_key: str = "p_value", alpha: float = 0.1) -> list[dict]:
    ps = [r.get(p_key) for r in results]
    flags = benjamini_hochberg(ps, alpha=alpha)
    out = []
    for r, ok in zip(results, flags):
        rr = dict(r)
        rr["fdr_pass"] = bool(ok)
        if not ok and rr.get("status") == "DISCOVERED":
            rr["status"] = "REJECTED_FDR"
        out.append(rr)
    return out


def permutation_null_mean(
    returns: np.ndarray,
    mask: np.ndarray,
    n_perm: int = 200,
    seed: int = 42,
) -> dict[str, Any]:
    """Compare observed mean under mask vs means under random masks of same size."""
    returns = np.asarray(returns, dtype=float)
    mask = np.asarray(mask, dtype=bool)
    valid = ~np.isnan(returns)
    r = returns[valid]
    m = mask[valid]
    n_sel = int(m.sum())
    if n_sel < 5 or len(r) < 20:
        return {"status": "LOW_SAMPLE", "p_perm": None}
    obs = float(r[m].mean())
    rng = np.random.default_rng(seed)
    null = []
    for _ in range(n_perm):
        idx = rng.choice(len(r), size=n_sel, replace=False)
        null.append(float(r[idx].mean()))
    null = np.array(null)
    p_perm = float(np.mean(np.abs(null) >= abs(obs)))
    return {
        "status": "OK",
        "obs_mean": obs,
        "null_mean": float(null.mean()),
        "null_std": float(null.std()),
        "p_perm": p_perm,
        "n_perm": n_perm,
    }


def bootstrap_ci(x: np.ndarray, n_boot: int = 500, alpha: float = 0.05, seed: int = 0) -> dict[str, float | None]:
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 10:
        return {"mean": None, "ci_low": None, "ci_high": None, "n": len(x)}
    rng = np.random.default_rng(seed)
    boots = [float(rng.choice(x, size=len(x), replace=True).mean()) for _ in range(n_boot)]
    lo = float(np.percentile(boots, 100 * alpha / 2))
    hi = float(np.percentile(boots, 100 * (1 - alpha / 2)))
    return {"mean": float(x.mean()), "ci_low": lo, "ci_high": hi, "n": len(x)}


def deflated_sharpe_proxy(sharpe: float, n_trials: int, n_obs: int) -> float:
    """Very rough Deflated Sharpe-style penalty for multiple trials (Bailey & López de Prado inspired)."""
    if n_obs < 10 or n_trials < 1:
        return sharpe
    # penalize by log(trials)
    penalty = 0.15 * np.log1p(n_trials) / np.sqrt(max(n_obs, 1))
    return float(sharpe - penalty)
