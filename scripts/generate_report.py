#!/usr/bin/env python3
"""Re-generate human report from an existing run_id raw results."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.reports.generator import (
    default_improvement_recommendations,
    generate_full_report,
    write_improvement_plan,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--outputs", default=str(ROOT / "outputs"))
    args = parser.parse_args()

    raw_path = Path(args.outputs) / f"{args.run_id}_raw_results.json"
    if not raw_path.exists():
        print(f"Missing {raw_path}")
        sys.exit(1)
    with open(raw_path, encoding="utf-8") as f:
        all_results = json.load(f)

    ok = [r for r in all_results if r.get("status") == "OK"]
    agg_pm = {"n_trades": 0, "n_wins": 0, "n_losses": 0, "wrong_direction_pct": 0, "unknown_loss_pct": 0, "sl_too_tight_pct": 0}
    if ok:
        for k in ("wrong_direction_pct", "unknown_loss_pct", "sl_too_tight_pct"):
            vals = [r.get("postmortem", {}).get(k, 0) for r in ok]
            agg_pm[k] = round(sum(vals) / len(vals), 1)
        agg_pm["n_trades"] = sum(r.get("postmortem", {}).get("n_trades", 0) for r in ok)

    classes = [r.get("comparison", {}).get("classification", "UNKNOWN") for r in ok]
    stage_class = max(set(classes), key=classes.count) if classes else "UNKNOWN"

    generate_full_report(
        run_id=args.run_id,
        universe_summary={"n_ok": len(ok)},
        stage_results={
            "discoveries": ok,
            "comparison": {"dominant_class": stage_class},
        },
        postmortem_summary=agg_pm,
        data_gaps=[],
        robust_features=[r for r in ok if r.get("comparison", {}).get("classification") == "ROBUST"],
        failed_features=[r for r in ok if r.get("comparison", {}).get("classification") in ("FAILED", "OVERFIT_SUSPECTED")],
        overfit_notes=[],
        output_dir=args.outputs,
    )
    write_improvement_plan(args.run_id, default_improvement_recommendations(agg_pm, stage_class), args.outputs)
    print("Report regenerated.")


if __name__ == "__main__":
    main()
