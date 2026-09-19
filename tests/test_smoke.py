"""Smoke tests — no network required for pure functions."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.features.price_vol import add_price_volume_features
from src.features.momentum import add_momentum_features
from src.features.volatility import add_volatility_features
from src.structure.swings import add_structure_features
from src.regime.classifier import add_regime_labels
from src.engines.signals import simple_candidate_signals
from src.validation.metrics import trades_to_metrics
from src.engines.backtest import TradeResult


def _dummy_ohlcv(n: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close + rng.normal(0, 0.2, n)
    vol = rng.uniform(10, 100, n)
    t0 = 1_700_000_000_000
    return pd.DataFrame(
        {
            "open_time": [t0 + i * 3_600_000 for i in range(n)],
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "quote_volume": vol * close,
            "n_trades": rng.integers(10, 500, n),
            "taker_buy_base": vol * 0.5,
            "taker_buy_quote": vol * close * 0.5,
        }
    )


def test_feature_pipeline():
    df = _dummy_ohlcv()
    cfg = {"volume_ma_period": 20, "atr_period": 14, "rsi_period": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "volatility_lookback": 20}
    df = add_price_volume_features(df, cfg)
    df = add_volatility_features(df, cfg)
    df = add_momentum_features(df, cfg)
    df = add_structure_features(df, lookback=3)
    df = add_regime_labels(df, lookback=20)
    df = simple_candidate_signals(df)
    assert "rsi" in df.columns
    assert "atr" in df.columns
    assert "structure_bias" in df.columns
    assert "signal_long" in df.columns
    assert "macd_conflict" in df.columns


def test_metrics_empty():
    m = trades_to_metrics([])
    assert m["n_trades"] == 0


def test_metrics_basic():
    trades = [
        TradeResult("1", "BTCUSDT", "1h", 1, 0, 0, 100, 99, 102, 0, 102, "TP", 2.0, 1.9, 0.02, 3),
        TradeResult("2", "BTCUSDT", "1h", 1, 0, 0, 100, 99, 102, 0, 99, "SL", -1.0, -1.1, -0.01, 2),
    ]
    m = trades_to_metrics(trades)
    assert m["n_trades"] == 2
    assert 0 <= m["win_rate"] <= 1
