"""
Wire live collector JSONL → research features (imbalance, CVD proxy, aggression).

Only uses collected history — never fabricates pre-collector periods.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger


def load_live_jsonl(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def load_symbol_live(live_root: Path, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = Path(live_root) / symbol
    if not d.exists():
        return pd.DataFrame(), pd.DataFrame()
    obs, trs = [], []
    for p in sorted(d.glob("orderbook_*.jsonl")):
        obs.append(load_live_jsonl(p))
    for p in sorted(d.glob("trades_*.jsonl")):
        trs.append(load_live_jsonl(p))
    ob = pd.concat(obs, ignore_index=True) if obs else pd.DataFrame()
    tr = pd.concat(trs, ignore_index=True) if trs else pd.DataFrame()
    return ob, tr


def merge_live_onto_ohlcv(
    ohlcv: pd.DataFrame,
    live_root: Path,
    symbol: str,
) -> pd.DataFrame:
    """As-of merge live imbalance/CVD onto candles. Pre-collector rows stay NaN + LIVE_COLLECT_ONLY."""
    out = ohlcv.copy()
    ob, tr = load_symbol_live(live_root, symbol)
    if ob.empty and tr.empty:
        out["ob_imbalance"] = float("nan")
        out["cvd_delta"] = float("nan")
        out["microstructure_status"] = "LIVE_COLLECT_ONLY_NO_HISTORY"
        return out

    def _ts_to_ms(s):
        return pd.to_datetime(s, utc=True).astype("int64") // 10**6

    if not ob.empty and "ts" in ob.columns:
        ob = ob.copy()
        ob["open_time"] = _ts_to_ms(ob["ts"])
        ob = ob.sort_values("open_time")
        m = pd.merge_asof(
            out.sort_values("open_time"),
            ob[["open_time", "imbalance"]].rename(columns={"imbalance": "ob_imbalance"}),
            on="open_time",
            direction="backward",
            tolerance=3_600_000,
        )
        out["ob_imbalance"] = m["ob_imbalance"].values
    else:
        out["ob_imbalance"] = float("nan")

    if not tr.empty and "ts" in tr.columns:
        tr = tr.copy()
        tr["open_time"] = _ts_to_ms(tr["ts"])
        tr = tr.sort_values("open_time")
        col = "delta" if "delta" in tr.columns else "aggression"
        if col in tr.columns:
            m2 = pd.merge_asof(
                out.sort_values("open_time"),
                tr[["open_time", col]].rename(columns={col: "cvd_delta"}),
                on="open_time",
                direction="backward",
                tolerance=3_600_000,
            )
            out["cvd_delta"] = m2["cvd_delta"].values
        else:
            out["cvd_delta"] = float("nan")
    else:
        out["cvd_delta"] = float("nan")

    out["microstructure_status"] = out["ob_imbalance"].apply(
        lambda x: "DATA_AVAILABLE" if pd.notna(x) else "LIVE_COLLECT_ONLY_NO_HISTORY"
    )
    n_avail = int(out["ob_imbalance"].notna().sum())
    logger.info(f"Microstructure merge {symbol}: {n_avail}/{len(out)} bars have live history")
    return out
