---
name: Multi-strategy live trading isolation
description: Rules for running 2+ live strategies (e.g. V3 + V66) against the same Binance account without cross-contamination.
---

# Multi-strategy live isolation

**Why:** V3 and V66 both place real orders on the same Binance futures account. Without strict scoping, one strategy's sync/conflict/report logic can see, cancel, or count the other strategy's orders — causing silent cross-contamination (e.g. V66 hourly report showing V3's fills, or V3's conflict resolver cancelling a V66 pending order on the same symbol).

**How to apply:** Every live-trading component must be either instantiated once per strategy_id, or take strategy_id as an explicit filter on every query:
- `LiveMirrorEngine` — one instance per strategy (`strategy_id`, `tag` params), each with its own effective-notional resolution and conflict resolution scope.
- `LiveOrderRepository` queries used for conflict detection, sync, and reporting must filter by `strategy_id` (symbol alone is not enough — both strategies can legitimately hold positions in the same symbol independently).
- `LiveHourlyReporter` — one instance per strategy, counts (`today_order_count`, `count_today_by_type`) filtered by `strategy_id`; only account-level fields (balance/positions/open orders from Binance itself) are inherently shared since they reflect the whole account, not the strategy.
- Runtime settings (`live_enabled`, `notional_usdt`) live per-strategy in `v3_runtime_settings`, keyed by strategy_id, with per-strategy defaults (`LIVE_DEFAULTS` in `settings/repository.py`).
- Telegram control commands (`/livemode`, `/setlive`) take the strategy as an explicit argument rather than acting globally.

When adding a third strategy or any new live-trading surface (new report, new sweep, new command), grep for every place `strategy_id` is threaded through the existing two and mirror the same pattern — don't add an unscoped query "just for now."
