import argparse
import os
import signal
import time
from pathlib import Path

from src.research.live_ingest import run_once


STOP = False


def stop_handler(signum, frame):
    global STOP
    STOP = True
    print("Stopping live ingest worker...")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Continuously ingest Railway live JSONL into SQLite."
    )
    parser.add_argument("--data-dir", default=os.environ.get("TRADAI_DATA_DIR", "/data"))
    parser.add_argument("--db", default=None)
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    db_path = (
        Path(args.db)
        if args.db
        else data_dir / "research" / "live_market.db"
    )

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    print(f"Live ingest worker started")
    print(f"data_dir={data_dir}")
    print(f"db={db_path}")
    print(f"interval={args.interval}s")

    while not STOP:
        started = time.monotonic()

        try:
            result = run_once(data_dir, db_path)

            print(
                f"files={result.files_seen} "
                f"lines={result.lines_seen} "
                f"inserted={result.rows_inserted} "
                f"duplicates={result.duplicates} "
                f"errors={result.errors}"
            )

        except Exception as exc:
            print(f"INGEST_WORKER_ERROR: {type(exc).__name__}: {exc}")

        elapsed = time.monotonic() - started
        sleep_for = max(0.0, args.interval - elapsed)

        end = time.monotonic() + sleep_for
        while not STOP and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))

    print("Live ingest worker stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
