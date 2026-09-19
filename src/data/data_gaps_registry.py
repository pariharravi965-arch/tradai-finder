"""Explicit registry of what Delta public API cannot provide.

Never fabricate these. Research that needs them must wait for external data.
"""

DATA_GAPS = [
    {"factor": "historical_order_book_snapshots", "status": "DATA_MISSING", "source": "delta_public", "proxy": "none", "value": "HIGH"},
    {"factor": "historical_tick_trades", "status": "DATA_MISSING", "source": "delta_public", "proxy": "OHLCV only", "value": "HIGH"},
    {"factor": "historical_cvd_true", "status": "DATA_MISSING", "source": "delta_public", "proxy": "taker_buy if available (often not)", "value": "HIGH"},
    {"factor": "liquidation_history", "status": "DATA_MISSING", "source": "delta_public", "proxy": "none reliable", "value": "HIGH"},
    {"factor": "basis_spot_futures", "status": "DATA_LIMITED", "source": "delta_public", "proxy": "mark vs index if ticker fields exist", "value": "MEDIUM"},
    {"factor": "options_iv_gex", "status": "DATA_MISSING", "source": "delta_public", "proxy": "none", "value": "MEDIUM"},
    {"factor": "onchain_etf_whale", "status": "DATA_MISSING", "source": "external", "proxy": "none", "value": "MEDIUM"},
    {"factor": "news_timestamps_complete", "status": "DATA_MISSING", "source": "external", "proxy": "manual calendar only", "value": "MEDIUM"},
]

DATA_AVAILABLE_DELTA = [
    "ohlcv_candles_1m_to_1d",
    "funding_history_FUNDING_symbol",
    "oi_history_OI_symbol",
    "live_l2_orderbook",
    "live_ticker_mark_funding_oi",
    "products_list",
]

# Auth unlocks only YOUR account history — not market-wide historical OB/liquidations
AUTH_UNLOCKS = [
    "wallet_balances",
    "positions",
    "own_order_history",
    "own_fills_for_learning",
]

LIVE_PUBLIC_EXTRA = [
    "l2_orderbook_live_snapshot",
    "recent_trades_cvd_proxy",
    "orderbook_imbalance_live",
]
