# TradAI Full Market Research & Improvement Finder

**Version:** 1.0.0 (Phase 1)
**Data Source:** Delta Exchange India Public API (api.india.delta.exchange)
**Purpose:** Systematic discovery, measurement, validation and AI-improvement recommendations from crypto market data — **not** a strategy profit maximizer.

## Core Principle

Assume nothing is correct. Treat every strategy, indicator and rule as a hypothesis.
Pipeline for every discovery:

1. Observe → 2. Measure → 3. Hypothesis → 4. Test → 5. Validate  
6. Out-of-sample → 7. Walk-forward → 8. Regime → 9. Coin → 10. Timeframe  
11. Cost-adjusted → 12. Robustness → 13. Failure analysis → 14. Improvement recommendation

## Full Discovery Pipeline (Gated Order)

Every hypothesis must pass through this gate sequence, in order — no stage can be skipped or reordered:

```
ALL AVAILABLE FEATURES
        ↓
2-factor
        ↓
3-factor
        ↓
4-factor
        ↓
thresholds
        ↓
sequences
        ↓
regime conditions
        ↓
NO-TRADE discovery
        ↓
FDR
        ↓
Permutation
        ↓
Bootstrap
        ↓
8D Discovery
        ↓
FIX HYPOTHESIS
        ↓
60D OOS
        ↓
1Y OOS
        ↓
Stability / Decay
        ↓
Postmortem
        ↓
Next Experiment
```

**Safeguard note — FDR is not proof.** When the FDR check fails, the system marks the result `REJECTED_FDR` — that's a good, necessary safeguard against false discoveries from multiple testing. But **passing FDR does not prove a hypothesis is true.** It only means the result survived a multiple-testing correction on the discovery sample. `60D OOS` and `1Y OOS` — real, held-out, out-of-sample evidence — remain mandatory before any hypothesis can be treated as validated, regardless of FDR outcome. A hypothesis that passes FDR but fails 60D/1Y OOS is still rejected (`REJECTED_OOS`), and no earlier stage may substitute for that out-of-sample test.

*(Aur jo FDR fail → "REJECTED_FDR" dikhaya hai, woh good safeguard hai, lekin FDR pass hona truth prove nahi karta. 60D/1Y stability aur proper out-of-sample evidence fir bhi mandatory rahega.)*

## What Delta Public Data CAN Provide

| Dataset | Source | History | Notes |
|---------|--------|---------|-------|
| OHLCV (1m–1d) | REST `/fapi/v1/klines` + data.binance.vision | ~2019/2020+ | Full depth via vision dumps |
| Funding Rate | REST + vision monthly zips | ~2020+ | Excellent |
| Open Interest Hist | `/futures/data/openInterestHist` | Limited (~30 days deep via API) | Gaps possible; mark DATA_LIMITED |
| Long/Short Ratio | `/futures/data/globalLongShortAccountRatio` | Limited | Recent only |
| Taker Buy/Sell | `/futures/data/takerlongshortRatio` | Limited | Recent only |
| Mark/Index Price | REST | Good | Available |
| Exchange Info | REST | Live | Listing dates, filters |

## What CANNOT Be Obtained Reliably from Public API Alone

| Dataset | Status | Action |
|---------|--------|--------|
| Historical full order-book depth | UNAVAILABLE | Interface exists, returns DATA_MISSING |
| Tick-level liquidations (deep history) | UNAVAILABLE / LIMITED | Mark DATA_MISSING; do not fabricate |
| Historical news/event timestamps (complete) | EXTERNAL | Optional module; incomplete → flag |
| Exact historical spread | UNAVAILABLE | Proxy via high-low or mark-price only |
| Point-in-time order-flow (CVD historical) | LIMITED | Can approximate from aggTrades (heavy) |

**Rule:** Never invent missing data. Always record `DATA_AVAILABLE` / `DATA_MISSING` / `DATA_LIMITED`.

## Architecture Overview

```
tradai_finder/
├── config/                 # YAML configs, coin lists, research windows
├── data/                   # raw / processed / cache
├── db/                     # SQLite / DuckDB databases
├── src/
│   ├── data/               # Ingestion (Delta REST + Vision)
│   ├── features/           # Price, vol, momentum, volume, OI, funding...
│   ├── structure/          # Swing, BOS, CHOCH, MSS, structure labels
│   ├── regime/             # Trend / chop / vol regime classification
│   ├── engines/            # Entry, exit, SL, R:R, no-trade, cost
│   ├── validation/         # 8D → 60D → 1Y, walk-forward, overfit
│   ├── postmortem/         # Wrong-direction, UNKNOWN, SL analysis
│   ├── reports/            # Discovery report, AI improvement plan
│   └── utils/              # Logging, versioning, stats, leakage guards
├── scripts/                # CLI entry points
├── tests/
├── outputs/                # JSON/CSV reports, improvement files
└── logs/
```

## Research Stages

| Stage | Window | Purpose |
|-------|--------|---------|
| A | 8 days | Micro-pattern discovery (label as 8D_DISCOVERY) |
| B | 60 days | Survival / degradation test |
| C | 1 year | Long-term robustness |
| WF | Rolling train→test | Walk-forward (optimization only on train) |

## Anti-Overfitting Design

- Strict causal lag (no future candle information at decision time t)
- Signal timestamp ≠ execution timestamp
- Multiple-testing awareness
- Parameter stability (nearby thresholds)
- Train/test degradation tracking
- Coin / timeframe / regime consistency matrices
- Explicit OVERFIT_RISK: LOW / MEDIUM / HIGH

## Quick Start

```bash
cd tradai_finder
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 1. Download / update data (Delta public — no key required)
python scripts/download_data.py --coins BTC,ETH,SOL --timeframes 15m,1h,4h --days 90

# 2. Build features + structure + regimes
python scripts/build_features.py --run-id demo_001

# 3. Run Stage A (8D finder) then B/C validation
python scripts/run_finder.py --stage all --run-id demo_001

# 4. Generate reports + AI improvement plan
python scripts/generate_report.py --run-id demo_001
```

## Configuration

Edit `config/default.yaml`. All windows, fees, coins, timeframes and thresholds are configurable.

## Phase Roadmap

- **Phase 1 (this release):** Data layer, feature engines, structure, regime, basic discovery, validation scaffolding, post-mortem skeleton, report generator, improvement JSON.
- **Phase 2:** Full walk-forward, advanced liquidity proxies, multi-factor interaction search with FDR control, committee logic.
- **Phase 3:** Adaptive weights research, full counterfactual engine, live paper integration hooks.

## Final Question Every Run Must Answer

> “Mere current TradAI ko improve karne ke liye market ke data se ab tak sabse important kya seekha gaya, kya galat prove hua, kya unknown hai, kya missing hai, aur next version mein exactly kya test/change karna chahiye?”

Answer must be evidence-based and traceable to experiment IDs.
