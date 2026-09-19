"""Six laws as measurable research questions — not magic predictors.

Dekha Kya? → What observable evidence exists?
Sikha Kya? → What did historical data demonstrate?
Samjha Kya? → What mechanism appears consistent?
Vastavikta Kya? → What survives costs, slippage, unseen data?
Balance Kya? → Does improvement help one metric while hurting another?
Kya Sudhar Sambhav Hai? → Is there measurable room for improvement?
"""
from __future__ import annotations

from typing import Any


def apply_laws_to_finding(finding: dict) -> dict:
    """Annotate a research finding with the six-law checklist."""
    n = finding.get("n_trades") or finding.get("sample") or 0
    oos = finding.get("oos_expectancy") or finding.get("metrics_60d", {}).get("expectancy")
    ins = finding.get("is_expectancy") or finding.get("metrics_8d", {}).get("expectancy")
    costs = finding.get("costs_included", True)
    classif = finding.get("classification") or finding.get("comparison", {}).get("classification")

    dekha = {
        "question": "Dekha Kya?",
        "answer": f"Observable sample n={n}, features={finding.get('features', [])}",
        "pass": n >= 20,
    }
    sikha = {
        "question": "Sikha Kya?",
        "answer": f"In-sample expectancy={ins}, class={classif}",
        "pass": ins is not None and (ins > 0 if isinstance(ins, (int, float)) else False),
    }
    samjha = {
        "question": "Samjha Kya?",
        "answer": finding.get("mechanism") or finding.get("signal_reason") or "mechanism not stated",
        "pass": bool(finding.get("mechanism") or finding.get("signal_reason")),
    }
    vastavikta = {
        "question": "Vastavikta Kya?",
        "answer": f"OOS expectancy={oos}, costs_included={costs}, class={classif}",
        "pass": classif in ("ROBUST", "PARTIALLY_ROBUST") and costs,
    }
    balance = {
        "question": "Balance Kya?",
        "answer": finding.get("tradeoff") or "check WR vs frequency vs DD",
        "pass": True,  # informational
    }
    sudhar = {
        "question": "Kya Sudhar Sambhav Hai?",
        "answer": finding.get("improvement_hint") or "see AI improvement plan",
        "pass": classif not in ("ROBUST",),  # room if not already robust
    }

    laws = [dekha, sikha, samjha, vastavikta, balance, sudhar]
    return {
        **finding,
        "laws": laws,
        "laws_pass_count": sum(1 for x in laws if x["pass"]),
        "laws_ready_for_ai": sum(1 for x in laws if x["pass"]) >= 4 and vastavikta["pass"],
    }


def laws_report_block(findings: list[dict]) -> str:
    lines = ["## Six Laws Evaluation"]
    for f in findings[:10]:
        annotated = apply_laws_to_finding(f)
        lines.append(f"### {f.get('symbol', '?')} {f.get('timeframe', '')} — {f.get('classification', '')}")
        lines.append(f"Laws pass: {annotated['laws_pass_count']}/6 · AI-ready: {annotated['laws_ready_for_ai']}")
        for law in annotated["laws"]:
            mark = "✓" if law["pass"] else "✗"
            lines.append(f"- {mark} **{law['question']}** {law['answer']}")
    return "\n".join(lines)
