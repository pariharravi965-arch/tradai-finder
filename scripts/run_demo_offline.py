#!/usr/bin/env python3
"""Offline demo — synthetic data so the full research pipeline can be exercised
without Binance connectivity (e.g. restricted regions).

This does NOT replace real data research. Labels all results as SYNTHETIC.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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


def make_synthetic(symbol: str, timeframe: str, n: int = 2000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # Random walk with mild trend regimes
    rets = rng.normal(0.0001, 0.008, n)
    close = 50_000 * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0.001, 0.01, n))
    low = close * (1 - rng.uniform(0.001, 0.01, n))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    vol = rng.uniform(100, 5000, n)
    t0 = 1_700_000_000_000
    step = {"1h": 3_600_000, "4h": 14_400_000, "15m": 900_000}.get(timeframe, 3_600_000)
    return pd.DataFrame(
        {
            "open_time": [t0 + i * step for i in range(n)],
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "quote_volume": vol * close,
            "n_trades": rng.integers(50, 2000, n),
            "taker_buy_base": vol * rng.uniform(0.3, 0.7, n),
            "taker_buy_quote": vol * close * 0.5,
        }
    )


def main() -> None:
    with open(ROOT / "config" / "default.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    setup_logger("INFO", str(ROOT / "logs" / "demo.log"))
    run_id = get_run_id("demo")
    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)

    fe = FeatureEngine(cfg)
    symbols = ["BTCUSDT", "ETHUSDT"]
    tfs = ["1h", "4h"]
    all_results = []

    for symbol in symbols:
        for tf in tfs:
            df = make_synthetic(symbol, tf, n=1500, seed=hash(symbol + tf) % 10_000)
            feat = fe.transform(df, symbol, tf, funding=None, oi=None, oi_status="DATA_MISSING")
            feat = simple_candidate_signals(feat)

            def slice_days(d, days):
                end = int(d["open_time"].iloc[-1])
                step = {"1h": 3_600_000, "4h": 14_400_000}.get(tf, 3_600_000)
                start = end - days * 24 * 3600 * 1000
                return d[d["open_time"] >= start].reset_index(drop=True)

            d8, d60, d1y = slice_days(feat, 8), slice_days(feat, 60), slice_days(feat, 365)
            bt = {"rr": 2.0, "sl_atr_mult": 1.2, "fee_rate": 0.0004, "slippage_bps": 2.0}
            t8 = run_vector_backtest(d8, symbol, tf, **bt)
            t60 = run_vector_backtest(d60, symbol, tf, **bt)
            t1y = run_vector_backtest(d1y, symbol, tf, **bt)
            m8, m60, m1y = trades_to_metrics(t8), trades_to_metrics(t60), trades_to_metrics(t1y)
            comparison = compare_windows(m8, m60, m1y)
            mid = len(d60) // 2
            overfit = assess_overfit_risk(
                run_vector_backtest(d60.iloc[:mid], symbol, tf, **bt),
                run_vector_backtest(d60.iloc[mid:], symbol, tf, **bt),
            )
            pm = summarize_postmortem(postmortem_trades(t60))
            all_results.append(
                {
                    "symbol": symbol,
                    "timeframe": tf,
                    "status": "OK",
                    "synthetic": True,
                    "metrics_8d": m8,
                    "metrics_60d": m60,
                    "metrics_1y": m1y,
                    "comparison": comparison,
                    "overfit": overfit,
                    "postmortem": pm,
                    "oi_status": "DATA_MISSING",
                }
            )
            logger.info(f"{symbol} {tf}: 60d trades={m60['n_trades']} WR={m60.get('win_rate')} class={comparison['classification']}")

    ok = [r for r in all_results if r["status"] == "OK"]
    agg_pm = {"n_trades": 0, "n_wins": 0, "n_losses": 0, "wrong_direction_pct": 0, "unknown_loss_pct": 0, "sl_too_tight_pct": 0}
    for k in ("wrong_direction_pct", "unknown_loss_pct", "sl_too_tight_pct"):
        vals = [r["postmortem"].get(k, 0) for r in ok]
        agg_pm[k] = round(sum(vals) / max(len(vals), 1), 1)
    agg_pm["n_trades"] = sum(r["postmortem"].get("n_trades", 0) for r in ok)
    agg_pm["n_wins"] = sum(r["postmortem"].get("n_wins", 0) for r in ok)
    agg_pm["n_losses"] = sum(r["postmortem"].get("n_losses", 0) for r in ok)

    classes = [r["comparison"]["classification"] for r in ok]
    stage_class = max(set(classes), key=classes.count) if classes else "UNKNOWN"

    generate_full_report(
        run_id=run_id,
        universe_summary={"coins": symbols, "timeframes": tfs, "mode": "SYNTHETIC_DEMO"},
        stage_results={
            "discoveries": [
                {
                    "symbol": r["symbol"],
                    "timeframe": r["timeframe"],
                    "classification": r["comparison"]["classification"],
                    "metrics_60d": r["metrics_60d"],
                }
                for r in ok
            ],
            "comparison": {"dominant_class": stage_class},
        },
        postmortem_summary=agg_pm,
        data_gaps=[
            {"factor": "open_interest_history", "status": "DATA_MISSING"},
            {"factor": "historical_order_book", "status": "DATA_MISSING"},
            {"factor": "liquidation_history", "status": "DATA_MISSING"},
            {"factor": "NOTE", "status": "SYNTHETIC", "note": "This run used synthetic prices — not real market evidence"},
        ],
        robust_features=[r for r in ok if r["comparison"]["classification"] == "ROBUST"],
        failed_features=[r for r in ok if r["comparison"]["classification"] in ("FAILED", "OVERFIT_SUSPECTED")],
        overfit_notes=[r["overfit"] for r in ok],
        output_dir=out_dir,
    )
    write_improvement_plan(run_id, default_improvement_recommendations(agg_pm, stage_class), out_dir)
    with open(out_dir / f"{run_id}_raw_results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, default=str)

    print(f"\nDEMO RUN_ID={run_id} (SYNTHETIC DATA)")
    print(f"Dominant class: {stage_class}")
    print(f"Post-mortem: WRONG_DIR≈{agg_pm['wrong_direction_pct']}% UNKNOWN≈{agg_pm['unknown_loss_pct']}% SL_TIGHT≈{agg_pm['sl_too_tight_pct']}%")
    print(f"Reports in: {out_dir}")


if __name__ == "__main__":
    main()
