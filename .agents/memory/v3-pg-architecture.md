---
name: V3 PostgreSQL Final Architecture
description: V3 permanent data storage migrated to PostgreSQL; SQLite is cache-only. All rules for future strategy additions.
---

## Rule
**This is the LAST V3 DB refactoring. No V4/V5 allowed.**

## Storage split
- **PostgreSQL** (permanent): v3_candidates, v3_push_queue, v3_paper_orders, v3_order_events, v3_feature_store, v3_signal_id_seq
- **SQLite** (`data/market_data.db`): klines cache, universe cache, runner caches only

## Key files
- `src/binance_ai_trader/v3/storage/pg.py` — connection factory + DDL (`get_conn()`, `init_schema()`)
- `src/binance_ai_trader/v3/storage/repository.py` — `StorageRepository` facade
- `src/binance_ai_trader/v3/storage/migration.py` — idempotent SQLite→PG migrator (ON CONFLICT DO NOTHING)

## All repos use PG (db_path ignored)
- `V3CandidateRepository` — PG, db_path param kept for backward compat only
- `V3PushQueueRepository` — PG
- `V3PaperOrderRepository` — PG
- `V3FeatureStoreRepository` — PG
- `V3RiskEngine` — PG (reads v3_paper_orders for open count)
- `V3DedupEngine` — PG (reads v3_candidates for dedup window)

## Telegram types (4 only)
1. [V3] Started — startup.py
2. Signal — notifier.py (candidate push)
3. Paper Portfolio — shadow_report.py (hourly)
4. Weekly Review — weekly_review.py (every 7 days)
Health check task exists but sends no Telegram message.

## New tasks added in build_v3_tasks()
- `v3_weekly_review` (7d interval) — sends weekly stats to Telegram

## startup sequence in run_server.py
1. Health server
2. `init_schema()` — PG DDL
3. `run_migration()` — SQLite→PG (idempotent)
4. `_drop_v2_tables()` — SQLite cleanup
5. build V3 tasks → [V3] Started

**Why:** SQLite resets on Reserved VM redeploy; PostgreSQL persists permanently across all deployments.

**How to apply:** Any new strategy (Monster, Breakout, AI) must use the same repos and pipeline. No new order/candidate tables.
