from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS live_orderbook_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    ts TEXT,
    status TEXT,
    bid_vol REAL,
    ask_vol REAL,
    imbalance REAL,
    levels INTEGER,
    note TEXT,
    source_file TEXT NOT NULL,
    source_offset INTEGER NOT NULL,
    ingested_at REAL NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_live_ob_symbol_ts
ON live_orderbook_snapshots(symbol, ts);

CREATE TABLE IF NOT EXISTS live_tradeflow_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    ts TEXT,
    status TEXT,
    buy_volume REAL,
    sell_volume REAL,
    delta REAL,
    aggression REAL,
    n_trades INTEGER,
    note TEXT,
    source_file TEXT NOT NULL,
    source_offset INTEGER NOT NULL,
    ingested_at REAL NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_live_tf_symbol_ts
ON live_tradeflow_snapshots(symbol, ts);

CREATE TABLE IF NOT EXISTS live_ingest_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    source_offset INTEGER NOT NULL,
    error TEXT NOT NULL,
    raw_line TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS live_ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    finished_at REAL NOT NULL,
    files_seen INTEGER NOT NULL,
    lines_seen INTEGER NOT NULL,
    rows_inserted INTEGER NOT NULL,
    duplicates INTEGER NOT NULL,
    errors INTEGER NOT NULL
);
"""


@dataclass
class IngestResult:
    files_seen: int = 0
    lines_seen: int = 0
    rows_inserted: int = 0
    duplicates: int = 0
    errors: int = 0


def db_connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def canonical_json(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_id(kind: str, record: dict[str, Any]) -> str:
    payload = f"{kind}|{canonical_json(record)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def checkpoint_path(data_dir: Path) -> Path:
    return data_dir / "live" / "_ingest_checkpoint.json"


def load_checkpoint(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in value.items()}
    except (OSError, ValueError, TypeError):
        return {}


def save_checkpoint(path: Path, checkpoint: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(checkpoint, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def iter_jsonl_files(data_dir: Path) -> Iterable[Path]:
    yield from sorted((data_dir / "live").glob("*/*.jsonl"))


def _insert_orderbook(
    conn: sqlite3.Connection,
    record: dict[str, Any],
    source_file: str,
    source_offset: int,
    now: float,
) -> bool:
    eid = event_id("orderbook", record)
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO live_orderbook_snapshots
        (event_id, symbol, ts, status, bid_vol, ask_vol, imbalance, levels,
         note, source_file, source_offset, ingested_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            eid,
            str(record["symbol"]),
            record.get("ts"),
            record.get("status"),
            record.get("bid_vol"),
            record.get("ask_vol"),
            record.get("imbalance"),
            record.get("levels"),
            record.get("note"),
            source_file,
            source_offset,
            now,
            canonical_json(record),
        ),
    )
    return cur.rowcount == 1


def _insert_tradeflow(
    conn: sqlite3.Connection,
    record: dict[str, Any],
    source_file: str,
    source_offset: int,
    now: float,
) -> bool:
    eid = event_id("tradeflow", record)
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO live_tradeflow_snapshots
        (event_id, symbol, ts, status, buy_volume, sell_volume, delta,
         aggression, n_trades, note, source_file, source_offset, ingested_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            eid,
            str(record["symbol"]),
            record.get("ts"),
            record.get("status"),
            record.get("buy_volume"),
            record.get("sell_volume"),
            record.get("delta"),
            record.get("aggression"),
            record.get("n_trades"),
            record.get("note"),
            source_file,
            source_offset,
            now,
            canonical_json(record),
        ),
    )
    return cur.rowcount == 1


def ingest_file(
    conn: sqlite3.Connection,
    path: Path,
    start_offset: int,
    checkpoint: dict[str, int],
    checkpoint_file: Path,
    result: IngestResult,
) -> None:
    kind = "orderbook" if path.name.startswith("orderbook_") else "tradeflow"

    with path.open("rb") as fh:
        fh.seek(start_offset)
        while True:
            offset = fh.tell()
            raw = fh.readline()
            if not raw:
                break

            # Do not consume an incomplete final line. It will be retried next run.
            if not raw.endswith(b"\n"):
                fh.seek(offset)
                break

            result.lines_seen += 1
            line = raw.rstrip(b"\r\n")
            if not line.strip():
                checkpoint[str(path)] = fh.tell()
                continue

            try:
                record = json.loads(line.decode("utf-8"))
                if not isinstance(record, dict):
                    raise ValueError("JSON record is not an object")
                if "symbol" not in record:
                    raise ValueError("missing symbol")
                now = time.time()
                if kind == "orderbook":
                    inserted = _insert_orderbook(
                        conn, record, str(path), offset, now
                    )
                else:
                    inserted = _insert_tradeflow(
                        conn, record, str(path), offset, now
                    )

                if inserted:
                    result.rows_inserted += 1
                else:
                    result.duplicates += 1

            except Exception as exc:
                result.errors += 1
                conn.execute(
                    """
                    INSERT INTO live_ingest_errors
                    (source_file, source_offset, error, raw_line, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(path),
                        offset,
                        f"{type(exc).__name__}: {exc}",
                        line.decode("utf-8", errors="replace"),
                        time.time(),
                    ),
                )

            checkpoint[str(path)] = fh.tell()
            save_checkpoint(checkpoint_file, checkpoint)

    conn.commit()


def run_once(data_dir: Path, db_path: Path) -> IngestResult:
    started = time.time()
    result = IngestResult()
    checkpoint_file = checkpoint_path(data_dir)
    checkpoint = load_checkpoint(checkpoint_file)

    conn = db_connect(db_path)
    try:
        for path in iter_jsonl_files(data_dir):
            result.files_seen += 1
            offset = checkpoint.get(str(path), 0)
            ingest_file(conn, path, offset, checkpoint, checkpoint_file, result)

        conn.execute(
            """
            INSERT INTO live_ingest_runs
            (started_at, finished_at, files_seen, lines_seen, rows_inserted, duplicates, errors)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                started,
                time.time(),
                result.files_seen,
                result.lines_seen,
                result.rows_inserted,
                result.duplicates,
                result.errors,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Railway live JSONL into SQLite.")
    parser.add_argument("--data-dir", default="/data")
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite output path. Default: <data-dir>/research/live_market.db",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    db_path = Path(args.db) if args.db else data_dir / "research" / "live_market.db"

    result = run_once(data_dir, db_path)
    print(
        f"files={result.files_seen} lines={result.lines_seen} "
        f"inserted={result.rows_inserted} duplicates={result.duplicates} "
        f"errors={result.errors}"
    )
    return 0 if result.errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
