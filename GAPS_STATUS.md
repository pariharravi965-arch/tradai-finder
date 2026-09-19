# TradAI Finder — Gap Status (Delta India)

## Next Upgrade Priorities (Critical Path)

Ordered priority list — closing these is what turns the discovery pipeline from a scaffold into a leak-proof, statistically honest system:

| # | Priority | Notes |
|---|----------|-------|
| 1 | 🔴 **Future-feature firewall** | Blocking. Prevents any feature computed with future/lookahead information from entering discovery — must land before anything downstream can be trusted. |
| 2 | Automatic feature registry / pool | Central catalog of all available features (with availability tags) that discovery draws from, instead of ad-hoc feature lists per run. |
| 3 | Wire 2–4 factor discovery to the complete feature universe | `discovery/multi_factor.py` must search the full registry (#2), not a hardcoded subset. |
| 4 | Apply FDR correctly to discovery results | Ensure `validation/fdr_stats.py`'s BH-FDR is applied to the *actual* discovery result set (post multi-factor/threshold/sequence search) — see the FDR-is-not-proof safeguard already noted there. |
| 5 | Threshold → fixed hypothesis → 60D OOS → 1Y OOS | Full chain must be wired end-to-end via `validation/pipeline_8_60_1y.py`, with no stage skippable. |
| 6 | Purged CV + embargo | Add purged cross-validation with an embargo window to prevent train/test leakage across nearby timestamps — currently missing from validation. |
| 7 | True statistical overfit controls | Beyond the current rough `deflated_sharpe_proxy` — proper PBO (probability of backtest overfitting) and deflated Sharpe, not just a log-penalty approximation. |
| 8 | Full research dataset runner | A runner that executes the whole pipeline (features → discovery → FDR → 8D/60D/1Y) across the complete dataset/universe, not single-symbol demo runs. |
| 9 | Actual E2E test: 8D → 60D → 1Y | Extend `tests/test_no_lookahead.py` (or a new test) into a real end-to-end regression test covering the full 8D→60D→1Y path, not just lookahead checks. |
| 10 | Feed the AI loop from validated evidence only | `ai_loop/knowledge.py` / `ai_loop/closed_loop.py` must only ingest hypotheses that survived FDR **and** 60D/1Y OOS (per the safeguard in README) — never raw discovery hits. |

## Implemented in Phase 2 (this release)

| Area | Status |
|------|--------|
| Deep structure (HH/HL, BOS, CHoCH, FVG, OB proxy, sweep, equal H/L, premium/discount) | ✅ |
| VWAP family + rolling VP approx (POC/VAH/VAL/HVN/LVN) | ✅ |
| Sessions (Asia/London/NY, hour, DOW, daily open) | ✅ |
| Derivatives regimes (OI×price 4-way, funding extremes, 3-way state) | ✅ |
| MTF context (HTF bias, align/conflict) | ✅ |
| Hypothesis generator (H1–H6 + no-trade rules) | ✅ |
| Counterfactual SL grid | ✅ |
| UNKNOWN clustering | ✅ |
| Six laws as research checklist | ✅ |
| Data gaps registry (no fabrication) | ✅ |
| Delta public client | ✅ |

## Still DATA_MISSING (cannot invent)

- Historical order book / tick CVD / aggression / absorption / iceberg
- Liquidation history / cascades
- True basis without spot feed
- Options IV / GEX
- On-chain / ETF / whale / news timestamps

## Still incomplete (Phase 3+)

- Full automatic combinatorial discovery + FDR
- Portfolio risk / multi-coin concurrent
- True AI retrain loop (weights update from postmortem)
- Purged CV / deflated Sharpe / PBO
- Cross-coin full correlation matrix engine
- Committee research integration as multi-analyst votes

## Distinction

**Data होना ≠ research में इस्तेमाल।**  
Jo available hai (OHLCV, funding, OI) usse deep structure, VP, MTF, CF, laws — sab wire ho chuka hai.  
Jo missing hai usko registry mein `DATA_MISSING` mark kiya hai.
