"""Timestamp alignment + missing-data engine across OHLCV / MARK / OI / FUNDING."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger


def load_parquet_safe(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception as e:
        logger.warning(f"read fail {path}: {e}")
        return pd.DataFrame()


def align_series(
    ohlcv: pd.DataFrame,
    mark: Optional[pd.DataFrame] = None,
    oi: Optional[pd.DataFrame] = None,
    funding: Optional[pd.DataFrame] = None,
) -> tuple[pd.DataFrame, dict]:
    """As-of merge auxiliary series onto OHLCV open_time (ms). Report gaps."""
    report = {"ohlcv_rows": len(ohlcv), "gaps": []}
    if ohlcv.empty or "open_time" not in ohlcv.columns:
        report["gaps"].append("OHLCV_EMPTY")
        return ohlcv, report

    out = ohlcv.sort_values("open_time").copy()

    def _merge(name: str, aux: Optional[pd.DataFrame], value_col: str, out_col: str):
        if aux is None or aux.empty:
            out[out_col] = np.nan
            out[f"{out_col}_status"] = "DATA_MISSING"
            report["gaps"].append(f"{name}_MISSING")
            return
        a = aux.sort_values("open_time")
        if value_col not in a.columns and "close" in a.columns:
            value_col = "close"
        if value_col not in a.columns:
            out[out_col] = np.nan
            report["gaps"].append(f"{name}_NO_VALUE_COL")
            return
        m = pd.merge_asof(
            out[["open_time"]],
            a[["open_time", value_col]].rename(columns={value_col: out_col}),
            on="open_time",
            direction="backward",
        )
        out[out_col] = m[out_col].values
        miss = float(out[out_col].isna().mean() * 100)
        out[f"{out_col}_status"] = "DATA_AVAILABLE" if miss < 20 else "DATA_LIMITED"
        if miss > 5:
            report["gaps"].append(f"{name}_MISSING_PCT_{miss:.1f}")

    _merge("MARK", mark, "close", "mark_price")
    _merge("OI", oi, "close", "oi")
    _merge("FUNDING", funding, "close", "funding_rate")

    # Basis proxy: mark vs close
    if "mark_price" in out.columns:
        out["basis_proxy"] = out["mark_price"] - out["close"]
        out["basis_pct"] = out["basis_proxy"] / out["close"] * 100
    else:
        out["basis_proxy"] = np.nan
        out["basis_pct"] = np.nan

    report["aligned_rows"] = len(out)
    report["mark_coverage"] = float(out["mark_price"].notna().mean()) if "mark_price" in out.columns else 0
    report["oi_coverage"] = float(out["oi"].notna().mean()) if "oi" in out.columns else 0
    report["funding_coverage"] = float(out["funding_rate"].notna().mean()) if "funding_rate" in out.columns else 0
    return out, report
