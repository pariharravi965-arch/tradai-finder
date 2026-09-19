# Jo Delta public / auth se nahi ho sakta (honest list)

## Absolute DATA_MISSING (fabricate mat karo)

| Item | Why |
|------|-----|
| Multi-year historical L2 order-book archive | Exchange REST only gives **live** L2 snapshot |
| Multi-year tick-by-tick trades for all coins | Only **recent** trades endpoint |
| True historical CVD series | Needs full tick history — not published |
| Market-wide liquidation history | No public historical liquidation API on Delta India |
| Absorption / iceberg detection (historical) | Needs order-book event stream history |
| Options full IV surface history | Limited; not same as Deribit-depth history |
| On-chain / ETF / whale / miner flows | Not on Delta API — external vendors only |
| Complete news timestamps with market impact labels | External calendar + manual curation |
| Guaranteed “edge” strategy | Research may conclude NO ROBUST DISCOVERY |

## DATA_LIMITED (partial / proxy only)

| Item | What we have |
|------|----------------|
| CVD / aggression | **Live + recent trades proxy**; grows if `live_collector.py` runs continuously |
| Order-book imbalance | **Live snapshots** via collector → your own JSONL history |
| Basis | MARK − close proxy (not always true spot) |
| OI / Funding history | Via `OI:SYMBOL` / `FUNDING:SYMBOL` candles — good but resolution-dependent |
| Mark price history | `MARK:SYMBOL` candles — available |
| Options/IV | Spot-check only if products listed; deep research needs external |

## Auth key unlocks (only YOUR account)

- Balances, positions  
- Your order history / fills (learning loop)  
- **Not** market-wide historical microstructure  

## Research honesty rules

1. Live collector **abhi** chalao → future mein microstructure DB banegi  
2. Jo missing hai usko report mein `DATA_MISSING` likho  
3. Proxy use karo to label `PROXY` / `DATA_LIMITED`  
4. Positive backtest ≠ proof  

## Priority progress (this build)

| # | Item | Status |
|---|------|--------|
| 1–6 | Max Delta hist (OHLCV, OI, Funding, Mark) | ✅ `download_full_universe.py` |
| 7 | Options/IV | ❌ DATA_MISSING / limited |
| 8 | 25-coin universe verify | ✅ report JSON |
| 9 | MTF 15m–1d dataset | ✅ multi-TF download |
| 10 | Quality + alignment | ✅ `timestamp_align.py` + quality scores |
| 11–21 | Structure/VP/sessions/deriv/MTF… | ✅ Phase 2 modules (earlier) |
| 23–26 | Live OB/trades/CVD/imbalance collect | ✅ `live_collector.py` **start now** |
| 27–28 | Absorption / liquidations hist | ❌ cannot |
| 30–52 | Discovery brain | partial (hypotheses, CF, UNKNOWN cluster) |
| 53–67 | Portfolio / AI loop / full E2E | partial scaffolding |
EOF
