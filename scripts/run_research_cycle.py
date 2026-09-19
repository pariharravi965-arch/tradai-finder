#!/usr/bin/env python3
"""Full research cycle — multi-factor, FDR, thresholds, CF, wrong-dir, AI loop, micro wire."""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import yaml
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ai_loop.closed_loop import LearningLoop
from src.ai_loop.knowledge import build_ai_improvement_plan, write_plan
from src.counterfactual.full_cf import counterfactual_matrix, summarize_cf_matrix
from src.data.quality_gate import DataQualityGate
from src.discovery.engine import discover_from_dataframe, no_trade_factor_scan
from src.discovery.multi_factor import scan_interactions
from src.discovery.threshold_search import search_thresholds
from src.features.base import FeatureEngine
from src.microstructure.live_features import merge_live_onto_ohlcv
from src.postmortem.wrong_direction import batch_wrong_direction
from src.registry.store import RegistryStore
from src.validation.fdr_stats import apply_fdr_to_results, bootstrap_ci, permutation_null_mean
from src.validation.pipeline_8_60_1y import evaluate_fixed_hypothesis, split_by_time
from src.utils.logging_setup import setup_logger

def _fwd_metrics(part, signal_col="signal_long"):
    if part.empty or "close" not in part.columns or signal_col not in part.columns:
        return {"expectancy": None, "status": "NO_DATA"}
    sig = part[signal_col].fillna(0).astype(bool)
    fwd = part["close"].shift(-3) / part["close"] - 1.0
    sub = fwd[sig & fwd.notna()]
    if len(sub) < 5:
        return {"expectancy": None, "n_trades_proxy": len(sub), "status": "LOW_SAMPLE"}
    ci = bootstrap_ci(sub.values.astype(float))
    return {"expectancy": float(sub.mean()), "hit_rate": float((sub > 0).mean()),
            "n_trades_proxy": len(sub), "ci_low": ci["ci_low"], "ci_high": ci["ci_high"], "status": "OK"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config" / "default.yaml"))
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--data-dir", default=str(ROOT / "data" / "raw" / "klines"))
    parser.add_argument("--skip-interactions", action="store_true")
    parser.add_argument("--skip-cf", action="store_true")
    args = parser.parse_args()
    setup_logger("INFO", str(ROOT / "logs" / "research_cycle.log"))
    cfg = {}
    cp = Path(args.config)
    if cp.exists():
        with open(cp, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    DataQualityGate(ROOT / "data" / "raw", ROOT / "data" / "processed").run()
    path = Path(args.data_dir) / f"{args.symbol}_{args.timeframe}.parquet"
    if not path.exists():
        print(json.dumps({"error": "NO_DATA_FILE", "path": str(path)})); return 1
    df = pd.read_parquet(path)
    feat = FeatureEngine(cfg).transform(df, args.symbol, args.timeframe)
    live_root = ROOT / "data" / "raw" / "live"
    if live_root.exists():
        feat = merge_live_onto_ohlcv(feat, live_root, args.symbol)
    registry = RegistryStore(ROOT / "data" / "registry")
    windows = split_by_time(feat, discovery_days=8, validation_days=60, robustness_days=365)
    factor_cols = [c for c in feat.columns if c.startswith("sig_") or c in (
        "bos_up","bos_dn","choch_up","sweep_low","sweep_high","fvg_bull","mtf_align","mtf_conflict",
        "equal_low","equal_high","funding_extreme_pos","oi_shock","no_trade_candidate")]
    disc = discover_from_dataframe(windows.discovery, factor_cols, min_sample=12, registry=registry,
                                   coin=args.symbol, timeframe=args.timeframe, training_period="8d")
    disc = apply_fdr_to_results(disc, p_key="p_value", alpha=0.15)
    interactions = [] if args.skip_interactions or windows.discovery.empty else scan_interactions(
        windows.discovery, max_order=3, min_sample=12, max_combos=1500)
    interactions = apply_fdr_to_results(interactions, p_key="p_value", alpha=0.15)
    thresholds = search_thresholds(windows.discovery, min_sample=12) if not windows.discovery.empty else []
    no_trade = no_trade_factor_scan(windows.discovery, min_sample=10)
    perm_report = {}
    if disc and "close" in windows.discovery.columns:
        top = next((d for d in disc if d.get("fdr_pass")), disc[0])
        if top and top.get("factor") in windows.discovery.columns:
            fwd = windows.discovery["close"].shift(-3) / windows.discovery["close"] - 1.0
            mask = windows.discovery[top["factor"]].fillna(0).astype(bool).values
            perm_report = permutation_null_mean(fwd.values.astype(float), mask, n_perm=100)
    val_results = []
    if "signal_long" in feat.columns:
        val_results.append(evaluate_fixed_hypothesis(windows, "signal_long", lambda p: _fwd_metrics(p, "signal_long")))
    cf_summary, wd_summary = {}, {}
    if not args.skip_cf and not windows.discovery.empty and "atr" in windows.discovery.columns:
        disc_df = windows.discovery.reset_index(drop=True)
        sl = disc_df.get("signal_long", pd.Series(dtype=float))
        sig_idx = disc_df.index[sl.fillna(0).astype(bool)].tolist()[:20]
        if sig_idx:
            dirs = [1]*len(sig_idx)
            cf_summary = summarize_cf_matrix(counterfactual_matrix(disc_df, sig_idx, dirs))
            wd_summary = batch_wrong_direction(disc_df, list(zip(sig_idx, dirs)))
    gaps = []
    cat_path = ROOT / "data" / "processed" / "data_availability_catalog.json"
    if cat_path.exists():
        cat = json.loads(cat_path.read_text())
        gaps = list(cat.get("external_required", [])) + list(cat.get("blocked_until_live_history", []))
    plan = build_ai_improvement_plan(disc, val_results,
        {"reason_counts": dict(wd_summary.get("verdict_counts", {})),
         "top_reason_tags": dict(wd_summary.get("top_reason_tags", []))},
        gaps, registry=registry)
    plan.update({"interactions_top": interactions[:10], "thresholds_top": [t for t in thresholds if t.get("status")=="CANDIDATE"][:10],
                 "permutation": perm_report, "counterfactual": cf_summary,
                 "wrong_direction": {"verdict_counts": wd_summary.get("verdict_counts"),
                                     "top_reason_tags": wd_summary.get("top_reason_tags")}})
    loop = LearningLoop(registry, ROOT / "data" / "registry" / "experiment_queue.json")
    rejected = [d.get("factor") for d in disc if d.get("status") in ("REJECTED","REJECTED_FDR","LOW_SAMPLE")]
    queue = loop.from_postmortem({"top_reason_tags": dict(wd_summary.get("top_reason_tags", []) or [])},
                                  None, [x for x in rejected if x], gaps)
    plan["experiment_queue_size"] = len(queue)
    out_dir = ROOT / "outputs"; stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    plan_path = write_plan(plan, out_dir / f"ai_plan_{args.symbol}_{args.timeframe}_{stamp}.json")
    summary = {
        "symbol": args.symbol, "timeframe": args.timeframe,
        "n_discovery_bars": len(windows.discovery), "n_validation_bars": len(windows.validation),
        "n_robustness_bars": len(windows.robustness),
        "n_single_discovered_fdr": sum(1 for d in disc if d.get("fdr_pass")),
        "n_interactions_fdr": sum(1 for d in interactions if d.get("fdr_pass")),
        "n_threshold_candidates": sum(1 for t in thresholds if t.get("status")=="CANDIDATE"),
        "validation": val_results, "cf": cf_summary,
        "wrong_direction_verdicts": wd_summary.get("verdict_counts"),
        "ai_plan": str(plan_path), "experiment_queue": len(queue),
        "honesty": "8D discovers+FDR; 60D/1Y fixed; no fabricated data",
    }
    print(json.dumps(summary, indent=2, default=str))
    (out_dir / f"research_cycle_{args.symbol}_{args.timeframe}_{stamp}.json").write_text(
        json.dumps({**summary, "discovery": disc[:30], "interactions": interactions[:20],
                    "thresholds": thresholds[:20], "no_trade": no_trade}, indent=2, default=str), encoding="utf-8")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
