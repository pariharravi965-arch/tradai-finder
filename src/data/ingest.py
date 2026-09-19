"""Data ingestion — Delta Exchange India (public, no key)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

from .delta_client import INTERVAL_MS, INTERVAL_SEC, DeltaPublicClient
from .quality import DataQualityReport, score_data_quality


class DataIngester:
    def __init__(self, cfg: dict, client: Optional[DeltaPublicClient] = None):
        self.cfg = cfg
        dcfg = cfg.get("delta", {})
        self.client = client or DeltaPublicClient(
            base_url=dcfg.get("base_url", "https://api.india.delta.exchange"),
            rate_limit_sleep=dcfg.get("rate_limit_sleep", 0.12),
        )
        paths = cfg.get("paths", {})
        self.raw_dir = Path(paths.get("data_raw", "data/raw"))
        self.processed_dir = Path(paths.get("data_processed", "data/processed"))
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    def resolve_symbol(self, coin: str) -> str:
        aliases = self.cfg.get("symbol_aliases", {})
        c = coin.upper().strip()
        if c in aliases:
            return aliases[c]
        if c.endswith("USD"):
            return c
        if c.endswith("USDT"):
            return c.replace("USDT", "USD")
        return f"{c}USD"

    def _parquet_path(self, symbol: str, timeframe: str, kind: str = "klines") -> Path:
        return self.raw_dir / kind / f"{symbol}_{timeframe}.csv"

    def load_cached_klines(self, symbol: str, timeframe: str) -> pd.DataFrame:
        path = self._parquet_path(symbol, timeframe)
        if path.exists():
            return pd.read_csv(path)
        return pd.DataFrame()

    def save_klines(self, df: pd.DataFrame, symbol: str, timeframe: str) -> Path:
        path = self._parquet_path(symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        return path

    def download_klines(
        self,
        symbol: str,
        timeframe: str,
        days: int = 90,
        end: Optional[datetime] = None,
        force: bool = False,
    ):
        end = end or datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        start_sec = int(start.timestamp())
        end_sec = int(end.timestamp())
        start_ms = start_sec * 1000
        end_ms = end_sec * 1000

        cached = self.load_cached_klines(symbol, timeframe)
        if not force and not cached.empty:
            c_first = int(cached["open_time"].iloc[0])
            c_last = int(cached["open_time"].iloc[-1])
            if c_first <= start_ms and c_last >= end_ms - INTERVAL_MS.get(timeframe, 60_000):
                logger.info(f"Cache hit {symbol} {timeframe} ({len(cached)} bars)")
                df = cached[(cached["open_time"] >= start_ms) & (cached["open_time"] <= end_ms)]
                report = score_data_quality(
                    df, symbol, timeframe, INTERVAL_MS.get(timeframe, 60_000),
                    self.cfg.get("data_quality", {}).get("max_missing_pct", 5.0),
                )
                return df.reset_index(drop=True), report

        logger.info(f"Delta download {symbol} {timeframe} last {days}d ...")
        df = self.client.fetch_candles_range(symbol, timeframe, start_sec, end_sec)
        if df.empty:
            logger.warning(f"No candles for {symbol} {timeframe}")
            report = score_data_quality(df, symbol, timeframe, INTERVAL_MS.get(timeframe, 60_000))
            return df, report

        if not cached.empty:
            df = pd.concat([cached, df], ignore_index=True)
            df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

        self.save_klines(df, symbol, timeframe)
        window = df[(df["open_time"] >= start_ms) & (df["open_time"] <= end_ms)].reset_index(drop=True)
        report = score_data_quality(
            window, symbol, timeframe, INTERVAL_MS.get(timeframe, 60_000),
            self.cfg.get("data_quality", {}).get("max_missing_pct", 5.0),
        )
        logger.info(f"{symbol} {timeframe}: {len(window)} bars, quality={report.score:.1f} ({report.status})")
        return window, report

    def download_funding(self, symbol: str, days: int = 90) -> pd.DataFrame:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        start_sec = int(start.timestamp())
        end_sec = int(end.timestamp())
        path = self.raw_dir / "funding" / f"{symbol}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            cached = pd.read_csv(path)
            if not cached.empty and int(cached["fundingTime"].iloc[-1]) > (end_sec - 8 * 3600) * 1000:
                return cached[cached["fundingTime"] >= start_sec * 1000]
        df = self.client.funding_history(symbol, start_sec, end_sec)
        if not df.empty:
            df.to_csv(path, index=False)
        return df

    def download_oi_hist(self, symbol: str, period: str = "1h", days: int = 30):
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        start_sec = int(start.timestamp())
        end_sec = int(end.timestamp())
        return self.client.oi_history(symbol, start_sec, end_sec, resolution=period)

    def build_universe_report(self, coins, timeframes, days: int = 90):
        reports = []
        available = set(self.client.list_perpetuals())
        for coin in coins:
            symbol = self.resolve_symbol(coin)
            entry = {
                "coin": coin,
                "symbol": symbol,
                "listed": (symbol in available) if available else True,
                "timeframes": {},
            }
            for tf in timeframes:
                try:
                    df, q = self.download_klines(symbol, tf, days=days)
                    entry["timeframes"][tf] = q.to_dict()
                    if q.n_rows > 0:
                        entry["listed"] = True
                except Exception as e:
                    entry["timeframes"][tf] = {"status": "ERROR", "error": str(e)}
            reports.append(entry)
        out = self.processed_dir / "universe_report.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(reports, f, indent=2, default=str)
        logger.info(f"Universe report -> {out}")
        return reports
