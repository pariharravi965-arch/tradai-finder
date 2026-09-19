"""Delta Exchange India public REST client.

No API key required for market data.
Base: https://api.india.delta.exchange

Endpoints used:
  GET /v2/history/candles  — OHLCV (max ~2000 per call)
  GET /v2/tickers/{symbol}
  GET /v2/products
  GET /v2/history/candles?symbol=FUNDING:BTCUSD  — funding history
  GET /v2/history/candles?symbol=OI:BTCUSD       — OI history

Timestamps are Unix **seconds** (not ms).
Response candles are newest-first — we always sort ascending.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential


# resolution → seconds per bar
INTERVAL_SEC = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "1d": 86400,
    "1w": 604800,
}

# Keep ms map for quality engine compatibility
INTERVAL_MS = {k: v * 1000 for k, v in INTERVAL_SEC.items()}


class DeltaPublicClient:
    def __init__(
        self,
        base_url: str = "https://api.india.delta.exchange",
        rate_limit_sleep: float = 0.12,
    ):
        self.base_url = base_url.rstrip("/")
        self.rate_limit_sleep = rate_limit_sleep
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "TradAI-Finder/1.0-delta",
            }
        )

    def _sleep(self) -> None:
        time.sleep(self.rate_limit_sleep)

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=20))
    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        self._sleep()
        url = f"{self.base_url}{path}"
        r = self.session.get(url, params=params, timeout=30)
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", 5))
            logger.warning(f"Delta rate limit 429 — sleep {retry_after}s")
            time.sleep(retry_after)
            r.raise_for_status()
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Products / symbols
    # ------------------------------------------------------------------
    def list_perpetuals(self) -> list[str]:
        """Return active perpetual symbols (e.g. BTCUSD, ETHUSD)."""
        try:
            raw = self._get("/v2/products")
            result = raw.get("result") or []
            symbols = []
            for p in result:
                if not isinstance(p, dict):
                    continue
                sym = p.get("symbol") or ""
                ctype = (p.get("contract_type") or "").lower()
                state = (p.get("state") or p.get("trading_status") or "").lower()
                if "perpetual" in ctype and state in ("live", "active", "trading", ""):
                    symbols.append(sym)
                elif ctype == "" and sym.endswith("USD") and "option" not in (p.get("description") or "").lower():
                    # fallback: many perpetuals end with USD
                    if p.get("contract_type") in (None, "perpetual_futures", "perpetual"):
                        symbols.append(sym)
            return sorted(set(symbols))
        except Exception as e:
            logger.warning(f"list_perpetuals failed: {e}")
            return []

    def ticker(self, symbol: str) -> dict:
        try:
            raw = self._get(f"/v2/tickers/{symbol}")
            return raw.get("result") or {}
        except Exception as e:
            logger.warning(f"ticker {symbol}: {e}")
            return {}

    # ------------------------------------------------------------------
    # Candles
    # ------------------------------------------------------------------
    def candles(
        self,
        symbol: str,
        resolution: str,
        start_sec: int,
        end_sec: int,
    ) -> pd.DataFrame:
        """Fetch one page of candles. Max ~2000 bars."""
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "start": int(start_sec),
            "end": int(end_sec),
        }
        raw = self._get("/v2/history/candles", params)
        rows = raw.get("result") or []
        if not rows:
            return pd.DataFrame()

        # Newest-first → sort ascending
        if len(rows) >= 2 and rows[0].get("time", 0) > rows[-1].get("time", 0):
            rows = list(reversed(rows))

        df = pd.DataFrame(rows)
        # Normalize columns to finder schema (open_time in ms)
        rename = {}
        if "time" in df.columns:
            rename["time"] = "open_time"
        df = df.rename(columns=rename)
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "open_time" in df.columns:
            # Delta returns seconds → store as ms for consistency with rest of finder
            df["open_time"] = (pd.to_numeric(df["open_time"], errors="coerce") * 1000).astype("int64")
        # Optional fields
        if "volume" not in df.columns:
            df["volume"] = 0.0
        df["quote_volume"] = df.get("quote_volume", df["volume"] * df["close"])
        df["n_trades"] = df.get("n_trades", 0)
        df["taker_buy_base"] = float("nan")
        df["taker_buy_quote"] = float("nan")
        return df[
            [c for c in [
                "open_time", "open", "high", "low", "close", "volume",
                "quote_volume", "n_trades", "taker_buy_base", "taker_buy_quote",
            ] if c in df.columns]
        ].copy()

    def fetch_candles_range(
        self,
        symbol: str,
        resolution: str,
        start_sec: int,
        end_sec: int,
        max_per_page: int = 1800,
    ) -> pd.DataFrame:
        """Paginate candles from start_sec to end_sec (Unix seconds)."""
        step = INTERVAL_SEC.get(resolution, 3600)
        all_dfs: list[pd.DataFrame] = []
        cursor = start_sec
        safety = 0
        while cursor < end_sec and safety < 500:
            safety += 1
            page_end = min(cursor + max_per_page * step, end_sec)
            df = self.candles(symbol, resolution, cursor, page_end)
            if df.empty:
                # advance anyway to avoid stall
                cursor = page_end + step
                continue
            all_dfs.append(df)
            last_sec = int(df["open_time"].iloc[-1] // 1000)
            next_cursor = last_sec + step
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(df) < max_per_page // 2:
                # likely reached end of available data
                if last_sec >= end_sec - step:
                    break
        if not all_dfs:
            return pd.DataFrame()
        out = pd.concat(all_dfs, ignore_index=True)
        out = out.drop_duplicates(subset=["open_time"]).sort_values("open_time")
        out = out[(out["open_time"] >= start_sec * 1000) & (out["open_time"] <= end_sec * 1000)]
        return out.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Funding history  (symbol = FUNDING:BTCUSD)
    # ------------------------------------------------------------------
    def funding_history(
        self,
        symbol: str,
        start_sec: int,
        end_sec: int,
    ) -> pd.DataFrame:
        """Funding rate history via FUNDING:{symbol} candle endpoint."""
        fund_sym = f"FUNDING:{symbol}" if not symbol.startswith("FUNDING:") else symbol
        try:
            df = self.fetch_candles_range(fund_sym, "1h", start_sec, end_sec, max_per_page=1500)
        except Exception as e:
            logger.warning(f"funding history {symbol}: {e}")
            return pd.DataFrame()
        if df.empty:
            return pd.DataFrame()
        # close often holds the funding rate value on FUNDING series
        out = pd.DataFrame(
            {
                "fundingTime": df["open_time"].astype("int64"),
                "fundingRate": pd.to_numeric(df["close"], errors="coerce"),
                "symbol": symbol.replace("FUNDING:", ""),
            }
        )
        return out.dropna(subset=["fundingRate"]).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Open interest history (symbol = OI:BTCUSD)
    # ------------------------------------------------------------------
    def oi_history(
        self,
        symbol: str,
        start_sec: int,
        end_sec: int,
        resolution: str = "1h",
    ) -> tuple[pd.DataFrame, str]:
        oi_sym = f"OI:{symbol}" if not symbol.startswith("OI:") else symbol
        try:
            df = self.fetch_candles_range(oi_sym, resolution, start_sec, end_sec, max_per_page=1500)
        except Exception as e:
            logger.warning(f"OI history {symbol}: {e}")
            return pd.DataFrame(), "DATA_MISSING"
        if df.empty:
            return pd.DataFrame(), "DATA_MISSING"
        out = pd.DataFrame(
            {
                "timestamp": df["open_time"].astype("int64"),
                "sumOpenInterest": pd.to_numeric(df["close"], errors="coerce"),
            }
        )
        span_days = (out["timestamp"].iloc[-1] - out["timestamp"].iloc[0]) / (86400 * 1000)
        status = "DATA_AVAILABLE" if span_days >= 7 else "DATA_LIMITED"
        return out.dropna().reset_index(drop=True), status

    @staticmethod
    def sec_to_dt(sec: int) -> datetime:
        return datetime.fromtimestamp(sec, tz=timezone.utc)

    @staticmethod
    def dt_to_sec(dt: datetime) -> int:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
