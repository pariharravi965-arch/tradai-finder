"""Performance metrics for research candidates."""
from __future__ import annotations

from typing import Sequence

import numpy as np

from ..engines.backtest import TradeResult


def trades_to_metrics(trades: Sequence[TradeResult]) -> dict:
    if not trades:
        return {
            "n_trades": 0,
            "win_rate": float("nan"),
            "avg_r": float("nan"),
            "expectancy": float("nan"),
            "profit_factor": float("nan"),
            "max_dd_r": float("nan"),
            "avg_holding": float("nan"),
            "net_avg_r": float("nan"),
            "tp_rate": float("nan"),
            "sl_rate": float("nan"),
        }
    rs = np.array([t.net_r for t in trades], dtype=float)
    wins = rs > 0
    losses = rs <= 0
    gross_win = rs[wins].sum() if wins.any() else 0.0
    gross_loss = abs(rs[losses].sum()) if losses.any() else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else float("nan")

    equity = np.cumsum(rs)
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    max_dd = float(dd.min()) if len(dd) else float("nan")

    return {
        "n_trades": len(trades),
        "win_rate": float(wins.mean()) if len(rs) else float("nan"),
        "avg_r": float(np.mean([t.gross_r for t in trades])),
        "net_avg_r": float(rs.mean()),
        "expectancy": float(rs.mean()),
        "profit_factor": float(pf) if np.isfinite(pf) else pf,
        "max_dd_r": max_dd,
        "avg_holding": float(np.mean([t.holding_bars for t in trades])),
        "tp_rate": float(np.mean([t.exit_reason == "TP" for t in trades])),
        "sl_rate": float(np.mean([t.exit_reason == "SL" for t in trades])),
    }


def _degradation(a: dict, b: dict, key: str = "expectancy") -> float:
    if a.get("n_trades", 0) == 0 or b.get("n_trades", 0) == 0:
        return float("nan")
    va, vb = a.get(key, float("nan")), b.get(key, float("nan"))
    if va == 0 or (isinstance(va, float) and np.isnan(va)):
        return float("nan")
    return float((va - vb) / abs(va) * 100)


def _classify(m8: dict, m60: dict, m1y: dict) -> str:
    if m60.get("n_trades", 0) < 10:
        return "LOW_SAMPLE"
    e60 = m60.get("expectancy", float("nan"))
    e1y = m1y.get("expectancy", float("nan"))
    if isinstance(e60, float) and np.isnan(e60):
        return "FAILED"
    if e60 <= 0 and (isinstance(e1y, float) and (np.isnan(e1y) or e1y <= 0)):
        return "FAILED"
    deg = _degradation(m8, m60)
    if not (isinstance(deg, float) and np.isnan(deg)) and deg > 80:
        return "OVERFIT_SUSPECTED"
    if e1y > 0 and e60 > 0:
        return "ROBUST"
    if e60 > 0:
        return "PARTIALLY_ROBUST"
    return "WEAK"


def compare_windows(m8: dict, m60: dict, m1y: dict) -> dict:
    return {
        "8d": m8,
        "60d": m60,
        "1y": m1y,
        "degradation_8_to_60_pct": _degradation(m8, m60),
        "degradation_60_to_1y_pct": _degradation(m60, m1y),
        "classification": _classify(m8, m60, m1y),
    }
