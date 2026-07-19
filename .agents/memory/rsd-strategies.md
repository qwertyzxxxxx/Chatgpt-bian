---
name: RSD divergence strategies
description: rsd_long and rsd_short implementation details, watchlist field mapping, and wiring decisions.
---

## Strategy IDs and prefix
- `rsd_long`  → signal prefix **RSD**
- `rsd_short` → signal prefix **RSD**
- Both registered in `v3/candidates/repository.py` `_STRATEGY_PREFIXES`

## Architecture
- Same two-phase pattern as wave_long/wave_short (WaveWatchlistRepo + V3Pipeline)
- Phase-1: scan Top-100 USDT universe for H1 BOS → `repo.add()`
- Phase-2: per-watchlist-item check for M15 RSI divergence + trigger → `CandidateInput`
- **No `universe_config` param** — both strategies use self-contained `_get_universe()` calling `tickers_24h()` directly
- `WaveWatchlistRepo` reused (SQLite, same DB file as wave strategies)

## WaveWatchlistRepo field mapping
**LONG**: `platform_high=bos_level`, `platform_low=impulse_low`, `breakout_close=impulse_high`
**SHORT**: `platform_high=impulse_high`, `platform_low=bos_level`, `breakout_close=impulse_low`

## CandidateInput.meta_json
Added `meta_json: str = "{}"` field to `CandidateInput` (backward-compatible default).
`build_rsd_tasks()` passes `candidate.meta_json` as `metadata_json` in paper orders.
All other task builders continue using hardcoded `"{}"`.

## Wiring
- CLI flag: `--enable-rsd` (+ `--rsd-report-interval-hours`, `--rsd-dedup-hours`, `--rsd-max-open-orders`)
- Task builder: `build_rsd_tasks(db_path, base_url, ...)` in `v3/runner/tasks.py`
- Single settler shared between rsd_long and rsd_short

## Key files
- `v3/strategies/rsd_common.py` — shared constants + pure indicator functions
- `v3/strategies/rsd_long.py`   — RSDivLongStrategy
- `v3/strategies/rsd_short.py`  — RSDivShortStrategy

**Why no universe_config:** RSD strategies scan the full USDT universe themselves (not a curated watchlist). Passing UniverseConfig would require UniverseConfig.default() which doesn't exist.
