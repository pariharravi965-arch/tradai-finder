"""Counterfactual SL/TP/entry research — pure research, not live signals."""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from ..engines.backtest import TradeResult


def counterfactual_sl_grid(
    df: pd.DataFrame,
    trades: Sequence[TradeResult],
    atr_mults: list[float] | None = None,
    rr: float = 2.0,
) -> list[dict]:
    """For each historical trade, re-simulate with alternate ATR SL multiples."""
    atr_mults = atr_mults or [1.0, 1.2, 1.55, 1.85, 2.1, 2.5]
    if df is None or df.empty or not trades:
        return []
    # index by open_time for lookup
    tmap = {int(r["open_time"]): i for i, r in df.iterrows()}
    results = []
    for tr in trades:
        entry_t = tr.entry_time
        if entry_t not in tmap:
            # nearest
            candidates = [t for t in tmap if t >= entry_t]
            if not candidates:
                continue
            entry_t = min(candidates)
        i = tmap[entry_t]
        if i >= len(df) - 2:
            continue
        row = df.iloc[i]
        atr = float(row["atr"]) if "atr" in df.columns and pd.notna(row.get("atr")) else float(row["close"]) * 0.01
        entry = tr.entry_price
        direction = tr.direction
        for mult in atr_mults:
            dist = atr * mult
            if direction == 1:
                sl = entry - dist
                tp = entry + dist * rr
            else:
                sl = entry + dist
                tp = entry - dist * rr
            outcome = "TIME"
            exit_px = entry
            for j in range(i + 1, min(i + 1 + max(tr.holding_bars * 2, 20), len(df))):
                bar = df.iloc[j]
                if direction == 1:
                    if bar["low"] <= sl:
                        outcome, exit_px = "SL", sl
                        break
                    if bar["high"] >= tp:
                        outcome, exit_px = "TP", tp
                        break
                else:
                    if bar["high"] >= sl:
                        outcome, exit_px = "SL", sl
                        break
                    if bar["low"] <= tp:
                        outcome, exit_px = "TP", tp
                        break
            if direction == 1:
                r_mult = (exit_px - entry) / dist if dist else 0
            else:
                r_mult = (entry - exit_px) / dist if dist else 0
            results.append(
                {
                    "trade_id": tr.trade_id,
                    "orig_net_r": tr.net_r,
                    "orig_exit": tr.exit_reason,
                    "cf_sl_atr": mult,
                    "cf_outcome": outcome,
                    "cf_r": round(float(r_mult), 3),
                    "improved": float(r_mult) > tr.net_r,
                }
            )
    return results


def summarize_counterfactuals(cf_rows: list[dict]) -> dict:
    if not cf_rows:
        return {"n": 0}
    df = pd.DataFrame(cf_rows)
    by_mult = {}
    for mult, g in df.groupby("cf_sl_atr"):
        by_mult[float(mult)] = {
            "n": len(g),
            "avg_r": round(float(g["cf_r"].mean()), 3),
            "tp_rate": round(float((g["cf_outcome"] == "TP").mean()), 3),
            "sl_rate": round(float((g["cf_outcome"] == "SL").mean()), 3),
            "improved_pct": round(float(g["improved"].mean()) * 100, 1),
        }
    # After original SL: would TP have hit? (from rows where orig was SL)
    return {"n": len(df), "by_sl_atr": by_mult}
