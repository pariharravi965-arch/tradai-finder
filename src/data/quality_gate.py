"""
Step 5 — Data Quality Gate

Blocks research on datasets that fail hard checks.
Emits per-file and universe-level gate reports.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from loguru import logger

from .delta_client import INTERVAL_MS
from .quality import score_data_quality


@dataclass
class GateResult:
    path: str
    symbol: str
    timeframe: str
    series_type: str  # ohlcv | oi | funding | mark
    n_rows: int
    quality_status: str
    quality_score: float
    gate: str  # PASS | WARN | FAIL
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _infer_meta(path: Path) -> tuple[str, str, str]:
    """Parse filename like BTCUSD_1h.csv or OI_BTCUSD_1h.csv."""
    stem = path.stem
    series = "ohlcv"
    if stem.startswith("OI_") or "/oi/" in str(path).lower():
        series = "oi"
        stem = stem.replace("OI_", "")
    elif stem.startswith("FUNDING_") or "/funding/" in str(path).lower():
        series = "funding"
        stem = stem.replace("FUNDING_", "")
    elif stem.startswith("MARK_") or "/mark/" in str(path).lower():
        series = "mark"
        stem = stem.replace("MARK_", "")
    # stem now SYMBOL_TF
    parts = stem.rsplit("_", 1)
    if len(parts) == 2:
        return parts[0], parts[1], series
    return stem, "unknown", series


class DataQualityGate:
    def __init__(
        self,
        data_root: Path,
        out_dir: Path,
        min_score: float = 70.0,
        min_rows: int = 15,
    ):
        self.data_root = Path(data_root)
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.min_score = min_score
        self.min_rows = min_rows

    def evaluate_file(self, path: Path) -> GateResult:
        symbol, tf, series = _infer_meta(path)
        if path.stat().st_size == 0:
            return GateResult(str(path), symbol, tf, series, 0, "UNUSABLE", 0.0, "FAIL", ["EMPTY_FILE"])
        try:
            df = pd.read_csv(path)
        except Exception as e:
            return GateResult(str(path), symbol, tf, series, 0, "UNUSABLE", 0.0, "FAIL", [f"READ_ERROR:{e}"])
        interval = INTERVAL_MS.get(tf, 0)
        q = score_data_quality(df, symbol, tf, interval)
        gate = "PASS"
        flags = list(q.flags)
        if q.status == "UNUSABLE" or q.score < self.min_score * 0.5 or q.n_rows < self.min_rows:
            gate = "FAIL"
        elif q.status == "DEGRADED" or q.score < self.min_score:
            gate = "WARN"
        return GateResult(str(path), symbol, tf, series, q.n_rows, q.status, q.score, gate, flags)

    def run(self, pattern: str = "**/*.csv") -> dict[str, Any]:
        files = sorted(self.data_root.glob(pattern))
        results: list[GateResult] = []
        for p in files:
            if "live" in str(p):
                continue
            results.append(self.evaluate_file(p))

        n_pass = sum(1 for r in results if r.gate == "PASS")
        n_warn = sum(1 for r in results if r.gate == "WARN")
        n_fail = sum(1 for r in results if r.gate == "FAIL")
        research_allowed = [r.to_dict() for r in results if r.gate in ("PASS", "WARN") and r.series_type == "ohlcv"]
        blocked = [r.to_dict() for r in results if r.gate == "FAIL"]

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "n_files": len(results),
            "n_pass": n_pass,
            "n_warn": n_warn,
            "n_fail": n_fail,
            "min_score": self.min_score,
            "min_rows": self.min_rows,
            "research_allowed_ohlcv": research_allowed,
            "blocked": blocked,
            "all": [r.to_dict() for r in results],
            "gate_ok_for_discovery": n_fail == 0 and n_pass > 0,
        }
        path = self.out_dir / "quality_gate_report.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info(f"Quality gate: PASS={n_pass} WARN={n_warn} FAIL={n_fail} → {path}")
        return report
