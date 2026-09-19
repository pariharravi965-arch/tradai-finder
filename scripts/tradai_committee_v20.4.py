#!/usr/bin/env python3
"""
TradAI Committee Advisor — Committee Engine Edition v18
==============================================================
Single-file · Pure local analysis · No paid AI APIs

v15 focus (from v14 backtest post-mortem — WR 28% → quality first):
  • HARD anti-WRONG_DIRECTION gates (MACD conflict, HTF flat, low confluence)
  • Explicit WAIT_FOR_CONFIRMATION status (not just NO TRADE)
  • Wider SL (ATR mult 1.05 → 1.85) to cut SL_TOO_TIGHT
  • Higher MIN_SCORE (6.9 → 7.85) + stricter structure/alignment floors
  • Require ≥2 TF agreement; penalise 1H-flat + only-5m bias
  • Location + extended price hard penalty (was negative contributor)
  • Momentum/MACD hard veto when opposing bias
  • Entry quality: borderline setups forced to WAIT
  • All v14 diagnosis / post-mortem / learning retained

Still NOT claimed: full ML training, tick backtest, options/on-chain feeds

Advisor only — never places orders.
Requires optional: pip install websocket-client

Version: committee-v20.4
"""

# ═══════════════════════════════════════════════════════════════
# 0. IMPORTS
# ═══════════════════════════════════════════════════════════════
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import traceback
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    import websocket  # websocket-client
    HAS_WS = True
except ImportError:
    HAS_WS = False

# ═══════════════════════════════════════════════════════════════
# 1. CONFIGURATION + SECURITY
# ═══════════════════════════════════════════════════════════════
BOT_VERSION = "committee-v20.4"
# v20.4: fix snap NameError in BT · min RRR 1:2 hard · SOL/INJ safe skip
# v20.3: drag coins stricter (confirm+1, score_th+0.4) — no coin delete, volume preserve
# v20.2: inject Delta finder v2 60d recipes (mode/SL/TP/th/confirm/hold/chase)
# v20.1: committee BT window 60d (2880 x 30m) — match finder 60d horizon
# v20.0: 2y aligned finder recipes locked
# v19.4 QUALITY: strong-coin only · cut drag coins · WR path to 55-60% · keep volume floor
# v19.3 VOLUME: softer arm/fire · strong-coin watchlist · target ~150-200 trades/8d · min RR 1:2 · WR path
# v19.2: auto-FIRE after confirm · WAIT pe full recalculate · skips ≠ losses
# v19.1: PENDING→CONFIRM→FIRE | pre-calc SL/TP | skips ≠ losses | post-trade loss ≈ WRONG_DIRECTION only

BASE_URL = os.getenv("BASE_URL", "https://api.india.delta.exchange")

# Prefer env vars in production. Defaults for continuity with prior working bot.
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "8855636235:AAEnAmPV0sJNjQogEq5ptpXAAdS1fQyCmO0",
)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "5035169590")

DEFAULT_SYMBOL = os.getenv("SYMBOL", "BTCUSD")
DB_PATH = os.getenv("DB_PATH", "advisor_local_v17.db")
LOG_PATH = os.getenv("LOG_PATH", "advisor_local_v17.log")

# ── Quality gates (v17 — 30m/45m primary, WR target)
MIN_SCORE_TRADE = float(os.getenv("MIN_SCORE_TRADE", "6.20"))  # v20.2 finder-aligned  # v19.4 quality  # v19.3 volume  # v18.2 WR push
MIN_SCORE_WAIT  = float(os.getenv("MIN_SCORE_WAIT", "5.20"))
MIN_RR = float(os.getenv("MIN_RR", "2.0"))  # v19 1:2  # v18.9 mandatory 1:2  # v18.8 mandatory 1:2  # v18.7 8d-match; rr2 recipes still 2.0  # v18.6 hard floor 1:2
SL_ATR_MULT = float(os.getenv("SL_ATR_MULT", "1.85"))
SL_MIN_PCT = float(os.getenv("SL_MIN_PCT", "0.0060"))
SL_MAX_PCT = float(os.getenv("SL_MAX_PCT", "0.038"))
TP_RR_MULT = float(os.getenv("TP_RR_MULT", "2.0"))
TP_RR_TRANSITION = float(os.getenv("TP_RR_TRANSITION", "2.0"))  # was 1.6; min 1:2  # v17.3: closer in chop for hit rate
RISK_USD_DEFAULT = float(os.getenv("RISK_USD", "0.25"))
REWARD_USD_MIN = float(os.getenv("REWARD_USD_MIN", "0.50"))
LEVERAGE_DEFAULT = float(os.getenv("LEVERAGE", "10"))
RISK_PCT_DEFAULT = float(os.getenv("RISK_PCT", "1.0"))
ACCOUNT_USD_DEFAULT = float(os.getenv("ACCOUNT_USD", "100.0"))

# Hard floors — anti WRONG_DIRECTION (~50% cut target)
MIN_STRUCTURE_SCORE = 5.9
MIN_ALIGNMENT_SCORE = 5.5
MIN_MOMENTUM_SCORE  = 5.0  # v17.3 follow-through
MIN_LOCATION_SCORE  = 4.8
MIN_ENTRY_TIMING    = 5.85  # v19.3 softer  # v18.2: drive ET toward 0
MIN_VOLUME_SCORE    = 5.0   # INSUFFICIENT_DATA / LOW_VOLUME filter
REQUIRE_HTF_AGREE   = True
REQUIRE_15M_AGREE   = True  # 15m must match bias
REQUIRE_1H_NOT_FLAT = True  # #1 post-mortem: 1H Flat → MFE 0 never-favorable
REQUIRE_ENTRY_CONFIRM = True
REQUIRE_BOS_OR_STRONG = True  # prefer BOS
REQUIRE_BOS_HARD = False      # 30m: BOS preferred, not hard
ANTI_CHASE_BARS = 5           # v17.2: stronger anti-chase (~50% timing cut target)
PRIMARY_TF = "30m"
MID_TF = "45m"  # built from 15m×3
BT_BARS_DEFAULT = 2880        # v20.1: 2880×30m ≈ 60 days — match aligned finder 60d

# Per-coin profiles — not every coin same strategy (v17.7)
# EDGE: proven positive NetR — flexible gates
# MID: standard strict
# HARD: historically weak — stricter, still allow trades
# SKIP: almost never trade unless exceptional score
COIN_PROFILE = {
    "AAVEUSD": {"tier": "EDGE", "score_adj": -0.15, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "SHORT", "recipe": "60d_v19", "mode": "SHORT_ONLY", "threshold": 0.25, "confirm_need": 2},
    "ADAUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": False, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "LONG", "recipe": "60d_v19", "mode": "LONG_ONLY", "threshold": 0.25, "confirm_need": 2},
    "ARBUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": False, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "LONG", "recipe": "60d_v19", "mode": "LONG_ONLY", "threshold": 0.25, "confirm_need": 2},
    "ATOMUSD": {"tier": "HARD", "score_adj": 0.45, "short_1h": True, "macd_hard": True, "bos_hard": True, "mom_min": 5.6, "prefer_dir": "BOTH", "recipe": "60d_v19", "mode": "BOTH", "threshold": 0.12, "confirm_need": 2},
    "AVAXUSD": {"tier": "EDGE", "score_adj": -0.15, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "BOTH", "recipe": "60d_v19", "mode": "BOTH", "threshold": 0.12, "confirm_need": 2},
    "AXSUSD": {"tier": "EDGE", "score_adj": -0.15, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "BOTH", "recipe": "60d_v19", "mode": "BOTH", "threshold": 0.12, "confirm_need": 2},
    "BNBUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": False, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "LONG", "recipe": "60d_v19", "mode": "LONG_ONLY", "threshold": 0.25, "confirm_need": 2},
    "BTCUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "SHORT", "recipe": "60d_v19", "mode": "SHORT_ONLY", "threshold": 0.18, "confirm_need": 2},
    "DOGEUSD": {"tier": "EDGE", "score_adj": -0.15, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "SHORT", "recipe": "60d_v19", "mode": "SHORT_ONLY", "threshold": 0.12, "confirm_need": 2},
    "DOTUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "BOTH", "recipe": "60d_v19", "mode": "BOTH", "threshold": 0.25, "confirm_need": 2},
    "ETHUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": False, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "LONG", "recipe": "60d_v19", "mode": "LONG_ONLY", "threshold": 0.12, "confirm_need": 2},
    "FETUSD": {"tier": "EDGE", "score_adj": -0.15, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "SHORT", "recipe": "60d_v19", "mode": "SHORT_ONLY", "threshold": 0.12, "confirm_need": 2},
    "FILUSD": {"tier": "SKIP", "score_adj": 0.08, "short_1h": False, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "LONG", "recipe": "60d_v19", "mode": "LONG_ONLY", "threshold": 0.12, "confirm_need": 2},
    "HBARUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": False, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "LONG", "recipe": "60d_v19", "mode": "LONG_ONLY", "threshold": 0.12, "confirm_need": 2},
    "ICPUSD": {"tier": "HARD", "score_adj": 0.45, "short_1h": True, "macd_hard": True, "bos_hard": True, "mom_min": 5.6, "prefer_dir": "BOTH", "recipe": "60d_v19", "mode": "BOTH", "threshold": 0.18, "confirm_need": 2},
    "INJUSD": {"tier": "HARD", "score_adj": 0.45, "short_1h": False, "macd_hard": True, "bos_hard": True, "mom_min": 5.6, "prefer_dir": "LONG", "recipe": "60d_v19", "mode": "LONG_ONLY", "threshold": 0.18, "confirm_need": 2},
    "JUPUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "BOTH", "recipe": "60d_v19", "mode": "BOTH", "threshold": 0.12, "confirm_need": 2},
    "LINKUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": False, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "LONG", "recipe": "60d_v19", "mode": "LONG_ONLY", "threshold": 0.25, "confirm_need": 2},
    "LTCUSD": {"tier": "SKIP", "score_adj": 0.08, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "SHORT", "recipe": "60d_v19", "mode": "SHORT_ONLY", "threshold": 0.18, "confirm_need": 2},
    "OPUSD": {"tier": "EDGE", "score_adj": -0.15, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "SHORT", "recipe": "60d_v19", "mode": "SHORT_ONLY", "threshold": 0.25, "confirm_need": 2},
    "RENDERUSD": {"tier": "EDGE", "score_adj": -0.15, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "SHORT", "recipe": "60d_v19", "mode": "SHORT_ONLY", "threshold": 0.12, "confirm_need": 2},
    "SEIUSD": {"tier": "HARD", "score_adj": 0.45, "short_1h": False, "macd_hard": True, "bos_hard": True, "mom_min": 5.6, "prefer_dir": "LONG", "recipe": "60d_v19", "mode": "LONG_ONLY", "threshold": 0.12, "confirm_need": 2},
    "SOLUSD": {"tier": "HARD", "score_adj": 0.45, "short_1h": False, "macd_hard": True, "bos_hard": True, "mom_min": 5.6, "prefer_dir": "LONG", "recipe": "60d_v19", "mode": "LONG_ONLY", "threshold": 0.18, "confirm_need": 2},
    "STRKUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "SHORT", "recipe": "60d_v19", "mode": "SHORT_ONLY", "threshold": 0.12, "confirm_need": 2},
    "SUIUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "SHORT", "recipe": "60d_v19", "mode": "SHORT_ONLY", "threshold": 0.12, "confirm_need": 2},
    "TIAUSD": {"tier": "EDGE", "score_adj": -0.15, "short_1h": False, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "LONG", "recipe": "60d_v19", "mode": "LONG_ONLY", "threshold": 0.12, "confirm_need": 2},
    "TONUSD": {"tier": "EDGE", "score_adj": -0.15, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "BOTH", "recipe": "60d_v19", "mode": "BOTH", "threshold": 0.12, "confirm_need": 2},
    "TRXUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "BOTH", "recipe": "60d_v19", "mode": "BOTH", "threshold": 0.12, "confirm_need": 2},
    "UNIUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": False, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "LONG", "recipe": "60d_v19", "mode": "LONG_ONLY", "threshold": 0.12, "confirm_need": 2},
    "WIFUSD": {"tier": "EDGE", "score_adj": -0.15, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "SHORT", "recipe": "60d_v19", "mode": "SHORT_ONLY", "threshold": 0.25, "confirm_need": 2},
    "WLDUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "SHORT", "recipe": "60d_v19", "mode": "SHORT_ONLY", "threshold": 0.12, "confirm_need": 2},
    "XLMUSD": {"tier": "MID", "score_adj": 0.08, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "BOTH", "recipe": "60d_v19", "mode": "BOTH", "threshold": 0.12, "confirm_need": 2},
    "XRPUSD": {"tier": "EDGE", "score_adj": -0.15, "short_1h": False, "macd_hard": False, "bos_hard": False, "mom_min": 5.4, "prefer_dir": "LONG", "recipe": "60d_v19", "mode": "LONG_ONLY", "threshold": 0.25, "confirm_need": 2},
}
TRADE_BLOCKLIST = set()  # v20.2 keep all for volume

# === v20.2 Delta finder v2 60d recipes ===

# v20.3 drag strict (volume keep, quality up)
DRAG_STRICT = {
    "ADAUSD": {'confirm_add': 1, 'score_add': 0.4, 'chase_tight': 1.25},
    "BCHUSD": {'confirm_add': 1, 'score_add': 0.5, 'chase_tight': 1.2},
    "LTCUSD": {'confirm_add': 1, 'score_add': 0.4, 'chase_tight': 1.25},
    "AVAXUSD": {'confirm_add': 1, 'score_add': 0.4, 'chase_tight': 1.25},
    "XRPUSD": {'confirm_add': 1, 'score_add': 0.3, 'chase_tight': 1.3},
    "SUIUSD": {'confirm_add': 1, 'score_add': 0.3, 'chase_tight': 1.3},
    "NEARUSD": {'confirm_add': 1, 'score_add': 0.5, 'chase_tight': 1.2},
}
COIN_PROFILE_V22 = {
    "LINKUSD": {"tier": "EDGE", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "SHORT", "recipe": "delta_v2_60d", "mode": "SHORT_ONLY", "confirm_need": 2, "score_th": 6.0, "chase": 1.3},
    "TRXUSD": {"tier": "EDGE", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "LONG", "recipe": "delta_v2_60d", "mode": "LONG_ONLY", "confirm_need": 2, "score_th": 6.4, "chase": 1.45},
    "ARBUSD": {"tier": "EDGE", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "BOTH", "recipe": "delta_v2_60d", "mode": "BOTH", "confirm_need": 2, "score_th": 6.0, "chase": 1.45},
    "ETHUSD": {"tier": "EDGE", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "LONG", "recipe": "delta_v2_60d", "mode": "LONG_ONLY", "confirm_need": 2, "score_th": 6.4, "chase": 1.45},
    "APTUSD": {"tier": "EDGE", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "BOTH", "recipe": "delta_v2_60d", "mode": "BOTH", "confirm_need": 2, "score_th": 6.0, "chase": 1.3},
    "NEARUSD": {"tier": "EDGE", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "LONG", "recipe": "delta_v2_60d", "mode": "LONG_ONLY", "confirm_need": 2, "score_th": 6.0, "chase": 1.3},
    "AAVEUSD": {"tier": "EDGE", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "LONG", "recipe": "delta_v2_60d", "mode": "LONG_ONLY", "confirm_need": 2, "score_th": 6.0, "chase": 1.3},
    "OPUSD": {"tier": "EDGE", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "SHORT", "recipe": "delta_v2_60d", "mode": "SHORT_ONLY", "confirm_need": 2, "score_th": 6.4, "chase": 1.3},
    "DOGEUSD": {"tier": "MID", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "BOTH", "recipe": "delta_v2_60d", "mode": "BOTH", "confirm_need": 2, "score_th": 6.8, "chase": 1.3},
    "LTCUSD": {"tier": "MID", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "LONG", "recipe": "delta_v2_60d", "mode": "LONG_ONLY", "confirm_need": 2, "score_th": 6.4, "chase": 1.3},
    "UNIUSD": {"tier": "MID", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "LONG", "recipe": "delta_v2_60d", "mode": "LONG_ONLY", "confirm_need": 2, "score_th": 6.0, "chase": 1.3},
    "AVAXUSD": {"tier": "MID", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "BOTH", "recipe": "delta_v2_60d", "mode": "BOTH", "confirm_need": 2, "score_th": 6.8, "chase": 1.3},
    "BNBUSD": {"tier": "MID", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "LONG", "recipe": "delta_v2_60d", "mode": "LONG_ONLY", "confirm_need": 2, "score_th": 6.8, "chase": 1.3},
    "XRPUSD": {"tier": "MID", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "BOTH", "recipe": "delta_v2_60d", "mode": "BOTH", "confirm_need": 2, "score_th": 6.4, "chase": 1.3},
    "DOTUSD": {"tier": "MID", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "BOTH", "recipe": "delta_v2_60d", "mode": "BOTH", "confirm_need": 2, "score_th": 6.8, "chase": 1.3},
    "BTCUSD": {"tier": "MID", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "BOTH", "recipe": "delta_v2_60d", "mode": "BOTH", "confirm_need": 2, "score_th": 6.4, "chase": 1.3},
    "ADAUSD": {"tier": "MID", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "BOTH", "recipe": "delta_v2_60d", "mode": "BOTH", "confirm_need": 2, "score_th": 6.8, "chase": 1.3},
    "SUIUSD": {"tier": "MID", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "BOTH", "recipe": "delta_v2_60d", "mode": "BOTH", "confirm_need": 3, "score_th": 6.4, "chase": 1.3},
    "FILUSD": {"tier": "MID", "score_adj": 0.0, "short_1h": True, "macd_hard": False, "bos_hard": False, "mom_min": 5.2, "prefer_dir": "BOTH", "recipe": "delta_v2_60d", "mode": "BOTH", "confirm_need": 2, "score_th": 6.0, "chase": 1.3},
}
COIN_RISK_V22 = {
    "LINKUSD": {"sl_atr": 1.85, "tp_rr": 2.4, "hold": 10, "chase": 1.3},
    "TRXUSD": {"sl_atr": 1.6, "tp_rr": 2.4, "hold": 10, "chase": 1.45},
    "ARBUSD": {"sl_atr": 1.85, "tp_rr": 2.4, "hold": 14, "chase": 1.45},
    "ETHUSD": {"sl_atr": 2.1, "tp_rr": 2.0, "hold": 14, "chase": 1.45},
    "APTUSD": {"sl_atr": 1.85, "tp_rr": 2.4, "hold": 14, "chase": 1.3},
    "NEARUSD": {"sl_atr": 1.6, "tp_rr": 2.4, "hold": 10, "chase": 1.3},
    "AAVEUSD": {"sl_atr": 1.85, "tp_rr": 2.4, "hold": 14, "chase": 1.3},
    "OPUSD": {"sl_atr": 1.85, "tp_rr": 2.4, "hold": 14, "chase": 1.3},
    "DOGEUSD": {"sl_atr": 2.1, "tp_rr": 2.4, "hold": 10, "chase": 1.3},
    "LTCUSD": {"sl_atr": 1.6, "tp_rr": 2.0, "hold": 14, "chase": 1.3},
    "UNIUSD": {"sl_atr": 1.6, "tp_rr": 2.0, "hold": 14, "chase": 1.3},
    "AVAXUSD": {"sl_atr": 2.1, "tp_rr": 2.0, "hold": 14, "chase": 1.3},
    "BNBUSD": {"sl_atr": 2.1, "tp_rr": 2.4, "hold": 14, "chase": 1.3},
    "XRPUSD": {"sl_atr": 1.6, "tp_rr": 2.0, "hold": 10, "chase": 1.3},
    "DOTUSD": {"sl_atr": 1.6, "tp_rr": 2.0, "hold": 14, "chase": 1.3},
    "BTCUSD": {"sl_atr": 2.1, "tp_rr": 2.4, "hold": 14, "chase": 1.3},
    "ADAUSD": {"sl_atr": 1.6, "tp_rr": 2.0, "hold": 14, "chase": 1.3},
    "SUIUSD": {"sl_atr": 1.85, "tp_rr": 2.4, "hold": 10, "chase": 1.3},
    "FILUSD": {"sl_atr": 1.6, "tp_rr": 2.4, "hold": 14, "chase": 1.3},
}
COIN_TIER_A = {k for k,v in COIN_PROFILE.items() if v["tier"] == "EDGE"}
COIN_TIER_C = {k for k,v in COIN_PROFILE.items() if v["tier"] in ("HARD", "SKIP")}
TIER_C_SCORE_EXTRA = 0.35
LONG_SCORE_EXTRA = 0.15  # softened vs 0.25 — more LONG room

# v18.3 — per-coin SL/TP from MAE/MFE recipes (not one-size-fits-all)
# sl_atr: stop distance in ATR · tp_rr: reward multiple · hold: max bars 30m
COIN_RISK = {
    "AAVEUSD": {"sl_atr": 1.85, "tp_rr": 2.0, "hold": 14},
    "ADAUSD": {"sl_atr": 1.7, "tp_rr": 2.5, "hold": 10},
    "ARBUSD": {"sl_atr": 1.85, "tp_rr": 2.2, "hold": 10},
    "ATOMUSD": {"sl_atr": 1.85, "tp_rr": 2.0, "hold": 10},
    "AVAXUSD": {"sl_atr": 1.85, "tp_rr": 2.2, "hold": 14},
    "AXSUSD": {"sl_atr": 1.55, "tp_rr": 2.5, "hold": 18},
    "BNBUSD": {"sl_atr": 1.55, "tp_rr": 2.0, "hold": 10},
    "BTCUSD": {"sl_atr": 1.85, "tp_rr": 2.0, "hold": 10},
    "DOGEUSD": {"sl_atr": 1.85, "tp_rr": 2.0, "hold": 14},
    "DOTUSD": {"sl_atr": 1.85, "tp_rr": 2.0, "hold": 18},
    "ETHUSD": {"sl_atr": 1.85, "tp_rr": 2.0, "hold": 10},
    "FETUSD": {"sl_atr": 1.7, "tp_rr": 2.0, "hold": 18},
    "FILUSD": {"sl_atr": 1.85, "tp_rr": 2.0, "hold": 10},
    "HBARUSD": {"sl_atr": 1.85, "tp_rr": 2.2, "hold": 10},
    "ICPUSD": {"sl_atr": 1.85, "tp_rr": 2.0, "hold": 14},
    "INJUSD": {"sl_atr": 1.7, "tp_rr": 2.0, "hold": 10},
    "JUPUSD": {"sl_atr": 1.85, "tp_rr": 2.0, "hold": 10},
    "LINKUSD": {"sl_atr": 1.7, "tp_rr": 2.2, "hold": 10},
    "LTCUSD": {"sl_atr": 1.55, "tp_rr": 2.2, "hold": 10},
    "OPUSD": {"sl_atr": 1.85, "tp_rr": 2.5, "hold": 18},
    "RENDERUSD": {"sl_atr": 1.55, "tp_rr": 2.2, "hold": 10},
    "SEIUSD": {"sl_atr": 1.7, "tp_rr": 2.0, "hold": 10},
    "SOLUSD": {"sl_atr": 1.55, "tp_rr": 2.0, "hold": 14},
    "STRKUSD": {"sl_atr": 1.85, "tp_rr": 2.0, "hold": 10},
    "SUIUSD": {"sl_atr": 1.85, "tp_rr": 2.5, "hold": 10},
    "TIAUSD": {"sl_atr": 1.85, "tp_rr": 2.5, "hold": 10},
    "TONUSD": {"sl_atr": 1.85, "tp_rr": 2.0, "hold": 10},
    "TRXUSD": {"sl_atr": 1.85, "tp_rr": 2.2, "hold": 14},
    "UNIUSD": {"sl_atr": 1.85, "tp_rr": 2.5, "hold": 10},
    "WIFUSD": {"sl_atr": 1.55, "tp_rr": 2.2, "hold": 10},
    "WLDUSD": {"sl_atr": 1.85, "tp_rr": 2.2, "hold": 18},
    "XLMUSD": {"sl_atr": 1.7, "tp_rr": 2.2, "hold": 10},
    "XRPUSD": {"sl_atr": 1.85, "tp_rr": 2.5, "hold": 10},
}







# ========== v19.0 COMPLETE 60-DAY SYSTEM ==========
# ENTRY: mode + min score + N confirms (structure/BOS/MTF/anti-chase/vol/macd/trend)
# EXIT:  SL = coin sl_atr × ATR | TP >= 2.0R | hold = coin bars
# FACTORS: per-coin weights from 60d net_edge | dead feeds weight 0
COIN_FACTOR_WEIGHTS = {
    "AAVEUSD": {"body_quality": 0.1837, "spread_proxy": 0.1633, "engulf_proxy": 0.0816, "up_down_volume_proxy": 0.0612, "cvd_proxy": 0.0612, "imbalance_proxy": 0.0612, "ob_bear": 0.0612, "mtf_macd": 0.051, "macd": 0.051, "rsi_slope": 0.0408, "wick_balance": 0.0408, "inside_bar": 0.0408, "delta_proxy": 0.0408, "volume_ratio": 0.0306, "depth_proxy": 0.0306},
    "ADAUSD": {"volume_persistence": 0.0976, "volume_trend": 0.0976, "ema20_50": 0.0854, "up_down_volume_proxy": 0.0732, "cvd_proxy": 0.0732, "imbalance_proxy": 0.0732, "ema50_slope": 0.061, "mtf_slope": 0.061, "volume_ratio": 0.061, "spread_proxy": 0.061, "depth_proxy": 0.061, "macd_hist": 0.0488, "rsi_slope": 0.0488, "roc_8": 0.0488, "ny_session": 0.0488},
    "ARBUSD": {"spread_proxy": 0.1373, "mtf_macd": 0.098, "macd": 0.098, "aggressive_buy": 0.098, "macd_hist": 0.0784, "volume_spike": 0.0784, "expansion": 0.0784, "atr_expand": 0.0784, "rsi_slope": 0.0392, "volume_dryup": 0.0392, "rejection": 0.0392, "pinbar_proxy": 0.0392, "delta_proxy": 0.0392, "follow_through": 0.0392, "roc_8": 0.0196},
    "ATOMUSD": {"spread_proxy": 0.2059, "volume_spike": 0.1471, "volume_ratio": 0.1176, "depth_proxy": 0.1176, "aggressive_buy": 0.1176, "volume_persistence": 0.0588, "volume_trend": 0.0588, "delta_proxy": 0.0588, "up_down_volume_proxy": 0.0294, "compression": 0.0294, "cvd_proxy": 0.0294, "imbalance_proxy": 0.0294},
    "AVAXUSD": {"sr_position": 0.1117, "ltf_align": 0.1006, "mtf_rsi": 0.0838, "rsi_14": 0.0838, "wick_balance": 0.0782, "distance_swing_high": 0.0726, "mtf_persistence": 0.067, "trend_persistence": 0.067, "regime_persistence": 0.067, "asia_session": 0.0615, "volume_dryup": 0.0447, "freshness": 0.0447, "volume_spike": 0.0419, "body_quality": 0.0391, "atr_pct": 0.0363},
    "AXSUSD": {"spread_proxy": 0.1111, "absorption_proxy": 0.1111, "volume_spike": 0.0889, "aggressive_buy": 0.0889, "volume_persistence": 0.0667, "volume_dryup": 0.0667, "volume_trend": 0.0667, "delta_proxy": 0.0667, "ny_session": 0.0667, "volume_ratio": 0.0444, "up_down_volume_proxy": 0.0444, "anti_chase": 0.0444, "entry_timing": 0.0444, "timing_quality": 0.0444, "depth_proxy": 0.0444},
    "BNBUSD": {"volume_ratio": 0.0873, "depth_proxy": 0.0873, "fvg_bull": 0.0873, "mtf_macd": 0.0714, "macd": 0.0714, "spread_proxy": 0.0714, "bos_up": 0.0635, "swing_break": 0.0635, "macd_hist": 0.0635, "london_session": 0.0635, "rsi_slope": 0.0556, "volume_persistence": 0.0556, "volume_trend": 0.0556, "body_quality": 0.0556, "up_down_volume_proxy": 0.0476},
    "BTCUSD": {"macd_hist": 0.1132, "lh_ll": 0.0943, "mtf_structure": 0.0943, "engulf_proxy": 0.0943, "ob_bear": 0.0943, "ema9_21": 0.0755, "mtf_direction": 0.0755, "ltf_align": 0.0755, "mtf_macd": 0.0755, "macd": 0.0755, "aggressive_sell": 0.0755, "fvg_bear": 0.0377, "spread_proxy": 0.0189},
    "DOGEUSD": {"volume_ratio": 0.087, "volume_persistence": 0.087, "volume_trend": 0.087, "depth_proxy": 0.087, "bos_dn": 0.0652, "swing_break": 0.0652, "macd_hist": 0.0652, "anti_chase": 0.0652, "entry_timing": 0.0652, "pullback_quality": 0.0652, "timing_quality": 0.0652, "freshness": 0.0652, "ema9_21": 0.0435, "price_vs_ema20": 0.0435, "mtf_direction": 0.0435},
    "DOTUSD": {"wick_balance": 0.1111, "ema50_slope": 0.098, "mtf_slope": 0.098, "hh_hl": 0.0719, "freshness": 0.0719, "spread_proxy": 0.0719, "mtf_conflict": 0.0654, "mtf_persistence": 0.0588, "mtf_structure": 0.0588, "trend_persistence": 0.0588, "regime_persistence": 0.0588, "volume_dryup": 0.0523, "ltf_align": 0.0458, "volume_ratio": 0.0392, "depth_proxy": 0.0392},
    "ETHUSD": {"wick_balance": 0.1521, "engulf_proxy": 0.1065, "sr_position": 0.0989, "mtf_rsi": 0.076, "rsi_14": 0.076, "rejection": 0.0608, "pinbar_proxy": 0.0608, "delta_proxy": 0.0608, "volume_persistence": 0.0532, "volume_trend": 0.0532, "ob_bull": 0.0532, "asia_session": 0.0456, "london_session": 0.038, "spread_proxy": 0.0342, "inside_bar": 0.0304},
    "FETUSD": {"anti_chase": 0.1299, "entry_timing": 0.1299, "timing_quality": 0.1299, "ltf_align": 0.0779, "freshness": 0.0779, "ema9_21": 0.0519, "ema50_price": 0.0519, "mtf_direction": 0.0519, "htf_bias": 0.0519, "roc_8": 0.0519, "ema50_slope": 0.039, "lh_ll": 0.039, "mtf_slope": 0.039, "mtf_structure": 0.039, "macd_hist": 0.039},
    "FILUSD": {"equal_high_proxy": 0.1, "ema20_50": 0.0769, "mtf_persistence": 0.0769, "trend_persistence": 0.0769, "regime_persistence": 0.0769, "ema50_slope": 0.0692, "mtf_slope": 0.0692, "ltf_align": 0.0692, "volume_dryup": 0.0615, "london_session": 0.0615, "wick_balance": 0.0538, "sr_position": 0.0538, "spread_proxy": 0.0538, "asia_session": 0.0538, "mtf_ema_stack": 0.0462},
    "HBARUSD": {"ema50_slope": 0.2083, "mtf_slope": 0.2083, "wick_balance": 0.0833, "asia_session": 0.0833, "volume_dryup": 0.0625, "inside_bar": 0.0625, "equal_high_proxy": 0.0625, "absorption_proxy": 0.0625, "spread_proxy": 0.0417, "hh_hl": 0.0208, "mtf_structure": 0.0208, "roc_8": 0.0208, "rejection": 0.0208, "pinbar_proxy": 0.0208, "sr_position": 0.0208},
    "ICPUSD": {"volume_dryup": 0.2105, "sr_position": 0.2105, "freshness": 0.1579, "ny_session": 0.1579, "lh_ll": 0.0526, "bos_dn": 0.0526, "mtf_rsi": 0.0526, "rsi_14": 0.0526, "ob_bear": 0.0526},
    "INJUSD": {"spread_proxy": 0.1286, "mtf_ema_stack": 0.1143, "ema20_50": 0.0857, "volume_persistence": 0.0714, "volume_trend": 0.0714, "engulf_proxy": 0.0714, "aggressive_buy": 0.0714, "up_down_volume_proxy": 0.0571, "volume_spike": 0.0571, "cvd_proxy": 0.0571, "imbalance_proxy": 0.0571, "ob_bull": 0.0571, "delta_proxy": 0.0429, "ema50_slope": 0.0286, "mtf_slope": 0.0286},
    "JUPUSD": {"roc_8": 0.0941, "ema50_slope": 0.0824, "bos_up": 0.0824, "mtf_slope": 0.0824, "asia_session": 0.0824, "mtf_persistence": 0.0706, "macd_hist": 0.0706, "trend_persistence": 0.0706, "regime_persistence": 0.0706, "swing_break": 0.0588, "fvg_bull": 0.0588, "marubozu_proxy": 0.0471, "freshness": 0.0471, "delta_proxy": 0.0471, "mtf_conflict": 0.0353},
    "LINKUSD": {"mtf_rsi": 0.1702, "rsi_14": 0.1702, "hh_hl": 0.1064, "mtf_structure": 0.1064, "sweep_low_proxy": 0.0851, "asia_session": 0.0851, "sr_position": 0.0638, "freshness": 0.0638, "inside_bar": 0.0426, "london_session": 0.0426, "compression": 0.0213, "equal_high_proxy": 0.0213, "ny_session": 0.0213},
    "LTCUSD": {"ema20_50": 0.1176, "mtf_ema_stack": 0.1176, "wick_balance": 0.1176, "roc_8": 0.0882, "volume_ratio": 0.0882, "depth_proxy": 0.0882, "lh_ll": 0.0588, "mtf_conflict": 0.0588, "mtf_structure": 0.0588, "stoch_proxy": 0.0588, "volume_spike": 0.0588, "fvg_bear": 0.0588, "volume_dryup": 0.0294},
    "OPUSD": {"sr_position": 0.1111, "freshness": 0.0972, "ema20_50": 0.0833, "mtf_rsi": 0.0833, "rsi_14": 0.0833, "wick_balance": 0.0694, "price_vs_ema20": 0.0556, "volume_ratio": 0.0556, "volume_dryup": 0.0556, "volume_spike": 0.0556, "inside_bar": 0.0556, "spread_proxy": 0.0556, "depth_proxy": 0.0556, "ema50_price": 0.0417, "htf_bias": 0.0417},
    "RENDERUSD": {"ema9_21": 0.0833, "lh_ll": 0.0833, "mtf_direction": 0.0833, "mtf_structure": 0.0833, "macd_hist": 0.0833, "anti_chase": 0.0833, "entry_timing": 0.0833, "timing_quality": 0.0833, "freshness": 0.0833, "ltf_align": 0.0417, "mtf_ema_stack": 0.0417, "mtf_rsi": 0.0417, "rsi_14": 0.0417, "rsi_slope": 0.0417, "roc_8": 0.0417},
    "SEIUSD": {"sr_position": 0.3214, "mtf_rsi": 0.2143, "rsi_14": 0.2143, "sweep_low_proxy": 0.1071, "engulf_proxy": 0.0357, "expansion": 0.0357, "atr_expand": 0.0357, "ob_bull": 0.0357},
    "SOLUSD": {"hh_hl": 0.1111, "mtf_structure": 0.1111, "asia_session": 0.0952, "ema20_50": 0.0794, "ema50_slope": 0.0794, "mtf_slope": 0.0794, "ema9_21": 0.0635, "mtf_direction": 0.0635, "mtf_ema_stack": 0.0635, "ema50_price": 0.0556, "htf_bias": 0.0556, "wick_balance": 0.0476, "expansion": 0.0317, "compression": 0.0317, "atr_expand": 0.0317},
    "STRKUSD": {"ema20_50": 0.1212, "freshness": 0.1212, "mtf_conflict": 0.0909, "ltf_align": 0.0909, "anti_chase": 0.0909, "entry_timing": 0.0909, "timing_quality": 0.0909, "wick_balance": 0.0606, "fvg_bear": 0.0606, "ema50_slope": 0.0303, "mtf_slope": 0.0303, "rsi_slope": 0.0303, "up_down_volume_proxy": 0.0303, "body_quality": 0.0303, "rejection": 0.0303},
    "SUIUSD": {"volume_persistence": 0.3, "volume_trend": 0.3, "wick_balance": 0.1, "rejection": 0.1, "pinbar_proxy": 0.1, "fvg_bear": 0.1},
    "TIAUSD": {"ema50_slope": 0.0904, "mtf_slope": 0.0904, "volume_persistence": 0.0904, "volume_trend": 0.0904, "volume_ratio": 0.0791, "spread_proxy": 0.0791, "depth_proxy": 0.0791, "hh_hl": 0.0678, "mtf_structure": 0.0678, "volume_spike": 0.0565, "london_session": 0.0565, "wick_balance": 0.0452, "roc_8": 0.0395, "expansion": 0.0339, "atr_expand": 0.0339},
    "TONUSD": {"rsi_slope": 0.0973, "up_down_volume_proxy": 0.0865, "cvd_proxy": 0.0865, "aggressive_buy": 0.0865, "imbalance_proxy": 0.0865, "mtf_rsi": 0.0757, "rsi_14": 0.0757, "body_quality": 0.0757, "ny_session": 0.0649, "stoch_proxy": 0.0486, "hh_hl": 0.0432, "mtf_structure": 0.0432, "macd_hist": 0.0432, "sr_position": 0.0432, "delta_proxy": 0.0432},
    "TRXUSD": {"freshness": 0.1126, "body_quality": 0.0721, "mtf_persistence": 0.0676, "trend_persistence": 0.0676, "regime_persistence": 0.0676, "distance_swing_high": 0.0676, "distance_swing_low": 0.0676, "ltf_align": 0.0631, "inside_bar": 0.0631, "sr_room": 0.0586, "liquidity_room": 0.0586, "liq_room": 0.0586, "rr_room_proxy": 0.0586, "recent_session_range": 0.0586, "candle_completeness": 0.0586},
    "UNIUSD": {"mtf_rsi": 0.1633, "rsi_14": 0.1633, "sr_position": 0.1327, "volume_spike": 0.0816, "asia_session": 0.0816, "follow_through": 0.0612, "rsi_slope": 0.051, "volume_ratio": 0.0408, "depth_proxy": 0.0408, "volume_persistence": 0.0306, "volume_trend": 0.0306, "spread_proxy": 0.0306, "delta_proxy": 0.0306, "aggressive_buy": 0.0306, "fvg_bull": 0.0306},
    "WIFUSD": {"volume_ratio": 0.0947, "spread_proxy": 0.0947, "depth_proxy": 0.0947, "volume_persistence": 0.0842, "volume_dryup": 0.0842, "volume_trend": 0.0842, "volume_spike": 0.0737, "macd_hist": 0.0632, "rsi_slope": 0.0632, "anti_chase": 0.0526, "entry_timing": 0.0526, "timing_quality": 0.0526, "stoch_proxy": 0.0421, "ema20_50": 0.0316, "bos_dn": 0.0316},
    "WLDUSD": {"wick_balance": 0.1714, "volume_ratio": 0.1429, "depth_proxy": 0.1429, "spread_proxy": 0.1143, "mtf_conflict": 0.0857, "mtf_rsi": 0.0571, "rsi_14": 0.0571, "inside_bar": 0.0571, "sr_position": 0.0571, "volume_persistence": 0.0286, "volume_dryup": 0.0286, "volume_trend": 0.0286, "freshness": 0.0286},
    "XLMUSD": {"freshness": 0.1359, "london_session": 0.1165, "anti_chase": 0.068, "entry_timing": 0.068, "timing_quality": 0.068, "ema50_slope": 0.0583, "mtf_slope": 0.0583, "body_quality": 0.0583, "expansion": 0.0583, "compression": 0.0583, "atr_expand": 0.0583, "equal_high_proxy": 0.0583, "asia_session": 0.0583, "ema20_50": 0.0388, "hh_hl": 0.0388},
    "XRPUSD": {"london_session": 0.0862, "volume_persistence": 0.0819, "volume_trend": 0.0819, "distance_swing_high": 0.0819, "rsi_slope": 0.0733, "anti_chase": 0.0733, "entry_timing": 0.0733, "timing_quality": 0.0733, "asia_session": 0.069, "freshness": 0.0603, "failed_auction": 0.056, "ema50_slope": 0.0474, "mtf_slope": 0.0474, "bb_width": 0.0474, "realized_vol": 0.0474},
}
COIN_PLAYBOOK = {
    "AAVEUSD": {"wr": 58.33, "net": 8.038, "pf": 1.63, "n": 36, "entry_mode": "SHORT_ONLY", "confirm_need": 2, "help": ["body_quality", "spread_proxy", "engulf_proxy", "up_down_volume_proxy", "cvd_proxy", "imbalance_proxy"], "avoid": ["anti_chase", "entry_timing", "timing_quality", "stoch_proxy"], "sl_atr": 1.85, "tp_rr": 2.0, "hold_bars": 14, "threshold": 0.25},
    "ADAUSD": {"wr": 54.55, "net": 6.01, "pf": 1.39, "n": 44, "entry_mode": "LONG_ONLY", "confirm_need": 2, "help": ["volume_persistence", "volume_trend", "ema20_50", "up_down_volume_proxy", "cvd_proxy", "imbalance_proxy"], "avoid": ["wick_balance", "distance_swing_high", "atr_pct", "bb_width"], "sl_atr": 1.7, "tp_rr": 2.5, "hold_bars": 10, "threshold": 0.25},
    "ARBUSD": {"wr": 50.0, "net": 14.56, "pf": 1.46, "n": 86, "entry_mode": "LONG_ONLY", "confirm_need": 2, "help": ["spread_proxy", "mtf_macd", "macd", "aggressive_buy", "macd_hist", "volume_spike"], "avoid": ["atr_pct", "bb_width", "realized_vol", "distance_swing_high"], "sl_atr": 1.85, "tp_rr": 2.2, "hold_bars": 10, "threshold": 0.25},
    "ATOMUSD": {"wr": 44.64, "net": 2.59, "pf": 1.03, "n": 224, "entry_mode": "BOTH", "confirm_need": 2, "help": ["spread_proxy", "volume_spike", "volume_ratio", "depth_proxy", "aggressive_buy", "volume_persistence"], "avoid": ["atr_pct", "bb_width", "realized_vol", "sr_room"], "sl_atr": 1.85, "tp_rr": 2.0, "hold_bars": 10, "threshold": 0.12},
    "AVAXUSD": {"wr": 55.5, "net": 42.502, "pf": 1.59, "n": 191, "entry_mode": "BOTH", "confirm_need": 2, "help": ["sr_position", "ltf_align", "mtf_rsi", "rsi_14", "wick_balance", "distance_swing_high"], "avoid": ["volume_persistence", "volume_trend", "volume_ratio", "depth_proxy"], "sl_atr": 1.85, "tp_rr": 2.2, "hold_bars": 14, "threshold": 0.12},
    "AXSUSD": {"wr": 57.14, "net": 3.91, "pf": 1.65, "n": 14, "entry_mode": "BOTH", "confirm_need": 2, "help": ["spread_proxy", "absorption_proxy", "volume_spike", "aggressive_buy", "volume_persistence", "volume_dryup"], "avoid": ["ltf_align", "ema9_21", "mtf_direction", "mtf_ema_stack"], "sl_atr": 1.55, "tp_rr": 2.5, "hold_bars": 18, "threshold": 0.12},
    "BNBUSD": {"wr": 52.58, "net": 27.055, "pf": 1.73, "n": 97, "entry_mode": "LONG_ONLY", "confirm_need": 2, "help": ["volume_ratio", "depth_proxy", "fvg_bull", "mtf_macd", "macd", "spread_proxy"], "avoid": ["distance_swing_high", "distance_swing_low", "sr_room", "pullback_quality"], "sl_atr": 1.55, "tp_rr": 2.0, "hold_bars": 10, "threshold": 0.25},
    "BTCUSD": {"wr": 51.22, "net": 0.77, "pf": 1.05, "n": 41, "entry_mode": "SHORT_ONLY", "confirm_need": 2, "help": ["macd_hist", "lh_ll", "mtf_structure", "engulf_proxy", "ob_bear", "ema9_21"], "avoid": ["up_down_volume_proxy", "pullback_quality", "cvd_proxy", "imbalance_proxy"], "sl_atr": 1.85, "tp_rr": 2.0, "hold_bars": 10, "threshold": 0.18},
    "DOGEUSD": {"wr": 69.23, "net": 3.683, "pf": 2.07, "n": 13, "entry_mode": "SHORT_ONLY", "confirm_need": 2, "help": ["volume_ratio", "volume_persistence", "volume_trend", "depth_proxy", "bos_dn", "swing_break"], "avoid": ["ema20_50", "ema50_slope", "hh_hl", "lh_ll"], "sl_atr": 1.85, "tp_rr": 2.0, "hold_bars": 14, "threshold": 0.12},
    "DOTUSD": {"wr": 54.61, "net": 49.695, "pf": 1.96, "n": 141, "entry_mode": "BOTH", "confirm_need": 2, "help": ["wick_balance", "ema50_slope", "mtf_slope", "hh_hl", "freshness", "spread_proxy"], "avoid": ["ema9_21", "mtf_direction", "pullback_quality", "price_vs_ema20"], "sl_atr": 1.85, "tp_rr": 2.0, "hold_bars": 18, "threshold": 0.25},
    "ETHUSD": {"wr": 53.12, "net": 26.497, "pf": 1.48, "n": 160, "entry_mode": "LONG_ONLY", "confirm_need": 2, "help": ["wick_balance", "engulf_proxy", "sr_position", "mtf_rsi", "rsi_14", "rejection"], "avoid": ["rsi_slope", "price_vs_ema20", "range_percentile", "distance_swing_low"], "sl_atr": 1.85, "tp_rr": 2.0, "hold_bars": 10, "threshold": 0.12},
    "FETUSD": {"wr": 58.62, "net": 11.139, "pf": 2.07, "n": 29, "entry_mode": "SHORT_ONLY", "confirm_need": 2, "help": ["anti_chase", "entry_timing", "timing_quality", "ltf_align", "freshness", "ema9_21"], "avoid": ["rsi_slope", "mtf_ema_stack", "ema20_50", "bos_dn"], "sl_atr": 1.7, "tp_rr": 2.0, "hold_bars": 18, "threshold": 0.12},
    "FILUSD": {"wr": 54.55, "net": 21.108, "pf": 1.42, "n": 132, "entry_mode": "LONG_ONLY", "confirm_need": 2, "help": ["equal_high_proxy", "ema20_50", "mtf_persistence", "trend_persistence", "regime_persistence", "ema50_slope"], "avoid": ["mtf_macd", "macd", "ema9_21", "mtf_direction"], "sl_atr": 1.85, "tp_rr": 2.0, "hold_bars": 10, "threshold": 0.12},
    "HBARUSD": {"wr": 51.15, "net": 23.166, "pf": 1.46, "n": 131, "entry_mode": "LONG_ONLY", "confirm_need": 2, "help": ["ema50_slope", "mtf_slope", "wick_balance", "asia_session", "volume_dryup", "inside_bar"], "avoid": ["ema9_21", "mtf_direction", "distance_swing_low", "range_percentile"], "sl_atr": 1.85, "tp_rr": 2.2, "hold_bars": 10, "threshold": 0.12},
    "ICPUSD": {"wr": 45.81, "net": 4.863, "pf": 1.07, "n": 155, "entry_mode": "BOTH", "confirm_need": 2, "help": ["volume_dryup", "sr_position", "freshness", "ny_session", "lh_ll", "bos_dn"], "avoid": ["pullback_quality", "stoch_proxy", "rsi_slope", "body_quality"], "sl_atr": 1.85, "tp_rr": 2.0, "hold_bars": 14, "threshold": 0.18},
    "INJUSD": {"wr": 47.66, "net": 13.576, "pf": 1.29, "n": 107, "entry_mode": "LONG_ONLY", "confirm_need": 2, "help": ["spread_proxy", "mtf_ema_stack", "ema20_50", "volume_persistence", "volume_trend", "engulf_proxy"], "avoid": ["bos_up", "swing_break", "atr_pct", "bb_width"], "sl_atr": 1.7, "tp_rr": 2.0, "hold_bars": 10, "threshold": 0.18},
    "JUPUSD": {"wr": 50.41, "net": 24.909, "pf": 1.53, "n": 121, "entry_mode": "BOTH", "confirm_need": 2, "help": ["roc_8", "ema50_slope", "bos_up", "mtf_slope", "asia_session", "mtf_persistence"], "avoid": ["pullback_quality", "rsi_slope", "price_vs_ema20", "stoch_proxy"], "sl_atr": 1.85, "tp_rr": 2.0, "hold_bars": 10, "threshold": 0.12},
    "LINKUSD": {"wr": 50.94, "net": 21.514, "pf": 1.5, "n": 106, "entry_mode": "LONG_ONLY", "confirm_need": 2, "help": ["mtf_rsi", "rsi_14", "hh_hl", "mtf_structure", "sweep_low_proxy", "asia_session"], "avoid": ["wick_balance", "range_percentile", "distance_swing_low", "sr_room"], "sl_atr": 1.7, "tp_rr": 2.2, "hold_bars": 10, "threshold": 0.25},
    "LTCUSD": {"wr": 52.38, "net": 2.312, "pf": 1.31, "n": 21, "entry_mode": "SHORT_ONLY", "confirm_need": 2, "help": ["ema20_50", "mtf_ema_stack", "wick_balance", "roc_8", "volume_ratio", "depth_proxy"], "avoid": ["rsi_slope", "bos_dn", "swing_break", "mtf_macd"], "sl_atr": 1.55, "tp_rr": 2.2, "hold_bars": 10, "threshold": 0.18},
    "OPUSD": {"wr": 57.38, "net": 20.809, "pf": 1.88, "n": 61, "entry_mode": "SHORT_ONLY", "confirm_need": 2, "help": ["sr_position", "freshness", "ema20_50", "mtf_rsi", "rsi_14", "wick_balance"], "avoid": ["volume_persistence", "volume_trend", "pullback_quality", "bos_dn"], "sl_atr": 1.85, "tp_rr": 2.5, "hold_bars": 18, "threshold": 0.25},
    "RENDERUSD": {"wr": 62.5, "net": 6.471, "pf": 2.29, "n": 16, "entry_mode": "SHORT_ONLY", "confirm_need": 2, "help": ["ema9_21", "lh_ll", "mtf_direction", "mtf_structure", "macd_hist", "anti_chase"], "avoid": ["ema20_50", "engulf_proxy", "spread_proxy", "fvg_bear"], "sl_atr": 1.55, "tp_rr": 2.2, "hold_bars": 10, "threshold": 0.12},
    "SEIUSD": {"wr": 43.83, "net": -1.847, "pf": 0.98, "n": 162, "entry_mode": "LONG_ONLY", "confirm_need": 2, "help": ["sr_position", "mtf_rsi", "rsi_14", "sweep_low_proxy", "engulf_proxy", "expansion"], "avoid": ["distance_swing_low", "sr_room", "pullback_quality", "liquidity_room"], "sl_atr": 1.7, "tp_rr": 2.0, "hold_bars": 10, "threshold": 0.12},
    "SOLUSD": {"wr": 47.66, "net": 14.144, "pf": 1.23, "n": 128, "entry_mode": "LONG_ONLY", "confirm_need": 2, "help": ["hh_hl", "mtf_structure", "asia_session", "ema20_50", "ema50_slope", "mtf_slope"], "avoid": ["bb_width", "realized_vol", "distance_swing_low", "sr_room"], "sl_atr": 1.55, "tp_rr": 2.0, "hold_bars": 14, "threshold": 0.18},
    "STRKUSD": {"wr": 51.76, "net": 7.415, "pf": 1.2, "n": 85, "entry_mode": "SHORT_ONLY", "confirm_need": 2, "help": ["ema20_50", "freshness", "mtf_conflict", "ltf_align", "anti_chase", "entry_timing"], "avoid": ["pullback_quality", "mtf_rsi", "rsi_14", "sr_position"], "sl_atr": 1.85, "tp_rr": 2.0, "hold_bars": 10, "threshold": 0.12},
    "SUIUSD": {"wr": 50.0, "net": 0.297, "pf": 1.02, "n": 32, "entry_mode": "SHORT_ONLY", "confirm_need": 2, "help": ["volume_persistence", "volume_trend", "wick_balance", "rejection", "pinbar_proxy", "fvg_bear"], "avoid": ["mtf_rsi", "rsi_14", "sr_position", "pullback_quality"], "sl_atr": 1.85, "tp_rr": 2.5, "hold_bars": 10, "threshold": 0.12},
    "TIAUSD": {"wr": 55.26, "net": 11.467, "pf": 1.84, "n": 38, "entry_mode": "LONG_ONLY", "confirm_need": 2, "help": ["ema50_slope", "mtf_slope", "volume_persistence", "volume_trend", "volume_ratio", "spread_proxy"], "avoid": ["anti_chase", "entry_timing", "timing_quality", "bos_up"], "sl_atr": 1.85, "tp_rr": 2.5, "hold_bars": 10, "threshold": 0.12},
    "TONUSD": {"wr": 55.71, "net": 10.663, "pf": 1.38, "n": 70, "entry_mode": "BOTH", "confirm_need": 2, "help": ["rsi_slope", "up_down_volume_proxy", "cvd_proxy", "aggressive_buy", "imbalance_proxy", "mtf_rsi"], "avoid": ["distance_swing_high", "mtf_conflict", "volume_ratio", "volume_spike"], "sl_atr": 1.85, "tp_rr": 2.0, "hold_bars": 10, "threshold": 0.12},
    "TRXUSD": {"wr": 54.35, "net": 61.446, "pf": 1.87, "n": 184, "entry_mode": "BOTH", "confirm_need": 2, "help": ["freshness", "body_quality", "mtf_persistence", "trend_persistence", "regime_persistence", "distance_swing_high"], "avoid": ["pullback_quality", "mtf_rsi", "rsi_14", "sr_position"], "sl_atr": 1.85, "tp_rr": 2.2, "hold_bars": 14, "threshold": 0.12},
    "UNIUSD": {"wr": 52.94, "net": 17.931, "pf": 1.36, "n": 153, "entry_mode": "LONG_ONLY", "confirm_need": 2, "help": ["mtf_rsi", "rsi_14", "sr_position", "volume_spike", "asia_session", "follow_through"], "avoid": ["mtf_conflict", "range_percentile", "atr_pct", "bb_width"], "sl_atr": 1.85, "tp_rr": 2.5, "hold_bars": 10, "threshold": 0.12},
    "WIFUSD": {"wr": 63.16, "net": 12.001, "pf": 1.9, "n": 38, "entry_mode": "SHORT_ONLY", "confirm_need": 2, "help": ["volume_ratio", "spread_proxy", "depth_proxy", "volume_persistence", "volume_dryup", "volume_trend"], "avoid": ["ema9_21", "ema50_slope", "mtf_direction", "mtf_slope"], "sl_atr": 1.55, "tp_rr": 2.2, "hold_bars": 10, "threshold": 0.25},
    "WLDUSD": {"wr": 48.84, "net": 6.924, "pf": 1.35, "n": 43, "entry_mode": "SHORT_ONLY", "confirm_need": 2, "help": ["wick_balance", "volume_ratio", "depth_proxy", "spread_proxy", "mtf_conflict", "mtf_rsi"], "avoid": ["price_vs_ema20", "ema50_price", "htf_bias", "roc_8"], "sl_atr": 1.85, "tp_rr": 2.2, "hold_bars": 18, "threshold": 0.12},
    "XLMUSD": {"wr": 50.7, "net": 13.283, "pf": 1.48, "n": 71, "entry_mode": "BOTH", "confirm_need": 2, "help": ["freshness", "london_session", "anti_chase", "entry_timing", "timing_quality", "ema50_slope"], "avoid": ["mtf_conflict", "volume_spike", "absorption_proxy", "volume_ratio"], "sl_atr": 1.7, "tp_rr": 2.2, "hold_bars": 10, "threshold": 0.12},
    "XRPUSD": {"wr": 63.41, "net": 15.811, "pf": 2.45, "n": 41, "entry_mode": "LONG_ONLY", "confirm_need": 2, "help": ["london_session", "volume_persistence", "volume_trend", "distance_swing_high", "rsi_slope", "anti_chase"], "avoid": ["macd_hist", "up_down_volume_proxy", "expansion", "atr_expand"], "sl_atr": 1.85, "tp_rr": 2.5, "hold_bars": 10, "threshold": 0.25},
}
GLOBAL_FACTOR_BUDGET_TOP = {"spread_proxy": 145.69, "asia_session": 118.93, "ema50_slope": 108.03, "mtf_slope": 108.03, "freshness": 98.12, "volume_dryup": 55.5, "atr_expand": 43.61, "mtf_structure": 42.62, "sr_position": 38.65, "wick_balance": 36.67, "expansion": 35.68, "hh_hl": 27.75, "fvg_bear": 23.79, "london_session": 18.83, "compression": 17.84, "ob_bear": 17.84, "sweep_low_proxy": 15.86, "lh_ll": 14.87, "ny_session": 11.89, "mtf_rsi": 6.94, "rsi_14": 6.94, "aggressive_sell": 5.95}

def coin_playbook(symbol: str) -> dict:
    sym = sanitize_symbol(symbol)
    return dict(COIN_PLAYBOOK.get(sym) or {
        "confirm_need": 2, "entry_mode": "BOTH", "help": [], "avoid": [],
        "sl_atr": 1.7, "tp_rr": 2.0, "hold_bars": 14, "threshold": 0.18,
    })

def coin_factor_weights(symbol: str) -> dict:
    sym = sanitize_symbol(symbol)
    return dict(COIN_FACTOR_WEIGHTS.get(sym) or {})

def v19_count_confirms(snap: dict, direction: str):
    d = {}
    long = direction == "LONG"
    s5 = snap.get("struct5") or snap.get("structure") or {}
    if not isinstance(s5, dict):
        s5 = {}
    struct = str(s5.get("bias") or s5.get("structure") or snap.get("struct") or "").lower()
    bos = bool(s5.get("bos") or s5.get("bos_up") or s5.get("bos_dn") or snap.get("bos"))
    d["structure"] = any(x in struct for x in (("bull","long","hh","hl","up") if long else ("bear","short","lh","ll","down")))
    d["bos"] = bool(bos)
    h1 = str(snap.get("bias_1h") or "").lower()
    if long:
        d["mtf"] = (not h1) or any(x in h1 for x in ("bull", "up", "long"))
        if "bear" in h1 and "bull" not in h1:
            d["mtf"] = False
    else:
        d["mtf"] = (not h1) or any(x in h1 for x in ("bear", "down", "short"))
        if "bull" in h1 and "bear" not in h1:
            d["mtf"] = False
    try:
        d["anti_chase"] = float(snap.get("move_atr") or snap.get("chase") or 0) < 1.2
    except Exception:
        d["anti_chase"] = True
    try:
        vol = snap.get("vol_score") or snap.get("volume")
        d["volume"] = True if vol is None else float(vol) >= 4.5
    except Exception:
        d["volume"] = True
    macd = str(snap.get("macd") or "").lower()
    d["macd"] = True if not macd else (("bear" not in macd) if long else ("bull" not in macd))
    trend = str(snap.get("trend") or "").lower()
    d["trend"] = True if not trend else (("bear" not in trend) if long else ("bull" not in trend))
    return sum(1 for v in d.values() if v), d

DEFAULT_RISK = {"sl_atr": 1.85, "tp_rr": 2.00, "hold": 20}




# ----- v19.1 PENDING → CONFIRM → FIRE -----
# T-15m / 1-2 bars: arm setup (levels ready). Fire only on confirm. Else SKIP (not a loss).

PENDING_SETUPS = {}  # symbol -> arm dict
ARM_MAX_BARS = 6  # v19.3 longer arm window     # ~30m*3 or wait window in BT bars
FIRE_MIN_CONFIRMS = 2  # v19.3 keep soft fire

def arm_setup(symbol: str, direction: str, entry: float, sl: float, tp: float,
              score: float, bar_index: int, meta: dict = None) -> dict:
    """Pre-calculate trade; do NOT enter yet."""
    sym = sanitize_symbol(symbol)
    arm = {
        "symbol": sym,
        "direction": direction,
        "entry_zone": float(entry),
        "sl": float(sl),
        "tp": float(tp),
        "score": float(score),
        "armed_bar": int(bar_index),
        "expires_bar": int(bar_index) + ARM_MAX_BARS,
        "meta": meta or {},
        "status": "PENDING",
    }
    PENDING_SETUPS[sym] = arm
    return arm

def clear_arm(symbol: str):
    PENDING_SETUPS.pop(sanitize_symbol(symbol), None)

def get_arm(symbol: str):
    return PENDING_SETUPS.get(sanitize_symbol(symbol))

def confirm_and_fire(symbol: str, snap: dict, bar_index: int, price: float) -> tuple:
    """
    Returns (action, payload)
      action: "FIRE" | "WAIT" | "SKIP" | "NONE"
    SKIP = no trade, not a loss (timing/chase/no-follow handled here).
    """
    sym = sanitize_symbol(symbol)
    arm = PENDING_SETUPS.get(sym)
    if not arm:
        return "NONE", {}
    if bar_index > arm["expires_bar"]:
        clear_arm(sym)
        return "SKIP", {"why": "ARM_EXPIRED", "arm": arm}
    direction = arm["direction"]
    n_ok, detail = v19_count_confirms(snap or {}, direction)
    # once armed, slightly softer confirm to actually FIRE in BT
    need = max(FIRE_MIN_CONFIRMS, int((coin_profile(sym) or {}).get("confirm_need") or FIRE_MIN_CONFIRMS) - 1)
    # chase: price already ran away from arm entry
    try:
        entry = float(arm["entry_zone"])
        atr = float((snap or {}).get("atr") or 0) or abs(entry) * 0.01
        move = abs(float(price) - entry) / max(atr, 1e-12)
        if move > 1.45:
            clear_arm(sym)
            return "SKIP", {"why": "CHASE_AFTER_ARM", "arm": arm, "confirms": detail}
    except Exception:
        pass
    if n_ok < need:
        return "WAIT", {"why": "WAIT_CONFIRM", "n": n_ok, "need": need, "confirms": detail, "arm": arm}
    # fire
    clear_arm(sym)
    return "FIRE", {"arm": arm, "confirms": detail, "n": n_ok}

def classify_post_trade_loss(mfe_r: float, mae_r: float, direction: str, hit: str) -> str:
    """v19.1 taxonomy: almost all adverse outcomes = WRONG_DIRECTION (market).
    Timing/no-follow should have been skipped pre-trade — not labeled as separate loss types.
    """
    return "WRONG_DIRECTION"



def enforce_min_rr(tp_rr, floor=None):
    """v20.4: never allow TP below 1:2."""
    try:
        f = float(floor if floor is not None else globals().get("MIN_RR", 2.0))
        v = float(tp_rr or f)
        return max(v, f)
    except Exception:
        return 2.0

def coin_risk(symbol: str) -> dict:
    _sym = str(symbol).upper().replace('USDT', 'USD')
    if 'COIN_RISK_V22' in globals() and _sym in COIN_RISK_V22:
        _r = dict(COIN_RISK_V22[_sym])
        if 'DRAG_STRICT' in globals() and _sym in DRAG_STRICT and DRAG_STRICT[_sym].get('chase_tight'):
            _r['chase'] = float(DRAG_STRICT[_sym]['chase_tight'])
        # v20.4 min RRR 1:2 hard
        try:
            if float(_r.get('tp_rr') or 0) < 2.0:
                _r['tp_rr'] = 2.0
        except Exception:
            _r['tp_rr'] = 2.0
        return _r
    """v19.0 complete risk — TP never below 1:2."""
    sym = sanitize_symbol(symbol)
    r = dict(DEFAULT_RISK)
    r.update(COIN_RISK.get(sym) or {})
    pb = coin_playbook(sym)
    r["sl_atr"] = float(r.get("sl_atr") or pb.get("sl_atr") or 1.7)
    r["tp_rr"] = max(2.0, float(r.get("tp_rr") or pb.get("tp_rr") or 2.0))
    r["hold"] = int(r.get("hold") or pb.get("hold_bars") or 14)
    return r


def coin_factor_weights(symbol: str) -> dict:
    """Per-coin factor weights from 60d finder (which factor works how much)."""
    sym = sanitize_symbol(symbol)
    return dict(COIN_FACTOR_WEIGHTS.get(sym) or {})


LEARN_FROM_BACKTEST = True
BLOCK_FLAT_HTF      = True
BLOCK_SHORT_1H_FLAT = True
PROTECT_CORE_WEIGHTS = True

CANDLE_CACHE_TTL = 35
TICKER_CACHE_TTL = 5
OB_CACHE_TTL = 4
HTTP_TIMEOUT = 12
TG_TIMEOUT = 25
MAX_MESSAGE_LEN = 3900
DELTA_RETRIES = 3
DELTA_RETRY_SLEEP = 0.5

# Security
ALLOWED_CHAT_IDS = {str(TELEGRAM_CHAT_ID)} if TELEGRAM_CHAT_ID else set()
MAX_CMD_PER_MIN = 18          # soft rate limit per chat
MAX_SYMBOL_LEN = 16
SAFE_SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,16}$")

POPULAR_COINS = [
    "ATOMUSD",
    "FTMUSD",
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "DOGEUSD",
    "ADAUSD", "AVAXUSD", "LINKUSD", "SUIUSD", "BNBUSD",
    "DOTUSD", "LTCUSD", "BCHUSD", "NEARUSD", "APTUSD",
    "ARBUSD", "OPUSD", "INJUSD", "FILUSD", "AAVEUSD",
    "UNIUSD", "MKRUSD", "PEPEUSD", "WIFUSD", "TIAUSD",
    "SEIUSD", "RENDERUSD",
    # v18 new
    "TRXUSD", "TONUSD", "JUPUSD", "WLDUSD", "STRKUSD",
    "ENAUSD", "ONDOUSD", "FETUSD", "IMXUSD", "CRVUSD",
]

WATCHLIST_DEFAULT = [
    "LINKUSD", "TRXUSD", "ARBUSD", "ETHUSD", "APTUSD", "NEARUSD", "AAVEUSD", "OPUSD", "DOGEUSD", "LTCUSD", "UNIUSD", "AVAXUSD", "BNBUSD", "XRPUSD", "DOTUSD", "BTCUSD", "ADAUSD", "SUIUSD", "FILUSD",
]

RHYTHM_COINS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BNBUSD", "AVAXUSD"]

SESSION_WINDOWS = {
    "ASIA": (3, 30, 11, 30),
    "LONDON": (12, 30, 17, 0),
    "NY": (18, 0, 23, 30),
    "OVERLAP_LN_NY": (18, 0, 21, 0),
}
HIGH_IMPACT_IST = [(18, 45, 20, 45)]

DEFAULT_WEIGHTS = {
    # v16.2 — protect core after v16.1 learning collapsed structure to 0.01
    "structure": 0.145,
    "alignment": 0.140,
    "entry_timing": 0.120,
    "momentum": 0.105,
    "location": 0.090,
    "volume": 0.075,      # INSUFFICIENT_DATA / LOW_VOLUME
    "rhythm": 0.070,
    "liquidity": 0.065,
    "fvg_ob": 0.045,
    "oi_funding": 0.040,
    "orderbook": 0.035,
    "orderflow": 0.030,
    "volatility": 0.020,
    "correlation": 0.015,
    "session": 0.010,
}
# floors so learning cannot kill primary filters
WEIGHT_FLOOR = {
    "structure": 0.10, "alignment": 0.10, "momentum": 0.08,
    "entry_timing": 0.10, "location": 0.06, "volume": 0.05,  # v17.4 entry protected
}

# ═══════════════════════════════════════════════════════════════
# 2. LOGGING (no secrets)
# ═══════════════════════════════════════════════════════════════
class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        if TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_TOKEN in msg:
            record.msg = msg.replace(TELEGRAM_BOT_TOKEN, "***TOKEN***")
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("committee-v20.4")
log.addFilter(RedactFilter())

# ═══════════════════════════════════════════════════════════════
# 3. GLOBAL STATE + RATE LIMIT
# ═══════════════════════════════════════════════════════════════
_state_lock = threading.RLock()

STATE = {
    "symbol": DEFAULT_SYMBOL,
    "last_update_id": 0,
    "last_decision": None,
    "last_scan": None,
    "account_usd": ACCOUNT_USD_DEFAULT,
    "risk_pct": RISK_PCT_DEFAULT,
    "watchlist": list(WATCHLIST_DEFAULT),
    "bot_active": True,
    "user_notes": "",
    "market_rhythm": None,
    "cmd_times": defaultdict(list),  # chat_id → timestamps
}

_ticker_cache: Dict[str, Tuple[float, dict]] = {}
_candle_cache: Dict[str, Tuple[float, dict]] = {}
_ob_cache: Dict[str, Tuple[float, dict]] = {}

# ═══════════════════════════════════════════════════════════════
# V12 DELTA PUBLIC WEBSOCKET (trades / L2 / funding)
# ═══════════════════════════════════════════════════════════════
WS_URL = os.getenv("DELTA_WS_URL", "wss://public-socket.india.delta.exchange")
# Also try legacy if public fails: wss://socket.india.delta.exchange
WS_ENABLED = os.getenv("WS_ENABLED", "1") != "0"
WS_STALE_SEC = float(os.getenv("WS_STALE_SEC", "12"))
WS_TRADE_BUF = 400

_ws_state = {
    "connected": False,
    "last_msg_ts": 0.0,
    "last_error": "",
    "symbols": set(),
}
_ws_trades: Dict[str, deque] = defaultdict(lambda: deque(maxlen=WS_TRADE_BUF))
_ws_books: Dict[str, dict] = {}
_ws_funding: Dict[str, float] = {}
_ws_ticker: Dict[str, dict] = {}
_ws_lock = threading.RLock()
_ws_thread = None


class DeltaPublicWS:
    """Background public WS with reconnect. Optional if websocket-client missing."""

    def __init__(self, symbols: List[str]):
        self.symbols = [s for s in symbols if s]
        self._stop = threading.Event()
        self._ws = None

    def stop(self):
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    def _subscribe_payload(self) -> dict:
        # Prefer new public channel names; server may accept legacy aliases
        chans = []
        for name in ("trades", "l2_orderbook", "funding_rate", "v2/ticker"):
            chans.append({"name": name, "symbols": list(self.symbols)})
        return {"type": "subscribe", "payload": {"channels": chans}}

    def _on_message(self, _ws, message: str):
        try:
            msg = json.loads(message)
        except Exception:
            return
        now = time.time()
        with _ws_lock:
            _ws_state["last_msg_ts"] = now
            _ws_state["connected"] = True
        mtype = str(msg.get("type") or msg.get("channel") or "")
        sym = msg.get("symbol") or msg.get("sy") or ""
        # trades
        if mtype in ("trades", "all_trades", "recent_trade", "all_trades_snapshot"):
            trades = msg.get("trades") or msg.get("result") or ([msg] if msg.get("price") else [])
            if isinstance(trades, dict):
                trades = [trades]
            for t in trades or []:
                self._ingest_trade(sym or t.get("symbol"), t)
        elif "trade" in mtype.lower() and msg.get("price"):
            self._ingest_trade(sym, msg)
        # orderbook
        elif mtype in ("l2_orderbook", "ob_l2", "l2_updates", "ob_updates"):
            self._ingest_book(sym, msg)
        # funding
        elif "funding" in mtype.lower():
            rate = safe_float(msg.get("funding_rate") or msg.get("rate") or msg.get("funding"))
            if sym:
                with _ws_lock:
                    _ws_funding[sym] = rate
        # ticker
        elif "ticker" in mtype.lower():
            if sym:
                with _ws_lock:
                    _ws_ticker[sym] = msg

    def _ingest_trade(self, sym, t: dict):
        if not sym or not t:
            return
        side = str(t.get("side") or t.get("taker_side") or t.get("buyer_role") or "").lower()
        size = safe_float(t.get("size") or t.get("quantity") or t.get("qty") or t.get("volume"))
        price = safe_float(t.get("price"))
        if not size:
            return
        # normalize side
        if side in ("buy", "b", "long", "bid"):
            s = "buy"
        elif side in ("sell", "s", "short", "ask"):
            s = "sell"
        else:
            s = "buy" if t.get("buyer_role") == "taker" else "sell" if t.get("seller_role") == "taker" else "unknown"
        with _ws_lock:
            _ws_trades[sym].append({"ts": time.time(), "side": s, "size": size, "price": price})

    def _ingest_book(self, sym, msg: dict):
        if not sym:
            return
        buy = msg.get("buy") or msg.get("bids") or []
        sell = msg.get("sell") or msg.get("asks") or []
        def norm(levels):
            out = []
            for x in levels[:40]:
                if isinstance(x, dict):
                    out.append((safe_float(x.get("price")), safe_float(x.get("size") or x.get("depth"))))
                elif isinstance(x, (list, tuple)) and len(x) >= 2:
                    out.append((safe_float(x[0]), safe_float(x[1])))
            return out
        bids, asks = norm(buy), norm(sell)
        bid_vol = sum(s for _, s in bids) or 1e-9
        ask_vol = sum(s for _, s in asks) or 1e-9
        best_bid = bids[0][0] if bids else 0
        best_ask = asks[0][0] if asks else 0
        mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
        spread = (best_ask - best_bid) if best_bid and best_ask else 0
        with _ws_lock:
            _ws_books[sym] = {
                "ts": time.time(),
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid": mid,
                "spread": spread,
                "spread_bps": (spread / mid * 10000) if mid else 0,
                "bid_vol": bid_vol,
                "ask_vol": ask_vol,
                "imbalance": (bid_vol - ask_vol) / (bid_vol + ask_vol),
                "bids": bids[:10],
                "asks": asks[:10],
                "source": "ws",
            }

    def _on_error(self, _ws, err):
        with _ws_lock:
            _ws_state["last_error"] = str(err)[:200]
            _ws_state["connected"] = False
        log.warning("ws error: %s", err)

    def _on_close(self, *_a):
        with _ws_lock:
            _ws_state["connected"] = False
        log.info("ws closed")

    def _on_open(self, ws):
        with _ws_lock:
            _ws_state["connected"] = True
            _ws_state["symbols"] = set(self.symbols)
        try:
            ws.send(json.dumps(self._subscribe_payload()))
            log.info("ws subscribed: %s", self.symbols)
        except Exception as e:
            log.warning("ws subscribe fail: %s", e)

    def run_forever(self):
        if not HAS_WS:
            log.warning("websocket-client not installed — WS disabled (pip install websocket-client)")
            return
        backoff = 2
        while not self._stop.is_set():
            try:
                self._ws = websocket.WebSocketApp(
                    WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                log.warning("ws loop: %s", e)
            with _ws_lock:
                _ws_state["connected"] = False
            if self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(60, backoff * 1.5)


def start_ws(symbols: List[str] = None):
    global _ws_thread
    if not WS_ENABLED or not HAS_WS:
        return
    symbols = symbols or list(dict.fromkeys([DEFAULT_SYMBOL] + RHYTHM_COINS[:4]))
    if _ws_thread and _ws_thread.is_alive():
        return
    client = DeltaPublicWS(symbols)
    _ws_thread = threading.Thread(target=client.run_forever, name="delta-ws", daemon=True)
    _ws_thread.start()
    log.info("WS thread started → %s", WS_URL)


def ws_status() -> dict:
    with _ws_lock:
        age = time.time() - _ws_state["last_msg_ts"] if _ws_state["last_msg_ts"] else 9999
        return {
            "enabled": WS_ENABLED and HAS_WS,
            "connected": _ws_state["connected"],
            "age_sec": round(age, 1),
            "stale": age > WS_STALE_SEC,
            "error": _ws_state.get("last_error", ""),
            "trade_symbols": list(_ws_trades.keys())[:8],
            "book_symbols": list(_ws_books.keys())[:8],
        }


def ws_trades_snapshot(symbol: str, n: int = 80) -> List[dict]:
    with _ws_lock:
        return list(_ws_trades.get(symbol, []))[-n:]


def ws_book_snapshot(symbol: str) -> Optional[dict]:
    with _ws_lock:
        b = _ws_books.get(symbol)
        if not b:
            return None
        if time.time() - b.get("ts", 0) > WS_STALE_SEC:
            return None
        return dict(b)


def ws_funding(symbol: str) -> Optional[float]:
    with _ws_lock:
        return _ws_funding.get(symbol)


# ═══════════════════════════════════════════════════════════════
# V11 ADVANCED DATA PROVIDER CONFIG
# ═══════════════════════════════════════════════════════════════
# Optional JSON endpoints. Leave blank when unavailable; the engine will
# explicitly mark a module as unavailable instead of inventing data.
TRADE_FEED_URL = os.getenv("TRADE_FEED_URL", "")
LIQUIDATION_FEED_URL = os.getenv("LIQUIDATION_FEED_URL", "")
OPTIONS_FEED_URL = os.getenv("OPTIONS_FEED_URL", "")
ONCHAIN_FEED_URL = os.getenv("ONCHAIN_FEED_URL", "")
STABLECOIN_FEED_URL = os.getenv("STABLECOIN_FEED_URL", "")
MACRO_FEED_URL = os.getenv("MACRO_FEED_URL", "")
NEWS_FEED_URL = os.getenv("NEWS_FEED_URL", "")

ADV_HISTORY_MAX = 720
ADV_SAMPLE_TTL = 2.0

_advanced_history = defaultdict(lambda: {
    "orderbook": deque(maxlen=ADV_HISTORY_MAX),
    "ticker": deque(maxlen=ADV_HISTORY_MAX),
    "flow": deque(maxlen=ADV_HISTORY_MAX),
    "liquidation": deque(maxlen=ADV_HISTORY_MAX),
    "features": deque(maxlen=ADV_HISTORY_MAX),
})
_adv_lock = threading.RLock()


def _safe_json_url(url: str, params: dict = None) -> Optional[dict]:
    """Optional external feed. No URL means unavailable; no fake values."""
    if not url:
        return None
    try:
        r = requests.get(url, params=params or {}, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        log.warning("advanced feed error: %s", e)
        return None


def _push_history(symbol: str, key: str, value: dict):
    with _adv_lock:
        _advanced_history[symbol][key].append({"ts": time.time(), **value})


def _history(symbol: str, key: str, n: int = 120) -> list:
    with _adv_lock:
        return list(_advanced_history[symbol][key])[-n:]


def _pct_change(a, b):
    a, b = safe_float(a), safe_float(b)
    return ((a - b) / b * 100.0) if b else 0.0


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _slope(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    xm = (n - 1) / 2.0
    ym = _mean(xs)
    den = sum((i - xm) ** 2 for i in range(n)) or 1.0
    return sum((i - xm) * (y - ym) for i, y in enumerate(xs)) / den


def _corr(a, b):
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    a, b = list(a)[-n:], list(b)[-n:]
    ma, mb = _mean(a), _mean(b)
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((x - mb) ** 2 for x in b))
    if not da or not db:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (da * db)


def _rank_percentile(xs, x):
    if not xs:
        return 50.0
    return 100.0 * sum(v <= x for v in xs) / len(xs)


def _returns(closes):
    return [((closes[i] / closes[i-1]) - 1.0) for i in range(1, len(closes)) if closes[i-1]]


def _skew(xs):
    if len(xs) < 3:
        return 0.0
    m, sd = _mean(xs), _std(xs)
    return _mean([((x-m)/sd)**3 for x in xs]) if sd else 0.0


def _kurtosis(xs):
    if len(xs) < 4:
        return 0.0
    m, sd = _mean(xs), _std(xs)
    return _mean([((x-m)/sd)**4 for x in xs]) - 3.0 if sd else 0.0


def _autocorr(xs, lag=1):
    if len(xs) <= lag + 2:
        return 0.0
    return _corr(xs[lag:], xs[:-lag])


def _percentile_range(xs, q):
    if not xs:
        return 0.0
    ys = sorted(xs)
    pos = clamp((len(ys)-1) * q, 0, len(ys)-1)
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    return ys[lo] + (ys[hi]-ys[lo]) * (pos-lo)


def advanced_candle_microstructure(opens, highs, lows, closes, volumes) -> dict:
    if not closes:
        return {"available": False}
    body = abs(closes[-1]-opens[-1])
    rng = max(highs[-1]-lows[-1], 1e-12)
    upper = highs[-1]-max(opens[-1], closes[-1])
    lower = min(opens[-1], closes[-1])-lows[-1]
    close_loc = (closes[-1]-lows[-1])/rng
    overlap = 0.0
    if len(closes) >= 2:
        prev_lo, prev_hi = lows[-2], highs[-2]
        inter = max(0.0, min(highs[-1], prev_hi)-max(lows[-1], prev_lo))
        overlap = inter / max(min(rng, prev_hi-prev_lo), 1e-12)
    ranges = [max(h-l, 0.0) for h,l in zip(highs[-12:], lows[-12:])]
    avg_rng = _mean(ranges[:-1]) or rng
    direction = 1 if closes[-1] > opens[-1] else -1 if closes[-1] < opens[-1] else 0
    return {
        "available": True, "body_ratio": body/rng, "upper_wick_ratio": upper/rng,
        "lower_wick_ratio": lower/rng, "close_location": close_loc,
        "overlap_pct": overlap*100, "range_expansion": rng/max(avg_rng,1e-12),
        "direction": direction,
    }


def microstructure_engine(opens, highs, lows, closes, volumes) -> dict:
    """Candle-level microstructure; True tick/order-flow requires trade feed."""
    if len(closes) < 8:
        return {"available": False, "mode": "candle"}
    mh, ml = find_swings_advanced(highs, lows, 1, 1)
    last = closes[-1]
    micro_bos = None
    if mh and last > mh[-1]["price"]:
        micro_bos = "Bullish micro-BOS"
    elif ml and last < ml[-1]["price"]:
        micro_bos = "Bearish micro-BOS"
    cm = advanced_candle_microstructure(opens, highs, lows, closes, volumes)
    vel = abs(closes[-1]-closes[-2])
    prev_vel = _mean([abs(closes[i]-closes[i-1]) for i in range(max(1,len(closes)-6),len(closes)-1)])
    return {
        "available": True, "mode": "candle_proxy", "micro_bos": micro_bos,
        "velocity": vel, "velocity_ratio": vel/max(prev_vel,1e-12),
        "candle": cm, "micro_hh": bool(mh and mh[-1]["price"] > mh[-2]["price"]) if len(mh)>=2 else False,
        "micro_hl": bool(ml and ml[-1]["price"] > ml[-2]["price"]) if len(ml)>=2 else False,
        "micro_lh": bool(mh and mh[-1]["price"] < mh[-2]["price"]) if len(mh)>=2 else False,
        "micro_ll": bool(ml and ml[-1]["price"] < ml[-2]["price"]) if len(ml)>=2 else False,
    }


def orderflow_engine(symbol: str, closes, volumes, highs=None, lows=None) -> dict:
    """
    Priority:
      1) Delta WS live trades → mode=ws_trades (True CVD path)
      2) Optional TRADE_FEED_URL JSON
      3) Candle-volume proxy (never labeled as real)
    """
    buys = sells = delta = 0.0
    trades = []
    mode = "unavailable"

    # 1) WebSocket trades
    ws_tr = ws_trades_snapshot(symbol, 120)
    if ws_tr:
        for t in ws_tr:
            qty = safe_float(t.get("size"))
            side = t.get("side")
            if side == "buy":
                buys += qty
            elif side == "sell":
                sells += qty
        delta = buys - sells
        mode = "ws_trades"
        trades = ws_tr
    else:
        raw = _safe_json_url(TRADE_FEED_URL, {"symbol": symbol})
        if isinstance(raw, dict):
            trades = raw.get("trades") or raw.get("result") or []
        if isinstance(trades, list) and trades:
            for t in trades:
                qty = safe_float(t.get("size") or t.get("qty") or t.get("volume"))
                side = str(t.get("side") or t.get("taker_side") or "").lower()
                if side in ("buy", "b", "long"):
                    buys += qty
                elif side in ("sell", "s", "short"):
                    sells += qty
            delta = buys - sells
            mode = "real_trade_feed"
        elif closes and volumes:
            for i in range(max(0, len(closes) - 12), len(closes)):
                v = safe_float(volumes[i])
                d = 1 if closes[i] > (closes[i - 1] if i else closes[i]) else (
                    -1 if closes[i] < (closes[i - 1] if i else closes[i]) else 0
                )
                if d > 0:
                    buys += v
                elif d < 0:
                    sells += v
            delta = buys - sells
            mode = "candle_volume_proxy"

    total = buys + sells
    aggression = (delta / total) if total else 0.0
    hist = _history(symbol, "flow", 120)
    _push_history(symbol, "flow", {"delta": delta, "buys": buys, "sells": sells, "aggression": aggression, "mode": mode})
    deltas = [safe_float(x.get("delta")) for x in hist]
    cvd = sum(deltas) + delta
    speed = 0.0
    if mode == "ws_trades" and trades:
        t0 = trades[0].get("ts") or time.time()
        t1 = trades[-1].get("ts") or time.time()
        dur = max(t1 - t0, 0.5)
        speed = len(trades) / dur
    return {
        "available": mode != "unavailable",
        "mode": mode,
        "buy_volume": buys,
        "sell_volume": sells,
        "delta": delta,
        "aggression": aggression,
        "cvd": cvd,
        "delta_accel": delta - (deltas[-1] if deltas else 0),
        "absorption": abs(aggression) < 0.08 and abs(delta) > 0,
        "exhaustion": abs(aggression) > 0.75 and len(deltas) >= 3 and abs(delta) < abs(_mean(deltas[-3:])) if deltas else False,
        "trade_count": len(trades),
        "execution_speed": speed,
        "ws": ws_status(),
    }


def orderbook_advanced(symbol: str, ob: dict) -> dict:
    if not ob:
        return {"available":False,"mode":"unavailable"}
    now=time.time()
    snap={"imbalance":safe_float(ob.get("imbalance")),"spread_bps":safe_float(ob.get("spread_bps")),
          "bid_vol":safe_float(ob.get("bid_vol")),"ask_vol":safe_float(ob.get("ask_vol")),
          "best_bid":safe_float(ob.get("best_bid")),"best_ask":safe_float(ob.get("best_ask"))}
    _push_history(symbol,"orderbook",snap)
    hist=_history(symbol,"orderbook",120)
    prev=hist[-2] if len(hist)>=2 else snap
    bid_delta=snap["bid_vol"]-safe_float(prev.get("bid_vol"))
    ask_delta=snap["ask_vol"]-safe_float(prev.get("ask_vol"))
    mid=snap["mid"] if "mid" in snap else (snap["best_bid"]+snap["best_ask"])/2
    def depth_at(levels, pct):
        lo=mid*(1-pct/100); hi=mid*(1+pct/100)
        bv=sum(s for p,s in (ob.get("bids") or []) if p>=lo)
        av=sum(s for p,s in (ob.get("asks") or []) if p<=hi)
        return bv,av
    d1=depth_at(None,0.10); d2=depth_at(None,0.25)
    return {"available":True,"mode":"l2_snapshot_history","bid_delta":bid_delta,"ask_delta":ask_delta,
            "stacking_bid":bid_delta>0,"stacking_ask":ask_delta>0,"pulling_bid":bid_delta<0,"pulling_ask":ask_delta<0,
            "depth_10pct":d1,"depth_25pct":d2,
            "depth_imbalance_10":(d1[0]-d1[1])/max(d1[0]+d1[1],1e-9),
            "depth_imbalance_25":(d2[0]-d2[1])/max(d2[0]+d2[1],1e-9),
            "wall_bid":max((s for _,s in (ob.get("bids") or [])),default=0),
            "wall_ask":max((s for _,s in (ob.get("asks") or [])),default=0),
            "history_samples":len(hist)}


def liquidation_engine(symbol: str) -> dict:
    raw=_safe_json_url(LIQUIDATION_FEED_URL,{"symbol":symbol})
    rows=(raw.get("liquidations") if isinstance(raw,dict) else None) if raw else None
    if not rows:
        return {"available":False,"mode":"unavailable","detail":"Liquidation feed not configured"}
    long_amt=sum(safe_float(x.get("size") or x.get("amount")) for x in rows if str(x.get("side","")).lower() in ("long","buy"))
    short_amt=sum(safe_float(x.get("size") or x.get("amount")) for x in rows if str(x.get("side","")).lower() in ("short","sell"))
    clusters=[]
    for x in rows:
        price=safe_float(x.get("price"))
        amt=safe_float(x.get("size") or x.get("amount"))
        if price and amt: clusters.append((price,amt))
    total=long_amt+short_amt
    return {"available":True,"mode":"external_feed","long":long_amt,"short":short_amt,
            "net":long_amt-short_amt,"intensity":total,"clusters":clusters[:100],
            "cascade":total>0 and len(rows)>=5}


def advanced_oi_funding(ticker: dict, flow: dict, liq: dict) -> dict:
    symbol=ticker.get("symbol") or STATE["symbol"]
    oi=safe_float(ticker.get("oi_value_usd")); fund=safe_float(ticker.get("funding_rate"))
    hist=_history(symbol,"ticker",120)
    _push_history(symbol,"ticker",{"oi":oi,"funding":fund,"price":safe_float(ticker.get("mark"))})
    h=_history(symbol,"ticker",120)
    ois=[safe_float(x.get("oi")) for x in h if x.get("oi") is not None]
    funds=[safe_float(x.get("funding")) for x in h if x.get("funding") is not None]
    prices=[safe_float(x.get("price")) for x in h if x.get("price") is not None]
    oi_d=oi-(ois[-2] if len(ois)>=2 else oi)
    fund_d=fund-(funds[-2] if len(funds)>=2 else fund)
    oi_acc=oi_d-(ois[-2]-ois[-3] if len(ois)>=3 else 0)
    fund_acc=fund_d-(funds[-2]-funds[-3] if len(funds)>=3 else 0)
    pchg=_pct_change(prices[-1],prices[-2]) if len(prices)>=2 else 0
    return {"oi":oi,"oi_delta":oi_d,"oi_accel":oi_acc,"funding":fund,"funding_delta":fund_d,
            "funding_accel":fund_acc,"oi_price_corr":_corr(_returns(prices[-30:]),_returns(ois[-30:])) if len(prices)>=5 and len(ois)>=5 else 0,
            "flow_cvd":flow.get("cvd",0),"liquidation_net":liq.get("net",0),"four_way_score":clamp(5+pchg+oi_d/max(oi,1)*100+flow.get("aggression",0)*2-fund*100,0,10)}


def advanced_statistics(closes) -> dict:
    r=_returns(closes[-120:])
    if len(r)<10: return {"available":False}
    mean=_mean(r); sd=_std(r)
    z=((r[-1]-mean)/sd) if sd else 0
    return {"available":True,"mean_return":mean,"std_return":sd,"z_return":z,
            "p10":_percentile_range(r,.10),"p50":_percentile_range(r,.50),"p90":_percentile_range(r,.90),
            "skew":_skew(r),"kurtosis":_kurtosis(r),"autocorr1":_autocorr(r,1),
            "outlier":abs(z)>=2.5,"tail_risk_95":_percentile_range(r,.05),
            "conditional_up_after_up":(sum(1 for a,b in zip(r[:-1],r[1:]) if a>0 and b>0)/max(1,sum(1 for a in r[:-1] if a>0))),
            "expectancy_per_bar":mean}


def advanced_regime(closes, stats, orderbook=None) -> dict:
    r=_returns(closes[-80:]); vol=_std(r); trend=_slope(closes[-30:])/(max(abs(_mean(closes[-30:])),1e-9)) if len(closes)>=30 else 0
    ptrend=clamp(abs(trend)*5000,0,1); pvol=clamp(vol/0.01,0,1)
    if pvol>.75: regime="HIGH_VOL"
    elif ptrend>.60: regime="TREND"
    elif pvol<.25 and ptrend<.25: regime="LOW_VOL_RANGE"
    else: regime="TRANSITION"
    return {"regime":regime,"trend_probability":ptrend,"vol_probability":pvol,
            "transition_probability":clamp(1-abs(ptrend-pvol),0,1),
            "liquidity_regime":"BID_HEAVY" if orderbook and orderbook.get("imbalance",0)>.15 else "ASK_HEAVY" if orderbook and orderbook.get("imbalance",0)<-.15 else "BALANCED"}


def optional_external_modules(symbol: str) -> dict:
    out={}
    for name,url in (("options",OPTIONS_FEED_URL),("onchain",ONCHAIN_FEED_URL),("stablecoin",STABLECOIN_FEED_URL),("macro",MACRO_FEED_URL),("news",NEWS_FEED_URL)):
        data=_safe_json_url(url,{"symbol":symbol})
        out[name]=data if data is not None else {"available":False,"mode":"unavailable"}
    return out


def liquidity_migration(symbol: str, ob_adv: dict) -> dict:
    h=_history(symbol,"orderbook",60)
    if len(h)<3: return {"available":False}
    bid=[safe_float(x.get("bid_vol")) for x in h]; ask=[safe_float(x.get("ask_vol")) for x in h]
    return {"available":True,"bid_slope":_slope(bid),"ask_slope":_slope(ask),
            "bid_migration":"IN" if bid[-1]>bid[0] else "OUT" if bid[-1]<bid[0] else "FLAT",
            "ask_migration":"IN" if ask[-1]>ask[0] else "OUT" if ask[-1]<ask[0] else "FLAT",
            "vacuum":abs(_slope(bid))+abs(_slope(ask))>max(_mean(bid+ask),1e-9)*.15}


def advanced_divergence(closes, flow, oi_adv, stats) -> dict:
    p=_returns(closes[-40:])
    div={}
    if len(p)>=5:
        # Directional disagreement flags; exact CVD/OI histories require their feeds.
        div["price_flow"]=(closes[-1]>closes[-5] and flow.get("delta",0)<0) or (closes[-1]<closes[-5] and flow.get("delta",0)>0)
        div["price_oi"]=(closes[-1]>closes[-5] and oi_adv.get("oi_delta",0)<0) or (closes[-1]<closes[-5] and oi_adv.get("oi_delta",0)>0)
        div["price_volatility"]=bool(stats.get("outlier") and abs(p[-1])<abs(_mean(p[-5:])) if p else False)
    return div


def market_maker_proxy(ob_adv: dict, flow: dict) -> dict:
    imb=safe_float(ob_adv.get("depth_25pct_imbalance",0))
    ag=safe_float(flow.get("aggression",0))
    absorption=bool(flow.get("absorption"))
    return {"inventory_pressure":"LONG" if imb>.2 else "SHORT" if imb<-.2 else "BALANCED",
            "liquidity_behavior":"ABSORBING" if absorption else "PROVIDING",
            "hedging_pressure":"BUY" if ag<-.35 else "SELL" if ag>.35 else "NEUTRAL",
            "confidence":clamp(5+abs(imb)*3+abs(ag)*2,0,10),
            "proxy":True}


def correlation_engine(symbol: str, closes: list) -> dict:
    series={symbol:closes}
    for sym in ("BTCUSD","ETHUSD","SOLUSD"):
        if sym==symbol: continue
        k=fetch_candles("30m",40,sym)
        if k: series[sym]=k["close"]
    base=closes
    corr={}
    for sym,x in series.items():
        if sym!=symbol: corr[sym]=_corr(_returns(base[-50:]),_returns(x[-50:]))
    return {"pairwise":corr,"strength":_mean([abs(v) for v in corr.values()]) if corr else 0,
            "breakdown":any(abs(v)<.15 for v in corr.values()) if corr else False}


def advanced_feature_vector(snap: dict) -> dict:
    return {
        "structure":safe_float((snap.get("struct5") or {}).get("bias") == snap.get("direction_hint")),
        "rhythm":safe_float((snap.get("rhythm5") or {}).get("tempo")),
        "flow_delta":safe_float((snap.get("orderflow") or {}).get("delta")),
        "flow_aggression":safe_float((snap.get("orderflow") or {}).get("aggression")),
        "ob_imbalance":safe_float((snap.get("orderbook") or {}).get("imbalance")),
        "oi_delta":safe_float((snap.get("oi_funding") or {}).get("oi_delta")),
        "funding_delta":safe_float((snap.get("oi_funding") or {}).get("funding_delta")),
        "regime":(snap.get("advanced_regime") or {}).get("regime"),
        "score":safe_float(snap.get("score",0)),
    }


def learning_advanced(symbol: str, feature_vector: dict, outcome: Optional[float]=None) -> dict:
    """Feature/outcome storage + simple importance estimate; not ML training."""
    conn=sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS feature_observations(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, features_json TEXT,
        outcome REAL, regime TEXT)""")
    conn.execute("INSERT INTO feature_observations(ts,symbol,features_json,outcome,regime) VALUES(?,?,?,?,?)",
                 (iso_now(),symbol,json.dumps(feature_vector,default=str),outcome,feature_vector.get("regime")))
    conn.commit()
    rows=conn.execute("SELECT features_json,outcome FROM feature_observations WHERE symbol=? AND outcome IS NOT NULL ORDER BY id DESC LIMIT 500",(symbol,)).fetchall()
    conn.close()
    importance={}
    if len(rows)>=20:
        pairs=[]
        for key in feature_vector:
            xs=[]; ys=[]
            for fj,y in rows:
                try:
                    f=json.loads(fj); v=f.get(key)
                    if isinstance(v,(int,float)) and y is not None: xs.append(float(v)); ys.append(float(y))
                except Exception: pass
            importance[key]=round(abs(_corr(xs,ys)),3) if len(xs)>=10 else 0.0
    return {"samples":len(rows),"feature_importance":importance,"mode":"online_stats"}


def decision_quality(snap: dict, fs: dict, score: float, bias: str) -> dict:
    hard_conflicts=[]
    if bias=="LONG" and safe_float((snap.get("orderflow") or {}).get("aggression"))<-.25: hard_conflicts.append("Orderflow bearish")
    if bias=="SHORT" and safe_float((snap.get("orderflow") or {}).get("aggression"))>.25: hard_conflicts.append("Orderflow bullish")
    if bias=="LONG" and safe_float((snap.get("orderbook") or {}).get("imbalance"))<-.25: hard_conflicts.append("Orderbook bearish")
    if bias=="SHORT" and safe_float((snap.get("orderbook") or {}).get("imbalance"))>.25: hard_conflicts.append("Orderbook bullish")
    why_not_prob=clamp(0.35+len(hard_conflicts)*.12+(0.15 if snap.get("news_window") else 0),0,0.95)
    setup_prob=clamp(score/10*(1-why_not_prob*.45),0.02,.98)
    return {"setup_probability":setup_prob,"no_trade_probability":why_not_prob,
            "conflicts":hard_conflicts,"evidence_priority":sorted(fs,key=fs.get,reverse=True)[:6],
            "dynamic_confidence":round(setup_prob*100,1)}

# ═══════════════════════════════════════════════════════════════
# 4. UTILS + SECURITY HELPERS
# ═══════════════════════════════════════════════════════════════
def ist_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))


def ist_str(fmt: str = "%H:%M:%S") -> str:
    return ist_now().strftime(fmt)


def iso_now() -> str:
    return ist_now().isoformat()


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def truncate(s: str, n: int = 280) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def sanitize_symbol(raw: str) -> str:
    s = (raw or "").strip().upper().replace("/", "").replace("-", "").replace(" ", "")
    if not s:
        return DEFAULT_SYMBOL
    if s.endswith("USDT"):
        s = s[:-1] + "D"
    if not s.endswith("USD") and len(s) <= 6:
        s = s + "USD"
    s = re.sub(r"[^A-Z0-9]", "", s)[:MAX_SYMBOL_LEN]
    if not SAFE_SYMBOL_RE.match(s):
        return DEFAULT_SYMBOL
    return s or DEFAULT_SYMBOL


def current_session() -> str:
    now = ist_now()
    mins = now.hour * 60 + now.minute
    for name, (h1, m1, h2, m2) in SESSION_WINDOWS.items():
        if h1 * 60 + m1 <= mins <= h2 * 60 + m2:
            return name
    return "OFF_HOURS"


def is_high_impact_window() -> bool:
    now = ist_now()
    for h1, m1, h2, m2 in HIGH_IMPACT_IST:
        if (h1, m1) <= (now.hour, now.minute) <= (h2, m2):
            return True
    return False


def is_allowed_chat(chat_id) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    return str(chat_id) in ALLOWED_CHAT_IDS


def rate_limit_ok(chat_id: str) -> bool:
    """Simple per-chat command rate limit."""
    now = time.time()
    with _state_lock:
        times = STATE["cmd_times"][chat_id]
        times = [t for t in times if now - t < 60]
        STATE["cmd_times"][chat_id] = times
        if len(times) >= MAX_CMD_PER_MIN:
            return False
        times.append(now)
        return True


# ═══════════════════════════════════════════════════════════════
# 5. TELEGRAM
# ═══════════════════════════════════════════════════════════════
def tg_api(method: str, payload: dict = None, timeout: int = 20) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, json=payload or {}, timeout=timeout)
        return r.json() if r.content else {}
    except Exception as e:
        log.warning("tg_api %s: %s", method, e)
        return {"ok": False, "description": str(e)}


def tg_send(text: str, chat_id=None, reply_markup=None) -> bool:
    chat_id = str(chat_id or TELEGRAM_CHAT_ID)
    text = text[:MAX_MESSAGE_LEN]
    for parse_mode in ("Markdown", None):
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        data = tg_api("sendMessage", payload)
        if data.get("ok"):
            return True
        if parse_mode and "parse" in str(data.get("description", "")).lower():
            continue
        log.warning("tg_send fail: %s", str(data.get("description", ""))[:160])
        return False
    return False


def tg_send_action(chat_id, action="typing"):
    tg_api("sendChatAction", {"chat_id": str(chat_id), "action": action})


def main_keyboard():
    return {
        "keyboard": [
            [{"text": "🔄 Re-Analyze"}, {"text": "🌐 Best Any Coin"}],
            [{"text": "✅ Save Setup"}, {"text": "⏭ Skip Log"}],
            [{"text": "📊 Day Score"}, {"text": "📅 Month Score"}],
            [{"text": "📈 Backtest"}, {"text": "🧠 Learning"}],
            [{"text": "🩺 Diagnosis"}, {"text": "🧾 Post-Mortem"}],
            [{"text": "🧪 BT Diagnosis"}, {"text": "🎲 MonteCarlo"}],
            [{"text": "🌊 Market Rhythm"}, {"text": "📘 Orderbook"}],
            [{"text": "🪙 Change Coin"}, {"text": "⚙️ Settings"}],
            [{"text": "❓ Help"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def coin_keyboard():
    rows, row = [], []
    for i, c in enumerate(POPULAR_COINS[:24]):
        row.append({"text": c})
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "« Back"}])
    return {"keyboard": rows, "resize_keyboard": True}


# ═══════════════════════════════════════════════════════════════
# 6. DATABASE + LEARNING
# ═══════════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS setups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, symbol TEXT, status TEXT, direction TEXT,
            score REAL, confidence INTEGER, entry REAL, sl REAL, tp REAL,
            rrr REAL, reason TEXT, user_action TEXT, session TEXT,
            notes TEXT, decision_json TEXT, source TEXT,
            outcome TEXT DEFAULT 'PENDING',
            outcome_pnl REAL DEFAULT 0.0,
            outcome_ts TEXT,
            filter_scores_json TEXT
        );
        CREATE TABLE IF NOT EXISTS filter_weights (
            filter_name TEXT PRIMARY KEY,
            weight REAL,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pattern_stats (
            pattern_key TEXT PRIMARY KEY,
            total INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0.0,
            avg_score REAL DEFAULT 0.0,
            last_updated TEXT
        );
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, symbol TEXT, bars INTEGER,
            trades INTEGER, wins INTEGER, net_pnl REAL,
            win_rate REAL, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, kind TEXT, detail TEXT
        );
        CREATE TABLE IF NOT EXISTS daily_stats (
            day TEXT PRIMARY KEY,
            analyses INTEGER DEFAULT 0,
            trade_calls INTEGER DEFAULT 0,
            saves INTEGER DEFAULT 0,
            skips INTEGER DEFAULT 0,
            scans INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS backtest_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER, ts TEXT, symbol TEXT, direction TEXT,
            entry REAL, sl REAL, tp REAL, outcome REAL, pnl_r REAL, bars_held INTEGER,
            fees REAL DEFAULT 0, slippage REAL DEFAULT 0, funding REAL DEFAULT 0, note TEXT,
            -- v14 enriched diagnosis fields
            source TEXT DEFAULT 'backtest',
            bar_index INTEGER,
            score REAL,
            regime TEXT,
            session_label TEXT,
            structure_label TEXT,
            rhythm_state TEXT,
            feature_scores_json TEXT,
            primary_reason TEXT,
            reasons_json TEXT,
            mfe_r REAL,
            mae_r REAL,
            counterfactual TEXT,
            hit_type TEXT,
            rrr REAL,
            latency_bars INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS learning_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, samples INTEGER,
            feature_importance_json TEXT, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS trade_postmortem (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setup_id INTEGER,
            ts TEXT,
            symbol TEXT,
            direction TEXT,
            outcome TEXT,
            pnl REAL,
            primary_reason TEXT,
            reasons_json TEXT,
            feature_scores_json TEXT,
            regime TEXT,
            session TEXT,
            classification TEXT,
            rrr REAL,
            score REAL,
            confidence INTEGER,
            mtf_align INTEGER,
            entry REAL,
            sl REAL,
            tp REAL,
            notes TEXT,
            source TEXT DEFAULT 'live'
        );
        CREATE TABLE IF NOT EXISTS diagnosis_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            scope TEXT,
            report_json TEXT,
            summary TEXT
        );
        CREATE TABLE IF NOT EXISTS skipped_setups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, symbol TEXT, direction TEXT, score REAL,
            classification TEXT, reason TEXT, filter_scores_json TEXT,
            would_trade INTEGER DEFAULT 0
        );
        """
    )
    # migrate older setups columns if missing
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(setups)").fetchall()]
        if "primary_reason" not in cols:
            conn.execute("ALTER TABLE setups ADD COLUMN primary_reason TEXT")
        if "reasons_json" not in cols:
            conn.execute("ALTER TABLE setups ADD COLUMN reasons_json TEXT")
        if "regime" not in cols:
            conn.execute("ALTER TABLE setups ADD COLUMN regime TEXT")
        if "classification" not in cols:
            conn.execute("ALTER TABLE setups ADD COLUMN classification TEXT")
    except Exception:
        pass
    cur = conn.execute("SELECT COUNT(*) FROM filter_weights")
    if cur.fetchone()[0] == 0:
        for name, w in DEFAULT_WEIGHTS.items():
            conn.execute(
                "INSERT INTO filter_weights (filter_name, weight, updated_at) VALUES (?,?,?)",
                (name, w, iso_now()),
            )
    else:
        # v15 migration: if old low location weight detected, reset to data-tuned defaults
        try:
            loc = conn.execute(
                "SELECT weight FROM filter_weights WHERE filter_name='location'"
            ).fetchone()
            et = conn.execute(
                "SELECT weight FROM filter_weights WHERE filter_name='entry_timing'"
            ).fetchone()
            stw = conn.execute(
                "SELECT weight FROM filter_weights WHERE filter_name='structure'"
            ).fetchone()
            need = (loc and float(loc[0]) < 0.06) or (not et) or (stw and float(stw[0]) < 0.05)
            if need:
                for name, w in DEFAULT_WEIGHTS.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO filter_weights (filter_name, weight, updated_at) VALUES (?,?,?)",
                        (name, w, iso_now()),
                    )
                log.info("v16.2 weight reset (core floors + entry_timing)")
        except Exception as e:
            log.warning("weight migration: %s", e)
    conn.commit()
    conn.close()
    log.info("DB ready → %s", DB_PATH)


def log_event(kind: str, detail: str = ""):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO events (ts, kind, detail) VALUES (?,?,?)",
            (iso_now(), kind, truncate(detail, 400)),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("log_event: %s", e)


def bump_daily(field: str, n: int = 1):
    day = ist_now().strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            f"""INSERT INTO daily_stats (day, {field}) VALUES (?,?)
                ON CONFLICT(day) DO UPDATE SET {field} = {field} + ?""",
            (day, n, n),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("bump_daily: %s", e)


def load_weights() -> Dict[str, float]:
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT filter_name, weight FROM filter_weights").fetchall()
        conn.close()
        if not rows:
            return dict(DEFAULT_WEIGHTS)
        w = {k: float(v) for k, v in rows}
        for k, v in DEFAULT_WEIGHTS.items():
            if k not in w:
                w[k] = v
        total = sum(w.values()) or 1.0
        return {k: v / total for k, v in w.items()}
    except Exception:
        return dict(DEFAULT_WEIGHTS)


def nudge_weights(filter_scores: dict, is_win: bool, step_win: float = 0.004, step_loss: float = 0.003):
    if not filter_scores:
        return
    try:
        weights = load_weights()
        step = step_win if is_win else -step_loss
        for name, sc in filter_scores.items():
            if name not in weights or str(name).startswith("_"):
                continue
            if sc >= 7.0:
                weights[name] = weights[name] + step
            elif sc <= 3.5 and not is_win:
                weights[name] = weights[name] - abs(step) * 0.5
        # protect core filters from collapse (v16.1 bug)
        if PROTECT_CORE_WEIGHTS:
            for k, flo in WEIGHT_FLOOR.items():
                if k in weights:
                    weights[k] = max(flo, weights[k])
        for k in list(weights.keys()):
            weights[k] = max(0.01, weights[k])
        total = sum(weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}
        # re-apply floors after normalize
        if PROTECT_CORE_WEIGHTS:
            for k, flo in WEIGHT_FLOOR.items():
                if k in weights and weights[k] < flo:
                    weights[k] = flo
            total = sum(weights.values()) or 1.0
            weights = {k: v / total for k, v in weights.items()}
        conn = sqlite3.connect(DB_PATH)
        for name, w in weights.items():
            conn.execute(
                "INSERT OR REPLACE INTO filter_weights (filter_name, weight, updated_at) VALUES (?,?,?)",
                (name, w, iso_now()),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("nudge_weights: %s", e)


def learn_from_backtest(run_id: int = None, limit: int = 80) -> str:
    """Apply soft weight nudges from recent backtest trades (smaller steps than live)."""
    if not LEARN_FROM_BACKTEST:
        return "backtest learning off"
    try:
        conn = sqlite3.connect(DB_PATH)
        q = """SELECT pnl_r, feature_scores_json, primary_reason, mfe_r
               FROM backtest_trades WHERE source='backtest' OR source IS NULL"""
        args = []
        if run_id:
            q += " AND run_id=?"
            args.append(run_id)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(q, args).fetchall()
        conn.close()
        if not rows:
            return "no backtest rows to learn"
        nudged = 0
        for pnl_r, fs_json, primary, mfe in rows:
            try:
                fs = json.loads(fs_json or "{}")
            except Exception:
                continue
            is_win = (pnl_r or 0) > 0
            # extra penalty when never-favorable / wrong direction
            # smaller steps; extra only on never-favorable wrong-dir
            extra = 0.0008 if (not is_win and ((mfe or 0) < 0.25 or primary == "WRONG_DIRECTION")) else 0.0
            nudge_weights(fs, is_win=is_win, step_win=0.0012, step_loss=0.0010 + extra)
            nudged += 1
        return f"learned from {nudged} backtest trades"
    except Exception as e:
        log.warning("learn_from_backtest: %s", e)
        return f"learn error: {e}"


def update_pattern_stats(pattern_key: str, is_win: bool, pnl: float = 0.0, score: float = 0.0):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute(
            "SELECT total, wins, losses, total_pnl, avg_score FROM pattern_stats WHERE pattern_key=?",
            (pattern_key,),
        )
        row = cur.fetchone()
        if row:
            total, wins, losses, total_pnl, avg_score = row
            total += 1
            wins += 1 if is_win else 0
            losses += 0 if is_win else 1
            total_pnl += pnl
            avg_score = ((avg_score * (total - 1)) + score) / total
        else:
            total, wins, losses = 1, (1 if is_win else 0), (0 if is_win else 1)
            total_pnl, avg_score = pnl, score
        conn.execute(
            """INSERT OR REPLACE INTO pattern_stats
               (pattern_key, total, wins, losses, total_pnl, avg_score, last_updated)
               VALUES (?,?,?,?,?,?,?)""",
            (pattern_key, total, wins, losses, total_pnl, avg_score, iso_now()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("pattern_stats: %s", e)


def learning_warning(symbol: str, direction: str, pattern_key: str = "") -> str:
    notes = []
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT total, wins FROM pattern_stats WHERE pattern_key=?",
            (pattern_key,),
        ).fetchone()
        if row and row[0] >= 3:
            wr = row[1] / row[0] * 100
            if wr < 40:
                notes.append(f"Pattern WR low ({wr:.0f}% / {row[0]}t)")
            elif wr >= 60:
                notes.append(f"Pattern WR supportive ({wr:.0f}% / {row[0]}t)")
        rows = conn.execute(
            """SELECT status, user_action FROM setups
               WHERE symbol=? AND direction=? ORDER BY id DESC LIMIT 12""",
            (symbol, direction or ""),
        ).fetchall()
        conn.close()
        if len(rows) >= 4:
            skips = sum(1 for r in rows if r[0] == "NO_TRADE" or r[1] == "skip")
            if skips / len(rows) >= 0.65:
                notes.append(f"Recent {direction} often skipped")
    except Exception:
        pass
    return " · ".join(notes) if notes else "Learning DB warming / neutral."


def save_setup(d: dict, user_action: str = "saved", source: str = "live") -> bool:
    """
    Log a setup decision. Does NOT count as WIN/LOSS.
    Learning weights update only after resolve_outcome(WIN/LOSS/BE/EXPIRED).
    Unavailable/skipped coins are still loggable but tagged — never crash.
    """
    if not d:
        return False
    try:
        if d.get("unavailable") and user_action == "saved":
            # Don't treat dead-coin as a real trade save
            user_action = "skip_unavailable"
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """INSERT INTO setups
            (ts,symbol,status,direction,score,confidence,entry,sl,tp,rrr,
             reason,user_action,session,notes,decision_json,source,
             outcome,outcome_pnl,outcome_ts,filter_scores_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                iso_now(),
                d.get("symbol") or STATE["symbol"],
                d.get("status"),
                d.get("direction"),
                d.get("score"),
                d.get("confidence"),
                d.get("entry"),
                d.get("sl"),
                d.get("tp"),
                d.get("rrr"),
                d.get("reason"),
                user_action,
                current_session(),
                d.get("user_notes") or "",
                json.dumps(
                    {
                        "classification": d.get("classification"),
                        "filter_scores": d.get("filter_scores"),
                        "why_trade": d.get("why_trade"),
                        "why_not": d.get("why_not"),
                        "data_quality": d.get("data_quality"),
                    },
                    default=str,
                )[:3500],
                source,
                "PENDING",
                0.0,
                None,
                json.dumps(d.get("filter_scores") or {}, default=str)[:2000],
            ),
        )
        conn.commit()
        conn.close()
        bump_daily("saves" if user_action == "saved" else "skips")
        # CRITICAL: do NOT treat save as win — outcome must be resolved later
        log_event("setup_logged", f"{d.get('symbol')} {user_action} outcome=PENDING")
        return True
    except Exception as e:
        log.warning("save_setup: %s", e)
        return False


LOSS_REASON_CODES = [
    "WRONG_DIRECTION", "WEAK_STRUCTURE", "FALSE_BOS", "FALSE_BREAKOUT",
    "LIQUIDITY_SWEEP_FAILED", "LOW_VOLUME", "HIGH_VOLATILITY", "CHOPPY_MARKET",
    "LATE_ENTRY", "BAD_RRR", "SL_TOO_TIGHT", "TP_TOO_FAR", "FVG_FAILED",
    "OB_FAILED", "RSI_CONFLICT", "MACD_CONFLICT", "OI_CONFLICT",
    "FUNDING_CONFLICT", "CVD_CONFLICT", "ORDERBOOK_CONFLICT", "REGIME_MISMATCH",
    "MTF_CONFLICT", "NEWS_EVENT", "DATA_QUALITY", "UNKNOWN",
]


def attribute_loss_reasons(fs: dict, decision_json: dict, outcome: str, pnl: float) -> Tuple[str, List[str]]:
    """Heuristic multi-label loss reasons from stored filter scores + decision meta."""
    if outcome != "LOSS":
        return "", []
    reasons = []
    fs = fs or {}
    meta = decision_json or {}
    dq = meta.get("data_quality") or {}
    why_not = meta.get("why_not") or []

    if fs.get("structure", 10) < 5.0:
        reasons.append("WEAK_STRUCTURE")
    if fs.get("alignment", 10) < 5.0:
        reasons.append("MTF_CONFLICT")
    if fs.get("rhythm", 10) < 4.5:
        reasons.append("CHOPPY_MARKET")
    if fs.get("liquidity", 10) < 5.0:
        reasons.append("LIQUIDITY_SWEEP_FAILED")
    if fs.get("volume", 10) < 4.5:
        reasons.append("LOW_VOLUME")
    if fs.get("volatility", 10) >= 8.5:
        reasons.append("HIGH_VOLATILITY")
    if fs.get("volatility", 10) <= 4.0:
        reasons.append("CHOPPY_MARKET")
    if fs.get("momentum", 10) < 4.5:
        reasons.append("MACD_CONFLICT")
    if fs.get("fvg_ob", 10) < 4.5:
        reasons.append("FVG_FAILED")
    if fs.get("oi_funding", 10) < 4.5:
        reasons.append("OI_CONFLICT")
    if fs.get("orderbook", 10) < 4.5:
        reasons.append("ORDERBOOK_CONFLICT")
    if fs.get("orderflow", 10) < 4.5:
        reasons.append("CVD_CONFLICT")
    if fs.get("correlation", 10) < 4.0:
        reasons.append("REGIME_MISMATCH")
    if fs.get("session", 10) <= 3.0:
        reasons.append("NEWS_EVENT")
    if dq and safe_float(dq.get("score"), 10) < 5.5:
        reasons.append("DATA_QUALITY")
    rrr = safe_float(meta.get("rrr") or 0)
    if 0 < rrr < 1.4:
        reasons.append("BAD_RRR")
    if any("extended" in str(w).lower() or "late" in str(w).lower() for w in why_not):
        reasons.append("LATE_ENTRY")
    if any("chop" in str(w).lower() or "range" in str(w).lower() for w in why_not):
        reasons.append("CHOPPY_MARKET")
    if not reasons:
        reasons.append("UNKNOWN")
    # primary = lowest scoring related filter or first reason
    primary = reasons[0]
    weak = sorted([(k, safe_float(v)) for k, v in fs.items()], key=lambda x: x[1])
    if weak and weak[0][1] < 4.5:
        map_f = {
            "structure": "WEAK_STRUCTURE", "alignment": "MTF_CONFLICT",
            "rhythm": "CHOPPY_MARKET", "liquidity": "LIQUIDITY_SWEEP_FAILED",
            "volume": "LOW_VOLUME", "momentum": "MACD_CONFLICT",
            "fvg_ob": "FVG_FAILED", "oi_funding": "OI_CONFLICT",
            "orderbook": "ORDERBOOK_CONFLICT", "orderflow": "CVD_CONFLICT",
            "correlation": "REGIME_MISMATCH", "session": "NEWS_EVENT",
        }
        primary = map_f.get(weak[0][0], primary)
        if primary not in reasons:
            reasons.insert(0, primary)
    # unique preserve order
    seen = set()
    uniq = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return primary, uniq[:6]


def write_postmortem(setup_id: int, symbol: str, direction: str, outcome: str, pnl: float,
                     fs: dict, decision_json: dict, row_extra: dict = None) -> dict:
    primary, reasons = attribute_loss_reasons(fs, decision_json, outcome, pnl)
    row_extra = row_extra or {}
    meta = decision_json or {}
    pm = {
        "setup_id": setup_id,
        "symbol": symbol,
        "direction": direction,
        "outcome": outcome,
        "pnl": pnl,
        "primary_reason": primary if outcome == "LOSS" else "",
        "reasons": reasons if outcome == "LOSS" else [],
        "feature_scores": fs,
        "regime": row_extra.get("regime") or meta.get("regime") or "",
        "session": row_extra.get("session") or "",
        "classification": row_extra.get("classification") or meta.get("classification") or "",
        "rrr": row_extra.get("rrr") or meta.get("rrr") or 0,
        "score": row_extra.get("score") or 0,
        "confidence": row_extra.get("confidence") or 0,
        "mtf_align": int(safe_float(fs.get("alignment"), 0) >= 7),
        "entry": row_extra.get("entry"),
        "sl": row_extra.get("sl"),
        "tp": row_extra.get("tp"),
    }
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """INSERT INTO trade_postmortem
            (setup_id,ts,symbol,direction,outcome,pnl,primary_reason,reasons_json,
             feature_scores_json,regime,session,classification,rrr,score,confidence,
             mtf_align,entry,sl,tp,notes,source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                setup_id, iso_now(), symbol, direction, outcome, pnl,
                pm["primary_reason"], json.dumps(pm["reasons"]),
                json.dumps(fs or {}), pm["regime"], pm["session"], pm["classification"],
                pm["rrr"], pm["score"], pm["confidence"], pm["mtf_align"],
                pm.get("entry"), pm.get("sl"), pm.get("tp"),
                "", "live",
            ),
        )
        if outcome == "LOSS" and primary:
            conn.execute(
                "UPDATE setups SET primary_reason=?, reasons_json=? WHERE id=?",
                (primary, json.dumps(pm["reasons"]), setup_id),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("postmortem: %s", e)
    return pm


def resolve_outcome(outcome: str, pnl: float = 0.0, setup_id: int = None) -> str:
    """
    Mark latest (or given) PENDING setup as WIN / LOSS / BE / EXPIRED
    → post-mortem + loss reasons + learning weights (only on WIN/LOSS).
    """
    outcome = (outcome or "").strip().upper()
    if outcome not in ("WIN", "LOSS", "BE", "EXPIRED"):
        return "Use: win | loss | be | expired"
    try:
        conn = sqlite3.connect(DB_PATH)
        if setup_id:
            row = conn.execute(
                """SELECT id, symbol, direction, score, filter_scores_json, status,
                          decision_json, session, entry, sl, tp, rrr, confidence
                   FROM setups WHERE id=?""",
                (setup_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT id, symbol, direction, score, filter_scores_json, status,
                          decision_json, session, entry, sl, tp, rrr, confidence
                   FROM setups WHERE outcome='PENDING' AND user_action='saved'
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        if not row:
            conn.close()
            return "No PENDING saved setup to resolve."
        (sid, symbol, direction, score, fs_json, _status,
         dec_json, session, entry, sl, tp, rrr, conf) = row
        conn.execute(
            "UPDATE setups SET outcome=?, outcome_pnl=?, outcome_ts=? WHERE id=?",
            (outcome, pnl, iso_now(), sid),
        )
        conn.commit()
        conn.close()

        fs = {}
        dec = {}
        try:
            fs = json.loads(fs_json) if fs_json else {}
        except Exception:
            pass
        try:
            dec = json.loads(dec_json) if dec_json else {}
        except Exception:
            pass
        # Post-mortem is DB-only — never needs live ticker for this coin
        pm = {}
        try:
            pm = write_postmortem(
                sid, symbol or "?", direction or "NONE", outcome, pnl, fs, dec,
                {"session": session, "entry": entry, "sl": sl, "tp": tp,
                 "rrr": rrr, "score": score, "confidence": conf,
                 "classification": (dec or {}).get("classification"),
                 "regime": (dec or {}).get("regime")},
            ) or {}
        except Exception as e:
            log.warning("postmortem write skipped: %s", e)
            pm = {"primary_reason": "", "reasons": []}

        is_win = outcome == "WIN"
        is_loss = outcome == "LOSS"
        try:
            if is_win or is_loss:
                nudge_weights(fs, is_win=is_win)
                # pattern memory
                pats = (dec or {}).get("patterns") or []
                direction = (dec or {}).get("direction") or ""
                for pat in pats[:5]:
                    update_pattern_stats(f"{pat}_{direction}", is_win=is_win, pnl=float(pnl or 0), score=float((dec or {}).get("score") or 0))
                pk = f"{symbol or '?'}_{direction or 'NONE'}"
                update_pattern_stats(pk, is_win=is_win, pnl=pnl, score=score or 0)
        except Exception as e:
            log.warning("learning nudge skipped: %s", e)

        log_event("outcome", f"id={sid} {symbol} {outcome} pnl={pnl} reason={pm.get('primary_reason')}")
        msg = f"Resolved setup #{sid} `{symbol or '—'}` → *{outcome}* (pnl={pnl})"
        msg += "\n_Post-mortem saved (no live coin data required)._"
        if outcome == "LOSS" and pm.get("primary_reason"):
            msg += f"\nPrimary: `{pm['primary_reason']}`"
            if pm.get("reasons"):
                msg += "\nAlso: " + ", ".join(f"`{r}`" for r in pm["reasons"][:4])
        try:
            conn = sqlite3.connect(DB_PATH)
            n = conn.execute(
                "SELECT COUNT(*) FROM setups WHERE outcome IN ('WIN','LOSS')"
            ).fetchone()[0]
            conn.close()
            if n and n % 25 == 0:
                msg += "\n\n" + format_diagnosis_report(limit=200)[:800]
        except Exception:
            pass
        return msg
    except Exception as e:
        log.warning("resolve_outcome: %s", e)
        return f"Resolve failed (no crash): {e}"


def build_diagnosis(limit: int = 500) -> dict:
    """Aggregate win-rate, loss reasons, feature contribution, regime/session splits."""
    out = {
        "ts": iso_now(),
        "n_resolved": 0,
        "wins": 0,
        "losses": 0,
        "be": 0,
        "win_rate": 0.0,
        "by_direction": {},
        "by_symbol": {},
        "by_session": {},
        "by_regime": {},
        "by_setup": {},
        "loss_reasons": {},
        "feature_contribution": {},
        "rrr_buckets": {},
        "drawdown_notes": [],
        "recommendations": [],
    }
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            """SELECT outcome, outcome_pnl, symbol, direction, session, score,
                      filter_scores_json, primary_reason, reasons_json, rrr
               FROM setups WHERE outcome IN ('WIN','LOSS','BE','EXPIRED')
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        pm_rows = conn.execute(
            """SELECT primary_reason, regime, classification, feature_scores_json, outcome, pnl
               FROM trade_postmortem ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
    except Exception as e:
        out["error"] = str(e)
        return out

    feat_sum = defaultdict(float)
    feat_n = defaultdict(int)
    consecutive_losses = 0
    max_loss_streak = 0

    for r in rows:
        outcome, pnl, symbol, direction, session, score, fs_json, preason, reasons_j, rrr = r
        out["n_resolved"] += 1
        if outcome == "WIN":
            out["wins"] += 1
            consecutive_losses = 0
        elif outcome == "LOSS":
            out["losses"] += 1
            consecutive_losses += 1
            max_loss_streak = max(max_loss_streak, consecutive_losses)
        else:
            out["be"] += 1
            consecutive_losses = 0

        def bump(bucket, key, win):
            if key not in bucket:
                bucket[key] = {"n": 0, "wins": 0}
            bucket[key]["n"] += 1
            if win:
                bucket[key]["wins"] += 1

        win = outcome == "WIN"
        bump(out["by_direction"], direction or "NONE", win)
        bump(out["by_symbol"], symbol or "?", win)
        bump(out["by_session"], session or "?", win)
        # rrr buckets
        rv = safe_float(rrr)
        if rv <= 0:
            rb = "na"
        elif rv < 1.5:
            rb = "1.0-1.5"
        elif rv < 2.2:
            rb = "1.5-2.2"
        elif rv < 3.0:
            rb = "2.2-3.0"
        else:
            rb = "3.0+"
        bump(out["rrr_buckets"], rb, win)

        if outcome == "LOSS" and preason:
            out["loss_reasons"][preason] = out["loss_reasons"].get(preason, 0) + 1
        if reasons_j:
            try:
                for rr in json.loads(reasons_j):
                    out["loss_reasons"][rr] = out["loss_reasons"].get(rr, 0) + 1
            except Exception:
                pass

        try:
            fs = json.loads(fs_json) if fs_json else {}
            sign = 1.0 if win else -1.0
            for k, v in fs.items():
                feat_sum[k] += sign * (safe_float(v) - 5.0)
                feat_n[k] += 1
        except Exception:
            pass

    for pr in pm_rows:
        primary, regime, classification, fs_json, outcome, pnl = pr
        if regime:
            if regime not in out["by_regime"]:
                out["by_regime"][regime] = {"n": 0, "wins": 0}
            out["by_regime"][regime]["n"] += 1
            if outcome == "WIN":
                out["by_regime"][regime]["wins"] += 1
        if classification:
            if classification not in out["by_setup"]:
                out["by_setup"][classification] = {"n": 0, "wins": 0}
            out["by_setup"][classification]["n"] += 1
            if outcome == "WIN":
                out["by_setup"][classification]["wins"] += 1

    decided = out["wins"] + out["losses"]
    out["win_rate"] = round(100.0 * out["wins"] / decided, 2) if decided else 0.0
    out["max_loss_streak"] = max_loss_streak
    for k, n in feat_n.items():
        out["feature_contribution"][k] = round(feat_sum[k] / n, 3) if n else 0.0

    # recommendations
    if out["loss_reasons"]:
        top = sorted(out["loss_reasons"].items(), key=lambda x: -x[1])[:3]
        for code, cnt in top:
            pct = 100.0 * cnt / max(out["losses"], 1)
            out["recommendations"].append(f"Top loss `{code}` ≈ {pct:.0f}% of losses — tighten filter")
    weak_feat = sorted(out["feature_contribution"].items(), key=lambda x: x[1])[:2]
    for k, v in weak_feat:
        if v < -0.3:
            out["recommendations"].append(f"Feature `{k}` negative contribution ({v}) — review weight")
    for regime, st in out["by_regime"].items():
        if st["n"] >= 5:
            wr = 100.0 * st["wins"] / st["n"]
            if wr < 40:
                out["recommendations"].append(f"Avoid / reduce trades in regime `{regime}` (WR {wr:.0f}%)")
    if max_loss_streak >= 5:
        out["drawdown_notes"].append(f"Max consecutive losses in sample: {max_loss_streak}")
        out["recommendations"].append("After 3 losses: pause or lower size (streak control)")

    return out


def format_diagnosis_report(limit: int = 300) -> str:
    d = build_diagnosis(limit=limit)
    if d.get("error"):
        return f"Diagnosis error: {d['error']}"
    if d["n_resolved"] == 0:
        return (
            "*Diagnosis*\n"
            "No resolved trades yet.\n"
            "Flow: Save setup → trade → `win`/`loss`/`be` → report fills."
        )
    lines = [
        f"*Diagnosis Report* (last {d['n_resolved']} resolved)",
        f"WR: `{d['win_rate']}%` · W `{d['wins']}` · L `{d['losses']}` · BE/Exp `{d['be']}`",
        f"Max loss streak: `{d.get('max_loss_streak', 0)}`",
        "",
        "*Loss reasons*",
    ]
    total_l = max(d["losses"], 1)
    for code, cnt in sorted(d["loss_reasons"].items(), key=lambda x: -x[1])[:8]:
        lines.append(f"• `{code}`: {cnt} ({100*cnt/total_l:.0f}%)")
    if not d["loss_reasons"]:
        lines.append("• (no losses tagged yet)")

    lines.append("\n*Feature contribution* (+ helps wins)")
    for k, v in sorted(d["feature_contribution"].items(), key=lambda x: -x[1])[:8]:
        lines.append(f"• `{k}`: {v:+.2f}")

    lines.append("\n*By direction*")
    for k, st in d["by_direction"].items():
        wr = 100 * st["wins"] / st["n"] if st["n"] else 0
        lines.append(f"• `{k}`: {wr:.0f}% ({st['n']}t)")

    if d["by_session"]:
        lines.append("\n*By session*")
        for k, st in sorted(d["by_session"].items(), key=lambda x: -x[1]["n"])[:6]:
            wr = 100 * st["wins"] / st["n"] if st["n"] else 0
            lines.append(f"• `{k}`: {wr:.0f}% ({st['n']}t)")

    if d["by_regime"]:
        lines.append("\n*By regime*")
        for k, st in sorted(d["by_regime"].items(), key=lambda x: -x[1]["n"])[:6]:
            wr = 100 * st["wins"] / st["n"] if st["n"] else 0
            lines.append(f"• `{k}`: {wr:.0f}% ({st['n']}t)")

    if d["rrr_buckets"]:
        lines.append("\n*RRR buckets*")
        for k, st in sorted(d["rrr_buckets"].items()):
            wr = 100 * st["wins"] / st["n"] if st["n"] else 0
            lines.append(f"• `{k}`: {wr:.0f}% ({st['n']}t)")

    if d["recommendations"]:
        lines.append("\n*Recommendations*")
        for r in d["recommendations"][:5]:
            lines.append(f"• {r}")

    # persist for shared review
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO diagnosis_reports (ts, scope, report_json, summary) VALUES (?,?,?,?)",
            (iso_now(), f"last_{limit}", json.dumps(d, default=str)[:12000],
             f"WR {d['win_rate']}% n={d['n_resolved']}"),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return "\n".join(lines)


def export_diagnosis_file(path: str = None) -> str:
    """Write full diagnosis JSON for user + Grok shared review."""
    path = path or os.path.join(os.path.dirname(DB_PATH) or ".", "diagnosis_export_v13.json")
    d = build_diagnosis(limit=2000)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, default=str)
        return path
    except Exception as e:
        return f"export fail: {e}"


def format_coin_backtest_postmortem(symbol: str = None, limit: int = 500) -> str:
    """
    FULL coin post-mortem v2 — saare trades + WRONG_DIRECTION cards (why / failure / better).
    Bare UNKNOWN banned; INSUFFICIENT_DATA + Missing: ... when data thin.
    """
    symbol = sanitize_symbol(symbol or STATE.get("symbol") or DEFAULT_SYMBOL)
    try:
        conn = sqlite3.connect(DB_PATH)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(backtest_trades)").fetchall()]
        has_conflict = "conflict_json" in cols
        conf_col = "conflict_json" if has_conflict else "NULL"
        q = f"""SELECT id, direction, pnl_r, primary_reason, reasons_json, regime,
                       structure_label, score, mfe_r, mae_r, counterfactual, hit_type,
                       {conf_col}, feature_scores_json
                FROM backtest_trades
                WHERE symbol=? AND (source='backtest' OR source IS NULL)
                ORDER BY id DESC LIMIT ?"""
        rows = conn.execute(q, (symbol, limit)).fetchall()
        conn.close()
    except Exception as e:
        return f"Coin post-mortem error: {e}"

    if not rows:
        return (
            f"*FULL Post-Mortem v2* `{symbol}`\n"
            f"No backtest trades yet.\n"
            f"Run 📈 Backtest first (v15)."
        )

    n = len(rows)
    wins = sum(1 for r in rows if (r[2] or 0) > 0)
    losses = n - wins
    net = sum((r[2] or 0) for r in rows)
    wr = 100 * wins / n if n else 0
    reason_c = Counter()
    against_c = Counter()
    better_c = Counter()
    failure_c = Counter()
    wd_cards = []
    insuff = 0
    regime_c = defaultdict(lambda: [0, 0])

    for r in rows:
        (tid, direction, pnl, primary, reasons_j, regime, structure, score,
         mfe, mae, cf, hit, conflict_j, fs_j) = r
        win = (pnl or 0) > 0
        if regime:
            regime_c[regime][0] += 1
            regime_c[regime][1] += 1 if win else 0
        if win:
            continue
        if primary:
            reason_c[primary] += 1
            if primary in ("UNKNOWN", "INSUFFICIENT_DATA") or str(primary).startswith("MISSING_"):
                insuff += 1
        try:
            for x in (json.loads(reasons_j) if reasons_j else []):
                # OHLC BT always lacks CVD/OI/L2 — don't inflate diagnosis with them
                if str(x).startswith("MISSING_"):
                    continue
                reason_c[x] += 1
        except Exception:
            pass
        conflict = {}
        try:
            conflict = json.loads(conflict_j) if conflict_j else {}
        except Exception:
            pass
        if primary == "WRONG_DIRECTION" or conflict.get("wrong_direction"):
            for a in (conflict.get("against") or []):
                against_c[a] += 1
            if conflict.get("better_decision"):
                better_c[conflict["better_decision"]] += 1
            if conflict.get("failure"):
                failure_c[conflict["failure"]] += 1
            if len(wd_cards) < 3:
                wd_cards.append({
                    "id": tid, "dir": direction, "pnl": pnl,
                    "why": conflict.get("why_lines") or [],
                    "failure": conflict.get("failure"),
                    "better": conflict.get("better_decision"),
                    "missing": conflict.get("missing") or [],
                    "against": conflict.get("against") or [],
                    "mfe": mfe, "mae": mae, "cf": cf, "hit": hit,
                })
        elif primary in ("UNKNOWN", "INSUFFICIENT_DATA") or not primary:
            if len(wd_cards) < 3 and conflict:
                wd_cards.append({
                    "id": tid, "dir": direction, "pnl": pnl,
                    "why": conflict.get("why_lines") or [],
                    "failure": conflict.get("failure") or "INSUFFICIENT_DATA",
                    "better": conflict.get("better_decision") or "NO TRADE / WAIT",
                    "missing": conflict.get("missing") or ["orderflow", "OI"],
                    "against": conflict.get("against") or [],
                    "mfe": mfe, "mae": mae, "cf": cf, "hit": hit,
                    "insuff": True,
                })

    lines = [
        f"*FULL Post-Mortem v2* `{symbol}` · n=`{n}`",
        f"WR `{wr:.1f}%` · W `{wins}` · L `{losses}` · NetR `{net:+.2f}`",
        "_source=backtest · backtest learning ON_",
        "",
        "*Loss reasons*",
    ]
    for code, cnt in reason_c.most_common(12):
        if code == "UNKNOWN":
            continue  # never show bare UNKNOWN as headline
        lines.append(f"• `{code}`: {cnt} ({100*cnt/max(losses,1):.0f}% of L)")
    if insuff:
        lines.append(f"• thin-data tags: `{insuff}` (see Missing: …)")

    if regime_c:
        lines.append("\n*By regime*")
        for rg, (nn, ww) in regime_c.items():
            lines.append(f"• `{rg}`: WR {100*ww/nn:.0f}% ({nn}t)")

    wd_n = reason_c.get("WRONG_DIRECTION", 0)
    if wd_n or wd_cards:
        lines.append(f"\n*WRONG_DIRECTION detail* ({wd_n})")
        if against_c:
            lines.append("Layers most against:")
            for a, cnt in against_c.most_common(10):
                lines.append(f"  ✗ `{a}`: {cnt}x")
        if better_c:
            lines.append("Better decision mode:")
            for b, cnt in better_c.most_common(3):
                lines.append(f"  → {b} ({cnt})")
        if failure_c:
            lines.append("Top failures:")
            for f, cnt in failure_c.most_common(3):
                lines.append(f"  • {f} ({cnt})")

        for card in wd_cards:
            lines.append("")
            lines.append(f"*Primary: {'INSUFFICIENT_DATA' if card.get('insuff') else 'WRONG_DIRECTION'}*  #{card['id']}")
            lines.append(f"`{symbol}` {card['dir']} · R=`{card['pnl']}` · hit `{card['hit']}`")
            lines.append("*Why:*")
            for w in (card.get("why") or [])[:12]:
                lines.append(f"• {w}")
            if card.get("insuff") or card.get("missing"):
                miss = card.get("missing") or []
                lines.append("Missing: " + " + ".join(str(m).split("(")[0].strip() for m in miss[:4]))
            if card.get("failure"):
                lines.append(f"*Failure:* {card['failure']}")
            if card.get("better"):
                lines.append(f"*Better decision:* {card['better']}")

    if not has_conflict:
        lines.append("\n_Re-run Backtest on v15 for full Why cards._")
    return "\n".join(lines)[:3900]



def format_last_postmortem() -> str:
    """
    Default: FULL coin backtest post-mortem for active symbol (all trades).
    Also tries live postmortem if no bt data.
    Pure DB — no live ticker needed.
    """
    # Prefer full-coin backtest view (user request: saare trades ek baar mein)
    try:
        coin_pm = format_coin_backtest_postmortem()
        if coin_pm and "No backtest trades" not in coin_pm:
            return coin_pm
    except Exception as e:
        log.warning("coin pm: %s", e)

    def _parse_reasons(raw):
        if not raw:
            return []
        if isinstance(raw, list):
            return raw
        try:
            return json.loads(raw) if isinstance(raw, str) else []
        except Exception:
            return []

    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            row = conn.execute(
                """SELECT setup_id, symbol, direction, outcome, pnl, primary_reason,
                          reasons_json, regime, session, classification, score, confidence
                   FROM trade_postmortem ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        except Exception:
            row = None
        if row:
            conn.close()
            sid, sym, direction, outcome, pnl, primary, reasons_j, regime, session, classification, score, conf = row
            reasons = _parse_reasons(reasons_j)
            lines = [
                f"*Post-Mortem* live #{sid}",
                f"`{sym or '—'}` {direction or '—'} → *{outcome}* pnl=`{pnl}`",
                f"Score `{score}` · Conf `{conf}` · Session `{session or '—'}`",
                f"Class: `{classification or '—'}` · Regime: `{regime or '—'}`",
                "_source=live_",
            ]
            if primary:
                lines.append(f"Primary: `{primary}`")
            if reasons:
                lines.append("Reasons: " + ", ".join(f"`{r}`" for r in reasons[:6]))
            return "\n".join(lines)

        try:
            row2 = conn.execute(
                """SELECT id, symbol, direction, outcome, outcome_pnl, primary_reason,
                          reasons_json, session, score, confidence, classification
                   FROM setups WHERE outcome IN ('WIN','LOSS','BE','EXPIRED')
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        except Exception:
            row2 = None
        conn.close()
        if row2:
            sid, sym, direction, outcome, pnl, primary, reasons_j, session, score, conf, classification = row2
            reasons = _parse_reasons(reasons_j)
            lines = [
                f"*Post-Mortem* setup #{sid}",
                f"`{sym or '—'}` {direction or '—'} → *{outcome}* pnl=`{pnl}`",
                f"Score `{score}` · Session `{session or '—'}`",
                "_source=setups_",
            ]
            if primary:
                lines.append(f"Primary: `{primary}`")
            if reasons:
                lines.append("Reasons: " + ", ".join(f"`{r}`" for r in reasons[:6]))
            return "\n".join(lines)

        return (
            "No post-mortem data yet.\n"
            "• Run 📈 Backtest (v15) → Post-Mortem = full coin report\n"
            "• Live: Save setup → `win` / `loss`"
        )
    except Exception as e:
        log.warning("postmortem view: %s", e)
        return f"Post-mortem unavailable right now (DB). Try again. ({e})"



def data_quality_report(snap: dict) -> dict:
    """
    Anti-fake-signal / data health layer.
    Reduces confidence when feeds are stale, missing, or proxy-only.
    """
    issues = []
    score = 10.0
    if not snap:
        return {"score": 0, "issues": ["No snapshot"], "mode": "dead"}

    # Candles / price
    if not snap.get("ltp"):
        issues.append("No LTP")
        score -= 4
    if not snap.get("_closes") and not (snap.get("struct5") or {}).get("detail"):
        issues.append("Structure data thin")
        score -= 1.5

    # Orderbook freshness (if present)
    ob = snap.get("orderbook") or snap.get("ob_raw") or {}
    if not ob:
        issues.append("Orderbook missing")
        score -= 1.0
    elif safe_float(ob.get("spread_bps"), 99) > 25:
        issues.append("Wide spread (illiquid/stale?)")
        score -= 1.5

    # OI / funding
    oif = snap.get("oi_funding") or {}
    if not oif or oif.get("detail") in (None, "—"):
        issues.append("OI/Funding thin")
        score -= 0.8

    # Order flow mode honesty
    flow = snap.get("orderflow") or {}
    mode = flow.get("mode") or "unavailable"
    if mode == "candle_volume_proxy":
        issues.append("Orderflow=PROXY (not real ticks)")
        score -= 1.2
    elif mode == "unavailable":
        issues.append("Orderflow unavailable")
        score -= 1.5
    elif mode in ("ws_trades", "real_trade_feed"):
        score += 0.4  # live/real ticks bonus

    # WebSocket health
    wss = ws_status()
    if wss.get("enabled"):
        if wss.get("stale") or not wss.get("connected"):
            issues.append(f"WS stale/age={wss.get('age_sec')}s")
            score -= 1.2
        else:
            score += 0.3
    else:
        issues.append("WS disabled or package missing")

    # Liquidation
    liq = snap.get("liquidation") or {}
    if liq and not liq.get("available", True):
        issues.append("Liquidation feed off")

    # News window caution
    if snap.get("news_window"):
        issues.append("High-impact window")
        score -= 0.5

    score = clamp(score, 0, 10)
    return {
        "score": round(score, 2),
        "issues": issues,
        "mode": mode,
        "ok": score >= 6.0,
        "detail": f"DQ {score:.1f}/10 · " + (", ".join(issues[:4]) if issues else "clean"),
    }


def period_score(days: int = 1) -> str:
    try:
        conn = sqlite3.connect(DB_PATH)
        since = (ist_now() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT status, user_action, score FROM setups WHERE ts >= ?",
            (since,),
        ).fetchall()
        conn.close()
        if not rows:
            return f"No data for last {days}d."
        n = len(rows)
        trades = sum(1 for r in rows if r[0] == "TRADE")
        saves = sum(1 for r in rows if r[1] == "saved")
        avg = sum(r[2] or 0 for r in rows) / n
        return (
            f"*{'Day' if days == 1 else 'Month'} Score*\n"
            f"Analyses: `{n}` · TRADE calls: `{trades}` · Saves: `{saves}`\n"
            f"Avg score: `{avg:.2f}`"
        )
    except Exception as e:
        return f"Score error: {e}"


def format_learning_summary() -> str:
    w = load_weights()
    lines = ["*Learning / Weights*"]
    for k, v in sorted(w.items(), key=lambda x: -x[1]):
        lines.append(f"`{k}`: {v*100:.1f}%")
    try:
        conn = sqlite3.connect(DB_PATH)
        n = conn.execute("SELECT COUNT(*) FROM setups").fetchone()[0]
        p = conn.execute("SELECT COUNT(*) FROM pattern_stats").fetchone()[0]
        conn.close()
        lines.append(f"\nSetups logged: `{n}` · Patterns: `{p}`")
    except Exception:
        pass
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 7. DELTA DATA LAYER
# ═══════════════════════════════════════════════════════════════
def _delta_get(path: str, params: dict = None, timeout: int = HTTP_TIMEOUT) -> Optional[dict]:
    url = f"{BASE_URL}{path}"
    for attempt in range(DELTA_RETRIES):
        try:
            r = requests.get(url, params=params or {}, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503):
                time.sleep(DELTA_RETRY_SLEEP * (attempt + 1))
                continue
            log.warning("delta %s → %s", path, r.status_code)
            return None
        except Exception as e:
            log.warning("delta err %s: %s", path, e)
            time.sleep(DELTA_RETRY_SLEEP)
    return None


def get_ticker_full(symbol: str = None) -> Optional[dict]:
    """Rich ticker: price, OI, funding, basis, quotes."""
    symbol = symbol or STATE["symbol"]
    now = time.time()
    if symbol in _ticker_cache:
        ts, data = _ticker_cache[symbol]
        if now - ts < TICKER_CACHE_TTL:
            return data
    raw = _delta_get(f"/v2/tickers/{symbol}")
    if not raw or "result" not in raw:
        return None
    r = raw["result"]
    mark = safe_float(r.get("mark_price") or r.get("close"))
    spot = safe_float(r.get("spot_price") or mark)
    data = {
        "symbol": symbol,
        "mark": mark,
        "close": safe_float(r.get("close")),
        "high": safe_float(r.get("high")),
        "low": safe_float(r.get("low")),
        "open": safe_float(r.get("open")),
        "volume": safe_float(r.get("volume")),
        "turnover_usd": safe_float(r.get("turnover_usd")),
        "spot_price": spot,
        "mark_basis": safe_float(r.get("mark_basis")),
        "funding_rate": safe_float(r.get("funding_rate")),
        "oi": safe_float(r.get("oi")),
        "oi_contracts": safe_float(r.get("oi_contracts")),
        "oi_value_usd": safe_float(r.get("oi_value_usd")),
        "oi_change_usd_6h": safe_float(r.get("oi_change_usd_6h")),
        "ltp_change_24h": safe_float(r.get("ltp_change_24h")),
        "quotes": r.get("quotes") or {},
    }
    _ticker_cache[symbol] = (now, data)
    return data


def get_orderbook(symbol: str = None, depth_levels: int = 25) -> Optional[dict]:
    symbol = symbol or STATE["symbol"]
    # Prefer live WS book when fresh
    wsb = ws_book_snapshot(symbol)
    if wsb:
        wsb = dict(wsb)
        wsb["source"] = "ws"
        return wsb
    now = time.time()
    if symbol in _ob_cache:
        ts, data = _ob_cache[symbol]
        if now - ts < OB_CACHE_TTL:
            return data
    raw = _delta_get(f"/v2/l2orderbook/{symbol}")
    if not raw or "result" not in raw:
        return None
    r = raw["result"]
    buys = r.get("buy") or []
    sells = r.get("sell") or []
    bids = [(safe_float(x.get("price")), safe_float(x.get("size") or x.get("depth"))) for x in buys[:depth_levels]]
    asks = [(safe_float(x.get("price")), safe_float(x.get("size") or x.get("depth"))) for x in sells[:depth_levels]]
    bid_vol = sum(s for _, s in bids) or 1e-9
    ask_vol = sum(s for _, s in asks) or 1e-9
    best_bid = bids[0][0] if bids else 0
    best_ask = asks[0][0] if asks else 0
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
    spread = (best_ask - best_bid) if best_bid and best_ask else 0
    spread_bps = (spread / mid * 10000) if mid else 0
    imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
    data = {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread": spread,
        "spread_bps": spread_bps,
        "bid_vol": bid_vol,
        "ask_vol": ask_vol,
        "imbalance": imbalance,
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "bids": bids[:8],
        "asks": asks[:8],
        "source": "rest",
    }
    _ob_cache[symbol] = (now, data)
    return data


def fetch_candles(resolution: str, limit: int = 60, symbol: str = None) -> Optional[dict]:
    symbol = symbol or STATE["symbol"]
    key = f"{symbol}_{resolution}_{limit}"
    now = time.time()
    if key in _candle_cache:
        ts, data = _candle_cache[key]
        if now - ts < CANDLE_CACHE_TTL:
            return data

    res_map = {
        "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "1d": "1d",
        "1": "1m", "5": "5m", "15": "15m", "60": "1h", "240": "4h",
    }
    res = res_map.get(str(resolution), str(resolution))
    sec_map = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400,
    }
    bar_sec = sec_map.get(res, 300)
    end_ts = int(time.time())
    start_ts = end_ts - (limit + 5) * bar_sec

    raw = _delta_get(
        "/v2/history/candles",
        {"symbol": symbol, "resolution": res, "start": start_ts, "end": end_ts},
    )
    if not raw or "result" not in raw:
        return None
    candles = raw["result"]
    if not candles:
        return None
    if len(candles) >= 2 and candles[0].get("time", 0) > candles[-1].get("time", 0):
        candles = list(reversed(candles))
    if len(candles) > limit:
        candles = candles[-limit:]

    out = {
        "open": [safe_float(c.get("open")) for c in candles],
        "high": [safe_float(c.get("high")) for c in candles],
        "low": [safe_float(c.get("low")) for c in candles],
        "close": [safe_float(c.get("close")) for c in candles],
        "volume": [safe_float(c.get("volume")) for c in candles],
        "time": [c.get("time") for c in candles],
    }
    _candle_cache[key] = (now, out)
    return out


# ═══════════════════════════════════════════════════════════════
# 8. INDICATORS
# ═══════════════════════════════════════════════════════════════
def calc_ema(prices: List[float], period: int) -> Optional[float]:
    if not prices or len(prices) < period:
        return None
    m = 2.0 / (period + 1)
    e = prices[0]
    for p in prices[1:]:
        e = (p - e) * m + e
    return e


def calc_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if not closes or len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calc_atr(highs, lows, closes, period: int = 14) -> float:
    if not closes or len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    n = min(period, len(trs))
    return sum(trs[-n:]) / n if n else 0.0


def calc_macd(closes: List[float]):
    if not closes or len(closes) < 26:
        return None, None, None
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    if ema12 is None or ema26 is None:
        return None, None, None
    macd_line = ema12 - ema26
    signal = macd_line * 0.7
    hist = macd_line - signal
    return macd_line, signal, hist


def calc_bollinger(closes: List[float], period: int = 20, mult: float = 2.0):
    if not closes or len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((x - mid) ** 2 for x in window) / period
    std = math.sqrt(var)
    return mid, mid + mult * std, mid - mult * std


def calc_vwap(highs, lows, closes, volumes) -> Optional[float]:
    if not closes or not volumes:
        return None
    n = min(len(closes), 40)
    tp = [(highs[-n + i] + lows[-n + i] + closes[-n + i]) / 3 for i in range(n)]
    vol = volumes[-n:]
    num = sum(t * v for t, v in zip(tp, vol))
    den = sum(vol) or 1.0
    return num / den


def volume_profile_approx(highs, lows, closes, volumes, bins: int = 12) -> dict:
    """Rough POC / value area from recent candles."""
    if not closes or len(closes) < 10:
        return {"poc": None, "vah": None, "val": None}
    n = min(len(closes), 50)
    price_min = min(lows[-n:])
    price_max = max(highs[-n:])
    if price_max <= price_min:
        return {"poc": closes[-1], "vah": closes[-1], "val": closes[-1]}
    step = (price_max - price_min) / bins
    hist = [0.0] * bins
    for i in range(-n, 0):
        mid = (highs[i] + lows[i] + closes[i]) / 3
        idx = int((mid - price_min) / step)
        idx = max(0, min(bins - 1, idx))
        hist[idx] += volumes[i] if volumes else 1.0
    poc_idx = max(range(bins), key=lambda i: hist[i])
    poc = price_min + (poc_idx + 0.5) * step
    # value area ~70% volume around POC
    total = sum(hist) or 1.0
    target = total * 0.7
    lo_i = hi_i = poc_idx
    acc = hist[poc_idx]
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        left = hist[lo_i - 1] if lo_i > 0 else 0
        right = hist[hi_i + 1] if hi_i < bins - 1 else 0
        if left >= right and lo_i > 0:
            lo_i -= 1
            acc += left
        elif hi_i < bins - 1:
            hi_i += 1
            acc += right
        else:
            break
    val = price_min + lo_i * step
    vah = price_min + (hi_i + 1) * step
    return {"poc": poc, "vah": vah, "val": val}


# ═══════════════════════════════════════════════════════════════
# 9. STRUCTURE + RHYTHM + LIQUIDITY (from v9, refined)
# ═══════════════════════════════════════════════════════════════
def find_swings_advanced(highs, lows, left: int = 2, right: int = 2):
    sh, sl = [], []
    n = len(highs)
    if n < left + right + 1:
        return sh, sl
    for i in range(left, n - right):
        is_sh = all(highs[i] >= highs[i - j] for j in range(1, left + 1)) and \
                all(highs[i] > highs[i + j] for j in range(1, right + 1))
        is_sl = all(lows[i] <= lows[i - j] for j in range(1, left + 1)) and \
                all(lows[i] < lows[i + j] for j in range(1, right + 1))
        if is_sh:
            strength = sum(1 for j in range(1, left + 1) if highs[i] > highs[i - j]) + \
                       sum(1 for j in range(1, right + 1) if highs[i] > highs[i + j])
            sh.append({"idx": i, "price": highs[i], "strength": strength})
        if is_sl:
            strength = sum(1 for j in range(1, left + 1) if lows[i] < lows[i - j]) + \
                       sum(1 for j in range(1, right + 1) if lows[i] < lows[i + j])
            sl.append({"idx": i, "price": lows[i], "strength": strength})
    return sh, sl


def analyze_structure(highs, lows, closes) -> dict:
    sh, sl = find_swings_advanced(highs, lows, 2, 2)
    result = {
        "label": "Unclear", "bias": "NONE", "bos": None, "choch": None,
        "last_sh": sh[-1]["price"] if sh else None,
        "last_sl": sl[-1]["price"] if sl else None,
        "protected_high": None, "protected_low": None,
        "swings_h": [s["price"] for s in sh[-6:]],
        "swings_l": [s["price"] for s in sl[-6:]],
        "detail": "",
    }
    if len(sh) < 2 or len(sl) < 2:
        return result
    recent_h = [s["price"] for s in sh[-3:]]
    recent_l = [s["price"] for s in sl[-3:]]
    hh = recent_h[-1] > recent_h[-2]
    lh = recent_h[-1] < recent_h[-2]
    hl = recent_l[-1] > recent_l[-2]
    ll = recent_l[-1] < recent_l[-2]
    if hh and hl:
        result["label"], result["bias"] = "Bullish (HH+HL)", "LONG"
    elif lh and ll:
        result["label"], result["bias"] = "Bearish (LH+LL)", "SHORT"
    elif hh and ll:
        result["label"] = "Expansion / Volatile"
    elif lh and hl:
        result["label"] = "Range / Chop"
    else:
        result["label"] = "Transition"
    last_close = closes[-1]
    if result["bias"] == "LONG" and result["last_sh"] and last_close > result["last_sh"]:
        result["bos"] = "Bullish BOS"
    elif result["bias"] == "SHORT" and result["last_sl"] and last_close < result["last_sl"]:
        result["bos"] = "Bearish BOS"
    if result["bias"] == "LONG" and result["last_sl"] and last_close < result["last_sl"]:
        result["choch"] = "Bearish CHOCH / MSS"
        result["bias"] = "SHORT"
    elif result["bias"] == "SHORT" and result["last_sh"] and last_close > result["last_sh"]:
        result["choch"] = "Bullish CHOCH / MSS"
        result["bias"] = "LONG"
    if sh:
        strongest = max(sh[-4:], key=lambda x: x["strength"]) if len(sh) >= 2 else sh[-1]
        result["protected_high"] = strongest["price"]
    if sl:
        strongest = max(sl[-4:], key=lambda x: x["strength"]) if len(sl) >= 2 else sl[-1]
        result["protected_low"] = strongest["price"]
    parts = [result["label"]]
    if result["bos"]:
        parts.append(result["bos"])
    if result["choch"]:
        parts.append(result["choch"])
    result["detail"] = " · ".join(parts)
    return result


def analyze_rhythm(highs, lows, closes) -> dict:
    n = len(closes)
    if n < 12:
        return {"state": "Unknown", "tempo": 5.0, "accel": 0.0, "detail": "Insufficient"}
    atr = calc_atr(highs, lows, closes, 10) or 1.0
    recent = closes[-8:]
    prev = closes[-16:-8] if n >= 16 else closes[: max(1, n - 8)]
    recent_move = recent[-1] - recent[0]
    prev_move = prev[-1] - prev[0] if len(prev) > 1 else 0
    vel_recent = abs(recent_move) / max(len(recent) - 1, 1)
    vel_prev = abs(prev_move) / max(len(prev) - 1, 1) if prev else vel_recent
    accel = (vel_recent - vel_prev) / (atr or 1.0)
    ranges = [highs[i] - lows[i] for i in range(-10, 0) if i + n >= 0]
    avg_r = sum(ranges[-5:]) / 5 if len(ranges) >= 5 else atr
    avg_p = sum(ranges[:5]) / 5 if len(ranges) >= 10 else avg_r
    compression = avg_r < avg_p * 0.78
    if abs(recent_move) > atr * 2.2 and vel_recent > vel_prev * 1.15:
        state = "Impulse"
    elif compression:
        state = "Compression"
    elif abs(recent_move) < atr * 0.7:
        state = "Consolidation"
    else:
        state = "Correction / Pullback"
    tempo = clamp(5.0 + (vel_recent / (atr or 1)) * 3 + accel * 2, 0, 10)
    direction = "Bullish" if recent_move > 0 else "Bearish" if recent_move < 0 else "Flat"
    return {
        "state": state, "direction": direction, "tempo": round(tempo, 2),
        "accel": round(accel, 3), "compression": compression,
        "detail": f"{state} · {direction} · tempo {tempo:.1f}",
    }


def market_wide_rhythm() -> dict:
    directions = []
    for sym in RHYTHM_COINS:
        try:
            k = fetch_candles("30m", 12, sym)
            if not k or len(k["close"]) < 8:
                continue
            move = k["close"][-1] - k["close"][-8]
            atr = calc_atr(k["high"], k["low"], k["close"], 8) or 1e-9
            if move > atr * 0.35:
                directions.append("UP")
            elif move < -atr * 0.35:
                directions.append("DOWN")
            else:
                directions.append("FLAT")
        except Exception:
            continue
    if not directions:
        return {"sync": "Unknown", "score": 5.0, "up": 0, "down": 0, "flat": 0, "detail": "No data"}
    up = directions.count("UP")
    down = directions.count("DOWN")
    flat = directions.count("FLAT")
    total = len(directions)
    if up >= total * 0.65:
        sync, score = "Risk-On (majority up)", 8.0 + (up / total) * 2
    elif down >= total * 0.65:
        sync, score = "Risk-Off (majority down)", 8.0 + (down / total) * 2
    elif up >= 2 and down >= 2:
        sync, score = "Mixed / Rotation", 4.0
    else:
        sync, score = "Neutral / Low energy", 5.5
    return {
        "sync": sync, "score": clamp(score, 0, 10),
        "up": up, "down": down, "flat": flat, "total": total,
        "detail": f"{sync} · ↑{up} ↓{down} →{flat}",
    }


def analyze_liquidity(highs, lows, closes) -> dict:
    sh, sl = find_swings_advanced(highs, lows, 2, 2)
    last_close, last_high, last_low = closes[-1], highs[-1], lows[-1]
    atr = calc_atr(highs, lows, closes, 10) or 1.0
    sweep_high = sweep_low = False
    if sh and last_high > sh[-1]["price"] and last_close < sh[-1]["price"]:
        if (last_high - sh[-1]["price"]) > atr * 0.15:
            sweep_high = True
    if sl and last_low < sl[-1]["price"] and last_close > sl[-1]["price"]:
        if (sl[-1]["price"] - last_low) > atr * 0.15:
            sweep_low = True
    return {
        "sweep_high": sweep_high, "sweep_low": sweep_low,
        "draw_liquidity": "Buy-side" if sweep_high else "Sell-side" if sweep_low else "None",
        "detail": f"SweepH={sweep_high} SweepL={sweep_low}",
    }


def detect_fvg(highs, lows, closes) -> dict:
    if len(closes) < 5:
        return {"bull_fvg": None, "bear_fvg": None, "detail": "—"}
    bull = bear = None
    for i in range(-4, -1):
        if highs[i - 2] < lows[i]:
            bull = {"top": lows[i], "bottom": highs[i - 2]}
        if lows[i - 2] > highs[i]:
            bear = {"top": lows[i - 2], "bottom": highs[i]}
    return {
        "bull_fvg": bull, "bear_fvg": bear,
        "detail": f"BullFVG={'Y' if bull else 'N'} BearFVG={'Y' if bear else 'N'}",
    }


def detect_order_block(opens, highs, lows, closes) -> dict:
    if len(closes) < 8:
        return {"bull_ob": None, "bear_ob": None, "detail": "—"}
    atr = calc_atr(highs, lows, closes, 8) or 1.0
    bull_ob = bear_ob = None
    for i in range(-6, -1):
        body = closes[i] - opens[i]
        prev_body = closes[i - 1] - opens[i - 1]
        if body > atr * 1.1 and prev_body < 0:
            bull_ob = {"high": highs[i - 1], "low": lows[i - 1]}
            break
    for i in range(-6, -1):
        body = closes[i] - opens[i]
        prev_body = closes[i - 1] - opens[i - 1]
        if body < -atr * 1.1 and prev_body > 0:
            bear_ob = {"high": highs[i - 1], "low": lows[i - 1]}
            break
    return {
        "bull_ob": bull_ob, "bear_ob": bear_ob,
        "detail": f"BullOB={'Y' if bull_ob else 'N'} BearOB={'Y' if bear_ob else 'N'}",
    }


def detect_candle_patterns(opens, highs, lows, closes) -> List[str]:
    """Rich candle vocabulary — stored in pattern_stats for next-trade memory."""
    if len(closes) < 3:
        return []
    patterns = []
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o)
    rng = h - l or 1e-9
    upper = h - max(c, o)
    lower = min(c, o) - l
    bull = c > o
    if body / rng < 0.12:
        patterns.append("Doji")
    if body / rng < 0.08 and upper > rng * 0.4 and lower > rng * 0.4:
        patterns.append("SpinningTop")
    if lower > body * 2.0 and upper < body * 0.6 and bull:
        patterns.append("Hammer")
    if lower > body * 2.0 and upper < body * 0.6 and not bull:
        patterns.append("HangingMan")
    if upper > body * 2.0 and lower < body * 0.6 and not bull:
        patterns.append("ShootingStar")
    if upper > body * 2.0 and lower < body * 0.6 and bull:
        patterns.append("InvertedHammer")
    if body / rng > 0.7 and bull:
        patterns.append("BullMarubozu")
    if body / rng > 0.7 and not bull:
        patterns.append("BearMarubozu")
    if len(closes) >= 2:
        o0, c0, h0, l0 = opens[-2], closes[-2], highs[-2], lows[-2]
        if c0 < o0 and c > o and c >= o0 and o <= c0:
            patterns.append("BullEngulf")
        if c0 > o0 and c < o and c <= o0 and o >= c0:
            patterns.append("BearEngulf")
        # Pinbar rejection
        if lower > rng * 0.6 and body / rng < 0.3 and bull:
            patterns.append("BullPin")
        if upper > rng * 0.6 and body / rng < 0.3 and not bull:
            patterns.append("BearPin")
        # Inside bar compression
        if h <= h0 and l >= l0:
            patterns.append("InsideBar")
        # Outside bar expansion
        if h >= h0 and l <= l0 and body > abs(c0 - o0):
            patterns.append("OutsideBar")
    if len(closes) >= 3:
        # Three soldiers / crows (simple)
        if all(closes[-i] > opens[-i] and closes[-i] > closes[-i - 1] for i in (1, 2, 3) if len(closes) >= i + 1):
            if closes[-1] > closes[-2] > closes[-3]:
                patterns.append("ThreeSoldiers")
        if all(closes[-i] < opens[-i] for i in (1, 2, 3) if len(closes) >= i):
            if closes[-1] < closes[-2] < closes[-3]:
                patterns.append("ThreeCrows")
    atr = calc_atr(highs, lows, closes, 8) or 1
    if body > atr * 1.4:
        patterns.append("Displacement")
    if body > atr * 1.8:
        patterns.append("StrongDisplacement")
    return patterns


def entry_timing_score(snap: dict, bias: str) -> dict:
    """
    Anti ENTRY_TIMING_FAILURE (22.8% of losses) + NEVER_FAVORABLE.
    Good entry = pullback into structure / confirm candle / not chasing extension.
    """
    score = 5.0
    reasons = []
    patterns = snap.get("patterns") or []
    extended = bool(snap.get("extended"))
    e9, e21 = snap.get("e9"), snap.get("e21")
    ltp = snap.get("ltp") or 0
    atr = snap.get("atr5") or 1
    rsi = snap.get("rsi5")
    rh = snap.get("rhythm5") or {}
    s5 = snap.get("struct5") or {}

    # 1) Never chase extension (harder in v17.2)
    if extended:
        score -= 3.2
        reasons.append("extended from EMA21 — chase risk")
    else:
        score += 1.2

    # 2) Pullback / mean-reversion toward EMA preferred
    if e9 and e21 and ltp:
        dist_e9 = abs(ltp - e9) / atr
        if dist_e9 < 0.75:
            score += 1.5
            reasons.append("near EMA9 — better timing")
        elif dist_e9 > 1.5:
            score -= 2.0
            reasons.append("far from EMA9 — late entry")

    # 3) Confirm candles aligned with bias
    bull_pats = {"Hammer", "BullEngulf", "BullPin", "BullMarubozu", "ThreeSoldiers", "InvertedHammer"}
    bear_pats = {"ShootingStar", "BearEngulf", "BearPin", "BearMarubozu", "ThreeCrows", "HangingMan"}
    if bias == "LONG" and any(p in patterns for p in bull_pats):
        score += 1.6
        reasons.append("bull confirm candle")
    if bias == "SHORT" and any(p in patterns for p in bear_pats):
        score += 1.6
        reasons.append("bear confirm candle")
    if bias == "LONG" and any(p in patterns for p in bear_pats):
        score -= 1.8
        reasons.append("opposing candle on long")
    if bias == "SHORT" and any(p in patterns for p in bull_pats):
        score -= 1.8
        reasons.append("opposing candle on short")

    # 4) RSI not exhausted
    if rsi is not None:
        if bias == "LONG" and rsi > 72:
            score -= 1.5
            reasons.append("RSI overbought — late long")
        elif bias == "SHORT" and rsi < 28:
            score -= 1.5
            reasons.append("RSI oversold — late short")
        elif bias == "LONG" and 40 <= rsi <= 62:
            score += 0.8
        elif bias == "SHORT" and 38 <= rsi <= 60:
            score += 0.8

    # 5) Rhythm: prefer after compression or healthy pullback, not mid-impulse chase
    if rh.get("state") == "Impulse" and extended:
        score -= 1.6
        reasons.append("chasing mid-impulse")
    # Prefer BOS already in place (not entering before break settles)
    if s5.get("bos") and not extended:
        score += 0.5
    if rh.get("compression") or "InsideBar" in patterns:
        score += 0.7
        reasons.append("compression/inside — coil before move")
    if rh.get("state") in ("Correction / Pullback", "Pullback"):
        score += 1.0
        reasons.append("pullback entry window")

    # 6) BOS without extension is OK; CHOCH alone is weaker
    if s5.get("bos") and not extended:
        score += 0.6
    if s5.get("choch") and not s5.get("bos"):
        score -= 0.4

    # v17.4 hard: far from EMA or extended → force not-ok
    if extended:
        score = min(score, MIN_ENTRY_TIMING - 0.5)
    if e9 and ltp and atr:
        dist_e9 = abs(ltp - e9) / max(atr, 1e-9)
        if dist_e9 > 1.6:
            score = min(score, MIN_ENTRY_TIMING - 0.4)
            reasons.append("hard-block: too far EMA9")
    score = clamp(score, 0, 10)
    ok = score >= MIN_ENTRY_TIMING
    return {"score": score, "ok": ok, "reasons": reasons[:4], "detail": f"entry_timing {score:.1f}"}


def pattern_memory_boost(patterns: List[str], bias: str) -> Tuple[float, str]:
    """Use saved pattern_stats WR to boost/penalize. Adaptive: only when data exists."""
    if not patterns:
        return 0.0, ""
    try:
        conn = sqlite3.connect(DB_PATH)
        boost = 0.0
        notes = []
        for p in patterns[:5]:
            key = f"{p}_{bias}"
            row = conn.execute(
                "SELECT total, wins FROM pattern_stats WHERE pattern_key=?", (key,)
            ).fetchone()
            if not row or row[0] < 4:
                continue
            total, wins = row[0], row[1]
            wr = wins / total
            if wr >= 0.55:
                boost += 0.35
                notes.append(f"{p} WR{wr:.0%}")
            elif wr <= 0.35:
                boost -= 0.45
                notes.append(f"{p} weakWR{wr:.0%}")
        conn.close()
        return clamp(boost, -1.2, 1.2), (", ".join(notes[:3]) if notes else "")
    except Exception:
        return 0.0, ""


def adaptive_factor_gate(fs: dict, snap: dict, bias: str) -> Tuple[bool, List[str]]:
    """
    Some factors only matter when primary confluence is weak.
    Avoid over-filtering when structure+alignment+timing already strong.
    """
    issues = []
    primary_strong = (
        fs.get("structure", 0) >= 7.2
        and fs.get("alignment", 0) >= 7.0
        and fs.get("entry_timing", 0) >= 6.5
    )
    # Always-on critical gates
    if fs.get("_macd_conflict"):
        issues.append("MACD conflict")
    if fs.get("location", 0) < MIN_LOCATION_SCORE:
        issues.append("location poor")
    if fs.get("entry_timing", 0) < MIN_ENTRY_TIMING:
        issues.append("entry timing weak")
    # Adaptive: only if primary not strong
    if not primary_strong:
        if fs.get("liquidity", 0) < 4.0:
            issues.append("liquidity weak (adaptive)")
        if fs.get("volume", 0) < 4.0:
            issues.append("volume weak (adaptive)")
        if fs.get("correlation", 0) < 3.5:
            issues.append("market sync weak (adaptive)")
    return len(issues) == 0, issues


# ═══════════════════════════════════════════════════════════════
# 10. OI + FUNDING + ORDERBOOK + BASIS ANALYSIS
# ═══════════════════════════════════════════════════════════════
def analyze_oi_funding(ticker: dict, price_change_pct: float) -> dict:
    """Price + OI matrix + funding extremes."""
    oi_chg = ticker.get("oi_change_usd_6h") or 0
    oi_usd = ticker.get("oi_value_usd") or 0
    funding = ticker.get("funding_rate") or 0  # already in decimal form on Delta
    # Delta funding_rate often like 0.01 = 1% (check magnitude)
    # From live: 0.01 → treat as percent-like; normalize to bps-ish
    fund_pct = funding * 100 if abs(funding) < 1 else funding  # heuristic

    # Price + OI matrix
    if price_change_pct > 0.15 and oi_chg > 0:
        matrix = "Long Buildup"
    elif price_change_pct > 0.15 and oi_chg < 0:
        matrix = "Short Covering"
    elif price_change_pct < -0.15 and oi_chg > 0:
        matrix = "Short Buildup"
    elif price_change_pct < -0.15 and oi_chg < 0:
        matrix = "Long Unwinding"
    else:
        matrix = "Neutral / Mixed"

    # Funding crowding
    if fund_pct > 0.05:
        fund_state = "Crowded Longs (high +funding)"
    elif fund_pct < -0.05:
        fund_state = "Crowded Shorts (high -funding)"
    elif abs(fund_pct) > 0.02:
        fund_state = "Elevated funding"
    else:
        fund_state = "Normal funding"

    score = 5.5
    if matrix in ("Long Buildup", "Short Buildup"):
        score += 1.5
    if "Crowded" in fund_state:
        score -= 1.0  # caution — squeeze risk opposite
    if matrix == "Neutral / Mixed":
        score -= 0.5

    return {
        "oi_usd": oi_usd,
        "oi_change_6h": oi_chg,
        "funding": funding,
        "fund_pct": fund_pct,
        "matrix": matrix,
        "fund_state": fund_state,
        "score": clamp(score, 0, 10),
        "detail": f"{matrix} · {fund_state}",
    }


def analyze_orderbook(ob: dict) -> dict:
    if not ob:
        return {"score": 5.0, "detail": "No OB", "imbalance": 0, "spread_bps": 0}
    imb = ob.get("imbalance") or 0
    spread_bps = ob.get("spread_bps") or 0
    score = 5.0 + imb * 3.5  # bid-heavy helps longs
    if spread_bps > 8:
        score -= 1.5  # wide spread = caution
    elif spread_bps < 2:
        score += 0.5
    return {
        "imbalance": round(imb, 3),
        "spread_bps": round(spread_bps, 2),
        "best_bid": ob.get("best_bid"),
        "best_ask": ob.get("best_ask"),
        "bid_vol": ob.get("bid_vol"),
        "ask_vol": ob.get("ask_vol"),
        "score": clamp(score, 0, 10),
        "detail": f"Imb {imb:+.2f} · Spread {spread_bps:.1f}bps",
    }


def analyze_basis(ticker: dict) -> dict:
    mark = ticker.get("mark") or 0
    spot = ticker.get("spot_price") or mark
    basis = ticker.get("mark_basis")
    if basis is None and spot:
        basis = (mark - spot) / spot if spot else 0
    if basis is None:
        basis = 0
    if basis > 0.0008:
        state = "Contango / Premium"
    elif basis < -0.0008:
        state = "Backwardation / Discount"
    else:
        state = "Flat basis"
    return {
        "basis": basis,
        "state": state,
        "mark": mark,
        "spot": spot,
        "detail": f"{state} ({basis*100:.3f}%)",
    }


# ═══════════════════════════════════════════════════════════════
# 11. STATISTICAL + REGIME
# ═══════════════════════════════════════════════════════════════
def statistical_snapshot(closes: List[float], atr: float) -> dict:
    if not closes or len(closes) < 20:
        return {"z": 0, "vol_pct": 50, "detail": "—"}
    window = closes[-20:]
    mean = sum(window) / len(window)
    var = sum((x - mean) ** 2 for x in window) / len(window)
    std = math.sqrt(var) or 1e-9
    z = (closes[-1] - mean) / std
    # vol percentile rough: compare recent ATR to longer
    ranges = [abs(closes[i] - closes[i - 1]) for i in range(-20, 0)]
    recent_vol = sum(ranges[-5:]) / 5
    long_vol = sum(ranges) / 20
    vol_pct = clamp((recent_vol / (long_vol or 1e-9)) * 50, 5, 95)
    return {
        "z": round(z, 2),
        "vol_pct": round(vol_pct, 1),
        "mean": mean,
        "std": std,
        "detail": f"Z={z:.2f} · VolPctl≈{vol_pct:.0f}",
    }


def detect_regime(closes, atr, struct_label: str, rhythm_state: str, mkt_sync: str) -> str:
    if not closes or len(closes) < 20:
        return "Unknown"
    ret = (closes[-1] - closes[-20]) / closes[-20] if closes[-20] else 0
    vol = atr / closes[-1] if closes[-1] else 0
    if "Risk-On" in mkt_sync and ret > 0.01:
        return "Risk-On Trend"
    if "Risk-Off" in mkt_sync and ret < -0.01:
        return "Risk-Off Trend"
    if vol > 0.025:
        return "High Volatility"
    if abs(ret) < 0.006 and vol < 0.012:
        return "Range / Low Vol"
    if "Chop" in struct_label or "Range" in struct_label:
        return "Choppy"
    if rhythm_state == "Impulse":
        return "Momentum"
    if ret > 0.012:
        return "Uptrend"
    if ret < -0.012:
        return "Downtrend"
    return "Transition"


# ═══════════════════════════════════════════════════════════════
# 12. FULL SNAPSHOT + SCORING
# ═══════════════════════════════════════════════════════════════
def local_market_snapshot(symbol: str = None) -> Optional[dict]:
    symbol = symbol or STATE["symbol"]
    t = get_ticker_full(symbol)
    if not t or t["mark"] <= 0:
        return None
    ltp = t["mark"]

    # v17 — primary 30m, mid 45m (15m×3), HTF 1h
    k5 = fetch_candles("30m", 80, symbol)   # primary (kept key name k5 for compat)
    k15_raw = fetch_candles("15m", 90, symbol)
    k1h = fetch_candles("1h", 40, symbol)
    if not k5:
        return None

    # Build 45m from 15m
    k15 = None
    if k15_raw and len(k15_raw.get("close") or []) >= 9:
        h45, l45, c45, v45 = _resample_ohlc(
            k15_raw["high"], k15_raw["low"], k15_raw["close"],
            k15_raw.get("volume") or [], 3,
        )
        o45 = c45  # approx open from close series end-points
        if k15_raw.get("open") and len(k15_raw["open"]) >= 3:
            oo = []
            for i in range(0, len(k15_raw["open"]), 3):
                oo.append(k15_raw["open"][i])
            o45 = oo[:len(c45)]
        k15 = {
            "open": o45 if len(o45) == len(c45) else c45,
            "high": h45, "low": l45, "close": c45, "volume": v45,
        }

    ob = get_orderbook(symbol)

    struct5 = analyze_structure(k5["high"], k5["low"], k5["close"])  # 30m
    struct15 = analyze_structure(k15["high"], k15["low"], k15["close"]) if k15 else struct5  # 45m
    struct1h = analyze_structure(k1h["high"], k1h["low"], k1h["close"]) if k1h else struct5
    rhythm5 = analyze_rhythm(k5["high"], k5["low"], k5["close"])
    liq = analyze_liquidity(k5["high"], k5["low"], k5["close"])
    fvg = detect_fvg(k5["high"], k5["low"], k5["close"])
    oblk = detect_order_block(k5["open"], k5["high"], k5["low"], k5["close"])
    patterns = detect_candle_patterns(k5["open"], k5["high"], k5["low"], k5["close"])

    atr5 = calc_atr(k5["high"], k5["low"], k5["close"]) or max(ltp * 0.004, 1)
    e9 = calc_ema(k5["close"], 9) or ltp
    e21 = calc_ema(k5["close"], 21) or ltp
    rsi5 = calc_rsi(k5["close"], 14)
    macd, _, hist = calc_macd(k5["close"])
    bb_mid, bb_up, bb_lo = calc_bollinger(k5["close"])
    vwap = calc_vwap(k5["high"], k5["low"], k5["close"], k5["volume"])
    vp = volume_profile_approx(k5["high"], k5["low"], k5["close"], k5["volume"])
    stats = statistical_snapshot(k5["close"], atr5)

    vol_ratio = 1.0
    vol_ok = False
    if k5.get("volume") and len(k5["volume"]) >= 10:
        avg = sum(k5["volume"][-10:-1]) / 9 or 1.0
        vol_ratio = k5["volume"][-1] / avg
        vol_ok = vol_ratio >= 1.05

    bb_width = None
    if bb_up and bb_lo and bb_mid:
        bb_width = (bb_up - bb_lo) / bb_mid
    vol_regime = "Normal"
    if bb_width is not None:
        if bb_width < 0.012:
            vol_regime = "Compression"
        elif bb_width > 0.045:
            vol_regime = "Expansion"

    extended = abs(ltp - e21) / e21 > 0.018 if e21 else False  # 30m wider
    price_chg_pct = t.get("ltp_change_24h") or 0

    oi_f = analyze_oi_funding(t, price_chg_pct)
    ob_a = analyze_orderbook(ob)
    basis_a = analyze_basis(t)

    biases = [struct5["bias"], struct15["bias"], struct1h["bias"]]
    long_votes = biases.count("LONG")
    short_votes = biases.count("SHORT")
    # 30m + 45m + 1h: need ≥2 TF agree
    if long_votes >= 2:
        direction_hint = "LONG"
    elif short_votes >= 2:
        direction_hint = "SHORT"
    else:
        direction_hint = "NONE"   # no lone-5m entries

    mkt_rhythm = STATE.get("market_rhythm")
    if not mkt_rhythm or time.time() - mkt_rhythm.get("_ts", 0) > 90:
        mkt_rhythm = market_wide_rhythm()
        mkt_rhythm["_ts"] = time.time()
        STATE["market_rhythm"] = mkt_rhythm

    regime = detect_regime(
        k5["close"], atr5, struct5.get("label", ""),
        rhythm5.get("state", ""), mkt_rhythm.get("sync", ""),
    )

    flow = orderflow_engine(symbol, k5["close"], k5["volume"], k5["high"], k5["low"])
    ob_adv = orderbook_advanced(symbol, ob)
    liq_adv = liquidation_engine(symbol)
    oi_adv = advanced_oi_funding(t, flow, liq_adv)
    micro = microstructure_engine(k5["open"], k5["high"], k5["low"], k5["close"], k5["volume"])
    adv_stats = advanced_statistics(k5["close"])
    adv_regime = advanced_regime(k5["close"], adv_stats, ob_a)
    liquidity_adv = liquidity_migration(symbol, ob_adv)
    divergence = advanced_divergence(k5["close"], flow, oi_adv, adv_stats)
    mm = market_maker_proxy(ob_adv, flow)
    correlations = correlation_engine(symbol, k5["close"])
    externals = optional_external_modules(symbol)

    return {
        "symbol": symbol, "ltp": ltp, "atr5": atr5,
        "struct5": struct5, "struct15": struct15, "struct1h": struct1h,
        "rhythm5": rhythm5, "liquidity": liq, "fvg": fvg, "oblk": oblk,
        "patterns": patterns, "e9": e9, "e21": e21, "rsi5": rsi5,
        "macd": macd, "macd_hist": hist, "bb_up": bb_up, "bb_lo": bb_lo,
        "bb_width": bb_width, "vol_regime": vol_regime,
        "vwap": vwap, "vp": vp, "stats": stats,
        "vol_ratio": vol_ratio, "vol_ok": vol_ok, "extended": extended,
        "direction_hint": direction_hint,
        "oi_funding": oi_f, "orderbook": ob_a, "basis": basis_a,
        "ticker": t, "ob_raw": ob,
        "orderflow": flow, "orderbook_advanced": ob_adv, "liquidations": liq_adv,
        "oi_advanced": oi_adv, "microstructure": micro, "advanced_stats": adv_stats,
        "advanced_regime": adv_regime, "liquidity_advanced": liquidity_adv,
        "divergence": divergence, "market_maker": mm, "correlations": correlations,
        "external": externals,
        "session": current_session(), "news_window": is_high_impact_window(),
        "market_rhythm": mkt_rhythm, "regime": regime,
        "_closes": k5["close"],
    }


def score_filters(snap: dict) -> Dict[str, float]:
    scores = {}
    bias = snap.get("direction_hint") or "NONE"

    # Structure (stricter — v15)
    s5 = snap.get("struct5") or {}
    s15 = snap.get("struct15") or {}
    s1h = snap.get("struct1h") or {}
    sc = 4.0
    if s5.get("bias") == bias and bias != "NONE":
        sc += 2.2
    if s15.get("bias") == bias and bias != "NONE":
        sc += 1.8
    if s1h.get("bias") == bias and bias != "NONE":
        sc += 1.6
    if s5.get("bos"):
        sc += 1.0
    if s5.get("choch"):
        sc += 0.6
    if s5.get("label") in ("Range / Chop", "Unclear"):
        sc -= 2.2
    # HTF flat while lower TF directional → structure penalty
    if bias != "NONE" and s1h.get("bias") in (None, "NONE", "Flat") and s15.get("bias") in (None, "NONE", "Flat"):
        sc -= 1.8
    scores["structure"] = clamp(sc, 0, 10)

    # Rhythm
    r5 = snap.get("rhythm5") or {}
    sc = r5.get("tempo", 5.0)
    if r5.get("state") == "Impulse" and r5.get("direction") == ("Bullish" if bias == "LONG" else "Bearish"):
        sc = min(10, sc + 1.8)
    if r5.get("compression"):
        sc = max(3.5, sc - 1.0)
    scores["rhythm"] = clamp(sc, 0, 10)

    # Alignment (v15 stricter)
    sc = 3.5
    votes = [s5.get("bias"), s15.get("bias"), s1h.get("bias")]
    if bias != "NONE":
        sc += votes.count(bias) * 2.0   # was 1.7
    if snap.get("e9") and snap.get("e21"):
        if bias == "LONG" and snap["e9"] > snap["e21"]:
            sc += 1.2
        elif bias == "SHORT" and snap["e9"] < snap["e21"]:
            sc += 1.2
    # 1H flat penalty
    if bias != "NONE" and s1h.get("bias") in (None, "NONE", "Flat"):
        sc -= 1.3
    scores["alignment"] = clamp(sc, 0, 10)

    # Liquidity
    liq = snap.get("liquidity") or {}
    sc = 5.5
    if bias == "LONG" and liq.get("sweep_low"):
        sc = 8.6
    if bias == "SHORT" and liq.get("sweep_high"):
        sc = 8.6
    scores["liquidity"] = clamp(sc, 0, 10)

    # FVG + OB
    fvg = snap.get("fvg") or {}
    oblk = snap.get("oblk") or {}
    sc = 5.0
    if bias == "LONG" and fvg.get("bull_fvg"):
        sc += 1.8
    if bias == "SHORT" and fvg.get("bear_fvg"):
        sc += 1.8
    if bias == "LONG" and oblk.get("bull_ob"):
        sc += 1.3
    if bias == "SHORT" and oblk.get("bear_ob"):
        sc += 1.3
    if "Displacement" in (snap.get("patterns") or []):
        sc += 0.7
    scores["fvg_ob"] = clamp(sc, 0, 10)

    # Momentum — HARD MACD veto (v15 anti WRONG_DIRECTION)
    sc = 5.0
    hist = snap.get("macd_hist")
    macd_conflict = False
    if hist is not None:
        if bias == "LONG" and hist > 0:
            sc += 2.0
        elif bias == "SHORT" and hist < 0:
            sc += 2.0
        else:
            sc -= 2.8          # was only -0.7
            macd_conflict = True
    rsi = snap.get("rsi5")
    if rsi is not None:
        if bias == "LONG" and 40 <= rsi <= 68:
            sc += 1.3
        elif bias == "SHORT" and 32 <= rsi <= 60:
            sc += 1.3
        elif bias == "LONG" and rsi > 78:
            sc -= 2.4
        elif bias == "SHORT" and rsi < 22:
            sc -= 2.4
    scores["momentum"] = clamp(sc, 0, 10)
    scores["_macd_conflict"] = 1.0 if macd_conflict else 0.0   # flag for gates

    # Volume
    if snap.get("vol_ok"):
        scores["volume"] = clamp(6.5 + (snap.get("vol_ratio", 1) - 1) * 3.2, 0, 10)
    else:
        scores["volume"] = 4.2

    # Volatility
    vr = snap.get("vol_regime", "Normal")
    if vr == "Compression":
        scores["volatility"] = 4.0
    elif vr == "Expansion":
        scores["volatility"] = 7.4 if bias != "NONE" else 5.0
    else:
        scores["volatility"] = 6.4

    # OI + Funding
    oif = snap.get("oi_funding") or {}
    sc = oif.get("score", 5.5)
    matrix = oif.get("matrix", "")
    if bias == "LONG" and matrix in ("Long Buildup", "Short Covering"):
        sc += 1.2
    if bias == "SHORT" and matrix in ("Short Buildup", "Long Unwinding"):
        sc += 1.2
    if "Crowded Longs" in oif.get("fund_state", "") and bias == "LONG":
        sc -= 1.5
    if "Crowded Shorts" in oif.get("fund_state", "") and bias == "SHORT":
        sc -= 1.5
    scores["oi_funding"] = clamp(sc, 0, 10)

    # Orderbook
    oba = snap.get("orderbook") or {}
    sc = oba.get("score", 5.0)
    imb = oba.get("imbalance", 0)
    if bias == "LONG" and imb > 0.15:
        sc += 1.0
    if bias == "SHORT" and imb < -0.15:
        sc += 1.0
    if bias == "LONG" and imb < -0.25:
        sc -= 1.2
    if bias == "SHORT" and imb > 0.25:
        sc -= 1.2
    scores["orderbook"] = clamp(sc, 0, 10)

    # Location (was strongest negative contributor in v14 post-mortem)
    scores["location"] = 2.5 if snap.get("extended") else 7.5
    if snap.get("extended"):
        scores["location"] = max(0.5, scores["location"] - 1.5)  # extra penalty
    vwap = snap.get("vwap")
    if vwap and snap.get("ltp"):
        if bias == "LONG" and snap["ltp"] >= vwap:
            scores["location"] = min(10, scores["location"] + 0.9)
        if bias == "SHORT" and snap["ltp"] <= vwap:
            scores["location"] = min(10, scores["location"] + 0.9)
        # extended beyond VWAP in trade direction still risky
        if bias == "LONG" and snap.get("extended") and snap["ltp"] > vwap:
            scores["location"] = max(1.0, scores["location"] - 1.2)
        if bias == "SHORT" and snap.get("extended") and snap["ltp"] < vwap:
            scores["location"] = max(1.0, scores["location"] - 1.2)

    # Entry timing (v16 — top failure mode in post-mortem)
    et = entry_timing_score(snap, bias)
    scores["entry_timing"] = et["score"]
    scores["_entry_timing_ok"] = 1.0 if et["ok"] else 0.0
    # Pattern memory adaptive boost
    pboost, pnote = pattern_memory_boost(snap.get("patterns") or [], bias)
    if pboost:
        scores["entry_timing"] = clamp(scores["entry_timing"] + pboost, 0, 10)
        scores["_pattern_note"] = pnote

    # Session
    if snap.get("news_window"):
        scores["session"] = 2.0
    elif snap.get("session") in ("LONDON", "NY", "OVERLAP_LN_NY"):
        scores["session"] = 7.7
    elif snap.get("session") == "ASIA":
        scores["session"] = 6.0
    else:
        scores["session"] = 4.5

    # Correlation
    mr = snap.get("market_rhythm") or {}
    sync = mr.get("sync", "")
    sc = 5.5
    if bias == "LONG" and "Risk-On" in sync:
        sc = 8.6
    elif bias == "SHORT" and "Risk-Off" in sync:
        sc = 8.6
    elif "Mixed" in sync:
        sc = 3.7
    scores["correlation"] = clamp(sc, 0, 10)

    # V11 advanced order-flow / microstructure layers
    flow = snap.get("orderflow") or {}
    ag = safe_float(flow.get("aggression"))
    sc = 5.0 + (ag * 3.0)
    if bias == "LONG" and ag < -0.20: sc -= 2.0
    if bias == "SHORT" and ag > 0.20: sc -= 2.0
    if flow.get("absorption"): sc += 0.4
    scores["orderflow"] = clamp(sc,0,10)

    oba = snap.get("orderbook_advanced") or {}
    sc = 5.0 + safe_float(oba.get("depth_25pct_imbalance"))*3.0
    if bias == "LONG" and oba.get("pulling_bid"): sc -= 0.7
    if bias == "SHORT" and oba.get("pulling_ask"): sc -= 0.7
    scores["micro_orderbook"] = clamp(sc,0,10)

    oi = snap.get("oi_advanced") or {}
    sc = 5.0
    if bias == "LONG" and safe_float(oi.get("oi_delta"))>0: sc += 1.0
    if bias == "SHORT" and safe_float(oi.get("oi_delta"))>0: sc += 1.0
    if (bias=="LONG" and safe_float(oi.get("funding"))>.0005) or (bias=="SHORT" and safe_float(oi.get("funding"))<-.0005): sc -= .8
    scores["oi_funding_advanced"] = clamp(sc,0,10)

    advreg=snap.get("advanced_regime") or {}
    scores["regime_quality"] = clamp(5.0 + safe_float(advreg.get("trend_probability"))*2 - safe_float(advreg.get("transition_probability")),0,10)

    div=snap.get("divergence") or {}
    scores["divergence"] = clamp(6.0 - sum(1 for v in div.values() if v),0,10)

    liqa=snap.get("liquidations") or {}
    if liqa.get("available"):
        net=safe_float(liqa.get("net"))
        sc=5.0 + (1.0 if (bias=="LONG" and net<0) or (bias=="SHORT" and net>0) else 0)
        if liqa.get("cascade"): sc-=1.0
        scores["liquidation"] = clamp(sc,0,10)
    else:
        scores["liquidation"] = 5.0

    return scores


def weighted_score(filter_scores: dict) -> float:
    weights = load_weights()
    total = wsum = 0.0
    for k, sc in filter_scores.items():
        w = weights.get(k, 0.04)
        total += sc * w
        wsum += w
    return total / wsum if wsum else 5.0


def build_verdicts(snap: dict, fs: dict, bias: str, status: str) -> Tuple[List[str], List[str]]:
    why_trade, why_not = [], []
    if bias == "NONE":
        why_not.append("No clear multi-TF directional bias (≥2 TF required)")
    if fs.get("structure", 0) >= 7:
        why_trade.append(f"Strong structure ({snap.get('struct5', {}).get('detail', '')})")
    else:
        why_not.append("Structure confluence weak")
    if fs.get("rhythm", 0) >= 7:
        why_trade.append(f"Rhythm supportive ({snap.get('rhythm5', {}).get('state')})")
    if fs.get("liquidity", 0) >= 7.5:
        why_trade.append("Liquidity sweep in trade direction")
    if fs.get("oi_funding", 0) >= 7:
        why_trade.append(f"OI matrix: {snap.get('oi_funding', {}).get('matrix')}")
    if "Crowded" in str(snap.get("oi_funding", {}).get("fund_state", "")):
        why_not.append("Crowded funding — squeeze risk")
    if fs.get("orderbook", 0) >= 7:
        why_trade.append("Orderbook imbalance aligned")
    elif fs.get("orderbook", 0) <= 4:
        why_not.append("Orderbook opposing / wide spread")
    if fs.get("correlation", 0) >= 7.5:
        why_trade.append(f"Market rhythm: {snap.get('market_rhythm', {}).get('sync')}")
    elif "Mixed" in str(snap.get("market_rhythm", {}).get("sync", "")):
        why_not.append("Mixed market — rotation risk")
    if snap.get("extended"):
        why_not.append("Price extended from EMA21 — location risk")
    if snap.get("news_window"):
        why_not.append("High-impact news window")
    if fs.get("volatility", 0) <= 4.5:
        why_not.append("Volatility compression — wait expansion")
    if fs.get("_macd_conflict"):
        why_not.append("MACD histogram opposing bias — high WRONG_DIRECTION risk")
    if fs.get("momentum", 0) < MIN_MOMENTUM_SCORE:
        why_not.append("Momentum score below floor")
    if status == "TRADE" and not why_trade:
        why_trade.append("All quality gates cleared")
    if status == "WAIT" and not why_not:
        why_not.append("Borderline setup — wait for clearer confirmation")
    if status not in ("TRADE", "WAIT") and not why_not:
        why_not.append("Score or confluence below bar")
    return why_trade[:5], why_not[:6]


# ═══════════════════════════════════════════════════════════════
# 13. DECISION ENGINE
# ═══════════════════════════════════════════════════════════════
def unavailable_decision(symbol: str, why: str = "Data unavailable") -> dict:
    """Safe NO_TRADE stub — never raises; used when coin invalid/offline."""
    return {
        "status": "NO_TRADE",
        "hard": "NO TRADE",
        "direction": "NONE",
        "score": 0,
        "confidence": 0,
        "entry": 0,
        "sl": 0,
        "tp": 0,
        "rrr": 0,
        "reason": why,
        "symbol": symbol or "?",
        "filter_scores": {},
        "classification": "UNAVAILABLE",
        "why_trade": [],
        "why_not": [why, "Coin skipped — continue next"],
        "data_quality": {"score": 0, "issues": [why], "ok": False, "detail": why},
        "skipped": True,
        "unavailable": True,
    }


def symbol_is_tradeable(symbol: str) -> Tuple[bool, str]:
    """Quick check: ticker + at least some candles. Never throws."""
    try:
        symbol = sanitize_symbol(symbol)
        t = get_ticker_full(symbol)
        if not t or safe_float(t.get("mark")) <= 0:
            return False, "Ticker unavailable"
        k = fetch_candles("5m", 30, symbol)
        if not k or len(k.get("close") or []) < 10:
            return False, "Candles unavailable"
        return True, "ok"
    except Exception as e:
        return False, f"Check error: {e}"


def run_committee(symbol: str = None, source: str = "live") -> dict:
    symbol = sanitize_symbol(symbol or STATE["symbol"])
    try:
        ok, why = symbol_is_tradeable(symbol)
        if not ok:
            log.warning("committee skip %s: %s", symbol, why)
            return unavailable_decision(symbol, why)
        snap = local_market_snapshot(symbol)
        if not snap:
            return unavailable_decision(symbol, "Snapshot failed / no data")
    except Exception as e:
        log.warning("committee exception %s: %s", symbol, e)
        return unavailable_decision(symbol, f"Error: {e}")

    bias = snap.get("direction_hint") or "NONE"
    fs = score_filters(snap)
    score = weighted_score(fs)
    dq = decision_quality(snap, fs, score, bias)
    dqr = data_quality_report(snap)
    # Data-quality gate: cut confidence when feeds are proxy/stale/missing
    conf_raw = dq.get("dynamic_confidence", score * 10)
    conf = int(clamp(conf_raw * (0.55 + 0.045 * dqr.get("score", 5)), 5, 97))

    atr = snap["atr5"]
    ltp = snap["ltp"]
    # v18.3 per-coin SL/TP (MAE/MFE recipes) — not one global ATR mult
    risk = coin_risk(symbol)
    sl_mult = float(risk.get("sl_atr") or SL_ATR_MULT)
    sl_dist = clamp(atr * sl_mult, ltp * SL_MIN_PCT, ltp * SL_MAX_PCT)
    struct = snap.get("struct5") or {}
    if bias == "LONG" and struct.get("last_sl"):
        candidate = ltp - struct["last_sl"]
        if ltp * SL_MIN_PCT < candidate < ltp * SL_MAX_PCT * 1.45:
            sl_dist = max(sl_dist, candidate)   # never tighter than structure SL
    elif bias == "SHORT" and struct.get("last_sh"):
        candidate = struct["last_sh"] - ltp
        if ltp * SL_MIN_PCT < candidate < ltp * SL_MAX_PCT * 1.45:
            sl_dist = max(sl_dist, candidate)

    # Per-coin RR, still ≥ ~1.5 practice floor; regime can tighten TP
    rr_mult = max(float(risk.get("tp_rr") or TP_RR_MULT), 1.5)
    if snap.get("vol_regime") == "Expansion":
        rr_mult = max(rr_mult, min(rr_mult + 0.25, 2.4))
    if snap.get("vol_regime") == "Compression":
        rr_mult = max(1.5, min(rr_mult, 1.85))
    regime = str(snap.get("regime") or "")
    if "TRANSITION" in regime.upper() or "CHOP" in regime.upper():
        rr_mult = max(1.5, min(rr_mult, min(rr_mult, TP_RR_TRANSITION + 0.1)))

    entry = ltp
    if bias == "LONG":
        sl, tp = entry - sl_dist, entry + sl_dist * rr_mult
    elif bias == "SHORT":
        sl, tp = entry + sl_dist, entry - sl_dist * rr_mult
    else:
        sl = tp = entry
    rrr = rr_mult if bias != "NONE" else 0

    # ── v17 QUALITY GATES (post-mortem driven) ─────────────────
    status, hard, classification = "NO_TRADE", "NO TRADE", "NoSetup"
    data_ok = dqr.get("score", 10) >= 5.0
    s15 = snap.get("struct15") or {}
    s1h = snap.get("struct1h") or {}
    macd_conflict = bool(fs.get("_macd_conflict"))
    mr = snap.get("market_rhythm") or {}
    opposing_mkt = (bias == "LONG" and "Risk-Off" in str(mr.get("sync", ""))) or \
                   (bias == "SHORT" and "Risk-On" in str(mr.get("sync", "")))

    htf_agree = (s15.get("bias") == bias) or (s1h.get("bias") == bias)
    agree_15m = s15.get("bias") == bias
    both_htf_flat = (
        s1h.get("bias") in (None, "NONE", "Flat")
        and s15.get("bias") in (None, "NONE", "Flat")
    )
    short_1h_flat = (
        bias == "SHORT" and s1h.get("bias") in (None, "NONE", "Flat")
    )
    mtf_conflict = (
        s15.get("bias") in ("LONG", "SHORT")
        and s1h.get("bias") in ("LONG", "SHORT")
        and s15.get("bias") != s1h.get("bias")
    )
    struct_ok = fs.get("structure", 0) >= MIN_STRUCTURE_SCORE
    align_ok  = fs.get("alignment", 0) >= MIN_ALIGNMENT_SCORE
    mom_ok    = fs.get("momentum", 0) >= MIN_MOMENTUM_SCORE and not macd_conflict
    loc_ok    = fs.get("location", 0) >= MIN_LOCATION_SCORE
    rhythm_ok = fs.get("rhythm", 0) >= 4.5
    timing_ok = fs.get("entry_timing", 0) >= MIN_ENTRY_TIMING
    vol_ok    = fs.get("volume", 0) >= MIN_VOLUME_SCORE or bool(snap.get("vol_ok"))
    adapt_ok, adapt_issues = adaptive_factor_gate(fs, snap, bias)

    wait_reasons = []
    # Anti WRONG_DIRECTION (~50% reduction target from v16.1 data)
    if bias == "NONE":
        classification = "NoClearBias"
    elif not data_ok:
        classification = "DataQualityBlock"
        wait_reasons.append("Insufficient / thin data — skip")
    elif BLOCK_FLAT_HTF and both_htf_flat:
        classification = "NEVER_FAVORABLE_Risk"
        wait_reasons.append("1H+15m Flat — never-favorable risk")
    elif BLOCK_SHORT_1H_FLAT and short_1h_flat and not agree_15m:
        classification = "SHORT_1H_Flat"
        wait_reasons.append("SHORT with 1H flat — high wrong-direction risk")
    elif REQUIRE_1H_NOT_FLAT and s1h.get("bias") in (None, "NONE", "Flat"):
        classification = "1H_Flat_Block"
        wait_reasons.append("1H Flat — never-favorable risk (MFE≈0 pattern)")
    elif bias == "SHORT" and s1h.get("bias") != "SHORT":
        classification = "SHORT_Needs_1H"
        wait_reasons.append("SHORT without 1H bearish — WD pattern (72% of WD was SHORT)")
    elif REQUIRE_BOS_HARD and not (snap.get("struct5") or {}).get("bos") and (snap.get("struct5") or {}).get("choch"):
        classification = "CHOCH_Only"
        wait_reasons.append("CHOCH without BOS — wait for real break")
    elif REQUIRE_BOS_HARD and not (snap.get("struct5") or {}).get("bos"):
        classification = "Need_BOS"
        wait_reasons.append("No BOS — structure break required")
    elif mtf_conflict:
        classification = "MTF_Conflict"
        wait_reasons.append("15m vs 1H conflict — no trade")
    elif macd_conflict:
        classification = "MACD_Conflict"
        wait_reasons.append("MACD opposing bias — wait confirmation")
    elif opposing_mkt:
        classification = "MarketRhythmOppose"
        wait_reasons.append("Market-wide rhythm against trade")
    elif REQUIRE_15M_AGREE and not agree_15m:
        classification = "Need15mAgree"
        wait_reasons.append("15m not aligned — wait confirmation")
    elif not htf_agree and REQUIRE_HTF_AGREE:
        classification = "HTF_NotAligned"
        wait_reasons.append("15m/1H not agreeing — wait HTF confirmation")
    elif not vol_ok:
        classification = "InsufficientVolume"
        wait_reasons.append("Low volume / thin data — filter (INSUFFICIENT_DATA)")
    elif not timing_ok and REQUIRE_ENTRY_CONFIRM and score < (MIN_SCORE_TRADE + 0.4):
        # weak timing → WAIT unless score is clearly strong
        classification = "EntryTimingWeak"
        wait_reasons.append("Entry timing weak — wait pullback/confirm candle")
        et = entry_timing_score(snap, bias)
        wait_reasons.extend(et.get("reasons") or [])
    elif score >= coin_score_floor(symbol, bias, MIN_SCORE_TRADE) and rrr >= MIN_RR and struct_ok and align_ok and mom_ok and loc_ok and rhythm_ok and vol_ok and timing_ok and adapt_ok and (struct.get('bos') if REQUIRE_BOS_HARD else (not REQUIRE_BOS_OR_STRONG or struct.get('bos') or fs.get('structure',0) >= 6.8)):
        status, hard = "TRADE", "TRADE"
        classification = struct.get("label", "Setup")
        if struct.get("bos"):
            classification += " + BOS"
        if (snap.get("liquidity") or {}).get("sweep_low") or (snap.get("liquidity") or {}).get("sweep_high"):
            classification += " + Sweep"
        if snap.get("patterns"):
            classification += " + " + ",".join(snap["patterns"][:2])
        oim = (snap.get("oi_funding") or {}).get("matrix", "")
        if oim and oim != "Neutral / Mixed":
            classification += f" + {oim}"
        pnote = fs.get("_pattern_note")
        if pnote:
            wait_reasons = []  # not wait
    elif score >= MIN_SCORE_WAIT and bias != "NONE":
        status, hard = "WAIT", "WAIT FOR CONFIRMATION"
        classification = "WaitConfirmation"
        if not struct_ok:
            wait_reasons.append("Structure score below floor")
        if not align_ok:
            wait_reasons.append("MTF alignment weak")
        if not mom_ok:
            wait_reasons.append("Momentum / MACD not ready")
        if not loc_ok:
            wait_reasons.append("Location extended / poor")
        if not timing_ok:
            wait_reasons.append("Entry timing — wait pullback")
        if not rhythm_ok:
            wait_reasons.append("Rhythm not supportive")
        if adapt_issues:
            wait_reasons.extend(adapt_issues[:2])
        if score < MIN_SCORE_TRADE:
            wait_reasons.append(f"Score {score:.1f} < {MIN_SCORE_TRADE}")
    else:
        classification = "ScoreBelowBar" if bias != "NONE" else "NoClearBias"

    why_trade, why_not = build_verdicts(snap, fs, bias, status)
    if wait_reasons:
        why_not = wait_reasons + why_not
        why_not = why_not[:6]
    reason = " | ".join(filter(None, [
        struct.get("detail"),
        (snap.get("rhythm5") or {}).get("detail"),
        (snap.get("oi_funding") or {}).get("detail"),
        (snap.get("orderbook") or {}).get("detail"),
    ]))[:280]

    d = {
        "status": status, "hard": hard, "direction": bias,
        "score": round(score, 2), "confidence": conf,
        "entry": round(entry, 4), "sl": round(sl, 4), "tp": round(tp, 4),
        "sl_dist": round(sl_dist, 4), "tp_dist": round(abs(tp - entry), 4),
        "rrr": round(rrr, 2), "reason": reason,
        "classification": classification,
        "filter_scores": {k: round(v, 2) for k, v in fs.items()},
        "structure_detail": struct,
        "rhythm_detail": snap.get("rhythm5"),
        "oi_funding": snap.get("oi_funding"),
        "orderbook": snap.get("orderbook"),
        "basis": snap.get("basis"),
        "market_rhythm": snap.get("market_rhythm"),
        "regime": snap.get("regime"),
        "vwap": snap.get("vwap"),
        "vp": snap.get("vp"),
        "stats": snap.get("stats"),
        "why_trade": why_trade,
        "why_not": why_not,
        "learn": learning_warning(symbol, bias, f"{symbol}_{bias}_{classification}"),
        "symbol": symbol, "snap": snap,
        "session": snap.get("session"), "vol_regime": snap.get("vol_regime"),
        "patterns": snap.get("patterns"),
        "decision_quality": dq,
        "data_quality": dqr,
        "orderflow": snap.get("orderflow"),
        "orderbook_advanced": snap.get("orderbook_advanced"),
        "liquidations": snap.get("liquidations"),
        "oi_advanced": snap.get("oi_advanced"),
        "microstructure": snap.get("microstructure"),
        "advanced_stats": snap.get("advanced_stats"),
        "advanced_regime": snap.get("advanced_regime"),
        "liquidity_advanced": snap.get("liquidity_advanced"),
        "divergence": snap.get("divergence"),
        "market_maker": snap.get("market_maker"),
        "correlations": snap.get("correlations"),
        "external": snap.get("external"),
    }
    STATE["last_decision"] = d
    bump_daily("analyses")
    if status == "TRADE":
        bump_daily("trade_calls")
    return d


def suggest_size(entry: float, sl: float, tp: float = None, account_usd=None,
                 risk_pct=None, risk_usd: float = None, leverage: float = None) -> dict:
    """
    Practice sizing: default risk $0.25 → target ≥$0.50 profit at RRR≥2, 10x lev.
    contracts ≈ risk_usd / |entry-sl|  (price pts risk per contract on linear USD pair)
    """
    risk_usd = risk_usd if risk_usd is not None else RISK_USD_DEFAULT
    lev = leverage if leverage is not None else LEVERAGE_DEFAULT
    dist = abs(entry - sl)
    if dist <= 0 or entry <= 0:
        return {"contracts": 0, "risk_usd": 0, "reward_usd": 0, "rrr": 0,
                "note": "Invalid SL", "leverage": lev}
    # contracts so that SL loss ≈ risk_usd (1 contract moves $1 per $1 price on USD linear)
    contracts = max(1, int(round(risk_usd / dist)))
    actual_risk = contracts * dist
    tp_dist = abs(tp - entry) if tp and tp != entry else dist * TP_RR_MULT
    actual_reward = contracts * tp_dist
    rrr = tp_dist / dist if dist else 0
    # margin approx for 10x: notional/lev
    notional = contracts * entry
    margin = notional / lev if lev else notional
    note = (
        f"Risk ${actual_risk:.2f} → TP ${actual_reward:.2f} · "
        f"RRR 1:{rrr:.1f} · ~{contracts}c · {lev:.0f}x · margin~${margin:.2f}"
    )
    return {
        "contracts": contracts,
        "risk_usd": round(actual_risk, 2),
        "reward_usd": round(actual_reward, 2),
        "rrr": round(rrr, 2),
        "dist": round(dist, 4),
        "tp_dist": round(tp_dist, 4),
        "leverage": lev,
        "margin_usd": round(margin, 2),
        "note": note,
    }


# ═══════════════════════════════════════════════════════════════
# 14. SCAN / BACKTEST / FORMATTERS
# ═══════════════════════════════════════════════════════════════
def scan_watchlist(symbols: List[str] = None, top_n: int = 6) -> List[dict]:
    symbols = symbols or (STATE.get("watchlist") or WATCHLIST_DEFAULT)
    results = []
    for sym in symbols:
        try:
            d = run_committee(sanitize_symbol(sym), source="scan")
            results.append(d)
        except Exception as e:
            log.warning("scan %s: %s", sym, e)
            results.append(unavailable_decision(sanitize_symbol(sym), f"Scan error: {e}"))
    # Prefer TRADE, then available NO_TRADE, push UNAVAILABLE last
    results.sort(
        key=lambda x: (
            0 if x.get("status") == "TRADE" else (2 if x.get("unavailable") else 1),
            -(x.get("score") or 0),
        )
    )
    STATE["last_scan"] = results
    bump_daily("scans")
    # return top_n available-first, but include unavailable count in full last_scan
    available = [r for r in results if not r.get("unavailable")]
    return (available[:top_n] if available else results[:top_n])


def best_any_coin() -> dict:
    try:
        results = scan_watchlist()
        for d in results:
            if d.get("status") == "TRADE" and not d.get("unavailable"):
                return d
        for d in results:
            if not d.get("unavailable"):
                return d
        return unavailable_decision(STATE.get("symbol") or DEFAULT_SYMBOL, "All watchlist coins unavailable")
    except Exception as e:
        log.warning("best_any_coin: %s", e)
        return unavailable_decision(STATE.get("symbol") or DEFAULT_SYMBOL, str(e))


def format_scan_report(results: List[dict]) -> str:
    lines = ["*Watchlist Scan*"]
    for d in results:
        if d.get("unavailable") or d.get("classification") == "UNAVAILABLE":
            badge = "⚪"
            tag = "SKIPPED/UNAVAILABLE"
        elif d.get("status") == "TRADE":
            badge = "🟢"
            tag = str(d.get("classification", ""))[:24]
        else:
            badge = "🔴"
            tag = str(d.get("classification", ""))[:24]
        lines.append(
            f"{badge} `{d.get('symbol')}` {d.get('direction') or '—'} "
            f"sc={d.get('score')} · {tag}"
        )
    return "\n".join(lines)


def _bt_feature_scores(st, rh, atr, closes, volumes, direction: str) -> dict:
    """Look-ahead-safe feature snapshot from PAST bars only (sub series)."""
    fs = {
        "structure": 7.2 if st.get("bias") == direction else (3.5 if st.get("bias") == "NONE" else 2.5),
        "rhythm": clamp(rh.get("tempo", 5.5), 0, 10),
        "alignment": 7.0 if st.get("bias") == direction else 3.5,
        "liquidity": 5.5,
        "momentum": 5.5,
        "volume": 5.5,
        "volatility": 5.5,
        "fvg_ob": 5.5,
        "location": 6.0,
        "session": 6.0,
        "correlation": 5.5,
        "oi_funding": 5.5,
        "orderbook": 5.5,
        "orderflow": 5.5,
    }
    if st.get("bos"):
        fs["structure"] = min(10, fs["structure"] + 1.2)
    if st.get("choch"):
        fs["structure"] = min(10, fs["structure"] + 0.8)
    if rh.get("compression"):
        fs["rhythm"] = max(2.0, fs["rhythm"] - 1.5)
        fs["volatility"] = 3.5
    if rh.get("state") == "Impulse" and rh.get("direction") == ("Bullish" if direction == "LONG" else "Bearish"):
        fs["rhythm"] = min(10, fs["rhythm"] + 1.5)
        fs["momentum"] = 7.5
    if volumes and len(volumes) >= 10:
        avg = sum(volumes[-10:-1]) / 9 or 1
        ratio = volumes[-1] / avg
        fs["volume"] = clamp(5 + (ratio - 1) * 3, 1, 10)
    if closes and len(closes) >= 15:
        rsi = calc_rsi(closes, 14)
        if rsi is not None:
            if direction == "LONG" and rsi > 75:
                fs["momentum"] = 2.5
            elif direction == "SHORT" and rsi < 25:
                fs["momentum"] = 2.5
            elif direction == "LONG" and 40 <= rsi <= 65:
                fs["momentum"] = max(fs["momentum"], 7.0)
            elif direction == "SHORT" and 35 <= rsi <= 60:
                fs["momentum"] = max(fs["momentum"], 7.0)
    label = st.get("label") or ""
    if "Chop" in label or "Range" in label:
        fs["structure"] = min(fs["structure"], 4.0)
        fs["alignment"] = min(fs["alignment"], 4.0)
    return {k: round(v, 2) for k, v in fs.items()}


def _resample_ohlc(h, l, c, v, every: int):
    """Coarse HTF bars from LTF series (look-ahead safe if called on past-only slice)."""
    if every <= 1 or len(c) < every:
        return h, l, c, v
    hh, ll, cc, vv = [], [], [], []
    for i in range(0, len(c), every):
        chunk_h = h[i:i + every]
        chunk_l = l[i:i + every]
        chunk_c = c[i:i + every]
        chunk_v = v[i:i + every] if v else []
        if not chunk_c:
            continue
        hh.append(max(chunk_h))
        ll.append(min(chunk_l))
        cc.append(chunk_c[-1])
        vv.append(sum(chunk_v) if chunk_v else 0)
    return hh, ll, cc, vv


def _bias_label(b: str) -> str:
    if b in ("LONG", "Bullish", "UP"):
        return "Bullish"
    if b in ("SHORT", "Bearish", "DOWN"):
        return "Bearish"
    if b in ("NONE", "N/A", "", None):
        return "Flat/None"
    return str(b)


def _bt_build_conflict(direction: str, fs: dict, st5, st15, st1h, rh,
                       hit_type: str, mfe_r: float, mae_r: float,
                       counterfactual: str, ambiguous: bool,
                       rsi_val: Optional[float] = None) -> dict:
    """
    Post-Mortem v2 card data for a trade.
    Honest: CVD/OB/OI/funding marked unavailable in pure OHLC backtest — never invent.
    """
    opp = "SHORT" if direction == "LONG" else "LONG"
    b5 = (st5 or {}).get("bias") or "NONE"
    b15 = (st15 or {}).get("bias") or "NONE"
    b1h = (st1h or {}).get("bias") or "NONE"
    lab5 = _bias_label(b5)
    lab15 = _bias_label(b15)
    lab1h = _bias_label(b1h)

    mtf_conflict = (
        (b15 not in (direction, "NONE") and b15 != "NONE")
        or (b1h not in (direction, "NONE") and b1h != "NONE")
    )
    weak_struct = fs.get("structure", 10) < 5 or "Chop" in str((st5 or {}).get("label") or "")
    macd_bear = fs.get("momentum", 5) < 4.5 and direction == "LONG"
    macd_bull = fs.get("momentum", 5) < 4.5 and direction == "SHORT"
    macd_lab = "Bearish" if (macd_bear or (direction == "SHORT" and fs.get("momentum", 5) >= 6)) else (
        "Bullish" if (macd_bull or (direction == "LONG" and fs.get("momentum", 5) >= 6)) else "Neutral"
    )
    if direction == "LONG" and fs.get("momentum", 5) < 4.5:
        macd_lab = "Bearish"
    elif direction == "SHORT" and fs.get("momentum", 5) < 4.5:
        macd_lab = "Bullish"
    elif direction == "LONG":
        macd_lab = "Bullish" if fs.get("momentum", 5) >= 6 else "Neutral"
    else:
        macd_lab = "Bearish" if fs.get("momentum", 5) >= 6 else "Neutral"

    # Liquidity label (score-based proxy — honest)
    liq = fs.get("liquidity", 5)
    if liq < 4.5:
        liq_lab = "Weak / against"
    elif direction == "SHORT" and liq < 5.5:
        liq_lab = "Possible long-side sweep risk"
    elif direction == "LONG" and liq < 5.5:
        liq_lab = "Possible short-side sweep risk"
    else:
        liq_lab = "OK"

    # Missing real feeds in OHLC BT
    missing = []
    missing.append("orderflow/CVD (OHLC proxy only)")
    missing.append("orderbook L2 history")
    missing.append("OI series")
    missing.append("funding series")

    layers = {
        "1h_structure": {"value": lab1h, "against": b1h not in (direction, "NONE") and b1h != "NONE", "note": "proxy_from_5m"},
        "15m_structure": {"value": lab15, "against": b15 not in (direction, "NONE") and b15 != "NONE", "note": "proxy_from_5m"},
        "5m_structure": {"value": lab5, "against": b5 not in (direction, "NONE") and b5 != "NONE", "note": (st5 or {}).get("label") or ""},
        "MTF_Conflict": {"value": "YES" if mtf_conflict else "NO", "against": mtf_conflict, "note": ""},
        "BOS": {"value": "yes" if (st5 or {}).get("bos") else "no", "against": bool((st5 or {}).get("bos")) and b5 == opp, "note": ""},
        "CHOCH": {"value": "yes" if (st5 or {}).get("choch") else "no", "against": bool((st5 or {}).get("choch")) and b5 == opp, "note": ""},
        "liquidity": {"value": liq_lab, "against": liq < 5, "note": f"score={liq}"},
        "CVD": {"value": "unavailable", "against": False, "note": "no real CVD in OHLC backtest"},
        "orderbook": {"value": "unavailable", "against": False, "note": "no L2 history"},
        "OI": {"value": "unavailable", "against": False, "note": "no historical OI"},
        "funding": {"value": "unavailable", "against": False, "note": "no historical funding"},
        "volume": {"value": round(fs.get("volume", 5), 2), "against": fs.get("volume", 5) < 4.5, "note": ""},
        "volatility": {"value": "high" if ambiguous else round(fs.get("volatility", 5), 2), "against": ambiguous, "note": "same-candle TP/SL" if ambiguous else ""},
        "location": {"value": round(fs.get("location", 5), 2), "against": fs.get("location", 5) < 5, "note": ""},
        "FVG_OB": {"value": round(fs.get("fvg_ob", 5), 2), "against": fs.get("fvg_ob", 5) < 5, "note": ""},
        "MACD": {"value": macd_lab, "against": (direction == "LONG" and macd_lab == "Bearish") or (direction == "SHORT" and macd_lab == "Bullish"), "note": f"RSI={rsi_val}" if rsi_val is not None else ""},
        "structure_quality": {"value": "Weak" if weak_struct else "OK", "against": weak_struct, "note": (st5 or {}).get("label") or ""},
        "rhythm": {"value": (rh or {}).get("state"), "against": bool((rh or {}).get("compression")), "note": (rh or {}).get("detail") or ""},
        "entry_timing": {"value": hit_type, "against": hit_type == "SL" and mfe_r < 0.3, "note": f"MFE={mfe_r:.2f}R MAE={mae_r:.2f}R cf={counterfactual or '—'}"},
    }

    against = [k for k, v in layers.items() if v.get("against")]

    # Failure one-liner (like user card)
    fail_parts = []
    if mtf_conflict:
        fail_parts.append("MTF conflict")
    if weak_struct:
        fail_parts.append("weak structure")
    if layers["MACD"]["against"]:
        fail_parts.append(f"MACD {macd_lab}")
    if hit_type == "SL" and mfe_r < 0.3:
        fail_parts.append("never favorable (low MFE)")
    if counterfactual == "TP_LATER":
        fail_parts.append("SL too tight (TP later)")
    if ambiguous:
        fail_parts.append("intrabar ambiguity")
    if not fail_parts:
        fail_parts.append("price moved against entry")
    failure = " + ".join(fail_parts) + f" > {direction} signal"

    if mtf_conflict:
        better = "NO TRADE / WAIT (HTF conflict)"
    elif weak_struct or "Chop" in str((st5 or {}).get("label") or ""):
        better = "NO TRADE / WAIT (choppy / weak structure)"
    elif counterfactual == "TP_LATER":
        better = "WAIT or wider SL (direction recovered later)"
    elif mfe_r < 0.25:
        better = "NO TRADE (never favorable)"
    else:
        better = "NO TRADE / WAIT"

    wrong_dir = (hit_type == "SL" and mfe_r < 0.3) or (
        len([x for x in against if x in ("5m_structure", "15m_structure", "1h_structure", "MTF_Conflict", "BOS")]) >= 2
    )

    # Card-style why lines (user format)
    why_lines = [
        f"1H: {lab1h}",
        f"15m: {lab15}",
        f"5m: {lab5}",
        f"MTF Conflict: {'YES' if mtf_conflict else 'NO'}",
        f"CVD: unavailable (OHLC BT)",
        f"Liquidity: {liq_lab}",
        f"MACD: {macd_lab}",
        f"Structure: {'Weak ' + lab5.lower() if weak_struct else lab5}",
        f"BOS: {'yes' if (st5 or {}).get('bos') else 'no'} · CHOCH: {'yes' if (st5 or {}).get('choch') else 'no'}",
        f"Volume: {fs.get('volume', 5):.1f} · FVG/OB: {fs.get('fvg_ob', 5):.1f}",
        f"Entry: {hit_type} · MFE {mfe_r:.2f}R · MAE {mae_r:.2f}R",
    ]

    return {
        "layers": layers,
        "against": against,
        "against_count": len(against),
        "wrong_direction": wrong_dir,
        "better_decision": better,
        "failure": failure,
        "why_lines": why_lines,
        "missing": missing,
        "mtf_conflict": mtf_conflict,
        "summary": failure,
    }


def _bt_loss_reasons(fs: dict, st, rh, hit_type: str, mfe_r: float, mae_r: float,
                     counterfactual: str, ambiguous: bool,
                     conflict: dict = None) -> Tuple[str, List[str]]:
    """
    OHLC backtest: do NOT spam MISSING_CVD/OI/L2 as loss reasons (always absent).
    Prefer real market codes: WRONG_DIRECTION, ENTRY_TIMING, MTF, volume, etc.
    """
    reasons = []
    conflict = conflict or {}
    against = conflict.get("against") or []

    # v17.6 — from v17.4 post-mortem: ET losses had MFE≈0.57 (not late entry)
    # MFE≈0 → WRONG_DIRECTION; small MFE+fast SL → timing; MFE>0.4 then SL → failed continuation
    if hit_type == "SL" and mfe_r < 0.15:
        reasons.append("WRONG_DIRECTION")
    elif hit_type == "SL" and mfe_r < 0.40 and (conflict.get("bars_held") or 99) <= 3:
        reasons.append("WRONG_DIRECTION")  # v19.1: timing was pre-trade skip duty
    elif hit_type == "SL" and mfe_r < 0.40:
        reasons.append("WRONG_DIRECTION")  # v19.1: timing was pre-trade skip duty
    elif hit_type == "SL" and mfe_r >= 0.40:
        reasons.append("WRONG_DIRECTION")  # v19.1
    if conflict.get("wrong_direction") and "WRONG_DIRECTION" not in reasons:
        reasons.append("WRONG_DIRECTION")
    if conflict.get("mtf_conflict") or "MTF_Conflict" in against:
        reasons.append("MTF_CONFLICT")
    if fs.get("structure", 10) < 5 or "structure_quality" in against:
        reasons.append("WEAK_STRUCTURE")
    if "Chop" in str((st or {}).get("label") or "") or (rh or {}).get("compression"):
        reasons.append("CHOPPY_MARKET")
    # Real volume weakness only (not missing CVD)
    if fs.get("volume", 10) < 4.5:
        reasons.append("LOW_VOLUME")
    if fs.get("momentum", 10) < 4 or "MACD" in against:
        reasons.append("MACD_CONFLICT")
    if (mae_r > 0.95 and mfe_r > 1.0) or (hit_type == "SL" and counterfactual == "TP_LATER"):
        reasons.append("SL_TOO_TIGHT")
    if hit_type == "TIME" and mfe_r < 0.8:
        reasons.append("WRONG_DIRECTION")  # v19.1
    if ambiguous:
        reasons.append("HIGH_VOLATILITY")
    if "location" in against or fs.get("location", 10) < 4.5:
        reasons.append("BAD_LOCATION")

    seen = set()
    reasons = [r for r in reasons if not (r in seen or seen.add(r))]

    if not reasons:
        # Fallback — do NOT invent MISSING_* feed tags for OHLC BT
        if hit_type == "TIME":
            reasons = ["NO_FOLLOW_THROUGH"]
        elif hit_type == "SL":
            reasons = ["ENTRY_TIMING_FAILURE"]
        else:
            reasons = ["NO_FOLLOW_THROUGH"]

    priority = [
        "WRONG_DIRECTION", "ENTRY_TIMING_FAILURE", "MTF_CONFLICT", "SL_TOO_TIGHT",
        "CHOPPY_MARKET", "WEAK_STRUCTURE", "MACD_CONFLICT", "NO_FOLLOW_THROUGH",
        "LOW_VOLUME", "BAD_LOCATION", "HIGH_VOLATILITY", "INSUFFICIENT_DATA",
    ]
    primary = reasons[0]
    for p in priority:
        if p in reasons:
            primary = p
            break
    return primary, reasons[:8]




def coin_profile(symbol: str) -> dict:
    _sym = str(symbol).upper().replace('USDT', 'USD')
    if 'COIN_PROFILE_V22' in globals() and _sym in COIN_PROFILE_V22:
        _b = {}
        try:
            _b = dict(COIN_PROFILE.get(_sym) or {})
        except Exception:
            pass
        _b.update(COIN_PROFILE_V22[_sym])
        # v20.3: stricter on drag coins (no delete)
        if 'DRAG_STRICT' in globals() and _sym in DRAG_STRICT:
            _d = DRAG_STRICT[_sym]
            _b['confirm_need'] = int(_b.get('confirm_need') or 2) + int(_d.get('confirm_add') or 0)
            _b['score_th'] = float(_b.get('score_th') or 6.2) + float(_d.get('score_add') or 0)
            if _d.get('chase_tight'):
                _b['chase'] = float(_d['chase_tight'])
            _b['tier'] = 'HARD'
            _b['recipe'] = str(_b.get('recipe') or '') + '+drag_strict'
        return _b
    sym = sanitize_symbol(symbol)
    return COIN_PROFILE.get(sym, {
        "tier": "MID", "score_adj": 0.10, "short_1h": True,
        "macd_hard": True, "bos_hard": False, "mom_min": 5.5,
        "prefer_dir": "BOTH", "recipe": "default", "mode": "BOTH",
        "bos_req": False, "chase_block": False, "threshold": 0.18,
    })


def coin_score_floor(symbol: str, direction: str, base: float) -> float:
    """Per-coin / per-direction floor from profile."""
    prof = coin_profile(symbol)
    floor = base + float(prof.get("score_adj") or 0)
    if direction == "LONG":
        floor += LONG_SCORE_EXTRA
    if direction == "SHORT" and prof.get("tier") in ("HARD", "SKIP"):
        floor += 0.15
    return floor


def backtest_symbol(symbol: str = None, bars: int = None, fee_bps: float = 6.0,
                    slippage_bps: float = 2.0, funding_per_trade: float = 0.0,
                    latency_bars: int = 0, record_skips: bool = True,
                    min_score: float = None) -> dict:
    """
    v15 quality-gate OHLC backtest — look-ahead safe (only past bars for features).
    Same anti-WRONG_DIRECTION gates as live: ≥2 TF agree, MACD not opposing,
    wider SL, higher min score. source='backtest' — backtest learning ON.
    """
    if bars is None:
        bars = int(globals().get("BT_BARS_DEFAULT", 2880))
    if min_score is None:
        min_score = MIN_SCORE_TRADE
    symbol = sanitize_symbol(symbol or STATE["symbol"])
    try:
        ok, why = symbol_is_tradeable(symbol)
        if not ok:
            return {"ok": False, "error": f"UNAVAILABLE `{symbol}`: {why}", "unavailable": True}
        # v17: 30m primary (~50h with 100 bars)
        k = fetch_candles("30m", bars + 30, symbol)
    except Exception as e:
        return {"ok": False, "error": f"UNAVAILABLE `{symbol}`: {e}", "unavailable": True}
    if not k or len(k.get("close") or []) < 60:
        return {"ok": False, "error": f"UNAVAILABLE `{symbol}`: not enough 30m candles", "unavailable": True}

    trades = wins = losses = ambiguous = skips = 0
    net = gross_win = gross_loss = 0.0
    reason_counts = defaultdict(int)
    run_id = None
    try:
        conn = sqlite3.connect(DB_PATH)
        # migrate extra columns if old DB
        cols = [r[1] for r in conn.execute("PRAGMA table_info(backtest_trades)").fetchall()]
        for col, typ in [
            ("source", "TEXT DEFAULT 'backtest'"), ("bar_index", "INTEGER"),
            ("score", "REAL"), ("regime", "TEXT"), ("session_label", "TEXT"),
            ("structure_label", "TEXT"), ("rhythm_state", "TEXT"),
            ("feature_scores_json", "TEXT"), ("primary_reason", "TEXT"),
            ("reasons_json", "TEXT"), ("mfe_r", "REAL"), ("mae_r", "REAL"),
            ("counterfactual", "TEXT"), ("hit_type", "TEXT"), ("rrr", "REAL"),
            ("latency_bars", "INTEGER DEFAULT 0"), ("skipped", "INTEGER DEFAULT 0"),
            ("conflict_json", "TEXT"),
        ]:
            if col not in cols:
                try:
                    conn.execute(f"ALTER TABLE backtest_trades ADD COLUMN {col} {typ}")
                except Exception:
                    pass
        cur = conn.execute(
            "INSERT INTO backtest_runs(ts,symbol,bars,trades,wins,net_pnl,win_rate,notes) VALUES(?,?,?,?,?,?,?,?)",
            (iso_now(), symbol, bars, 0, 0, 0.0, 0.0,
             "v20.4 snap-fix minRR long-BT; source=backtest"),
        )
        run_id = cur.lastrowid
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("bt run insert: %s", e)

    hold_max = 20  # v17.3: 30m*20 = 10h — more time to reach 2R TP
    for i in range(24, len(k["close"]) - hold_max - 1):
        # --- LOOK-AHEAD SAFE: only bars[:i+1] for decision ---
        sub_h = k["high"][: i + 1]
        sub_l = k["low"][: i + 1]
        sub_c = k["close"][: i + 1]
        sub_v = k["volume"][: i + 1] if k.get("volume") else []
        st = analyze_structure(sub_h, sub_l, sub_c)
        rh = analyze_rhythm(sub_h, sub_l, sub_c)
        atr = calc_atr(sub_h, sub_l, sub_c) or (sub_c[-1] * 0.005)
        # On 30m series: 45m proxy ≈ rolling structure on wider window; 1h = every 2 bars
        h15, l15, c15, v15 = sub_h, sub_l, sub_c, sub_v  # mid uses same 30m as richer structure
        # 45m-like: slightly slower EMA structure on last bars
        st15 = analyze_structure(sub_h, sub_l, sub_c) if len(sub_c) >= 8 else {"bias": "NONE", "label": "n/a"}
        h1h, l1h, c1h, v1h = _resample_ohlc(sub_h, sub_l, sub_c, sub_v, 2)
        st1h = analyze_structure(h1h, l1h, c1h) if len(c1h) >= 5 else {"bias": "NONE", "label": "n/a"}
        if st1h.get("bias") in (None, "NONE") and len(c1h) >= 6:
            e8 = calc_ema(c1h, min(6, len(c1h) - 1))
            e3 = calc_ema(c1h, min(3, len(c1h) - 1))
            if e3 and e8:
                if e3 > e8 * 1.001:
                    st1h = {**st1h, "bias": "LONG", "label": "1H EMA slope up"}
                elif e3 < e8 * 0.999:
                    st1h = {**st1h, "bias": "SHORT", "label": "1H EMA slope down"}
        rsi_val = calc_rsi(sub_c, 14) if len(sub_c) >= 16 else None

        # ── v15 direction: require ≥2 TF agreement (same as live) ──
        biases = [st.get("bias"), st15.get("bias"), st1h.get("bias")]
        long_v = biases.count("LONG")
        short_v = biases.count("SHORT")
        if long_v >= 2:
            direction = "LONG"
        elif short_v >= 2:
            direction = "SHORT"
        else:
            direction = "NONE"   # pure 5m alone = skip (anti WRONG_DIR)
        # v19.2: if arm exists, evaluate fire even when this bar bias is NONE
        try:
            _pre_arm = get_arm(symbol)
            if _pre_arm and direction == "NONE":
                direction = _pre_arm.get("direction") or "NONE"
        except Exception:
            pass


        fs = _bt_feature_scores(st, rh, atr, sub_c, sub_v, direction if direction != "NONE" else "LONG")
        # alignment penalty when HTF disagrees
        if direction != "NONE":
            if st15.get("bias") not in (direction, "NONE", None) and st15.get("bias") != "NONE":
                fs["alignment"] = min(fs.get("alignment", 5), 3.0)
            if st1h.get("bias") not in (direction, "NONE", None) and st1h.get("bias") != "NONE":
                fs["alignment"] = min(fs.get("alignment", 5), 2.5)
            if st1h.get("bias") in (None, "NONE") and st15.get("bias") in (None, "NONE"):
                fs["alignment"] = min(fs.get("alignment", 5), 3.2)
                fs["structure"] = min(fs.get("structure", 5), 4.5)

        # MACD / momentum hard check
        _, _, hist = calc_macd(sub_c) if len(sub_c) >= 30 else (None, None, None)
        macd_conflict = False
        if hist is not None and direction != "NONE":
            if direction == "LONG" and hist < 0:
                macd_conflict = True
                fs["momentum"] = min(fs.get("momentum", 5), 3.0)
            elif direction == "SHORT" and hist > 0:
                macd_conflict = True
                fs["momentum"] = min(fs.get("momentum", 5), 3.0)

        score = sum(v for k, v in fs.items() if not str(k).startswith("_")) / max(len([k for k in fs if not str(k).startswith("_")]), 1)
        regime = "CHOP" if ("Chop" in (st.get("label") or "") or rh.get("compression")) else (
            "TREND" if direction in ("LONG", "SHORT") and rh.get("state") == "Impulse" else "TRANSITION"
        )

        agree_15m = st15.get("bias") == direction
        htf_agree = agree_15m or (st1h.get("bias") == direction)
        both_flat = (
            st1h.get("bias") in (None, "NONE", "Flat")
            and st15.get("bias") in (None, "NONE", "Flat")
        )
        short_1h_flat = direction == "SHORT" and st1h.get("bias") in (None, "NONE", "Flat")
        mtf_conflict = (
            st15.get("bias") in ("LONG", "SHORT")
            and st1h.get("bias") in ("LONG", "SHORT")
            and st15.get("bias") != st1h.get("bias")
        )
        e21 = calc_ema(sub_c, 21) if len(sub_c) >= 21 else sub_c[-1]
        extended_hard = abs(sub_c[-1] - e21) / max(e21, 1e-9) > 0.016 if e21 else False  # v17.2 tighter
        rsi_ok = True
        if rsi_val is not None:
            if direction == "LONG" and rsi_val > 75:
                rsi_ok = False
            if direction == "SHORT" and rsi_val < 25:
                rsi_ok = False
        # macd_hard set per coin_profile below
        vol_ok = fs.get("volume", 0) >= 4.8  # INSUFFICIENT_DATA / LOW_VOLUME filter
        bt_min = min(min_score, 5.80)
        struct_floor = 5.8 if direction == "SHORT" else 5.4
        prof = coin_profile(symbol)
        score_floor = coin_score_floor(symbol, direction, bt_min)
        if direction == "SHORT" and prof.get("short_1h"):
            score_floor += 0.18
        # v18.8 committee-law prefer_dir + mode hard gates
        pref = str(prof.get("prefer_dir") or "BOTH")
        mode = str(prof.get("mode") or "BOTH")
        
        if symbol in TRADE_BLOCKLIST:
            continue
        if mode == "SHORT_ONLY" and direction == "LONG":
            continue
        if mode == "LONG_ONLY" and direction == "SHORT":
            continue

        # v19.2: ALWAYS process existing ARM first (auto-fire) — even if this bar has no new signal
        # Full snap recalculated every bar while pending
        try:
            _st = st if isinstance(st, dict) else {}
            _snap_bt = {
                "struct5": _st, "structure": _st, "bos": _st.get("bos"),
                "bias_1h": (st1h or {}).get("bias") if isinstance(st1h, dict) else None,
                "macd": "Bullish" if hist > 0 else ("Bearish" if hist < 0 else "Neutral"),
                "vol_score": (fs or {}).get("volume") if isinstance(fs, dict) else None,
                "trend": _st.get("bias"),
                "atr": float(atr) if atr else None,
                "score": float(score) if score is not None else 0.0,
                "body": None,
                "move_atr": None,
            }
            try:
                if atr and len(sub_c) >= 4:
                    _snap_bt["move_atr"] = abs(sub_c[-1] - sub_c[-4]) / float(atr)
                if len(sub_c) >= 1 and len(sub_o) >= 1:
                    _rng = max(float(sub_h[-1]) - float(sub_l[-1]), 1e-12)
                    _snap_bt["body"] = (float(sub_c[-1]) - float(sub_o[-1])) / _rng
            except Exception:
                pass

            _arm = get_arm(symbol)
            if _arm:
                # full confirm check every bar
                _act, _pay = confirm_and_fire(symbol, _snap_bt, i, float(sub_c[-1]))
                if _act == "WAIT":
                    skips += 1
                    reason_counts["V19_WAIT_CONFIRM"] += 1
                    continue
                if _act == "SKIP":
                    skips += 1
                    reason_counts["V19_SKIP_" + str((_pay or {}).get("why") or "ARM")] += 1
                    continue
                if _act == "FIRE":
                    # force direction/levels from arm for this trade
                    direction = _arm.get("direction") or direction
                    # fall through to open trade below
                    pass
                else:
                    continue
            else:
                # No arm yet — need signal direction
                if direction not in ("LONG", "SHORT"):
                    continue
                _nconf, _cdet = v19_count_confirms(_snap_bt, direction)
                _need_arm = max(1, int((coin_profile(symbol) or {}).get("confirm_need") or 2) - 1)  # v19.3 volume
                if _nconf < _need_arm:
                    skips += 1
                    reason_counts["V19_NO_ARM"] += 1
                    continue
                if (_snap_bt.get("move_atr") or 0) > 1.40:
                    skips += 1
                    reason_counts["V19_CHASE_NO_ARM"] += 1
                    continue
                # ARM with full pre-calc (SL/TP/score/meta) — trade next bars on confirm
                try:
                    _cr = coin_risk(symbol)
                    _slm = float(_cr.get("sl_atr") or SL_ATR_MULT)
                    _tpr = max(2.0, float(_cr.get("tp_rr") or 2.0))
                    _px = float(sub_c[-1])
                    _atr = float(atr) if atr else _px * 0.01
                    if direction == "LONG":
                        _slp = _px - _slm * _atr
                        _tpp = _px + _slm * _atr * _tpr
                    else:
                        _slp = _px + _slm * _atr
                        _tpp = _px - _slm * _atr * _tpr
                    arm_setup(
                        symbol, direction, _px, _slp, _tpp,
                        float(score) if score else 0.0, i,
                        {
                            "confirms": _cdet,
                            "mode": mode,
                            "sl_atr": _slm,
                            "tp_rr": _tpr,
                            "fs": dict(fs) if isinstance(fs, dict) else {},
                            "struct": _st.get("bias") if isinstance(_st, dict) else None,
                        },
                    )
                except Exception:
                    pass
                _need_fire = max(2, int((coin_profile(symbol) or {}).get("confirm_need") or 2))
                _sc = float(score) if score is not None else 0.0
                if (_nconf >= _need_fire and (_snap_bt.get("move_atr") or 0) <= 1.30
                        and _sc >= MIN_SCORE_TRADE):
                    # v19.4 same-bar fire only if score also clears quality floor
                    reason_counts["V19_SAME_BAR_FIRE"] = reason_counts.get("V19_SAME_BAR_FIRE", 0) + 1
                    clear_arm(symbol)
                    # fall through to open trade
                else:
                    skips += 1
                    reason_counts["V19_ARMED_WAIT"] += 1
                    continue
        except Exception:
            pass
        if pref == "SHORT" and direction == "LONG":
            score_floor += 0.40
        elif pref == "LONG" and direction == "SHORT":
            score_floor += 0.40
        # bos_req from 8d law finder
        if prof.get("bos_req") or prof.get("bos_hard"):
            # v20.4: never use bare `snap` — BT builds _snap_bt; fall back to structure dict
            try:
                _sref = _snap_bt if isinstance(locals().get("_snap_bt"), dict) else {}
            except Exception:
                _sref = {}
            s5 = (_sref.get("struct5") or _sref.get("structure") or st or {})
            if not isinstance(s5, dict):
                s5 = {}
            bos = bool(s5.get("bos") or s5.get("bos_up") or s5.get("bos_dn") or st.get("bos"))
            # soft: if no bos flag, use structure score proxy later
            if not bos and score < 7.2:
                score_floor += 0.25

        # v18.2 WD cut: any direction needs HTF not opposing hard
        one_h_flat = st1h.get("bias") in (None, "NONE", "Flat")
        short_needs_1h = direction == "SHORT" and prof.get("short_1h") and st1h.get("bias") != "SHORT"
        long_needs_1h = direction == "LONG" and st1h.get("bias") == "SHORT"  # HTF oppose LONG
        macd_hard = macd_conflict if prof.get("macd_hard", True) else (macd_conflict and fs.get("structure", 5) < 6.0)
        # structure must agree with trade direction (WD killer)
        struct_against = st.get("bias") in ("LONG", "SHORT") and st.get("bias") != direction
        bos_req = bool(st.get("bos")) if prof.get("bos_hard") else (bool(st.get("bos")) or fs.get("structure", 0) >= 6.3)
        choch_only = bool(st.get("choch")) and not bool(st.get("bos"))
        bos_ok = bool(st.get("bos")) or fs.get("structure", 0) >= 6.5
        choppy = "Chop" in str(st.get("label") or "")
        look = min(ANTI_CHASE_BARS, len(sub_c))
        recent_hi = max(sub_h[-look:]) if look else sub_c[-1]
        recent_lo = min(sub_l[-look:]) if look else sub_c[-1]
        rng = max(recent_hi - recent_lo, 1e-9)
        # reject if close in top/bottom 25% of recent range (chase)
        chasing = False
        if direction == "LONG" and sub_c[-1] >= recent_hi - 0.25 * rng:
            chasing = True
        if direction == "SHORT" and sub_c[-1] <= recent_lo + 0.25 * rng:
            chasing = True
        if len(sub_c) >= 2:
            body = sub_c[-1] - sub_c[-2]
            if direction == "LONG" and body > 0.45 * rng:
                chasing = True
            if direction == "SHORT" and body < -0.45 * rng:
                chasing = True
        # pullback zone: must be near EMA21 (not running away)
        e21_bt = calc_ema(sub_c, 21) if len(sub_c) >= 21 else sub_c[-1]
        atr_bt = max(rng / 3, abs(sub_c[-1]) * 0.004)
        late_ext = abs(sub_c[-1] - e21_bt) / max(atr_bt, 1e-9) > 1.8
        # per-coin momentum floor
        mom_ok_bt = fs.get("momentum", 0) >= float(prof.get("mom_min") or 5.5)
        tempo_ok = rh.get("tempo", 0) >= (4.5 if prof.get("tier") == "EDGE" else 4.8)
        # dead TRANSITION without impulse → skip
        dead_transition = (
            regime in ("TRANSITION", "CHOP")
            and rh.get("state") not in ("Impulse", "Expansion")
            and fs.get("momentum", 0) < 6.2
        )
        take = (
            direction != "NONE"
            and score >= score_floor
            and agree_15m
            and not both_flat
            and not one_h_flat
            and not short_needs_1h
            and not long_needs_1h
            and not struct_against
            and not mtf_conflict
            and not macd_hard
            and not extended_hard
            and rsi_ok
            and vol_ok
            and bos_req
            and not (choch_only and not st.get("bos") and prof.get("bos_hard"))
            and not choppy
            and not chasing
            and not late_ext
            and mom_ok_bt
            and tempo_ok
            and not dead_transition
            and fs.get("structure", 0) >= max(5.0, struct_floor - 0.4)
            and fs.get("alignment", 0) >= 4.8
        )

        if not take:
            if record_skips and direction != "NONE":
                skips += 1
            continue

        # Enter 1 bar later by default (confirmation / less chase)
        lat = max(2, latency_bars) if latency_bars is not None else 2  # v17.2: +1 bar confirm
        entry_i = min(i + lat, len(k["close"]) - hold_max - 1)
        entry = k["close"][entry_i]
        # dynamic slip in high vol
        slip = slippage_bps
        if atr / entry > 0.012:
            slip = slippage_bps * 1.5
        # adverse slip on entry
        if direction == "LONG":
            entry *= (1 + slip / 10000.0)
        else:
            entry *= (1 - slip / 10000.0)

        # v18.3 per-coin SL/TP (match live recipes)
        risk = coin_risk(symbol)
        dist = clamp(atr * float(risk.get("sl_atr") or SL_ATR_MULT), entry * SL_MIN_PCT, entry * SL_MAX_PCT)
        rrr = float(risk.get("tp_rr") or TP_RR_MULT)
        if regime in ("TRANSITION", "CHOP"):
            rrr = min(rrr, max(1.55, TP_RR_TRANSITION))
        if direction == "LONG":
            sl, tp = entry - dist, entry + dist * rrr
        else:
            sl, tp = entry + dist, entry - dist * rrr
        hold_max = int(risk.get("hold") or 20)

        # path simulation from entry_i+1
        outcome = None
        held = 0
        note = ""
        hit_type = "TIME"
        mfe = 0.0
        mae = 0.0
        for j in range(entry_i + 1, min(entry_i + 1 + hold_max, len(k["close"]))):
            held += 1
            hi, lo = k["high"][j], k["low"][j]
            if direction == "LONG":
                mfe = max(mfe, hi - entry)
                mae = max(mae, entry - lo)
                hit_sl, hit_tp = lo <= sl, hi >= tp
            else:
                mfe = max(mfe, entry - lo)
                mae = max(mae, hi - entry)
                hit_sl, hit_tp = hi >= sl, lo <= tp
            if hit_sl and hit_tp:
                ambiguous += 1
                outcome = -1.0
                hit_type = "SL"
                note = "both TP/SL same candle -> conservative SL"
                break
            if hit_tp:
                outcome = rrr
                hit_type = "TP"
                break
            if hit_sl:
                outcome = -1.0
                hit_type = "SL"
                break
        if outcome is None:
            # time exit at last close
            last = k["close"][entry_i + held] if held else entry
            if direction == "LONG":
                outcome = (last - entry) / max(dist, 1e-12)
            else:
                outcome = (entry - last) / max(dist, 1e-12)
            hit_type = "TIME"
            note = "time_exit"

        mfe_r = mfe / max(dist, 1e-12)
        mae_r = mae / max(dist, 1e-12)

        # counterfactual: if SL, would TP have hit in next 5 bars?
        counterfactual = ""
        if hit_type == "SL":
            exit_j = entry_i + held
            for j in range(exit_j + 1, min(exit_j + 6, len(k["close"]))):
                hi, lo = k["high"][j], k["low"][j]
                if direction == "LONG" and hi >= tp:
                    counterfactual = "TP_LATER"
                    break
                if direction == "SHORT" and lo <= tp:
                    counterfactual = "TP_LATER"
                    break
            if not counterfactual and mfe_r < 0.25:
                counterfactual = "NEVER_FAVORABLE"

        friction = (fee_bps + slip) / 10000.0
        friction_r = friction * entry / max(dist, 1e-12) * 2  # entry+exit approx
        outcome -= friction_r
        outcome -= funding_per_trade

        primary, reasons = ("", [])
        conflict = {}
        try:
            conflict = _bt_build_conflict(
                direction, fs, st, st15, st1h, rh,
                hit_type, mfe_r, mae_r, counterfactual,
                bool(note.startswith("both")), rsi_val,
            )
        except Exception as e:
            log.warning("conflict build: %s", e)
            conflict = {}
        conflict["bars_held"] = held
        if outcome <= 0:
            primary, reasons = _bt_loss_reasons(
                fs, st, rh, hit_type, mfe_r, mae_r, counterfactual,
                bool(note.startswith("both")), conflict,
            )
            for r in reasons:
                reason_counts[r] += 1

        trades += 1
        net += outcome
        if outcome > 0:
            wins += 1
            gross_win += outcome
        else:
            losses += 1
            gross_loss += abs(outcome)

        if run_id:
            try:
                conn = sqlite3.connect(DB_PATH)
                # ensure conflict_json column
                try:
                    ccols = [r[1] for r in conn.execute("PRAGMA table_info(backtest_trades)").fetchall()]
                    if "conflict_json" not in ccols:
                        conn.execute("ALTER TABLE backtest_trades ADD COLUMN conflict_json TEXT")
                except Exception:
                    pass
                conn.execute(
                    """INSERT INTO backtest_trades
                    (run_id,ts,symbol,direction,entry,sl,tp,outcome,pnl_r,bars_held,
                     fees,slippage,funding,note,source,bar_index,score,regime,session_label,
                     structure_label,rhythm_state,feature_scores_json,primary_reason,reasons_json,
                     mfe_r,mae_r,counterfactual,hit_type,rrr,latency_bars,skipped,conflict_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id, iso_now(), symbol, direction, entry, sl, tp, outcome, outcome, held,
                        fee_bps, slip, funding_per_trade, note, "backtest", i, round(score, 2),
                        regime, current_session(), st.get("label"), rh.get("state"),
                        json.dumps(fs), primary, json.dumps(reasons),
                        round(mfe_r, 3), round(mae_r, 3), counterfactual, hit_type, rrr,
                        latency_bars, 0, json.dumps(conflict),
                    ),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                log.warning("bt trade insert: %s", e)

    wr = wins / trades * 100 if trades else 0
    pf = gross_win / gross_loss if gross_loss else (999 if gross_win else 0)
    # update run summary
    if run_id:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "UPDATE backtest_runs SET trades=?, wins=?, net_pnl=?, win_rate=? WHERE id=?",
                (trades, wins, net, wr, run_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    # v15.2: backtest se bhi seekho (soft nudges)
    learn_msg = learn_from_backtest(run_id=run_id, limit=min(80, max(trades, 1))) if trades else "no trades"

    # Wilson-ish simple CI for WR
    if trades:
        z = 1.96
        p = wins / trades
        den = 1 + z * z / trades
        centre = p + z * z / (2 * trades)
        margin = z * math.sqrt(p * (1 - p) / trades + z * z / (4 * trades * trades))
        wr_lo = max(0, (centre - margin) / den) * 100
        wr_hi = min(1, (centre + margin) / den) * 100
    else:
        wr_lo = wr_hi = 0

    return {
        "ok": True, "symbol": symbol, "run_id": run_id,
        "trades": trades, "wins": wins, "losses": losses, "ambiguous": ambiguous,
        "skips_signal": skips, "net_pnl": round(net, 3), "win_rate": round(wr, 1),
        "wr_ci95": (round(wr_lo, 1), round(wr_hi, 1)),
        "profit_factor": round(pf, 2), "expectancy": round(net / trades, 3) if trades else 0,
        "fees_bps": fee_bps, "slippage_bps": slippage_bps,
        "loss_reasons": {k: v for k, v in dict(reason_counts).items()
                        if not str(k).upper().startswith("V19_")
                        and "ARM" not in str(k).upper()
                        and "WAIT" not in str(k).upper()
                        and "NO_ARM" not in str(k).upper()
                        and "CHASE_NO" not in str(k).upper()},
        "skip_reasons": {k: v for k, v in dict(reason_counts).items()
                        if str(k).upper().startswith("V19_")
                        or "ARM" in str(k).upper()
                        or "WAIT" in str(k).upper()},
        "mode": "v15_quality_gate_ohlc", "source": "backtest",
        "latency_bars": latency_bars,
        "learning": learn_msg,
    }


def monte_carlo_stress(n_sims: int = 500, path: str = None) -> dict:
    """Shuffle historical trade PnL sequences → DD / streak / ruin-ish stats."""
    try:
        conn = sqlite3.connect(DB_PATH)
        pnls = [r[0] for r in conn.execute(
            "SELECT pnl_r FROM backtest_trades WHERE source='backtest' OR source IS NULL ORDER BY id"
        ).fetchall()]
        conn.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if len(pnls) < 20:
        return {"ok": False, "error": f"Need more trades (have {len(pnls)})"}
    import random
    max_dds = []
    max_streaks = []
    final_eqs = []
    for _ in range(n_sims):
        seq = list(pnls)
        random.shuffle(seq)
        eq = peak = 0.0
        dd = 0.0
        streak = max_st = 0
        for x in seq:
            eq += x
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
            if x <= 0:
                streak += 1
                max_st = max(max_st, streak)
            else:
                streak = 0
        max_dds.append(dd)
        max_streaks.append(max_st)
        final_eqs.append(eq)
    max_dds.sort()
    max_streaks.sort()
    final_eqs.sort()
    def pct(xs, p):
        return xs[int((len(xs) - 1) * p)]
    return {
        "ok": True, "n_trades": len(pnls), "n_sims": n_sims,
        "dd_p50": round(pct(max_dds, 0.5), 2),
        "dd_p95": round(pct(max_dds, 0.95), 2),
        "streak_p50": pct(max_streaks, 0.5),
        "streak_p95": pct(max_streaks, 0.95),
        "eq_p05": round(pct(final_eqs, 0.05), 2),
        "eq_p50": round(pct(final_eqs, 0.5), 2),
        "eq_p95": round(pct(final_eqs, 0.95), 2),
        "frac_neg_eq": round(sum(1 for e in final_eqs if e < 0) / n_sims, 3),
    }


def walk_forward_backtest(symbol: str = None, train_bars: int = 120, test_bars: int = 40,
                          windows: int = 3) -> dict:
    """Simple rolling walk-forward on OHLC (report only; no weight contamination)."""
    symbol = symbol or STATE["symbol"]
    total = train_bars + test_bars * windows + 50
    k = fetch_candles("5m", total, symbol)
    if not k or len(k["close"]) < train_bars + test_bars:
        return {"ok": False, "error": "Not enough candles for WF"}
    results = []
    start = 40
    for w in range(windows):
        # test window only metrics using same rules (no fitting on train for honesty)
        te0 = start + train_bars + w * test_bars
        te1 = te0 + test_bars
        if te1 >= len(k["close"]) - 10:
            break
        # mini backtest on slice via index constraints — reuse core loop simply
        sub = {
            "high": k["high"][:te1], "low": k["low"][:te1],
            "close": k["close"][:te1], "volume": (k.get("volume") or [0] * te1)[:te1],
        }
        # lightweight: call backtest on full then filter — expensive; instead local loop
        wins = trades = 0
        net = 0.0
        for i in range(max(40, te0), te1 - 8):
            sh, sl_, sc = sub["high"][: i + 1], sub["low"][: i + 1], sub["close"][: i + 1]
            st = analyze_structure(sh, sl_, sc)
            rh = analyze_rhythm(sh, sl_, sc)
            if st["bias"] == "NONE" or rh.get("tempo", 0) < 5.5:
                continue
            direction = st["bias"]
            entry = sc[-1]
            atr = calc_atr(sh, sl_, sc) or entry * 0.005
            dist = atr * 1.1
            slp = entry - dist if direction == "LONG" else entry + dist
            tp = entry + dist * 2 if direction == "LONG" else entry - dist * 2
            outcome = None
            for j in range(i + 1, min(i + 9, len(sub["close"]))):
                hi, lo = sub["high"][j], sub["low"][j]
                if direction == "LONG":
                    if lo <= slp and hi >= tp:
                        outcome = -1.0
                        break
                    if hi >= tp:
                        outcome = 2.0
                        break
                    if lo <= slp:
                        outcome = -1.0
                        break
                else:
                    if hi >= slp and lo <= tp:
                        outcome = -1.0
                        break
                    if lo <= tp:
                        outcome = 2.0
                        break
                    if hi >= slp:
                        outcome = -1.0
                        break
            if outcome is None:
                continue
            trades += 1
            net += outcome
            if outcome > 0:
                wins += 1
        wr = 100 * wins / trades if trades else 0
        results.append({"window": w + 1, "trades": trades, "wins": wins,
                        "wr": round(wr, 1), "net": round(net, 2)})
    return {"ok": True, "symbol": symbol, "windows": results, "source": "walk_forward"}


def diagnosis_from_backtest(limit: int = 2000) -> str:
    """Diagnosis using enriched backtest_trades (source=backtest) — does NOT touch live weights."""
    try:
        conn = sqlite3.connect(DB_PATH)
        run_ids = [r[0] for r in conn.execute(
            "SELECT id FROM backtest_runs ORDER BY id DESC LIMIT 5"
        ).fetchall()]
        if run_ids:
            ph = ",".join("?" * len(run_ids))
            rows = conn.execute(
                f"""SELECT direction, symbol, pnl_r, primary_reason, reasons_json, regime,
                          feature_scores_json, mfe_r, mae_r, counterfactual, hit_type, score
                   FROM backtest_trades WHERE run_id IN ({ph})
                   ORDER BY id DESC LIMIT ?""",
                (*run_ids, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT direction, symbol, pnl_r, primary_reason, reasons_json, regime,
                          feature_scores_json, mfe_r, mae_r, counterfactual, hit_type, score
                   FROM backtest_trades ORDER BY id DESC LIMIT ?""",
                (min(limit, 100),),
            ).fetchall()
        conn.close()
    except Exception as e:
        return f"BT diagnosis error: {e}"
    if not rows:
        return "No backtest trades yet. Run Backtest after v17 deploy."
    n = len(rows)
    wins = sum(1 for r in rows if r[2] and r[2] > 0)
    losses = n - wins
    wr = 100 * wins / n
    reasons = Counter()
    regimes = defaultdict(lambda: [0, 0])
    dirs = defaultdict(lambda: [0, 0])
    cf_tp_later = 0
    feat_sum = defaultdict(float)
    feat_n = defaultdict(int)
    for direction, symbol, pnl, primary, reasons_j, regime, fs_j, mfe, mae, cf, hit, score in rows:
        win = pnl is not None and pnl > 0
        dirs[direction or "?"][0] += 1
        dirs[direction or "?"][1] += 1 if win else 0
        if regime:
            regimes[regime][0] += 1
            regimes[regime][1] += 1 if win else 0
        if not win:
            if primary and not str(primary).startswith("MISSING_"):
                reasons[primary] += 1
            if reasons_j:
                try:
                    for x in json.loads(reasons_j):
                        if str(x).startswith("MISSING_"):
                            continue
                        reasons[x] += 1
                except Exception:
                    pass
            if cf == "TP_LATER":
                cf_tp_later += 1
        if fs_j:
            try:
                fs = json.loads(fs_j)
                sign = 1 if win else -1
                for k, v in fs.items():
                    feat_sum[k] += sign * (safe_float(v) - 5)
                    feat_n[k] += 1
            except Exception:
                pass
    lines = [
        f"*Backtest Diagnosis* (n=`{n}` · source=`backtest`)",
        f"WR: `{wr:.1f}%` · W `{wins}` · L `{losses}`",
        f"SL-too-tight signal (TP later): `{cf_tp_later}` ({100*cf_tp_later/max(losses,1):.0f}% of losses)",
        "",
        "*Loss reasons*",
    ]
    for code, cnt in reasons.most_common(8):
        lines.append(f"• `{code}`: {cnt} ({100*cnt/max(losses,1):.0f}% of L)")
    lines.append("\n*By direction*")
    for d, (nn, ww) in dirs.items():
        lines.append(f"• `{d}`: {100*ww/nn:.0f}% ({nn}t)")
    if regimes:
        lines.append("\n*By regime*")
        for r, (nn, ww) in regimes.items():
            lines.append(f"• `{r}`: {100*ww/nn:.0f}% ({nn}t)")
    lines.append("\n*Feature contribution*")
    for k, s in sorted(feat_sum.items(), key=lambda x: -x[1] / max(feat_n[x[0]], 1))[:8]:
        lines.append(f"• `{k}`: {s/max(feat_n[k],1):+.2f}")
    lines.append("\n_Live weights NOT updated from this report._")
    return "\n".join(lines)



def _split_skip_loss_reasons(reason_counts: dict):
    """v19.1: skips are not losses."""
    skips, losses = {}, {}
    for k, v in (reason_counts or {}).items():
        ku = str(k).upper()
        if ku.startswith("V19_") or "SKIP" in ku or "WAIT" in ku or "ARM" in ku or "CHASE_NO" in ku or "NO_ARM" in ku:
            skips[k] = v
        else:
            losses[k] = v
    return skips, losses

def format_backtest(res: dict) -> str:
    if not res.get("ok"):
        return f"Backtest fail: {res.get('error')}"
    lines = [
        f"*Backtest v20.4* `{res['symbol']}`",
        f"Trades: `{res['trades']}` · Wins: `{res['wins']}` · Losses: `{res['losses']}`",
        f"WR: `{res['win_rate']}%` · CI95: `{res.get('wr_ci95')}`",
        f"Net R: `{res['net_pnl']:+.2f}` · E[R]: `{res.get('expectancy', 0):+.3f}` · PF: `{res.get('profit_factor')}`",
        f"Ambiguous: `{res.get('ambiguous', 0)}` · Latency bars: `{res.get('latency_bars', 0)}`",
    ]
    lr = res.get("loss_reasons") or {}
    sk = res.get("skip_reasons") or {}
    if sk:
        lines.append("*Skipped (not losses · pending/confirm gates)*")
        for k, v in sorted(sk.items(), key=lambda x: -x[1])[:6]:
            lines.append(f"• `{k}`: {v}")
    if lr:
        lines.append("*Loss reasons (trades only)*")
        for k, v in sorted(lr.items(), key=lambda x: -x[1])[:6]:
            lines.append(f"• `{k}`: {v}")
    lines.append("_source=backtest · skips ≠ losses · v19.4 quality pending→confirm→fire_")
    return "\n".join(lines)


def format_report(d: dict) -> str:
    d = d or {}
    # Unavailable / skipped coin — short safe message, no crash on missing keys
    if d.get("unavailable") or d.get("classification") == "UNAVAILABLE" or d.get("skipped"):
        why = d.get("reason") or (d.get("why_not") or ["Coin unavailable"])[0]
        return (
            f"⚪ *SKIPPED / UNAVAILABLE*\n"
            f"Coin: `{d.get('symbol') or '?'}`\n"
            f"Reason: {why}\n"
            f"_Next coin continue — no crash._\n"
            f"`{BOT_VERSION}`"
        )
    hard = d.get("hard", "NO TRADE")
    if hard == "TRADE":
        badge = "🚨 *FINAL ACTION: TRADE* 🚨"
        emoji = "🟢"
    elif hard == "WAIT FOR CONFIRMATION" or d.get("status") == "WAIT":
        badge = "⏳ *FINAL ACTION: WAIT FOR CONFIRMATION* ⏳"
        emoji = "🟡"
    else:
        badge = "🚨 *FINAL ACTION: NO TRADE* 🚨"
        emoji = "🔴"
    fs = d.get("filter_scores") or {}
    # hide internal flag keys
    fs_show = {k: v for k, v in fs.items() if not str(k).startswith("_")}
    lines = [
        badge,
        f"{emoji} `{d.get('symbol')}` · {d.get('direction')} · Score `{d.get('score')}` · Conf `{d.get('confidence')}%`",
        f"Class: `{d.get('classification')}` · Regime: `{d.get('regime') or '—'}`",
        "",
        f"*Entry* `{d.get('entry')}`",
        f"*SL* `{d.get('sl')}` ({d.get('sl_dist') or 0} pts)",
        f"*TP* `{d.get('tp')}` · RRR `1:{d.get('rrr')}`",
        "",
        "*Confluence*",
    ]
    for k, v in sorted(fs_show.items(), key=lambda x: -x[1]):
        flag = "✓" if v >= 7 else ("~" if v >= 5 else "✗")
        lines.append(f"{flag} `{k}`: {v}")

    oif = d.get("oi_funding") or {}
    oba = d.get("orderbook") or {}
    basis = d.get("basis") or {}
    lines += [
        "",
        f"*OI/Funding*: {oif.get('detail', '—')}",
        f"*Orderbook*: {oba.get('detail', '—')}",
        f"*Basis*: {basis.get('detail', '—')}",
        f"*Structure*: {(d.get('structure_detail') or {}).get('detail', '—')}",
        f"*Rhythm*: {(d.get('rhythm_detail') or {}).get('detail', '—')}",
        f"*Market*: {(d.get('market_rhythm') or {}).get('detail', '—')}",
    ]
    if d.get("patterns"):
        lines.append(f"*Candles*: {', '.join(d['patterns'][:4])}")
    if d.get("vwap"):
        lines.append(f"*VWAP*: `{d['vwap']:.2f}`")
    stats = d.get("stats") or {}
    if stats.get("detail"):
        lines.append(f"*Stats*: {stats['detail']}")
    flow=d.get("orderflow") or {}
    lines.append(f"*Order Flow*: {flow.get('mode','—')} · Δ `{flow.get('delta',0):.2f}` · Agg `{flow.get('aggression',0):+.2f}`")
    adv=d.get("decision_quality") or {}
    lines.append(f"*Prob*: Setup `{adv.get('setup_probability',0)*100:.1f}%` · NoTrade `{adv.get('no_trade_probability',0)*100:.1f}%`")
    if adv.get("conflicts"):
        lines.append(f"*Conflicts*: {', '.join(adv['conflicts'][:3])}")

    lines.append("")
    if d.get("why_trade"):
        lines.append("*Why TRADE:*")
        for w in d["why_trade"]:
            lines.append(f"  • {w}")
    if d.get("why_not"):
        label = "*Why WAIT / NO TRADE:*" if hard in ("WAIT FOR CONFIRMATION", "NO TRADE") else "*Why NO TRADE:*"
        if hard == "WAIT FOR CONFIRMATION":
            label = "*Why WAIT FOR CONFIRMATION:*"
        lines.append(label)
        for w in d["why_not"]:
            lines.append(f"  • {w}")

    lines.append("")
    dqr = d.get("data_quality") or {}
    if dqr.get("detail"):
        lines.append(f"*Data Quality*: {dqr['detail']}")
    flow = d.get("orderflow") or {}
    if flow.get("mode"):
        lines.append(f"*Flow mode*: `{flow.get('mode')}` (proxy ≠ real ticks)")
    lines.append(f"_Learn_: {d.get('learn', '')}")
    lines.append("_Outcome_: after trade ends send `win` / `loss` / `be` / `expired`")
    lines.append(f"`{BOT_VERSION}` · {d.get('session')} · {d.get('vol_regime')}")

    # Practice SL / TP / RRR block (always when levels exist)
    entry = d.get("entry") or 0
    sl = d.get("sl") or 0
    tp = d.get("tp") or 0
    rrr = d.get("rrr") or 0
    if entry and sl and entry != sl:
        sz = suggest_size(entry, sl, tp=tp)
        lines.append("")
        lines.append("*Practice SL / TP (10x · $0.25 risk)*")
        lines.append(f"Entry `{entry}` · SL `{sl}` · TP `{tp}`")
        lines.append(f"RRR `1:{rrr}` (min 1:2 → risk $0.25 → profit ≥ $0.50)")
        lines.append(f"💰 {sz['note']}")
        if sz.get("reward_usd", 0) < REWARD_USD_MIN:
            lines.append(f"_Note: TP reward ${sz.get('reward_usd',0):.2f} < ${REWARD_USD_MIN:.2f} — widen TP or size_")
    return "\n".join(lines)


def format_orderbook_view(symbol: str = None) -> str:
    symbol = sanitize_symbol(symbol or STATE.get("symbol") or DEFAULT_SYMBOL)
    try:
        ok, why = symbol_is_tradeable(symbol)
        if not ok:
            return f"⚪ `{symbol}` · *UNAVAILABLE*\n{why}\n_Orderbook skipped — try another coin._"
        ob = get_orderbook(symbol)
        t = get_ticker_full(symbol)
    except Exception as e:
        return f"⚪ `{symbol}` orderbook error (no crash): {e}"
    if not ob:
        return f"⚪ Orderbook unavailable for `{symbol}`"
    lines = [
        f"*Orderbook* `{symbol}`",
        f"Best Bid `{ob['best_bid']}` · Best Ask `{ob['best_ask']}`",
        f"Spread `{ob['spread_bps']:.2f}` bps · Imbalance `{ob['imbalance']:+.3f}`",
        f"Bid vol `{ob['bid_vol']:.0f}` · Ask vol `{ob['ask_vol']:.0f}`",
        "",
        "*Top Bids*",
    ]
    for p, s in (ob.get("bids") or [])[:5]:
        lines.append(f"  `{p}` × {s:.0f}")
    lines.append("*Top Asks*")
    for p, s in (ob.get("asks") or [])[:5]:
        lines.append(f"  `{p}` × {s:.0f}")
    if t:
        lines.append("")
        lines.append(f"OI `${t.get('oi_value_usd', 0):,.0f}` · 6h Δ `${t.get('oi_change_usd_6h', 0):,.0f}`")
        lines.append(f"Funding `{t.get('funding_rate')}` · Basis `{t.get('mark_basis')}`")
    return "\n".join(lines)


def format_market_rhythm() -> str:
    mr = market_wide_rhythm()
    STATE["market_rhythm"] = {**mr, "_ts": time.time()}
    return (
        f"*Market-Wide Rhythm*\n"
        f"Status: `{mr['sync']}`\n"
        f"↑ {mr['up']}  ↓ {mr['down']}  → {mr['flat']}  (of {mr['total']})\n"
        f"Sync score: `{mr['score']:.1f}`"
    )


def format_help() -> str:
    return (
        f"*TradAI Local Committee {BOT_VERSION}*\n\n"
        "🔄 Re-Analyze · 🌐 Best Any Coin\n"
        "🩺 Diagnosis · 🧾 Post-Mortem\n"
        "✅ Save / ⏭ Skip · 📈 Backtest · 🧠 Learning\n"
        "account 250 · risk 1.5 · wl add/del SYMBOL\n\n"
        "*Outcome* after trade ends:\n"
        "`win` · `loss` · `be` · `expired`\n"
        "optional: `win 1.5` / `loss -1`\n\n"
        "v14: Enriched backtest · MFE/MAE · loss reasons ·\n"
        "counterfactual · CI95 · MonteCarlo · walk-forward\n"
        "source=backtest vs live separated (no contamination)\n"
        "`bt diagnosis` · `montecarlo` · `walkforward`\n"
        "pip install websocket-client\n"
        "Advisor only · No auto orders"
    )


def format_settings() -> str:
    return (
        f"*Settings* `{BOT_VERSION}`\n"
        f"Symbol: `{STATE['symbol']}`\n"
        f"Account: `${STATE['account_usd']:.0f}` · Risk `{STATE['risk_pct']}%`\n"
        f"Min Score: `{MIN_SCORE_TRADE}` · Min RR: `{MIN_RR}`\n"
        f"Watchlist: {len(STATE.get('watchlist') or [])} · Session: `{current_session()}`"
    )


# ═══════════════════════════════════════════════════════════════
# 15. COMMAND HANDLER
# ═══════════════════════════════════════════════════════════════
def process_update(update: dict):
    msg = update.get("message") or update.get("edited_message") or {}
    if not msg:
        return
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if not is_allowed_chat(chat_id):
        log.warning("blocked chat %s", chat_id)
        return
    if not rate_limit_ok(chat_id):
        tg_send("⏳ Slow down — rate limit.", chat_id)
        return

    text = (msg.get("text") or "").strip()
    text_lower = text.lower()

    if text in ("/start", "❓ Help", "/help"):
        tg_send(format_help(), chat_id, reply_markup=main_keyboard())
        return

    if text in ("🔄 Re-Analyze", "/analyze", "analyze", "/re"):
        tg_send_action(chat_id, "typing")
        try:
            d = run_committee()
            STATE["last_decision"] = d
            tg_send(format_report(d), chat_id, reply_markup=main_keyboard())
        except Exception as e:
            log.warning("re-analyze: %s", e)
            tg_send(f"⚪ Analyze failed (no crash): {e}", chat_id, reply_markup=main_keyboard())
        return

    if text in ("🌐 Best Any Coin", "/best", "best", "/scan"):
        tg_send_action(chat_id, "typing")
        tg_send("⏳ Scanning watchlist…", chat_id)
        try:
            d = best_any_coin()
            STATE["last_decision"] = d
            tg_send(format_report(d), chat_id, reply_markup=main_keyboard())
            scanned = STATE.get("last_scan") or []
            if scanned:
                tg_send(format_scan_report(scanned), chat_id)
        except Exception as e:
            log.warning("scan cmd: %s", e)
            tg_send(f"⚪ Scan failed (no crash): {e}", chat_id, reply_markup=main_keyboard())
        return

    if text in ("🌊 Market Rhythm", "/rhythm", "rhythm", "/market"):
        tg_send_action(chat_id, "typing")
        tg_send(format_market_rhythm(), chat_id, reply_markup=main_keyboard())
        return

    if text in ("📘 Orderbook", "/orderbook", "/ob", "orderbook"):
        tg_send_action(chat_id, "typing")
        try:
            tg_send(format_orderbook_view(), chat_id, reply_markup=main_keyboard())
        except Exception as e:
            tg_send(
                f"⚪ Orderbook unavailable for `{STATE.get('symbol')}` — {e}",
                chat_id, reply_markup=main_keyboard(),
            )
        return

    if text in ("✅ Save Setup", "/save"):
        d = STATE.get("last_decision")
        if not d:
            tg_send("No decision yet.", chat_id)
            return
        ok = save_setup(d, "saved")
        tg_send(
            "✅ Saved as PENDING.\nAfter result send: `win` / `loss` / `be` / `expired`"
            if ok else "Save failed.",
            chat_id,
        )
        return

    # Outcome resolution — ONLY this updates learning weights
    if text_lower in ("win", "/win", "loss", "/loss", "be", "/be", "expired", "/expired"):
        key = text_lower.replace("/", "")
        msg = resolve_outcome(key.upper())
        tg_send(msg, chat_id)
        return
    if text_lower.startswith("win ") or text_lower.startswith("loss "):
        parts = text_lower.split()
        try:
            pnl = float(parts[1]) if len(parts) > 1 else 0.0
        except Exception:
            pnl = 0.0
        msg = resolve_outcome(parts[0].upper(), pnl=pnl)
        tg_send(msg, chat_id)
        return

    if text in ("⏭ Skip Log", "/skip"):
        d = STATE.get("last_decision")
        if not d:
            tg_send("No decision yet.", chat_id)
            return
        ok = save_setup(d, "skip")
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                """INSERT INTO skipped_setups
                (ts,symbol,direction,score,classification,reason,filter_scores_json,would_trade)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    iso_now(), d.get("symbol"), d.get("direction"), d.get("score"),
                    d.get("classification"), (d.get("reason") or "")[:200],
                    json.dumps(d.get("filter_scores") or {}, default=str)[:2000],
                    1 if d.get("status") == "TRADE" else 0,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning("skip log: %s", e)
        tg_send("⏭ Skip logged (no-trade analysis)." if ok else "Fail.", chat_id)
        return

    if text in ("📊 Day Score", "/day"):
        tg_send(period_score(1), chat_id)
        return
    if text in ("📅 Month Score", "/month"):
        tg_send(period_score(30), chat_id)
        return

    if text in ("📈 Backtest", "/backtest"):
        tg_send_action(chat_id, "typing")
        try:
            res = backtest_symbol(STATE.get("symbol") or DEFAULT_SYMBOL)
            if res.get("unavailable"):
                tg_send(
                    f"⚪ Backtest skipped — `{STATE.get('symbol')}` unavailable\n"
                    f"{res.get('error')}\nSwitch coin and retry.",
                    chat_id,
                    reply_markup=main_keyboard(),
                )
            else:
                tg_send(format_backtest(res), chat_id, reply_markup=main_keyboard())
        except Exception as e:
            log.warning("backtest cmd: %s", e)
            tg_send(f"Backtest error (no crash): {e}", chat_id, reply_markup=main_keyboard())
        return

    if text_lower in ("backtest all", "/backtestall", "btall"):
        tg_send_action(chat_id, "typing")
        tg_send("⏳ Watchlist backtest (skips unavailable)…", chat_id)
        lines = ["*Watchlist Backtest*"]
        for sym in (STATE.get("watchlist") or WATCHLIST_DEFAULT)[:12]:
            try:
                res = backtest_symbol(sym, bars=120)
                if res.get("unavailable") or not res.get("ok"):
                    lines.append(f"⚪ `{sym}` SKIPPED — {res.get('error', 'unavailable')[:40]}")
                    continue
                lines.append(
                    f"{'🟢' if res.get('net_pnl', 0) > 0 else '🔴'} `{sym}` "
                    f"WR {res.get('win_rate')}% n={res.get('trades')} net `{res.get('net_pnl'):+.1f}R`"
                )
            except Exception as e:
                lines.append(f"⚪ `{sym}` SKIPPED — {e}")
                continue
        tg_send("\n".join(lines)[:3800], chat_id, reply_markup=main_keyboard())
        return

    if text in ("🧠 Learning", "/learning", "/weights"):
        tg_send(format_learning_summary(), chat_id)
        return

    if text in ("🩺 Diagnosis", "/diagnosis", "/diag", "diagnosis"):
        tg_send_action(chat_id, "typing")
        tg_send(format_diagnosis_report(limit=400), chat_id, reply_markup=main_keyboard())
        return

    if text in ("🧪 BT Diagnosis", "/btdiag", "bt diagnosis", "btdiagnosis"):
        tg_send_action(chat_id, "typing")
        tg_send(diagnosis_from_backtest(limit=2000), chat_id, reply_markup=main_keyboard())
        return

    if text in ("🎲 MonteCarlo", "/montecarlo", "montecarlo", "mc"):
        tg_send_action(chat_id, "typing")
        mc = monte_carlo_stress(400)
        if not mc.get("ok"):
            tg_send(f"MC: {mc.get('error')}", chat_id)
            return
        tg_send(
            f"*Monte Carlo* n_trades=`{mc['n_trades']}` sims=`{mc['n_sims']}`\n"
            f"DD p50/p95: `{mc['dd_p50']}` / `{mc['dd_p95']}` R\n"
            f"Loss streak p50/p95: `{mc['streak_p50']}` / `{mc['streak_p95']}`\n"
            f"Final eq p05/p50/p95: `{mc['eq_p05']}` / `{mc['eq_p50']}` / `{mc['eq_p95']}`\n"
            f"Frac negative equity: `{mc['frac_neg_eq']}`",
            chat_id,
            reply_markup=main_keyboard(),
        )
        return

    if text_lower in ("walkforward", "/walkforward", "wf"):
        tg_send_action(chat_id, "typing")
        wf = walk_forward_backtest(STATE["symbol"])
        if not wf.get("ok"):
            tg_send(f"WF: {wf.get('error')}", chat_id)
            return
        lines = [f"*Walk-forward* `{wf['symbol']}`"]
        for w in wf.get("windows") or []:
            lines.append(
                f"W{w['window']}: trades `{w['trades']}` WR `{w['wr']}%` net `{w['net']:+.1f}`"
            )
        tg_send("\n".join(lines), chat_id, reply_markup=main_keyboard())
        return

    if text in ("🧾 Post-Mortem", "/postmortem", "/pm", "postmortem"):
        try:
            # Pure DB — works even if current coin is delisted/unavailable
            tg_send(format_last_postmortem(), chat_id, reply_markup=main_keyboard())
        except Exception as e:
            log.warning("postmortem cmd: %s", e)
            tg_send(
                f"Post-mortem read failed (no crash): {e}\n"
                f"_Does not need live coin data._",
                chat_id,
                reply_markup=main_keyboard(),
            )
        return

    if text_lower in ("/export_diag", "export diag", "export diagnosis"):
        path = export_diagnosis_file()
        tg_send(f"Diagnosis export → `{path}`\nShare this JSON for joint review.", chat_id)
        return

    if text_lower.startswith("account "):
        try:
            val = float(text.split(maxsplit=1)[1])
            if 10 <= val <= 1_000_000:
                STATE["account_usd"] = val
                tg_send(f"Account → `${val:.0f}`", chat_id)
            else:
                tg_send("Range 10–1000000", chat_id)
        except Exception:
            tg_send("Example: account 250", chat_id)
        return

    if text_lower.startswith("risk "):
        try:
            val = float(text.split(maxsplit=1)[1])
            if 0.1 <= val <= 5:
                STATE["risk_pct"] = val
                tg_send(f"Risk → `{val}%`", chat_id)
            else:
                tg_send("Range 0.1–5%", chat_id)
        except Exception:
            tg_send("Example: risk 1.2", chat_id)
        return

    if text in ("🪙 Change Coin", "/coin"):
        tg_send(f"Current `{STATE['symbol']}` — tap or type:", chat_id, reply_markup=coin_keyboard())
        return

    if text == "« Back":
        tg_send("Main menu", chat_id, reply_markup=main_keyboard())
        return

    if text in ("📋 Watchlist", "/watchlist"):
        wl = STATE.get("watchlist") or []
        tg_send("*Watchlist*\n" + " ".join(f"`{c}`" for c in wl) +
                "\n\n`wl add SOLUSD` · `wl del SOLUSD`", chat_id, reply_markup=main_keyboard())
        return

    if text_lower.startswith("wl add "):
        sym = sanitize_symbol(text.split(maxsplit=2)[-1])
        if sym not in STATE["watchlist"]:
            STATE["watchlist"].append(sym)
        tg_send(f"Added `{sym}`", chat_id)
        return
    if text_lower.startswith("wl del "):
        sym = sanitize_symbol(text.split(maxsplit=2)[-1])
        STATE["watchlist"] = [c for c in STATE["watchlist"] if c != sym]
        tg_send(f"Removed `{sym}`", chat_id)
        return

    if text in ("⚙️ Settings", "/settings", "/status"):
        tg_send(format_settings(), chat_id, reply_markup=main_keyboard())
        return

    candidate = sanitize_symbol(text)
    if text.upper() in POPULAR_COINS or (
        candidate.endswith("USD") and 5 <= len(candidate) <= 12 and text.upper() == candidate
    ):
        try:
            ok, why = symbol_is_tradeable(candidate)
            t = get_ticker_full(candidate) if ok else None
            if ok and t and safe_float(t.get("mark")) > 0:
                STATE["symbol"] = candidate
                tg_send(
                    f"🪙 `{candidate}` · LTP `${t['mark']:,.2f}`\n"
                    f"OI `${t.get('oi_value_usd', 0):,.0f}` · Fund `{t.get('funding_rate')}`",
                    chat_id, reply_markup=main_keyboard(),
                )
            else:
                # do NOT switch active symbol to a dead coin
                tg_send(
                    f"⚪ `{candidate}` · *UNAVAILABLE*\n"
                    f"{why or 'No ticker/candles'}\n"
                    f"Active coin remains `{STATE.get('symbol')}` — try another.",
                    chat_id, reply_markup=main_keyboard(),
                )
        except Exception as e:
            tg_send(f"⚪ `{candidate}` skip — {e}", chat_id, reply_markup=main_keyboard())
        return

    if text:
        tg_send("Unknown — tap ❓ Help", chat_id, reply_markup=main_keyboard())


# ═══════════════════════════════════════════════════════════════
# 16. MAIN
# ═══════════════════════════════════════════════════════════════
def telegram_health() -> bool:
    data = tg_api("getMe")
    if data.get("ok"):
        log.info("Telegram OK → @%s", data["result"].get("username"))
        return True
    log.error("Telegram fail: %s", data)
    return False


def main():
    print("=" * 60, flush=True)
    print(f"[{ist_str()}] TradAI Local Committee {BOT_VERSION}", flush=True)
    print("=" * 60, flush=True)
    init_db()
    telegram_health()
    start_ws([DEFAULT_SYMBOL] + RHYTHM_COINS[:5])
    wss = ws_status()
    tg_send(
        f"🧠 *Local Committee Online*\n`{BOT_VERSION}`\n\n"
        f"WS: `{'ON' if wss.get('enabled') else 'OFF'}` · "
        f"{'connected' if wss.get('connected') else 'connecting…'}\n"
        f"Trades→CVD · L2 stream · Funding · Structure · Rhythm\n"
        f"Coin: `{STATE['symbol']}`\n"
        f"Outcome labels: win/loss/be · learning only after resolve\n",
        reply_markup=main_keyboard(),
    )
    log_event("boot", BOT_VERSION + " ws=" + str(wss.get("enabled")))

    while True:
        try:
            url = (
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
                f"?offset={STATE['last_update_id'] + 1}&timeout={TG_TIMEOUT}"
            )
            r = requests.get(url, timeout=TG_TIMEOUT + 10).json()
            if not r.get("ok"):
                time.sleep(2)
                continue
            for upd in r.get("result", []):
                STATE["last_update_id"] = upd["update_id"]
                try:
                    process_update(upd)
                except Exception as e:
                    log.error("process: %s\n%s", e, traceback.format_exc())
        except Exception as e:
            log.error("loop: %s", e)
            time.sleep(4)
        time.sleep(0.35)


if __name__ == "__main__":
    main()
