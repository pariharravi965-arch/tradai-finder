"""
Portfolio-level research stubs — simultaneous exposure, correlation risk, risk-of-ruin proxy.

Not a live execution portfolio. Research metrics only.
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import pandas as pd


def portfolio_from_trade_pnls(
    trade_pnls: Sequence[float],
    concurrent_groups: Sequence[int] | None = None,
) -> dict[str, Any]:
    """trade_pnls in R-multiples; optional group id for concurrent positions."""
    x = np.asarray(trade_pnls, dtype=float)
    if len(x) == 0:
        return {"n": 0}
    equity = np.cumsum(x)
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    max_dd = float(dd.min()) if len(dd) else 0.0
    # risk of ruin rough: fraction of paths in bootstrap that hit -X
    rng = np.random.default_rng(0)
    ruin = 0
    for _ in range(200):
        path = np.cumsum(rng.choice(x, size=min(100, len(x)), replace=True))
        if path.min() < -10:  # -10R
            ruin += 1
    return {
        "n": len(x),
        "total_r": float(x.sum()),
        "avg_r": float(x.mean()),
        "max_drawdown_r": max_dd,
        "sharpe_proxy": float(x.mean() / x.std()) if x.std() > 0 else 0.0,
        "risk_of_ruin_proxy_pct": ruin / 200 * 100,
        "note": "Research proxy only — not live capital model",
    }


def correlation_exposure(returns_panel: pd.DataFrame, positions: dict[str, float]) -> dict[str, Any]:
    """positions: symbol → signed weight."""
    if returns_panel.empty or not positions:
        return {"status": "NO_DATA"}
    cols = [c for c in positions if c in returns_panel.columns]
    if len(cols) < 2:
        return {"status": "SINGLE_NAME", "n": len(cols)}
    corr = returns_panel[cols].corr()
    # portfolio variance proxy
    w = np.array([positions[c] for c in cols])
    cov = returns_panel[cols].cov().values
    try:
        var = float(w @ cov @ w)
    except Exception:
        var = None
    return {
        "status": "OK",
        "symbols": cols,
        "avg_pairwise_corr": float(corr.values[np.triu_indices_from(corr.values, 1)].mean()) if len(cols) > 1 else 1.0,
        "port_var_proxy": var,
        "btc_concentration": abs(positions.get("BTCUSD", 0)) / (sum(abs(v) for v in positions.values()) or 1),
    }
