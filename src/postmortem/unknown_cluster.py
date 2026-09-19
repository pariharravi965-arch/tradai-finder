"""UNKNOWN loss clustering — discover hidden common factors."""
from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd


FEATURE_KEYS = [
    "market_regime", "vol_regime", "deep_bias", "structure_state",
    "deriv_regime", "session", "mtf_conflict", "sweep_high", "sweep_low",
    "fvg_bull", "fvg_bear", "macd_conflict", "hour_utc", "dow",
]


def cluster_unknown_losses(pm_list: list[dict], trades_meta: list[dict] | None = None) -> dict[str, Any]:
    """Group UNKNOWN losses by co-occurring feature states."""
    unknowns = [p for p in pm_list if p.get("primary_reason") == "UNKNOWN" or p.get("unknown_flag")]
    if not unknowns:
        return {"n_unknown": 0, "clusters": [], "note": "No UNKNOWN losses"}

    # If we have trade meta with feature snapshots
    clusters: Counter = Counter()
    if trades_meta:
        id_to_meta = {t.get("trade_id"): t for t in trades_meta}
        for u in unknowns:
            meta = id_to_meta.get(u.get("trade_id"), {})
            key_parts = []
            for k in FEATURE_KEYS:
                if k in meta and meta[k] is not None:
                    key_parts.append(f"{k}={meta[k]}")
            if key_parts:
                clusters[tuple(key_parts[:5])] += 1
            else:
                clusters[("NO_SNAPSHOT",)] += 1
    else:
        for u in unknowns:
            clusters[(u.get("exit_reason", "?"),)] += 1

    top = [{"pattern": list(k), "count": v, "pct": round(v / max(len(unknowns), 1) * 100, 1)} for k, v in clusters.most_common(15)]
    return {
        "n_unknown": len(unknowns),
        "clusters": top,
        "suggestion": "Promote top cluster patterns to named failure categories after 60D validation",
    }
