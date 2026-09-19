#!/usr/bin/env python3
"""
Live microstructure collector — start ASAP to build YOUR historical OB/trades DB.

Polls every N seconds:
  - L2 orderbook snapshot (imbalance, top depth)
  - Recent trades (CVD proxy window)

Stores to data/raw/live/{symbol}/orderbook_*.jsonl and trades_*.jsonl

Usage:
  PYTHONPATH=. python3 scripts/live_collector.py --symbols BTCUSD,ETHUSD,SOLUSD --interval 10
  # run in background / tmux for continuous collection
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.delta_auth import DeltaPublicExtra


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="BTCUSD,ETHUSD,SOLUSD")
    parser.add_argument("--interval", type=float, default=10.0, help="seconds between polls")
    parser.add_argument("--max-iters", type=int, default=0, help="0 = infinite")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    pub = DeltaPublicExtra()
    base = ROOT / "data" / "raw" / "live"
    base.mkdir(parents=True, exist_ok=True)

    ckpt = base / "_collector_checkpoint.json"
    print(f"Live collector started: {symbols} every {args.interval}s")
    print(f"Checkpoint file: {ckpt}")
    print(f"Output: {base}")
    print("Ctrl+C to stop. Keep running to accumulate microstructure history.")

    it = 0
    while True:
        it += 1
        ts = datetime.now(timezone.utc).isoformat()
        for sym in symbols:
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            ob_path = base / sym / f"orderbook_{day}.jsonl"
            tr_path = base / sym / f"trades_{day}.jsonl"
            ob_path.parent.mkdir(parents=True, exist_ok=True)

            imb = pub.orderbook_imbalance(sym, levels=15)
            imb["ts"] = ts
            imb["symbol"] = sym
            with open(ob_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(imb, default=str) + "\n")

            cvd = pub.cvd_proxy_from_recent_trades(sym)
            cvd["ts"] = ts
            cvd["symbol"] = sym
            with open(tr_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(cvd, default=str) + "\n")

            print(f"[{ts}] {sym} imb={imb.get('imbalance')} delta={cvd.get('delta')} n={cvd.get('n_trades')}")
        with open(ckpt, "w", encoding="utf-8") as cf:
            json.dump({"ts": ts, "iter": it, "symbols": symbols}, cf)

        if args.max_iters and it >= args.max_iters:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
