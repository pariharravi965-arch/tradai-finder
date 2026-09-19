#!/usr/bin/env python3
"""Test Delta public live extras + optional auth (keys from env)."""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.delta_auth import DeltaAuthClient, DeltaPublicExtra

sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSD"
pub = DeltaPublicExtra()
print("=== L2 imbalance ===")
print(pub.orderbook_imbalance(sym))
print("=== CVD proxy (recent) ===")
print(pub.cvd_proxy_from_recent_trades(sym))

auth = DeltaAuthClient()
print("=== Auth configured? ===", auth.is_configured)
if auth.is_configured:
    try:
        bal = auth.balances()
        print("Balances OK", type(bal))
    except Exception as e:
        print("Auth call failed:", e)
else:
    print("Set DELTA_API_KEY and DELTA_API_SECRET to test private endpoints.")
