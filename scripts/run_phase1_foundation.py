#!/usr/bin/env python3
"""
PHASE 1 Foundation — Steps 1–5 (architecture freeze)

  Step 1  Data Availability Engine
  Step 2  Maximum Historical Downloader
  Step 3  Live L2 + Trades Collector (optional short sample)
  Step 4  25-coin × timeframes dataset
  Step 5  Data Quality Gate

Usage:
  PYTHONPATH=. python3 scripts/run_phase1_foundation.py --days 30
  PYTHONPATH=. python3 scripts/run_phase1_foundation.py --days 7 --quick
  PYTHONPATH=. python3 scripts/run_phase1_foundation.py --days 60 --coins BTCUSD,ETHUSD,SOLUSD
  PYTHONPATH=. python3 scripts/run_phase1_foundation.py --live-sample 3   # 3 live polls then stop

Does NOT run strategy discovery. Data → quality only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.availability_engine import (
    ALL_TIMEFRAMES,
    DERIV_TIMEFRAMES,
    PRIMARY_TIMEFRAMES,
    PREFERRED_UNIVERSE,
    DataAvailabilityEngine,
)
from src.data.delta_client import DeltaPublicClient, INTERVAL_MS, INTERVAL_SEC
from src.data.quality_gate import DataQualityGate
from src.utils.logging_setup import setup_logger


def download_one(client: DeltaPublicClient, symbol: str, resolution: str, days: int, out_dir: Path) -> dict:
    end = int(time.time())
    start = end - days * 86400
    safe = symbol.replace(":", "_")
    path = out_dir / f"{safe}_{resolution}.csv"
    try:
        if resolution not in INTERVAL_SEC:
            return {"symbol": symbol, "resolution": resolution, "rows": 0, "status": "UNSUPPORTED_TF"}
        df = client.fetch_candles_range(symbol, resolution, start, end)
        if df is None or df.empty:
            return {"symbol": symbol, "resolution": resolution, "rows": 0, "status": "EMPTY", "path": str(path)}
        path.parent.mkdir(parents=True, exist_ok=True)
        # drop exact duplicate timestamps
        df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
        df.to_csv(path, index=False)
        return {
            "symbol": symbol,
            "resolution": resolution,
            "rows": len(df),
            "status": "OK",
            "first": int(df["open_time"].iloc[0]),
            "last": int(df["open_time"].iloc[-1]),
            "path": str(path),
        }
    except Exception as e:
        return {"symbol": symbol, "resolution": resolution, "rows": 0, "status": "ERROR", "error": str(e)[:180]}


def run_live_sample(symbols: list[str], n_iters: int, interval: float, out_root: Path) -> dict:
    from src.data.delta_auth import DeltaPublicExtra
    import json as _json

    pub = DeltaPublicExtra()
    base = out_root / "live"
    base.mkdir(parents=True, exist_ok=True)
    counts = {s: 0 for s in symbols}
    for i in range(n_iters):
        ts = datetime.now(timezone.utc).isoformat()
        for sym in symbols:
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            d = base / sym
            d.mkdir(parents=True, exist_ok=True)
            imb = pub.orderbook_imbalance(sym, levels=15)
            imb.update({"ts": ts, "symbol": sym})
            with open(d / f"orderbook_{day}.jsonl", "a", encoding="utf-8") as f:
                f.write(_json.dumps(imb, default=str) + "\n")
            cvd = pub.cvd_proxy_from_recent_trades(sym)
            cvd.update({"ts": ts, "symbol": sym})
            with open(d / f"trades_{day}.jsonl", "a", encoding="utf-8") as f:
                f.write(_json.dumps(cvd, default=str) + "\n")
            counts[sym] += 1
            logger.info(f"live {sym} imb={imb.get('imbalance')} delta={cvd.get('delta')}")
        if i < n_iters - 1:
            time.sleep(interval)
    return {"status": "LIVE_COLLECT_ONLY", "iters": n_iters, "counts": counts, "note": "Continue with live_collector.py for real history"}


def main():
    parser = argparse.ArgumentParser(description="TradAI Phase 1 Steps 1–5")
    parser.add_argument("--config", default=str(ROOT / "config" / "default.yaml"))
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--coins", default=None, help="Comma list override")
    parser.add_argument("--timeframes", default=None, help="Comma list; default PRIMARY")
    parser.add_argument("--all-tfs", action="store_true", help="Use full ALL_TIMEFRAMES (heavy)")
    parser.add_argument("--quick", action="store_true", help="3 coins, primary TFs only")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--live-sample", type=int, default=0, help="N live polls (0=skip)")
    parser.add_argument("--live-interval", type=float, default=8.0)
    args = parser.parse_args()

    setup_logger("INFO", str(ROOT / "logs" / "phase1_foundation.log"))
    cfg_path = Path(args.config)
    cfg = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    base_url = cfg.get("delta", {}).get("base_url", "https://api.india.delta.exchange")
    client = DeltaPublicClient(base_url=base_url)
    processed = ROOT / "data" / "processed"
    raw = ROOT / "data" / "raw"
    processed.mkdir(parents=True, exist_ok=True)

    # ---------- Step 1 ----------
    logger.info("=== STEP 1: Data Availability Engine ===")
    avail = DataAvailabilityEngine(client, processed)
    step1 = avail.run()
    universe = step1["universe"]["research_universe"]

    if args.quick:
        universe = [s for s in ["BTCUSD", "ETHUSD", "SOLUSD"] if s in universe] or universe[:3]
    if args.coins:
        universe = [c.strip() for c in args.coins.split(",")]

    if args.timeframes:
        tfs = [t.strip() for t in args.timeframes.split(",")]
    elif args.all_tfs:
        tfs = list(ALL_TIMEFRAMES)
    else:
        tfs = list(PRIMARY_TIMEFRAMES)

    logger.info(f"Research universe ({len(universe)}): {universe}")
    logger.info(f"Timeframes: {tfs}")

    download_log: list[dict] = []

    # ---------- Step 2 + 4 ----------
    if not args.skip_download:
        logger.info("=== STEP 2/4: Maximum Historical Download ===")
        for sym in universe:
            logger.info(f"--- {sym} ---")
            # OHLCV
            for tf in tfs:
                r = download_one(client, sym, tf, args.days, raw / "klines")
                download_log.append(r)
                logger.info(f"  OHLCV {tf}: {r.get('rows')} {r.get('status')}")
            # OI / FUNDING / MARK on deriv TFs
            for dtf in DERIV_TIMEFRAMES:
                if dtf not in tfs and not args.all_tfs:
                    # still fetch 1h deriv if primary includes 1h
                    if dtf != "1h":
                        continue
                for prefix in ("OI", "FUNDING", "MARK"):
                    r = download_one(client, f"{prefix}:{sym}", dtf if dtf in INTERVAL_SEC else "1h", args.days, raw / prefix.lower())
                    download_log.append(r)
                    logger.info(f"  {prefix} {dtf}: {r.get('rows')} {r.get('status')}")

        dl_path = processed / "download_manifest.json"
        dl_path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "days": args.days,
                    "universe": universe,
                    "timeframes": tfs,
                    "series": download_log,
                    "n_ok": sum(1 for x in download_log if x.get("status") == "OK"),
                    "n_fail": sum(1 for x in download_log if x.get("status") not in ("OK",)),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info(f"Download manifest → {dl_path}")
    else:
        logger.info("Skip download")

    # ---------- Step 3 ----------
    live_report = {}
    if args.live_sample > 0:
        logger.info("=== STEP 3: Live collector sample ===")
        core = [s for s in ["BTCUSD", "ETHUSD", "SOLUSD"] if s in universe] or universe[:2]
        live_report = run_live_sample(core, args.live_sample, args.live_interval, raw)
        (processed / "live_sample_report.json").write_text(json.dumps(live_report, indent=2), encoding="utf-8")
    else:
        logger.info("Step 3 skipped (use --live-sample N or scripts/live_collector.py)")

    # ---------- Step 5 ----------
    logger.info("=== STEP 5: Data Quality Gate ===")
    gate = DataQualityGate(raw, processed)
    gate_report = gate.run()

    # Final Phase-1 summary
    summary = {
        "phase": "PHASE_1_DATA_FOUNDATION",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "steps": {
            "1_availability": "DONE",
            "2_historical_download": "DONE" if not args.skip_download else "SKIPPED",
            "3_live_collector": "SAMPLE" if live_report else "NOT_RUN_USE_live_collector",
            "4_dataset": f"{len(universe)} coins × {tfs}",
            "5_quality_gate": "PASS" if gate_report.get("gate_ok_for_discovery") else "REVIEW",
        },
        "universe": universe,
        "timeframes": tfs,
        "quality": {
            "n_pass": gate_report.get("n_pass"),
            "n_warn": gate_report.get("n_warn"),
            "n_fail": gate_report.get("n_fail"),
            "gate_ok_for_discovery": gate_report.get("gate_ok_for_discovery"),
        },
        "availability_tags": step1["catalog"]["by_status"],
        "architecture_rule": "No discovery/backtest until gate_ok_for_discovery or explicit WARN accept",
        "next_phase": "PHASE_2 live collectors continuous + PHASE_3 deep structure (only on PASS data)",
    }
    out = processed / "phase1_summary.json"
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    logger.info(f"PHASE 1 summary → {out}")
    print(json.dumps(summary, indent=2, default=str))
    return 0 if gate_report.get("gate_ok_for_discovery") or gate_report.get("n_pass", 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
