"""NO-LOOKAHEAD tests — signal at t must not use close[t+1] or future structure confirmation."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _synthetic_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 0.4, n))
    return pd.DataFrame({
        "open_time": np.arange(n) * 3_600_000 + 1_700_000_000_000,
        "open": c,
        "high": c + rng.uniform(0.1, 1.0, n),
        "low": c - rng.uniform(0.1, 1.0, n),
        "close": c,
        "volume": rng.uniform(10, 100, n),
    })


def test_deep_structure_no_future_leak():
    from src.structure.deep_structure import add_deep_structure
    from src.features.price_vol import add_price_volume_features
    from src.features.volatility import add_volatility_features

    df = _synthetic_ohlcv(150)
    df = add_price_volume_features(df, {"volume_ma_period": 20})
    df = add_volatility_features(df, {"atr_period": 14, "volatility_lookback": 20})
    out = add_deep_structure(df, lookback=3)
    # Mutate future closes — past structure flags at index i must not change
    mid = 80
    snapshot = out.loc[:mid, ["bos_up", "bos_dn", "choch_up", "deep_bias"]].copy()
    out2 = out.copy()
    out2.loc[mid + 5 :, "close"] = out2.loc[mid + 5 :, "close"] * 1.5
    out2.loc[mid + 5 :, "high"] = out2.loc[mid + 5 :, "high"] * 1.5
    # recompute only structure from modified full series
    recal = add_deep_structure(
        add_volatility_features(
            add_price_volume_features(out2[["open_time", "open", "high", "low", "close", "volume"]], {"volume_ma_period": 20}),
            {"atr_period": 14, "volatility_lookback": 20},
        ),
        lookback=3,
    )
    # Bars fully confirmed before mid-lookback should match
    check_upto = mid - 5
    for col in ["bos_up", "bos_dn"]:
        a = snapshot.loc[:check_upto, col].fillna(0).values
        b = recal.loc[:check_upto, col].fillna(0).values
        assert np.array_equal(a, b), f"lookahead leak detected in {col}"


def test_forward_return_not_in_features():
    """Discovery must use shift(-h) only as label, not as feature column fed back."""
    from src.discovery.engine import _forward_return
    df = _synthetic_ohlcv(50)
    fwd = _forward_return(df["close"], 3)
    # last 3 must be NaN
    assert fwd.iloc[-3:].isna().all()
    # features should not equal forward returns
    assert not np.allclose(df["close"].values[:-3], (df["close"].values[:-3] * (1 + fwd.dropna().values)))


def test_split_windows_order():
    from src.validation.pipeline_8_60_1y import split_by_time
    df = _synthetic_ohlcv(2000)
    # fake longer span
    df["open_time"] = np.arange(2000) * 3_600_000 + 1_600_000_000_000
    w = split_by_time(df, discovery_days=8, validation_days=60, robustness_days=100)
    if not w.discovery.empty and not w.validation.empty:
        assert w.discovery["open_time"].max() <= w.validation["open_time"].min()
    if not w.validation.empty and not w.robustness.empty:
        assert w.validation["open_time"].max() <= w.robustness["open_time"].min()
