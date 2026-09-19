# TradAI Finder — Architecture Freeze

**Principle:** Data collect → research → validate → AI improve  
**Not:** Strategy-first or predefined-rule runner.

## Phases

| Phase | Name | Status target |
|-------|------|----------------|
| 1 | Data Foundation (Steps 1–5) | **THIS BUILD** |
| 2 | Live microstructure collectors | Collector scripts ready; run continuously |
| 3 | Deep market structure | Modules exist; gate on Phase-1 data |
| 4 | Market intelligence | Partial |
| 5 | Knowledge discovery engine | Partial hypotheses only |
| 6 | Backtest + postmortem | Scaffold |
| 7 | Leakage-proof 8D→60D→1Y | Scaffold |
| 8 | AI learning loop | Plan only |
| 9 | Research DB registries | Plan only |

## Phase 1 Steps (frozen order)

1. **Data Availability Engine** — tag every asset  
   `HISTORICAL_AVAILABLE` | `LIVE_COLLECT_ONLY` | `EXTERNAL_SOURCE_REQUIRED` | `PARTIAL`
2. **Maximum Historical Downloader** — OHLCV + OI + FUNDING + MARK  
3. **Live L2 + Trades Collector** — builds proprietary future history  
4. **25-coin × TF dataset** — universe discovered from exchange  
5. **Data Quality Gate** — PASS/WARN/FAIL before any discovery  

**Rule:** No discovery/backtest claims on FAIL data. LIVE_COLLECT_ONLY factors cannot claim historical edge until collector history exists.

## Status tags (mandatory in reports)

```text
HISTORICAL_AVAILABLE     → research OK after quality gate
PARTIAL                  → research OK with proxy label
LIVE_COLLECT_ONLY        → no historical edge claim yet
EXTERNAL_SOURCE_REQUIRED → blocked until external data
```

## Entry points

```bash
# Full Phase 1 (recommended first run — quick)
PYTHONPATH=. python3 scripts/run_phase1_foundation.py --days 7 --quick --live-sample 2

# Broader
PYTHONPATH=. python3 scripts/run_phase1_foundation.py --days 30

# Heavy (all TFs including 1m)
PYTHONPATH=. python3 scripts/run_phase1_foundation.py --days 14 --all-tfs --coins BTCUSD,ETHUSD

# Continuous live (tmux/screen)
PYTHONPATH=. python3 scripts/live_collector.py --symbols BTCUSD,ETHUSD,SOLUSD --interval 10
```

## Outputs

| File | Meaning |
|------|---------|
| `data/processed/data_availability_catalog.json` | Asset status tags |
| `data/processed/universe_discovery.json` | 25-coin verified list |
| `data/processed/download_manifest.json` | What was downloaded |
| `data/processed/quality_gate_report.json` | PASS/WARN/FAIL |
| `data/processed/phase1_summary.json` | Gate decision |
| `data/raw/klines/*.parquet` | OHLCV |
| `data/raw/oi|funding|mark/*.parquet` | Derivatives series |
| `data/raw/live/**/*.jsonl` | Growing microstructure |

## Explicit non-goals of Phase 1

- No strategy ranking  
- No “best signal” claim  
- No fabricated liquidations / historical OB  
- No AI model training  

## After Phase 1

Only if `phase1_summary.json` shows usable PASS/WARN OHLCV:

→ Phase 3 structure on PASS data  
→ Phase 5 discovery on gated features  
→ Phase 7 8D discovery only (never influence 60D/1Y labels)
