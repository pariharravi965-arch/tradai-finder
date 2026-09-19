"""Delta Exchange authenticated client.

Credentials ONLY from environment variables — never hardcode secrets.

  export DELTA_API_KEY="..."
  export DELTA_API_SECRET="..."

Auth unlocks (account-level):
  - wallet balances
  - positions
  - order history / fills (YOUR trades — for learning loop)

Auth does NOT unlock:
  - historical market-wide liquidations
  - historical order-book archives
  - tick-level CVD history

Public still used for candles / funding / OI.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any, Optional

import requests
from loguru import logger


class DeltaAuthClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: str = "https://api.india.delta.exchange",
    ):
        self.api_key = api_key or os.getenv("DELTA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("DELTA_API_SECRET", "")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if not self.api_key or not self.api_secret:
            logger.warning(
                "DELTA_API_KEY / DELTA_API_SECRET not set — authenticated endpoints disabled"
            )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _sign(self, method: str, path: str, query: str = "", body: str = "") -> dict:
        timestamp = str(int(time.time()))
        # Delta signature: method + timestamp + path + query_string + body
        payload = method.upper() + timestamp + path + query + body
        sig = hmac.new(
            self.api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "api-key": self.api_key,
            "timestamp": timestamp,
            "signature": sig,
            "User-Agent": "TradAI-Finder/1.1",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
    ) -> Any:
        if not self.is_configured:
            return {"success": False, "error": "API keys not configured (set DELTA_API_KEY / DELTA_API_SECRET)"}
        query = ""
        if params:
            # sorted query string
            query = "?" + "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
        body_str = json.dumps(body, separators=(",", ":")) if body else ""
        headers = self._sign(method, path, query if method.upper() == "GET" else "", body_str if method.upper() != "GET" else "")
        url = f"{self.base_url}{path}"
        if method.upper() == "GET":
            r = self.session.get(url, params=params, headers=headers, timeout=30)
        else:
            r = self.session.request(method.upper(), url, data=body_str, headers=headers, timeout=30)
        if r.status_code == 401:
            logger.error("Delta auth 401 — check key/secret/signature/clock sync")
        r.raise_for_status()
        return r.json()

    # ---- Account (read-only research use) ----
    def balances(self) -> Any:
        return self._request("GET", "/v2/wallet/balances")

    def positions(self) -> Any:
        return self._request("GET", "/v2/positions/margined")

    def order_history(self, product_ids: str | None = None, page_size: int = 50) -> Any:
        params: dict[str, Any] = {"page_size": min(page_size, 50)}
        if product_ids:
            params["product_ids"] = product_ids
        return self._request("GET", "/v2/orders/history", params=params)

    def fills(self, product_ids: str | None = None, page_size: int = 50) -> Any:
        params: dict[str, Any] = {"page_size": min(page_size, 50)}
        if product_ids:
            params["product_ids"] = product_ids
        return self._request("GET", "/v2/fills", params=params)


class DeltaPublicExtra:
    """Public endpoints that help order-flow proxies (no key)."""

    def __init__(self, base_url: str = "https://api.india.delta.exchange"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "TradAI-Finder/1.1"})

    def l2_orderbook(self, symbol: str) -> dict:
        try:
            r = self.session.get(f"{self.base_url}/v2/l2orderbook/{symbol}", timeout=15)
            r.raise_for_status()
            data = r.json()
            return data.get("result") or data
        except Exception as e:
            logger.warning(f"L2 orderbook {symbol}: {e}")
            return {}

    def recent_trades(self, symbol: str) -> list:
        """Recent public trades — useful for short-window CVD proxy, NOT deep history."""
        try:
            r = self.session.get(f"{self.base_url}/v2/trades/{symbol}", timeout=15)
            r.raise_for_status()
            data = r.json()
            return data.get("result") or []
        except Exception as e:
            logger.warning(f"recent trades {symbol}: {e}")
            return []

    def orderbook_imbalance(self, symbol: str, levels: int = 10) -> dict:
        ob = self.l2_orderbook(symbol)
        if not ob:
            return {"status": "DATA_MISSING", "imbalance": None}
        bids = ob.get("buy") or ob.get("bids") or []
        asks = ob.get("sell") or ob.get("asks") or []
        # format may be [[price, size], ...] or [{price, size}, ...]
        def _vol(side, n):
            total = 0.0
            for i, row in enumerate(side[:n]):
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    total += float(row[1])
                elif isinstance(row, dict):
                    total += float(row.get("size") or row.get("quantity") or 0)
            return total
        bv, av = _vol(bids, levels), _vol(asks, levels)
        imb = (bv - av) / (bv + av) if (bv + av) > 0 else 0.0
        return {
            "status": "DATA_AVAILABLE",
            "bid_vol": bv,
            "ask_vol": av,
            "imbalance": round(imb, 4),
            "levels": levels,
            "note": "LIVE snapshot only — not historical series",
        }

    def cvd_proxy_from_recent_trades(self, symbol: str) -> dict:
        trades = self.recent_trades(symbol)
        if not trades:
            return {"status": "DATA_MISSING", "cvd": None, "note": "no recent trades"}
        buy_v = sell_v = 0.0
        for t in trades:
            size = float(t.get("size") or t.get("quantity") or 0)
            # buyer is aggressor if seller is maker
            if t.get("buyer_role") == "taker" or t.get("side") in ("buy", "BUY"):
                buy_v += size
            else:
                sell_v += size
        return {
            "status": "DATA_LIMITED",
            "buy_volume": buy_v,
            "sell_volume": sell_v,
            "delta": buy_v - sell_v,
            "aggression": (buy_v - sell_v) / (buy_v + sell_v) if (buy_v + sell_v) else 0,
            "n_trades": len(trades),
            "note": "RECENT window only — not historical CVD series",
        }
