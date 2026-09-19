"""
AI knowledge extraction — evidence-based next experiments (no hard-coded success claims).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..registry.store import RegistryStore


def build_ai_improvement_plan(
    discovery_results: list[dict],
    validation_results: list[dict],
    postmortem_summary: dict,
    data_gaps: list[str],
    unknown_clusters: dict | None = None,
    registry: Optional[RegistryStore] = None,
) -> dict[str, Any]:
    """Machine-readable answers required by master prompt §31."""
    validated = [v for v in validation_results if v.get("classification") in ("ROBUST_CANDIDATE", "PARTIALLY_ROBUST")]
    rejected = [v for v in validation_results if v.get("classification") in ("REJECTED", "REJECTED_OOS", "UNSTABLE_LONG_TERM")]
    discovered = [d for d in discovery_results if d.get("status") == "DISCOVERED"]

    what_failed = [r.get("signal_col") or r.get("factor") for r in rejected][:20]
    why = []
    for r in rejected[:10]:
        why.append({
            "item": r.get("signal_col") or r.get("factor"),
            "classification": r.get("classification"),
            "discovery_exp": r.get("discovery_8d", {}).get("expectancy") if isinstance(r.get("discovery_8d"), dict) else None,
            "validation_exp": r.get("validation_60d", {}).get("expectancy") if isinstance(r.get("validation_60d"), dict) else None,
        })

    plan = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "WHAT_FAILED": what_failed,
        "WHY": why,
        "WHAT_DATA_WAS_MISSING": data_gaps,
        "WHAT_FEATURE_MAY_HELP": (unknown_clusters or {}).get("clusters", [])[:5],
        "WHAT_HYPOTHESIS_SHOULD_BE_TESTED": [
            d.get("factor") for d in discovered if d.get("factor")
        ][:15],
        "WHAT_SHOULD_NOT_BE_TRUSTED": [
            r.get("factor") or r.get("signal_col") for r in discovery_results if r.get("status") in ("REJECTED", "LOW_SAMPLE")
        ][:20],
        "WHAT_DECAYED": [r.get("signal_col") for r in rejected if r.get("classification") == "UNSTABLE_LONG_TERM"],
        "WHAT_REMAINED_STABLE": [v.get("signal_col") for v in validated],
        "WHAT_SHOULD_BE_TESTED_NEXT": [
            "Re-test DISCOVERED factors on fresh 60D only",
            "Expand live collector history before microstructure claims",
            "Wrong-direction counterfactual on UNKNOWN clusters",
            "No-trade conditions under mtf_conflict + extreme vol",
        ],
        "postmortem_top_reasons": postmortem_summary.get("reason_counts") or postmortem_summary,
        "honesty_note": "No claim of edge without ROBUST_CANDIDATE on unseen validation",
    }

    if registry:
        registry.register_ai_feedback(
            what_failed=what_failed,
            why=str(why)[:500],
            data_missing=data_gaps,
            feature_may_help=[str(x) for x in plan["WHAT_FEATURE_MAY_HELP"][:5]],
            hypothesis_to_test=plan["WHAT_HYPOTHESIS_SHOULD_BE_TESTED"][:10],
            do_not_trust=plan["WHAT_SHOULD_NOT_BE_TRUSTED"][:10],
            decayed=plan["WHAT_DECAYED"],
            remained_stable=plan["WHAT_REMAINED_STABLE"],
            test_next=plan["WHAT_SHOULD_BE_TESTED_NEXT"],
        )
    return plan


def write_plan(plan: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")
    return path
