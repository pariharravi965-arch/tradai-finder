"""Data quality scoring — never silently fill critical gaps."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class DataQualityReport:
    symbol: str
    timeframe: str
    n_rows: int = 0
    first_open_time: int | None = None
    last_open_time: int | None = None
    missing_pct: float = 0.0
    duplicate_pct: float = 0.0
    zero_volume_pct: float = 0.0
    abnormal_ohlc_count: int = 0
    gap_count: int = 0
    score: float = 100.0
    flags: list[str] = field(default_factory=list)
    status: str = "OK"  # OK | DEGRADED | UNUSABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "n_rows": self.n_rows,
            "first_open_time": self.first_open_time,
            "last_open_time": self.last_open_time,
            "missing_pct": self.missing_pct,
            "duplicate_pct": self.duplicate_pct,
            "zero_volume_pct": self.zero_volume_pct,
            "abnormal_ohlc_count": self.abnormal_ohlc_count,
            "gap_count": self.gap_count,
            "score": self.score,
            "flags": self.flags,
            "status": self.status,
        }


def score_data_quality(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    expected_interval_ms: int,
    max_missing_pct: float = 5.0,
) -> DataQualityReport:
    report = DataQualityReport(symbol=symbol, timeframe=timeframe)
    if df is None or df.empty:
        report.score = 0.0
        report.status = "UNUSABLE"
        report.flags.append("EMPTY")
        return report

    report.n_rows = len(df)
    report.first_open_time = int(df["open_time"].iloc[0])
    report.last_open_time = int(df["open_time"].iloc[-1])

    # Duplicates
    n_dup = df["open_time"].duplicated().sum()
    report.duplicate_pct = float(n_dup / max(len(df), 1) * 100)
    if report.duplicate_pct > 0.1:
        report.flags.append("DUPLICATES")

    # Zero volume
    if "volume" in df.columns:
        zv = (df["volume"] <= 0).sum()
        report.zero_volume_pct = float(zv / max(len(df), 1) * 100)
        if report.zero_volume_pct > 2:
            report.flags.append("HIGH_ZERO_VOLUME")

    # Abnormal OHLC
    bad = 0
    if all(c in df.columns for c in ["open", "high", "low", "close"]):
        bad = int(
            (
                (df["high"] < df["low"])
                | (df["high"] < df["open"])
                | (df["high"] < df["close"])
                | (df["low"] > df["open"])
                | (df["low"] > df["close"])
            ).sum()
        )
    report.abnormal_ohlc_count = bad
    if bad > 0:
        report.flags.append("ABNORMAL_OHLC")

    # Gaps
    if len(df) > 2 and expected_interval_ms > 0:
        diffs = df["open_time"].diff().dropna()
        gaps = (diffs > expected_interval_ms * 1.5).sum()
        report.gap_count = int(gaps)
        if gaps > len(df) * 0.01:
            report.flags.append("MANY_GAPS")

    # Expected vs actual coverage
    span = report.last_open_time - report.first_open_time
    expected_bars = max(span / expected_interval_ms, 1)
    report.missing_pct = float(max(0, (expected_bars - len(df)) / expected_bars * 100))

    # Score
    score = 100.0
    score -= min(report.missing_pct * 2, 40)
    score -= min(report.duplicate_pct * 5, 20)
    score -= min(report.zero_volume_pct, 15)
    score -= min(report.abnormal_ohlc_count * 2, 15)
    score -= min(report.gap_count * 0.5, 20)
    report.score = float(max(0, score))

    if report.score < 40 or report.missing_pct > max_missing_pct * 3:
        report.status = "UNUSABLE"
    elif report.score < 70 or report.missing_pct > max_missing_pct:
        report.status = "DEGRADED"
    else:
        report.status = "OK"
    return report
