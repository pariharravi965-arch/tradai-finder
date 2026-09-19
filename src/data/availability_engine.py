"""
Step 1 — Data Availability Engine

Every market factor is classified once, up front:
  HISTORICAL_AVAILABLE   → can download from Delta public history
  LIVE_COLLECT_ONLY      → only via continuous collector (becomes proprietary history)
  EXTERNAL_SOURCE_REQUIRED → not on Delta; need another vendor/manual

Never fabricate missing series. Research downstream must respect these tags.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

# ---------------------------------------------------------------------------
# Canonical status tags
# ---------------------------------------------------------------------------
HISTORICAL_AVAILABLE = "HISTORICAL_AVAILABLE"
LIVE_COLLECT_ONLY = "LIVE_COLLECT_ONLY"
EXTERNAL_SOURCE_REQUIRED = "EXTERNAL_SOURCE_REQUIRED"
PARTIAL = "PARTIAL"  # available but incomplete / proxy


@dataclass
class DataAsset:
    asset_id: str
    name: str
    status: str
    delta_symbol_pattern: Optional[str] = None  # e.g. "{SYM}", "OI:{SYM}", "MARK:{SYM}"
    resolutions: list[str] = field(default_factory=list)
    notes: str = ""
    research_ok: bool = True  # False if status blocks research claims


# Master catalog — frozen architecture
DELTA_DATA_CATALOG: list[DataAsset] = [
    DataAsset(
        "ohlcv",
        "OHLCV candles",
        HISTORICAL_AVAILABLE,
        "{SYM}",
        ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"],
        "Delta /v2/history/candles — 12h often unsupported",
        True,
    ),
    DataAsset(
        "open_interest",
        "Open Interest history",
        HISTORICAL_AVAILABLE,
        "OI:{SYM}",
        ["15m", "30m", "1h", "4h", "1d"],
        "OI:SYMBOL candle series",
        True,
    ),
    DataAsset(
        "funding_rate",
        "Funding rate history",
        HISTORICAL_AVAILABLE,
        "FUNDING:{SYM}",
        ["1h", "4h", "1d"],
        "FUNDING:SYMBOL — usually 8h cadence embedded in 1h bars",
        True,
    ),
    DataAsset(
        "mark_price",
        "Mark price history",
        HISTORICAL_AVAILABLE,
        "MARK:{SYM}",
        ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
        "MARK:SYMBOL candles",
        True,
    ),
    DataAsset(
        "basis_proxy",
        "Basis (mark − last)",
        PARTIAL,
        None,
        ["1h", "4h"],
        "Derived mark−close; not true spot index",
        True,
    ),
    DataAsset(
        "spot_index",
        "True spot / index price",
        PARTIAL,
        None,
        [],
        "Use MARK as proxy; dedicated index series not guaranteed per symbol",
        True,
    ),
    DataAsset(
        "l2_orderbook",
        "L2 order book",
        LIVE_COLLECT_ONLY,
        None,
        [],
        "Live /v2/l2orderbook/{SYM} only — no historical archive",
        False,  # historical research blocked until collector builds history
    ),
    DataAsset(
        "public_trades",
        "Public trades / ticks",
        LIVE_COLLECT_ONLY,
        None,
        [],
        "Recent /v2/trades/{SYM} only — deep history absent",
        False,
    ),
    DataAsset(
        "cvd",
        "Cumulative volume delta",
        LIVE_COLLECT_ONLY,
        None,
        [],
        "Derived from live/recent trades collector",
        False,
    ),
    DataAsset(
        "orderbook_imbalance",
        "Bid/ask imbalance",
        LIVE_COLLECT_ONLY,
        None,
        [],
        "From live L2 snapshots",
        False,
    ),
    DataAsset(
        "spread_depth",
        "Spread and depth",
        LIVE_COLLECT_ONLY,
        None,
        [],
        "From live L2",
        False,
    ),
    DataAsset(
        "absorption_proxy",
        "Absorption / large-order proxy",
        LIVE_COLLECT_ONLY,
        None,
        [],
        "Heuristic on live trades+OB; not true exchange event stream",
        False,
    ),
    DataAsset(
        "liquidations",
        "Liquidation history",
        EXTERNAL_SOURCE_REQUIRED,
        None,
        [],
        "Delta India public API has no reliable historical liquidation feed",
        False,
    ),
    DataAsset(
        "options_iv",
        "Options IV / GEX",
        EXTERNAL_SOURCE_REQUIRED,
        None,
        [],
        "Deep IV surface history not on Delta public candles",
        False,
    ),
    DataAsset(
        "onchain_etf_news",
        "On-chain / ETF / news timestamps",
        EXTERNAL_SOURCE_REQUIRED,
        None,
        [],
        "Requires external vendors",
        False,
    ),
]


# Preferred 25-coin research universe (verified against exchange at runtime)
PREFERRED_UNIVERSE = [
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BNBUSD",
    "DOGEUSD", "ADAUSD", "AVAXUSD", "LINKUSD", "DOTUSD",
    "TRXUSD", "LTCUSD", "BCHUSD", "UNIUSD", "NEARUSD",
    "APTUSD", "SUIUSD", "FILUSD", "ARBUSD", "OPUSD",
    "AAVEUSD", "INJUSD", "WIFUSD", "TIAUSD", "SEIUSD",  # SEI replaces missing ATOM
]

ALL_TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"]
# Primary research set (lighter than full 1m×25)
PRIMARY_TIMEFRAMES = ["15m", "30m", "1h", "2h", "4h", "1d"]
DERIV_TIMEFRAMES = ["1h", "4h", "1d"]


class DataAvailabilityEngine:
    """Discover exchange products, classify assets, write availability report."""

    def __init__(self, client: Any, out_dir: Path):
        self.client = client
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def discover_universe(self, preferred: list[str] | None = None) -> dict[str, Any]:
        preferred = preferred or PREFERRED_UNIVERSE
        listed = set()
        try:
            listed = set(self.client.list_perpetuals())
        except Exception as e:
            logger.error(f"list_perpetuals failed: {e}")
        found = [s for s in preferred if s in listed]
        missing = [s for s in preferred if s not in listed]
        # fill to ~25 from liquid perps if short
        if len(found) < 25 and listed:
            extras = sorted(listed - set(found))
            # prefer non-1000 meme wrappers for research
            extras = [x for x in extras if not x.startswith("1000") and x.endswith("USD")]
            for x in extras:
                if len(found) >= 25:
                    break
                if x not in found:
                    found.append(x)
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "exchange": "delta_india",
            "n_listed_perps": len(listed),
            "preferred": preferred,
            "found": found,
            "missing_from_preferred": missing,
            "research_universe": found[:25],
        }
        path = self.out_dir / "universe_discovery.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info(f"Universe: {len(found)} found, missing={missing} → {path}")
        return report

    def catalog_report(self) -> dict[str, Any]:
        rows = [asdict(a) for a in DELTA_DATA_CATALOG]
        by_status: dict[str, list[str]] = {}
        for a in DELTA_DATA_CATALOG:
            by_status.setdefault(a.status, []).append(a.asset_id)
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "assets": rows,
            "by_status": by_status,
            "historical_research_allowed": [a.asset_id for a in DELTA_DATA_CATALOG if a.research_ok and a.status in (HISTORICAL_AVAILABLE, PARTIAL)],
            "blocked_until_live_history": [a.asset_id for a in DELTA_DATA_CATALOG if a.status == LIVE_COLLECT_ONLY],
            "external_required": [a.asset_id for a in DELTA_DATA_CATALOG if a.status == EXTERNAL_SOURCE_REQUIRED],
        }
        path = self.out_dir / "data_availability_catalog.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info(f"Availability catalog → {path}")
        return report

    def run(self, preferred: list[str] | None = None) -> dict[str, Any]:
        uni = self.discover_universe(preferred)
        cat = self.catalog_report()
        summary = {
            "universe": uni,
            "catalog": cat,
            "next_steps": [
                "Run maximum historical downloader for research_universe × PRIMARY_TIMEFRAMES",
                "Start live_collector for L2 + trades on core symbols",
                "Pass Quality Gate before any discovery research",
            ],
        }
        (self.out_dir / "step1_availability_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )
        return summary
