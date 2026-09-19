"""Discovery report + AI improvement plan generator."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger


def generate_full_report(
    run_id: str,
    universe_summary: dict,
    stage_results: dict,
    postmortem_summary: dict,
    data_gaps: list[dict],
    robust_features: list[dict],
    failed_features: list[dict],
    overfit_notes: list[dict],
    output_dir: str | Path,
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_summary": universe_summary,
        "top_discoveries": stage_results.get("discoveries", []),
        "failed_features": failed_features,
        "post_mortem": postmortem_summary,
        "robust_features": robust_features,
        "overfit_notes": overfit_notes,
        "data_gaps": data_gaps,
        "stage_comparison": stage_results.get("comparison", {}),
        "final_verdict": _build_verdict(stage_results, postmortem_summary, data_gaps),
        "next_research": _next_research(postmortem_summary, data_gaps),
    }

    path = output_dir / f"{run_id}_discovery_report.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info(f"Discovery report → {path}")

    # Human-readable markdown
    md_path = output_dir / f"{run_id}_discovery_report.md"
    md_path.write_text(_to_markdown(report), encoding="utf-8")
    return path


def write_improvement_plan(run_id: str, recommendations: list[dict], output_dir: str | Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{run_id}_ai_improvement_plan.json"
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "recommendations": recommendations,
        "note": "status=candidate until validation completed. Do not deploy unvalidated changes.",
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    logger.info(f"AI improvement plan → {path}")
    return path


def _build_verdict(stage_results: dict, pm: dict, gaps: list) -> str:
    lines = []
    lines.append("=== FINAL RESEARCH VERDICT ===")
    lines.append(f"Losses attributed to WRONG_DIRECTION: {pm.get('wrong_direction_pct', 'n/a')}%")
    lines.append(f"Losses UNKNOWN: {pm.get('unknown_loss_pct', 'n/a')}%")
    lines.append(f"SL_TOO_TIGHT: {pm.get('sl_too_tight_pct', 'n/a')}%")
    if gaps:
        lines.append(f"Critical data gaps: {len(gaps)} items (see data_gaps)")
    else:
        lines.append("No critical data gaps flagged in this run.")
    lines.append(
        "Important: Positive 8D results that collapse on 60D/1Y are labeled OVERFIT_SUSPECTED — not deployable."
    )
    return "\n".join(lines)


def _next_research(pm: dict, gaps: list) -> list[str]:
    nxt = []
    if pm.get("wrong_direction_pct", 0) > 30:
        nxt.append("Prioritize HTF trend filter + BTC context conflict filter validation (60D + 1Y WF).")
    if pm.get("unknown_loss_pct", 0) > 20:
        nxt.append("Deep-dive UNKNOWN losses: cluster preceding feature states for hidden patterns.")
    if pm.get("sl_too_tight_pct", 0) > 15:
        nxt.append("Test structure-based SL vs ATR multiples; measure TP reach after SL hits.")
    for g in gaps[:5]:
        nxt.append(f"Acquire or proxy: {g.get('factor', g)}")
    if not nxt:
        nxt.append("Expand coin coverage and run full walk-forward on robust candidates only.")
    return nxt


def _to_markdown(report: dict) -> str:
    v = report.get("final_verdict", "")
    pm = report.get("post_mortem", {})
    lines = [
        f"# TradAI Finder Discovery Report — {report.get('run_id')}",
        f"Generated: {report.get('generated_at')}",
        "",
        "## Final Verdict",
        "```",
        v,
        "```",
        "",
        "## Post-Mortem Summary",
        f"- Trades: {pm.get('n_trades')}",
        f"- Wins / Losses: {pm.get('n_wins')} / {pm.get('n_losses')}",
        f"- WRONG_DIRECTION: {pm.get('wrong_direction_pct')}%",
        f"- UNKNOWN: {pm.get('unknown_loss_pct')}%",
        f"- SL_TOO_TIGHT: {pm.get('sl_too_tight_pct')}%",
        "",
        "## Data Gaps",
    ]
    for g in report.get("data_gaps", []):
        lines.append(f"- {g}")
    lines.append("")
    lines.append("## Next Research")
    for n in report.get("next_research", []):
        lines.append(f"- {n}")
    return "\n".join(lines)


def default_improvement_recommendations(pm: dict, stage_class: str) -> list[dict]:
    recs = []
    if pm.get("wrong_direction_pct", 0) >= 25:
        recs.append(
            {
                "id": "IMP001",
                "problem": "WRONG_DIRECTION",
                "evidence": f"{pm.get('wrong_direction_pct')}% of losing trades",
                "features_involved": ["structure_bias", "trend_regime", "btc_context"],
                "recommended_change": "Add higher-timeframe direction filter and BTC-conflict no-trade rule",
                "expected_purpose": "Reduce counter-trend entries",
                "risk": "May reduce trade frequency significantly",
                "validation_required": "60D + 1Y walk-forward, multi-coin",
                "priority": "HIGH",
                "status": "candidate",
            }
        )
    if pm.get("sl_too_tight_pct", 0) >= 15:
        recs.append(
            {
                "id": "IMP002",
                "problem": "SL_TOO_TIGHT",
                "evidence": f"{pm.get('sl_too_tight_pct')}% of losses exited within 2 bars",
                "features_involved": ["atr", "last_swing_low", "last_swing_high"],
                "recommended_change": "Test structure swing SL vs ATR*1.5; measure subsequent TP hit rate after stop",
                "expected_purpose": "Reduce noise stops",
                "risk": "Wider SL increases R risk and may worsen expectancy if direction wrong",
                "validation_required": "Counterfactual SL study on historical losers",
                "priority": "HIGH",
                "status": "candidate",
            }
        )
    if pm.get("unknown_loss_pct", 0) >= 15:
        recs.append(
            {
                "id": "IMP003",
                "problem": "UNKNOWN_LOSSES",
                "evidence": f"{pm.get('unknown_loss_pct')}% losses lack clear attribution",
                "features_involved": [],
                "recommended_change": "Cluster UNKNOWN pre-trade feature vectors; search for hidden common factors",
                "expected_purpose": "Discover missing information or new failure modes",
                "risk": "Low — research only",
                "validation_required": "Feature clustering + 60D re-test",
                "priority": "MEDIUM",
                "status": "candidate",
            }
        )
    if stage_class in ("OVERFIT_SUSPECTED", "FAILED", "WEAK"):
        recs.append(
            {
                "id": "IMP004",
                "problem": "EDGE_FRAGILITY",
                "evidence": f"Stage classification={stage_class}",
                "features_involved": [],
                "recommended_change": "Do not deploy current candidate rules; tighten no-trade filters and re-run discovery with stricter OOS gates",
                "expected_purpose": "Prevent false confidence deployment",
                "risk": "None (conservative)",
                "validation_required": "Full 8D→60D→1Y restart with higher sample thresholds",
                "priority": "CRITICAL",
                "status": "candidate",
            }
        )
    if not recs:
        recs.append(
            {
                "id": "IMP000",
                "problem": "INSUFFICIENT_EVIDENCE",
                "evidence": "No dominant failure mode above thresholds",
                "recommended_change": "Increase sample (more coins / longer history) before structural AI changes",
                "validation_required": "Re-run with full universe and 1Y window",
                "priority": "MEDIUM",
                "status": "candidate",
            }
        )
    return recs
