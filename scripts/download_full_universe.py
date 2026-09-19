#!/usr/bin/env python3
"""
Priority 1–10: Maximum Delta historical extraction.

Downloads for each coin:
  - OHLCV all primary timeframes
  - OI:SYMBOL historical
  - FUNDING:SYMBOL historical
  - MARK:SYMBOL historical
  - Universe verification + data-quality reports

Usage:
  PYTHONPATH=. python3 scripts/download_full_universe.py --days 60
  PYTHONPATH=. python3 scripts/download_full_universe.py --days 90 --timeframes 15m,30m,1h,4h,1d
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

from src.data.delta_client import DeltaPublicClient, INTERVAL_MS
from src.data.ingest import DataIngester
from src.data.quality import score_data_quality
from src.utils.logging_setup import setup_logger


DEFAULT_COINS = [
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BNBUSD",
    "DOGEUSD", "ADAUSD", "AVAXUSD", "LINKUSD", "DOTUSD",
    "TRXUSD", "LTCUSD", "BCHUSD", "UNIUSD", "ATOMUSD",
    "NEARUSD", "APTUSD", "SUIUSD", "FILUSD", "ARBUSD",
    "OPUSD", "AAVEUSD", "INJUSD", "WIFUSD", "TIAUSD",
]

DEFAULT_TFS = ["15m", "30m", "1h", "4h", "1d"]


def download_series(client: DeltaPublicClient, symbol: str, resolution: str, days: int, out_dir: Path):
    end = int(time.time())
    start = end - days * 86400
    path = out_dir / f"{symbol.replace(':', '_')}_{resolution}.parquet"
    try:
        df = client.fetch_candles_range(symbol, resolution, start, end)
        if df.empty:
            return {"symbol": symbol, "resolution": resolution, "rows": 0, "status": "EMPTY"}
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        q = score_data_quality(df, symbol, resolution, INTERVAL_MS.get(resolution, 3600_000))
        return {
            "symbol": symbol,
            "resolution": resolution,
            "rows": len(df),
            "status": q.status,
            "score": q.score,
            "first": int(df["open_time"].iloc[0]),
            "last": int(df["open_time"].iloc[-1]),
            "path": str(path),
        }
    except Exception as e:
        return {"symbol": symbol, "resolution": resolution, "rows": 0, "status": "ERROR", "error": str(e)[:200]}


def align_timestamps(report: dict) -> dict:
    """Check OHLCV vs MARK vs OI vs FUNDING time alignment for a coin."""
    issues = []
    by_kind = {}
    for r in report.get("series", []):
        if r.get("rows", 0) < 2:
            continue
        kind = r["symbol"].split(":")[0] if ":" in r["symbol"] else "OHLCV"
        by_kind[kind] = r
    if "OHLCV" in by_kind and "MARK" in by_kind:
        drift = abs(by_kind["OHLCV"]["last"] - by_kind["MARK"]["last"])
        if drift > 2 * 3600 * 1000:
            issues.append(f"OHLCV-MARK last drift {drift}ms")
    if "OHLCV" in by_kind and "OI" in by_kind:
        drift = abs(by_kind["OHLCV"]["last"] - by_kind["OI"]["last"])
        if drift > 6 * 3600 * 1000:
            issues.append(f"OHLCV-OI last drift {drift}ms")
    return {"alignment_ok": len(issues) == 0, "issues": issues}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config" / "default.yaml"))
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--coins", default=None)
    parser.add_argument("--timeframes", default=None)
    parser.add_argument("--skip-deriv", action="store_true", help="Skip OI/FUNDING/MARK")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    setup_logger("INFO", str(ROOT / "logs" / "download_full.log"))

    coins = [c.strip() for c in args.coins.split(",")] if args.coins else cfg.get("coins") or DEFAULT_COINS
    tfs = [t.strip() for t in args.timeframes.split(",")] if args.timeframes else DEFAULT_TFS

    client = DeltaPublicClient(base_url=cfg.get("delta", {}).get("base_url", "https://api.india.delta.exchange"))
    out_root = ROOT / "data" / "raw"
    universe = []

    # Verify products
    listed = set(client.list_perpetuals())
    logger.info(f"Delta perpetuals listed: {len(listed)}")

    for coin in coins:
        symbol = coin if coin.endswith("USD") else f"{coin}USD"
        entry = {
            "coin": coin,
            "symbol": symbol,
            "listed_on_exchange": symbol in listed if listed else None,
            "series": [],
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(f"=== {symbol} ===")

        # OHLCV all TFs
        for tf in tfs:
            r = download_series(client, symbol, tf, args.days, out_root / "klines")
            entry["series"].append(r)
            logger.info(f"  OHLCV {tf}: {r.get('rows')} {r.get('status')}")

        if not args.skip_deriv:
            # Prefer 1h for derivatives series (lighter)
            deriv_tf = "1h" if "1h" in tfs else tfs[0]
            for prefix in ("OI", "FUNDING", "MARK"):
                r = download_series(client, f"{prefix}:{symbol}", deriv_tf, args.days, out_root / prefix.lower())
                entry["series"].append(r)
                logger.info(f"  {prefix}: {r.get('rows')} {r.get('status')}")

        entry["alignment"] = align_timestamps(entry)
        # Summary quality
        ohlc_ok = sum(1 for s in entry["series"] if not str(s["symbol"]).startswith(("OI", "FUNDING", "MARK")) and s.get("rows", 0) > 0)
        entry["ohlcv_tfs_ok"] = ohlc_ok
        entry["ready"] = ohlc_ok >= max(1, len(tfs) // 2)
        universe.append(entry)

    report_path = ROOT / "data" / "processed" / "universe_full_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "timeframes": tfs,
        "n_coins": len(universe),
        "n_ready": sum(1 for u in universe if u.get("ready")),
        "coins": universe,
        "options_iv": {"status": "DATA_MISSING", "note": "Delta options history not wired; public options chain may exist but deep IV surface history is limited"},
        "spot_index": {"status": "PARTIAL", "note": "MARK series available; true spot index via MARK/index candle if exchange provides"},
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info(f"Report → {report_path}")
    print(f"\nReady coins: {summary['n_ready']}/{summary['n_coins']}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
