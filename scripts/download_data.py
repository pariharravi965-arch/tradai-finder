#!/usr/bin/env python3
"""Download OHLCV / funding / OI from Binance public API."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.ingest import DataIngester
from src.utils.logging_setup import setup_logger


def main() -> None:
    parser = argparse.ArgumentParser(description="TradAI Finder — data download")
    parser.add_argument("--config", default=str(ROOT / "config" / "default.yaml"))
    parser.add_argument("--coins", default=None, help="Comma-separated, e.g. BTC,ETH,SOL")
    parser.add_argument("--timeframes", default=None, help="Comma-separated, e.g. 15m,1h,4h")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    setup_logger(cfg.get("logging", {}).get("level", "INFO"), cfg.get("logging", {}).get("file"))

    coins = [c.strip() for c in args.coins.split(",")] if args.coins else cfg.get("coins", [])[:5]
    tfs = (
        [t.strip() for t in args.timeframes.split(",")]
        if args.timeframes
        else cfg.get("primary_timeframes", ["1h", "4h"])
    )

    ingester = DataIngester(cfg)
    reports = ingester.build_universe_report(coins, tfs, days=args.days)

    # Funding + OI for each resolved symbol
    for r in reports:
        symbol = r.get("symbol")
        if not r.get("listed"):
            continue
        try:
            fund = ingester.download_funding(symbol, days=args.days)
            logger.info(f"Funding {symbol}: {len(fund)} rows")
        except Exception as e:
            logger.warning(f"Funding fail {symbol}: {e}")
        try:
            oi, status = ingester.download_oi_hist(symbol, period="1h", days=min(30, args.days))
            logger.info(f"OI {symbol}: {len(oi)} rows status={status}")
        except Exception as e:
            logger.warning(f"OI fail {symbol}: {e}")

    logger.info("Download complete.")


if __name__ == "__main__":
    main()
