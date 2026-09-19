"""
External data adapters — stubs that report EXTERNAL_SOURCE_REQUIRED.

Do not fabricate liquidations / on-chain / IV / news.
"""
from __future__ import annotations

from typing import Any


def liquidations_adapter(*_a, **_k) -> dict[str, Any]:
    return {
        "status": "EXTERNAL_SOURCE_REQUIRED",
        "provider": None,
        "note": "Delta India public API has no reliable historical liquidation series",
    }


def onchain_adapter(*_a, **_k) -> dict[str, Any]:
    return {"status": "EXTERNAL_SOURCE_REQUIRED", "note": "Requires Glassnode/CryptoQuant/etc."}


def etf_flow_adapter(*_a, **_k) -> dict[str, Any]:
    return {"status": "EXTERNAL_SOURCE_REQUIRED", "note": "External ETF flow vendor"}


def news_events_adapter(*_a, **_k) -> dict[str, Any]:
    return {"status": "EXTERNAL_SOURCE_REQUIRED", "note": "Timestamped news/calendar vendor required"}


def options_iv_adapter(*_a, **_k) -> dict[str, Any]:
    return {"status": "EXTERNAL_SOURCE_REQUIRED", "note": "Deep IV/GEX history not on Delta candles"}


ADAPTERS = {
    "liquidations": liquidations_adapter,
    "onchain": onchain_adapter,
    "etf_flow": etf_flow_adapter,
    "news": news_events_adapter,
    "options_iv": options_iv_adapter,
}
