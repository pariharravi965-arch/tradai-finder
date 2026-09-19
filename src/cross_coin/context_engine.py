"""
Cross-coin intelligence — BTC leadership, relative strength, correlation, breadth.

Requires multi-coin OHLCV aligned on open_time (ms). Causal: uses only past bars.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger


def _ret(df: pd.DataFrame, col: str = "close", n: int = 1) -> pd.Series:
    return df[col].pct_change(n)


def build_cross_coin_panel(
    frames: dict[str, pd.DataFrame],
    anchor: str = "BTCUSD",
) -> pd.DataFrame:
    """Align closes of multiple symbols on open_time (inner as-of not needed if same TF)."""
    if not frames:
        return pd.DataFrame()
    series = {}
    for sym, df in frames.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        s = df.set_index("open_time")["close"].sort_index()
        series[sym] = s
    if not series:
        return pd.DataFrame()
    panel = pd.DataFrame(series).sort_index()
    panel = panel.ffill(limit=2)  # tiny gap fill only for align; not fabrication of history
    return panel


def add_cross_coin_features(
    panel: pd.DataFrame,
    anchor: str = "BTCUSD",
    lookback: int = 24,
) -> pd.DataFrame:
    """From multi-coin close panel → RS, corr, breadth, leadership proxies."""
    if panel.empty:
        return panel
    out = panel.copy()
    cols = list(out.columns)
    if anchor not in cols:
        anchor = cols[0]
        logger.warning(f"Anchor missing; using {anchor}")

    rets = out.pct_change()
    out["btc_ret"] = rets[anchor]
    # breadth: % coins green last bar
    coin_cols = [c for c in cols]
    out["breadth_up"] = (rets[coin_cols] > 0).sum(axis=1) / max(len(coin_cols), 1)
    out["breadth_down"] = (rets[coin_cols] < 0).sum(axis=1) / max(len(coin_cols), 1)
    out["market_ret_mean"] = rets[coin_cols].mean(axis=1)

    # relative strength vs BTC (ratio of cumulative returns)
    for c in coin_cols:
        if c == anchor:
            out[f"rs_{c}"] = 1.0
            continue
        ratio = out[c] / out[anchor]
        out[f"rs_{c}"] = ratio / ratio.rolling(lookback, min_periods=5).mean()
        out[f"corr_{c}"] = rets[c].rolling(lookback, min_periods=10).corr(rets[anchor])

    # leadership: BTC move magnitude vs alts average
    alt_cols = [c for c in coin_cols if c != anchor]
    if alt_cols:
        out["btc_lead_proxy"] = rets[anchor].abs() - rets[alt_cols].abs().mean(axis=1)
        out["alt_follow_lag1"] = rets[alt_cols].mean(axis=1).shift(-1)  # FOR RESEARCH ONLY on past folds — drop before live
        # causal leadership: BTC ret large, next-bar alt mean (label for discovery only with lag)
        out["btc_shock"] = (rets[anchor].abs() > rets[anchor].rolling(lookback).std() * 2).astype(int)

    out["anchor"] = anchor
    return out


def cross_coin_snapshot_for_symbol(
    panel_feat: pd.DataFrame,
    symbol: str,
    anchor: str = "BTCUSD",
) -> pd.DataFrame:
    """Slice features relevant to one symbol for merge onto its OHLCV."""
    if panel_feat.empty:
        return pd.DataFrame()
    keep = ["btc_ret", "breadth_up", "breadth_down", "market_ret_mean", "btc_lead_proxy", "btc_shock"]
    keep = [k for k in keep if k in panel_feat.columns]
    if f"rs_{symbol}" in panel_feat.columns:
        keep.append(f"rs_{symbol}")
    if f"corr_{symbol}" in panel_feat.columns:
        keep.append(f"corr_{symbol}")
    snap = panel_feat[keep].copy()
    snap = snap.reset_index().rename(columns={"index": "open_time"})
    if "open_time" not in snap.columns and panel_feat.index.name == "open_time":
        snap["open_time"] = panel_feat.index
    # rename rs/corr to generic
    snap = snap.rename(columns={f"rs_{symbol}": "rs_vs_btc", f"corr_{symbol}": "corr_vs_btc"})
    return snap
