"""Vectorized research backtest — cost-aware, causal execution.

Execution model:
  Signal on bar t (using data up to t close)
  Entry at bar t+1 open
  SL / TP checked on subsequent bars high/low
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class TradeResult:
    trade_id: str
    symbol: str
    timeframe: str
    direction: int  # 1 long, -1 short
    signal_time: int
    entry_time: int
    entry_price: float
    sl: float
    tp: float
    exit_time: int
    exit_price: float
    exit_reason: str
    gross_r: float
    net_r: float
    pnl_frac: float
    holding_bars: int
    regime: str = ""
    vol_regime: str = ""
    signal_reason: str = ""
    features_snapshot: dict = field(default_factory=dict)


def run_vector_backtest(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    rr: float = 2.0,
    sl_atr_mult: float = 1.2,
    fee_rate: float = 0.0004,
    slippage_bps: float = 2.0,
    max_holding_bars: int = 48,
) -> list[TradeResult]:
    """Simple sequential backtest for research (not high-frequency accurate)."""
    if df is None or len(df) < 10:
        return []

    required = ["open", "high", "low", "close", "atr", "open_time"]
    for c in required:
        if c not in df.columns:
            return []

    trades: list[TradeResult] = []
    slip = slippage_bps / 10_000
    n = len(df)
    i = 0
    trade_counter = 0

    while i < n - 2:
        row = df.iloc[i]
        sig_long = int(row.get("signal_long", 0) or 0)
        sig_short = int(row.get("signal_short", 0) or 0)
        if sig_long == 0 and sig_short == 0:
            i += 1
            continue
        if int(row.get("no_trade_candidate", 0) or 0) == 1:
            i += 1
            continue

        direction = 1 if sig_long else -1
        # Entry next bar open
        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry_row = df.iloc[entry_idx]
        atr = float(row["atr"]) if pd.notna(row["atr"]) and row["atr"] > 0 else float(row["close"]) * 0.01
        entry_price = float(entry_row["open"])
        # Slippage
        if direction == 1:
            entry_price *= 1 + slip
            sl = entry_price - sl_atr_mult * atr
            tp = entry_price + rr * (entry_price - sl)
        else:
            entry_price *= 1 - slip
            sl = entry_price + sl_atr_mult * atr
            tp = entry_price - rr * (sl - entry_price)

        exit_idx = None
        exit_price = None
        exit_reason = "TIME"
        for j in range(entry_idx + 1, min(entry_idx + 1 + max_holding_bars, n)):
            bar = df.iloc[j]
            if direction == 1:
                if bar["low"] <= sl:
                    exit_idx = j
                    exit_price = sl * (1 - slip)
                    exit_reason = "SL"
                    break
                if bar["high"] >= tp:
                    exit_idx = j
                    exit_price = tp * (1 - slip)
                    exit_reason = "TP"
                    break
            else:
                if bar["high"] >= sl:
                    exit_idx = j
                    exit_price = sl * (1 + slip)
                    exit_reason = "SL"
                    break
                if bar["low"] <= tp:
                    exit_idx = j
                    exit_price = tp * (1 + slip)
                    exit_reason = "TP"
                    break
        if exit_idx is None:
            exit_idx = min(entry_idx + max_holding_bars, n - 1)
            exit_price = float(df.iloc[exit_idx]["close"])
            exit_reason = "TIME"

        risk = abs(entry_price - sl)
        if risk <= 0:
            i = exit_idx + 1
            continue
        if direction == 1:
            gross_r = (exit_price - entry_price) / risk
            pnl_frac = (exit_price - entry_price) / entry_price
        else:
            gross_r = (entry_price - exit_price) / risk
            pnl_frac = (entry_price - exit_price) / entry_price

        # Fees on notional (approx 2 legs)
        fee_cost = 2 * fee_rate
        net_pnl_frac = pnl_frac - fee_cost
        net_r = gross_r - (fee_cost * entry_price / risk)

        trade_counter += 1
        tr = TradeResult(
            trade_id=f"{symbol}_{timeframe}_{trade_counter}",
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            signal_time=int(row["open_time"]),
            entry_time=int(entry_row["open_time"]),
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            exit_time=int(df.iloc[exit_idx]["open_time"]),
            exit_price=exit_price,
            exit_reason=exit_reason,
            gross_r=float(gross_r),
            net_r=float(net_r),
            pnl_frac=float(net_pnl_frac),
            holding_bars=exit_idx - entry_idx,
            regime=str(row.get("market_regime", "")),
            vol_regime=str(row.get("vol_regime", "")),
            signal_reason=str(row.get("signal_reason", "")),
        )
        trades.append(tr)
        i = exit_idx + 1  # no overlapping for research simplicity

    return trades
