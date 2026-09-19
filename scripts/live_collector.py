from __future__ import annotations

import argparse
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from src.data.delta_auth import DeltaPublicExtra


STOP = False


def handle_stop(signum, frame):
    global STOP
    STOP = True
    print(f"\nStop signal received ({signum}). Finishing current cycle...")


signal.signal(signal.SIGINT, handle_stop)
signal.signal(signal.SIGTERM, handle_stop)


def utc_now():
    return datetime.now(timezone.utc)


def atomic_json_write(path: Path, payload: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_jsonl(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str) + "\n")
        f.flush()


def collect_symbol(pub, symbol: str, base: Path, ts: str):
    day = utc_now().strftime("%Y%m%d")
    symbol_dir = base / symbol
    symbol_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "symbol": symbol,
        "ts": ts,
        "orderbook": "ERROR",
        "trades": "ERROR",
        "errors": [],
    }

    # Order-book snapshot
    try:
        imb = pub.orderbook_imbalance(symbol, levels=15)
        imb["ts"] = ts
        imb["symbol"] = symbol

        append_jsonl(
            symbol_dir / f"orderbook_{day}.jsonl",
            imb,
        )

        result["orderbook"] = (
            "OK" if imb.get("status") != "DATA_MISSING" else "MISSING"
        )
    except Exception as exc:
        result["errors"].append(f"orderbook: {type(exc).__name__}: {exc}")

    # Recent trades / CVD proxy
    try:
        cvd = pub.cvd_proxy_from_recent_trades(symbol)
        cvd["ts"] = ts
        cvd["symbol"] = symbol

        append_jsonl(
            symbol_dir / f"trades_{day}.jsonl",
            cvd,
        )

        result["trades"] = (
            "OK" if cvd.get("status") != "DATA_MISSING" else "MISSING"
        )
    except Exception as exc:
        result["errors"].append(f"trades: {type(exc).__name__}: {exc}")

    if result["errors"]:
        result["status"] = "PARTIAL_ERROR"
    elif result["orderbook"] == "OK" and result["trades"] == "OK":
        result["status"] = "OK"
    else:
        result["status"] = "PARTIAL"

    return result


def main():
    parser = argparse.ArgumentParser(
        description="TradAI live public microstructure collector"
    )
    parser.add_argument(
        "--symbols",
        default="BTCUSD,ETHUSD,SOLUSD",
        help="Comma-separated Delta symbols",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="Seconds between collection cycles",
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("TRADAI_DATA_DIR", "/data"),
        help="Persistent data directory",
    )
    parser.add_argument(
        "--max-iters",
        type=int,
        default=0,
        help="0 = run continuously",
    )
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    pub = DeltaPublicExtra()

    base = Path(args.data_dir) / "live"
    base.mkdir(parents=True, exist_ok=True)

    checkpoint = base / "_collector_checkpoint.json"
    status_log = base / "_collector_status.jsonl"

    state = {
        "started_at": utc_now().isoformat(),
        "last_cycle": None,
        "cycle": 0,
        "interval_seconds": args.interval,
        "symbols": symbols,
        "per_symbol": {},
        "stopped": False,
    }

    print(f"Live collector started: {symbols}")
    print(f"Interval: {args.interval}s")
    print(f"Checkpoint: {checkpoint}")
    print(f"Output: {base}")
    print("Public endpoints only — no API key required.")
    print("Ctrl+C to stop safely.")

    next_cycle = time.monotonic()
    cycle = 0

    while not STOP:
        cycle += 1
        started = utc_now()
        ts = started.isoformat()

        cycle_result = {
            "cycle": cycle,
            "ts": ts,
            "symbols": {},
        }

        for symbol in symbols:
            if STOP:
                break

            result = collect_symbol(pub, symbol, base, ts)
            cycle_result["symbols"][symbol] = result
            state["per_symbol"][symbol] = {
                "last_ts": ts,
                "status": result["status"],
                "errors": result["errors"],
            }

            print(
                f"[{ts}] {symbol} "
                f"OB={result['orderbook']} "
                f"TRADES={result['trades']} "
                f"errors={len(result['errors'])}"
            )

        finished = utc_now()

        state["last_cycle"] = finished.isoformat()
        state["cycle"] = cycle
        state["stopped"] = False

        atomic_json_write(checkpoint, state)
        append_jsonl(status_log, cycle_result)

        if args.max_iters and cycle >= args.max_iters:
            break

        # Monotonic scheduling reduces drift from slow API cycles.
        next_cycle += args.interval
        delay = next_cycle - time.monotonic()

        if delay > 0:
            time.sleep(delay)
        else:
            # If the cycle itself took longer than the interval,
            # immediately begin the next cycle without negative sleep.
            next_cycle = time.monotonic()

    state["stopped"] = True
    state["stopped_at"] = utc_now().isoformat()
    atomic_json_write(checkpoint, state)

    print("Collector stopped cleanly.")
    print(f"Checkpoint saved: {checkpoint}")


if __name__ == "__main__":
    main()
