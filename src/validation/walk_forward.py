"""Simple walk-forward scaffolding.

Optimization only on train; test is fully unseen.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

from ..engines.backtest import run_vector_backtest, TradeResult
from .metrics import trades_to_metrics


def simple_walk_forward(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    train_bars: int,
    test_bars: int,
    signal_fn: Callable,
    backtest_kwargs: dict | None = None,
) -> list[dict]:
    """Rolling walk-forward. Returns list of fold results."""
    backtest_kwargs = backtest_kwargs or {}
    results = []
    n = len(df)
    start = 0
    fold = 0
    while start + train_bars + test_bars <= n:
        train = df.iloc[start : start + train_bars].copy()
        test = df.iloc[start + train_bars : start + train_bars + test_bars].copy()
        # signals computed separately (in full pipeline features already present)
        train = signal_fn(train)
        test = signal_fn(test)
        train_trades = run_vector_backtest(train, symbol, timeframe, **backtest_kwargs)
        test_trades = run_vector_backtest(test, symbol, timeframe, **backtest_kwargs)
        results.append(
            {
                "fold": fold,
                "train_metrics": trades_to_metrics(train_trades),
                "test_metrics": trades_to_metrics(test_trades),
                "train_start": int(train["open_time"].iloc[0]),
                "test_start": int(test["open_time"].iloc[0]),
            }
        )
        start += test_bars
        fold += 1
    return results
