# TradAI Finder — Implementation Status (Master Upgrade)

## Reused (not rewritten)

Delta client, availability engine, quality gate, Phase-1 foundation, deep structure, VP/VWAP, sessions, derivatives regimes, MTF, feature pipeline, backtest/costs, counterfactual SL grid, postmortem, UNKNOWN cluster, six laws, committee script (external).

## Newly added this upgrade

| Module | Purpose |
|--------|---------|
| `AUDIT.md` | Full pre-upgrade map |
| `src/registry/store.py` | Hypothesis / experiment / AI feedback registries |
| `src/cross_coin/context_engine.py` | BTC RS, corr, breadth, leadership proxies |
| `src/discovery/engine.py` | Factor tests + sample/p-value filters + no-trade scan |
| `src/validation/pipeline_8_60_1y.py` | Leakage-proof 8D→60D→1Y splits |
| `src/ai_loop/knowledge.py` | Evidence-based AI improvement JSON |
| `scripts/run_research_cycle.py` | One gated research cycle |
| `tests/test_no_lookahead.py` | Lookahead regression tests |

## Successfully implemented (honest)

- P0 data availability tags + no fabricate policy  
- Historical OHLCV / OI / Funding / Mark download  
- Universe discovery from exchange products  
- Quality gate blocks/flags bad sets  
- Live L2 + recent trades collector (builds future proprietary history)  
- Deep structure / VP / sessions / deriv / MTF features  
- Discovery engine with LOW_SAMPLE / REJECTED / DISCOVERED  
- Fixed-hypothesis 8D→60D→1Y evaluation API  
- AI plan fields: WHAT_FAILED, WHY, DATA_MISSING, TEST_NEXT  
- No-lookahead unit tests  

## Impossible / unavailable (not claimed)

| Item | Status |
|------|--------|
| Multi-year L2 / tick CVD archive | LIVE_COLLECT_ONLY |
| Market-wide liquidation history | EXTERNAL_SOURCE_REQUIRED |
| Full options IV surface history | EXTERNAL_SOURCE_REQUIRED |
| On-chain / ETF / news | EXTERNAL_SOURCE_REQUIRED |
| Guaranteed profitable strategy | Not an objective |

## Partial / limited

- 12h TF often unsupported on Delta  
- ATOMUSD not listed → SEIUSD in preferred universe  
- Cross-coin needs multi-coin parquets loaded (engine ready)  
- Counterfactual: SL grid only (not full entry/TP matrix yet)  
- Portfolio multi-position: deferred P4  
- True AI model retrain: knowledge JSON only (no weight SGD loop)  

## How to run

```bash
# 1) Data foundation
PYTHONPATH=. python3 scripts/run_phase1_foundation.py --days 30

# 2) Live microstructure (continuous)
PYTHONPATH=. python3 scripts/live_collector.py --symbols BTCUSD,ETHUSD,SOLUSD --interval 10

# 3) Research cycle (after PASS data exists)
PYTHONPATH=. python3 scripts/run_research_cycle.py --symbol BTCUSD --timeframe 1h

# 4) Tests
PYTHONPATH=. python3 -m pytest tests/test_no_lookahead.py -q
```

## Final rule observed

If 8D discovers and 60D rejects → classification `REJECTED_OOS`.  
No fake win rates. Missing data reported, not invented.

## Brain upgrade (this session)

| # | Capability | Module |
|---|------------|--------|
| 1 | Multi-factor 2–4 interactions | `discovery/multi_factor.py` |
| 2 | Threshold discovery + holdout | `discovery/threshold_search.py` |
| 3 | BH-FDR + permutation + bootstrap CI | `validation/fdr_stats.py` |
| 4 | 8D→60D→1Y wired in research cycle | `run_research_cycle.py` |
| 5 | Full CF matrix (SL/TP/delay/opp/flat) | `counterfactual/full_cf.py` |
| 6 | Wrong-direction LONG/SHORT/NO-TRADE | `postmortem/wrong_direction.py` |
| 7 | Closed learning loop + queue | `ai_loop/closed_loop.py` |
| 8 | Live JSONL → features | `microstructure/live_features.py` |
| 9 | Portfolio risk proxies | `portfolio/risk_engine.py` |
| 10 | External adapter stubs | `adapters/external_stubs.py` |

Still not claimed: true model weight SGD, external liquidation feeds, multi-year L2 archive.
