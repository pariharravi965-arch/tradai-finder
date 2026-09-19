#!/usr/bin/env python3
"""Main research pipeline: features → signals → 8D/60D/1Y → postmortem → report."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.ingest import DataIngester
from src.engines.backtest import run_vector_backtest
from src.engines.signals import simple_candidate_signals
from src.features.base import FeatureEngine
from src.postmortem.analyzer import postmortem_trades, summarize_postmortem
from src.reports.generator import (
    default_improvement_recommendations,
    generate_full_report,
    write_improvement_plan,
)
from src.utils.logging_setup import setup_logger
from src.utils.versioning import RunContext, get_run_id
from src.validation.metrics import compare_windows, trades_to_metrics
from src.validation.overfit import assess_overfit_risk
from src.counterfactual.cf_engine import counterfactual_sl_grid, summarize_counterfactuals
from src.postmortem.unknown_cluster import cluster_unknown_losses
from src.laws.research_laws import apply_laws_to_finding, laws_report_block
from src.data.data_gaps_registry import DATA_GAPS, DATA_AVAILABLE_DELTA



def slice_last_days(df: pd.DataFrame, days: int) -> pd.DataFrame:
    if df.empty:
        return df
    end = int(df["open_time"].iloc[-1])
    start = end - days * 86_400_000
    return df[df["open_time"] >= start].reset_index(drop=True)


def process_symbol(
    ingester: DataIngester,
    fe: FeatureEngine,
    symbol: str,
    timeframe: str,
    days: int,
    cost_cfg: dict,
) -> dict:
    df, quality = ingester.download_klines(symbol, timeframe, days=days)
    if df.empty or quality.status == "UNUSABLE":
        return {"symbol": symbol, "timeframe": timeframe, "status": "SKIP", "quality": quality.to_dict()}

    funding = ingester.download_funding(symbol, days=days)
    oi, oi_status = ingester.download_oi_hist(symbol, period="1h", days=min(30, days))

    # HTF for MTF context
    htf_df = None
    htf_label = "1h"
    if timeframe in ("5m", "15m", "30m"):
        htf_label = "1h"
        try:
            hdf, _ = ingester.download_klines(symbol, "1h", days=days)
            if not hdf.empty:
                htf_df = fe.transform(hdf, symbol, "1h", funding=funding, oi=oi, oi_status=oi_status)
        except Exception:
            htf_df = None
    elif timeframe == "1h":
        htf_label = "4h"
        try:
            hdf, _ = ingester.download_klines(symbol, "4h", days=days)
            if not hdf.empty:
                htf_df = fe.transform(hdf, symbol, "4h", funding=funding, oi=oi, oi_status=oi_status)
        except Exception:
            htf_df = None

    feat = fe.transform(df, symbol, timeframe, funding=funding, oi=oi, oi_status=oi_status, htf_df=htf_df, htf_label=htf_label)
    # hypotheses already inside transform; keep simple signals as extra
    feat = simple_candidate_signals(feat)

    # Stage windows
    d8 = slice_last_days(feat, 8)
    d60 = slice_last_days(feat, 60)
    d1y = slice_last_days(feat, min(365, days))

    bt_kwargs = {
        "rr": 2.0,
        "sl_atr_mult": 1.2,
        "fee_rate": cost_cfg.get("default_fee", 0.0004),
        "slippage_bps": cost_cfg.get("slippage_bps", 2.0),
    }
    t8 = run_vector_backtest(d8, symbol, timeframe, **bt_kwargs)
    t60 = run_vector_backtest(d60, symbol, timeframe, **bt_kwargs)
    t1y = run_vector_backtest(d1y, symbol, timeframe, **bt_kwargs)

    m8 = trades_to_metrics(t8)
    m60 = trades_to_metrics(t60)
    m1y = trades_to_metrics(t1y)
    comparison = compare_windows(m8, m60, m1y)

    # Simple train/test split on 60d for overfit hint
    mid = len(d60) // 2
    train_tr = run_vector_backtest(d60.iloc[:mid], symbol, timeframe, **bt_kwargs)
    test_tr = run_vector_backtest(d60.iloc[mid:], symbol, timeframe, **bt_kwargs)
    overfit = assess_overfit_risk(train_tr, test_tr, n_params=4, n_coins=1)

    pm = postmortem_trades(t60)
    pm_sum = summarize_postmortem(pm)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "status": "OK",
        "quality": quality.to_dict(),
        "oi_status": oi_status,
        "metrics_8d": m8,
        "metrics_60d": m60,
        "metrics_1y": m1y,
        "comparison": comparison,
        "overfit": overfit,
        "postmortem": pm_sum,
        "n_features": len(feat.columns),
        "trades_60d": [
            {
                "trade_id": t.trade_id,
                "direction": t.direction,
                "net_r": t.net_r,
                "exit_reason": t.exit_reason,
                "regime": t.regime,
                "signal_reason": t.signal_reason,
            }
            for t in t60
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config" / "default.yaml"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--coins", default="BTC,ETH,SOL")
    parser.add_argument("--timeframes", default="1h,4h")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--stage", default="all", choices=["all", "a", "b", "c"])
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    setup_logger(cfg.get("logging", {}).get("level", "INFO"), str(ROOT / cfg.get("logging", {}).get("file", "logs/finder.log")))
    run_id = args.run_id or get_run_id("finder")
    ctx = RunContext(
        run_id=run_id,
        finder_version=cfg.get("finder_version", "1.0.0"),
        data_version="delta_india_public_v1",
        feature_version="1.0",
        strategy_version="candidate_v1",
        law_version=cfg.get("law_version", "1.0"),
        config_snapshot=cfg,
    )
    out_dir = ROOT / cfg.get("paths", {}).get("outputs", "outputs")
    ctx.save(out_dir / f"{run_id}_context.json")

    coins = [c.strip() for c in args.coins.split(",")]
    tfs = [t.strip() for t in args.timeframes.split(",")]
    ingester = DataIngester(cfg)
    fe = FeatureEngine(cfg)
    cost_cfg = cfg.get("costs", {})

    all_results = []
    data_gaps = []
    for coin in coins:
        symbol = ingester.resolve_symbol(coin)
        for tf in tfs:
            logger.info(f"=== {symbol} {tf} ===")
            try:
                res = process_symbol(ingester, fe, symbol, tf, args.days, cost_cfg)
                all_results.append(res)
                if res.get("oi_status") in ("DATA_MISSING", "DATA_LIMITED"):
                    data_gaps.append(
                        {
                            "factor": "open_interest_history",
                            "symbol": symbol,
                            "status": res["oi_status"],
                            "note": "Public API OI hist depth limited; do not fabricate",
                        }
                    )
            except Exception as e:
                logger.exception(f"Failed {symbol} {tf}: {e}")
                all_results.append({"symbol": symbol, "timeframe": tf, "status": "ERROR", "error": str(e)})

    # Aggregate postmortem
    agg_pm = {"n_trades": 0, "n_wins": 0, "n_losses": 0, "loss_reason_pct": {}, "wrong_direction_pct": 0, "unknown_loss_pct": 0, "sl_too_tight_pct": 0}
    classifications = []
    for r in all_results:
        if r.get("status") != "OK":
            continue
        pm = r.get("postmortem", {})
        agg_pm["n_trades"] += pm.get("n_trades", 0)
        agg_pm["n_wins"] += pm.get("n_wins", 0)
        agg_pm["n_losses"] += pm.get("n_losses", 0)
        classifications.append(r.get("comparison", {}).get("classification", "UNKNOWN"))
        for k in ("wrong_direction_pct", "unknown_loss_pct", "sl_too_tight_pct"):
            # simple average later
            pass

    # Average key pcts
    ok_results = [r for r in all_results if r.get("status") == "OK" and r.get("postmortem")]
    if ok_results:
        for k in ("wrong_direction_pct", "unknown_loss_pct", "sl_too_tight_pct"):
            vals = [r["postmortem"].get(k, 0) for r in ok_results]
            agg_pm[k] = round(sum(vals) / len(vals), 1)

    stage_class = max(set(classifications), key=classifications.count) if classifications else "UNKNOWN"
    stage_results = {
        "discoveries": [
            {
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                "classification": r.get("comparison", {}).get("classification"),
                "metrics_60d": r.get("metrics_60d"),
                "degradation_8_to_60": r.get("comparison", {}).get("degradation_8_to_60_pct"),
            }
            for r in ok_results
        ],
        "comparison": {"dominant_class": stage_class},
    }

    robust = [d for d in stage_results["discoveries"] if d.get("classification") == "ROBUST"]
    failed = [d for d in stage_results["discoveries"] if d.get("classification") in ("FAILED", "OVERFIT_SUSPECTED")]

    # Always flag order-book / liquidation as missing
    data_gaps.extend(
        [
            {"factor": "historical_order_book_depth", "status": "DATA_MISSING", "note": "Not available via public REST historically"},
            {"factor": "deep_liquidation_history", "status": "DATA_MISSING", "note": "Tick liquidations not on public historical API"},
        ]
    )

    report_path = generate_full_report(
        run_id=run_id,
        universe_summary={"coins": coins, "timeframes": tfs, "days": args.days, "n_ok": len(ok_results)},
        stage_results=stage_results,
        postmortem_summary=agg_pm,
        data_gaps=data_gaps,
        robust_features=robust,
        failed_features=failed,
        overfit_notes=[r.get("overfit") for r in ok_results if r.get("overfit")],
        output_dir=out_dir,
    )

    recs = default_improvement_recommendations(agg_pm, stage_class)
    write_improvement_plan(run_id, recs, out_dir)

    # Save raw results
    raw_path = out_dir / f"{run_id}_raw_results.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, default=str)
    logger.info(f"Raw results → {raw_path}")
    logger.info(f"Done. Report: {report_path}")
    print(f"\nRUN_ID={run_id}")
    print(f"Dominant classification: {stage_class}")
    print(f"WRONG_DIRECTION≈{agg_pm.get('wrong_direction_pct')}% | UNKNOWN≈{agg_pm.get('unknown_loss_pct')}% | SL_TOO_TIGHT≈{agg_pm.get('sl_too_tight_pct')}%")


if __name__ == "__main__":
    main()
