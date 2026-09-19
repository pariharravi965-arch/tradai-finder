"""Session / time-of-day / day-of-week factors (UTC + IST aware)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_session_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # open_time is ms
    ts = pd.to_datetime(out["open_time"], unit="ms", utc=True)
    out["hour_utc"] = ts.dt.hour
    out["dow"] = ts.dt.dayofweek  # 0=Mon
    out["is_weekend"] = (out["dow"] >= 5).astype(int)

    # IST = UTC+5:30
    ts_ist = ts + pd.Timedelta(hours=5, minutes=30)
    out["hour_ist"] = ts_ist.dt.hour

    # Sessions (UTC approx)
    # Asia: 00-08, London: 07-16, NY: 13-22
    hour = out["hour_utc"]
    session = np.array(["OFF"] * len(out), dtype=object)
    session[(hour >= 0) & (hour < 8)] = "ASIA"
    session[(hour >= 7) & (hour < 16)] = "LONDON"
    session[(hour >= 13) & (hour < 22)] = "NY"
    # overlap flags
    out["session"] = session
    out["asia_session"] = ((hour >= 0) & (hour < 8)).astype(int)
    out["london_session"] = ((hour >= 7) & (hour < 16)).astype(int)
    out["ny_session"] = ((hour >= 13) & (hour < 22)).astype(int)
    out["london_ny_overlap"] = ((hour >= 13) & (hour < 16)).astype(int)

    # Daily open (first bar of UTC day)
    day = ts.dt.floor("D")
    daily_open = out.groupby(day)["open"].transform("first")
    out["daily_open"] = daily_open
    out["dist_daily_open_pct"] = (out["close"] - daily_open) / daily_open * 100

    # Opening range proxy: first 2 bars of session high-low (approx using hour==0 or hour==7)
    out["session_or_high"] = np.nan
    out["session_or_low"] = np.nan
    return out
