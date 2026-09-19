"""Funding & OI feature merge — mark availability explicitly."""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_funding_features(klines: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    out = klines.copy()
    f = funding.copy()
    if "fundingTime" not in f.columns or "fundingRate" not in f.columns:
        out["funding_rate"] = np.nan
        out["funding_available"] = 0
        return out

    f = f.sort_values("fundingTime")
    # As-of merge: funding known only after fundingTime
    out = pd.merge_asof(
        out.sort_values("open_time"),
        f[["fundingTime", "fundingRate"]].rename(columns={"fundingTime": "open_time", "fundingRate": "funding_rate"}),
        on="open_time",
        direction="backward",
    )
    out["funding_available"] = out["funding_rate"].notna().astype(int)
    out["funding_abs"] = out["funding_rate"].abs()
    out["funding_positive"] = (out["funding_rate"] > 0).astype(int)
    out["funding_change"] = out["funding_rate"] - out["funding_rate"].shift(1)
    return out


def merge_oi_features(klines: pd.DataFrame, oi: pd.DataFrame, status: str = "DATA_AVAILABLE") -> pd.DataFrame:
    out = klines.copy()
    o = oi.copy()
    ts_col = "timestamp" if "timestamp" in o.columns else None
    oi_col = "sumOpenInterest" if "sumOpenInterest" in o.columns else None
    if ts_col is None or oi_col is None:
        out["oi"] = np.nan
        out["oi_available"] = 0
        out["oi_status"] = "DATA_MISSING"
        return out

    o = o.sort_values(ts_col)
    merge_df = o[[ts_col, oi_col]].rename(columns={ts_col: "open_time", oi_col: "oi"})
    out = pd.merge_asof(
        out.sort_values("open_time"),
        merge_df,
        on="open_time",
        direction="backward",
    )
    out["oi_available"] = out["oi"].notna().astype(int)
    out["oi_status"] = status
    out["oi_change"] = out["oi"].pct_change()
    # Price vs OI regimes
    price_up = out["close"] > out["close"].shift(1)
    oi_up = out["oi"] > out["oi"].shift(1)
    out["oi_price_regime"] = "UNKNOWN"
    out.loc[price_up & oi_up, "oi_price_regime"] = "PRICE_UP_OI_UP"
    out.loc[price_up & ~oi_up, "oi_price_regime"] = "PRICE_UP_OI_DOWN"
    out.loc[~price_up & oi_up, "oi_price_regime"] = "PRICE_DOWN_OI_UP"
    out.loc[~price_up & ~oi_up, "oi_price_regime"] = "PRICE_DOWN_OI_DOWN"
    return out
