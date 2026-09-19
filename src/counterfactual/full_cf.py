"""
Expanded counterfactual engine: SL/TP grids, entry delay, opposite, NO-TRADE path.
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import pandas as pd

from ..engines.backtest import TradeResult


def simulate_path(
    df: pd.DataFrame,
    entry_i: int,
    direction: int,
    sl_dist: float,
    tp_dist: float,
    max_bars: int = 30,
) -> dict[str, Any]:
    entry = float(df.iloc[entry_i]["close"])
    if direction == 1:
        sl, tp = entry - sl_dist, entry + tp_dist
    else:
        sl, tp = entry + sl_dist, entry - tp_dist
    mfe = mae = 0.0
    outcome = "TIME"
    exit_px = entry
    exit_j = entry_i
    for j in range(entry_i + 1, min(entry_i + 1 + max_bars, len(df))):
        bar = df.iloc[j]
        hi, lo = float(bar["high"]), float(bar["low"])
        if direction == 1:
            mfe = max(mfe, hi - entry)
            mae = min(mae, lo - entry)
            if lo <= sl:
                outcome, exit_px, exit_j = "SL", sl, j
                break
            if hi >= tp:
                outcome, exit_px, exit_j = "TP", tp, j
                break
        else:
            mfe = max(mfe, entry - lo)
            mae = min(mae, entry - hi)
            if hi >= sl:
                outcome, exit_px, exit_j = "SL", sl, j
                break
            if lo <= tp:
                outcome, exit_px, exit_j = "TP", tp, j
                break
    r = (exit_px - entry) / sl_dist * direction if sl_dist else 0
    return {
        "outcome": outcome,
        "r": float(r),
        "mfe": float(mfe),
        "mae": float(mae),
        "bars": int(exit_j - entry_i),
        "exit_price": float(exit_px),
    }


def counterfactual_matrix(
    df: pd.DataFrame,
    entry_indices: Sequence[int],
    directions: Sequence[int],
    atr_col: str = "atr",
    sl_mults: list[float] | None = None,
    tp_rrs: list[float] | None = None,
    entry_delays: list[int] | None = None,
) -> list[dict]:
    sl_mults = sl_mults or [1.2, 1.55, 1.85, 2.2]
    tp_rrs = tp_rrs or [1.5, 2.0, 2.5, 3.0]
    entry_delays = entry_delays or [0, 1, 2]
    rows = []
    for ei, d in zip(entry_indices, directions):
        if ei >= len(df) - 2:
            continue
        atr = float(df.iloc[ei][atr_col]) if atr_col in df.columns and pd.notna(df.iloc[ei].get(atr_col)) else float(df.iloc[ei]["close"]) * 0.01
        for delay in entry_delays:
            i = ei + delay
            if i >= len(df) - 2:
                continue
            for sm in sl_mults:
                for rr in tp_rrs:
                    path = simulate_path(df, i, d, atr * sm, atr * sm * rr)
                    # opposite
                    opp = simulate_path(df, i, -d, atr * sm, atr * sm * rr)
                    # no trade: 0
                    rows.append({
                        "entry_i": ei,
                        "delay": delay,
                        "direction": d,
                        "sl_atr": sm,
                        "tp_rr": rr,
                        "path": path,
                        "opposite_r": opp["r"],
                        "opposite_outcome": opp["outcome"],
                        "no_trade_r": 0.0,
                        "better_than_no_trade": path["r"] > 0,
                        "better_than_opposite": path["r"] > opp["r"],
                    })
    return rows


def summarize_cf_matrix(rows: list[dict]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    df = pd.DataFrame([{
        "sl_atr": r["sl_atr"],
        "tp_rr": r["tp_rr"],
        "delay": r["delay"],
        "r": r["path"]["r"],
        "outcome": r["path"]["outcome"],
        "opp_r": r["opposite_r"],
        "beat_opp": r["better_than_opposite"],
        "beat_flat": r["better_than_no_trade"],
    } for r in rows])
    by = {}
    for (sm, rr), g in df.groupby(["sl_atr", "tp_rr"]):
        by[f"sl{sm}_rr{rr}"] = {
            "n": len(g),
            "avg_r": round(float(g["r"].mean()), 4),
            "tp_rate": round(float((g["outcome"] == "TP").mean()), 3),
            "beat_opposite_pct": round(float(g["beat_opp"].mean()) * 100, 1),
            "beat_flat_pct": round(float(g["beat_flat"].mean()) * 100, 1),
        }
    return {"n": len(df), "by_sl_tp": by, "avg_r_overall": round(float(df["r"].mean()), 4)}
