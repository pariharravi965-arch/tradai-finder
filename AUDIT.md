# TradAI Finder — Architecture Audit (pre-upgrade)

Inspected: full `tradai_finder` tree, configs, scripts, src modules, processed reports.

## Existing working components (REUSE — do not rewrite)

| Area | Module(s) | Notes |
|------|-----------|--------|
| Delta public client | `src/data/delta_client.py` | Candles, products, FUNDING/OI/MARK series |
| Auth + live OB/trades | `src/data/delta_auth.py` | Env-only keys; L2 imbalance; recent CVD proxy |
| Availability tags | `src/data/availability_engine.py` | HISTORICAL / LIVE_COLLECT_ONLY / EXTERNAL |
| Quality + gate | `src/data/quality.py`, `quality_gate.py` | Score + PASS/WARN/FAIL |
| Timestamp align | `src/data/timestamp_align.py` | OHLCV↔MARK/OI/FUNDING merge_asof |
| Phase-1 runner | `scripts/run_phase1_foundation.py` | Steps 1–5 |
| Live collector | `scripts/live_collector.py` | JSONL OB + trades |
| Deep structure | `src/structure/deep_structure.py` | HH/HL, BOS, CHoCH, FVG, OB, sweep, premium |
| Swings | `src/structure/swings.py` | Base swings |
| VP / VWAP | `src/volume_profile/vp_vwap.py` | Rolling VP approx, VWAP family |
| Sessions | `src/sessions/session_engine.py` | Asia/London/NY, hour, DOW |
| Derivatives regimes | `src/derivatives/oi_funding_engine.py` | 4-way OI×price, funding extremes |
| MTF | `src/mtf/mtf_engine.py` | HTF bias, align/conflict |
| Features pipeline | `src/features/base.py` + price/mom/vol/deriv | Causal feature build |
| Hypothesis seeds | `src/discovery/hypothesis_gen.py` | H1–H6 + no-trade flags |
| Backtest + costs | `src/engines/backtest.py`, `costs.py` | Cost-aware |
| Signals | `src/engines/signals.py` | Candidate signals |
| Counterfactual SL | `src/counterfactual/cf_engine.py` | ATR SL grid |
| Postmortem | `src/postmortem/analyzer.py` | Failure reasons |
| UNKNOWN cluster | `src/postmortem/unknown_cluster.py` | Pattern counts |
| Validation metrics | `src/validation/metrics.py`, `overfit.py`, `walk_forward.py` | Partial |
| Six laws | `src/laws/research_laws.py` | Checklist fields |
| Reports | `src/reports/generator.py` | Markdown/JSON |
| Committee (external) | `scripts/tradai_committee_v20.4.py` | Delta trading advisor — separate |

## Gaps vs Master Upgrade (honest)

| Req section | Status | Action |
|------------|--------|--------|
| §2 Data availability tags | ✅ | Keep |
| §3 Max historical + 11 TF | ✅ partial (12h often unsupported) | Document RESAMPLED if used |
| §4 Universe discovery | ✅ | Keep dynamic list |
| §5 Quality engine | ✅ | Keep + harden tests |
| §6 Live collectors | ⚠️ basic | Add checkpoint/resume/disk monitor |
| §7 Deep structure | ✅ major | Keep |
| §8 Volume/liquidity | ✅ VP/VWAP | Keep |
| §9 Momentum/vol | ✅ basic | Keep — not treated as truth |
| §10 Derivatives | ✅ | Keep |
| §11 Cross-coin | ❌ missing | **NEW** |
| §12 MTF | ✅ basic | Keep |
| §13 Sessions | ✅ | Keep |
| §14–16 Discovery / hypothesis / no-trade | ⚠️ seed only | **Expand registry + filters** |
| §17 Backtest | ✅ partial | Keep costs/no-lookahead discipline |
| §18 Counterfactual | ⚠️ SL grid only | Extend later |
| §19–21 Postmortem / wrong-dir / UNKNOWN | ⚠️ partial | Strengthen |
| §22–23 Validation / overfit | ⚠️ scaffold | **Wire 8D→60D→1Y orchestrator** |
| §24 AI learning loop | ❌ plan only | **Knowledge extract + next-experiment JSON** |
| §25 Registries | ❌ | **NEW parquet/json registries** |
| §26 Six laws | ✅ | Keep |
| §27 Portfolio | ❌ | Deferred P4 |
| §28–29 DB/perf | partial parquet | Incremental OK |
| §30–31 Reports / AI output | partial | Strengthen |
| §32 No fake | policy | Enforce |
| §33 Tests | smoke only | **Add no-lookahead test** |

## Upgrade policy

1. Reuse all ✅ modules  
2. Add missing P0/P1/P2 without breaking imports  
3. Never fabricate HISTORICAL for LIVE_COLLECT_ONLY  
4. Discovery blocked if quality gate FAIL  
5. 8D must not use 60D/1Y labels  
