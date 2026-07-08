---
name: Live Mirror Architecture
description: How the V3 Live Mirror 1:1 real-trade module is structured and wired up.
---

# V3 Live Mirror Architecture

**Why:** Added 1:1 real-trade sync for V3 paper signals without changing any strategy logic.

## Module layout
`src/binance_ai_trader/v3/live/`
- `client.py` — BinanceFuturesClient (stdlib only, HMAC-SHA256 auth)
- `models.py` — LiveOrder, LiveEvent, PlaceResult dataclasses
- `repository.py` — LiveOrderRepository (PostgreSQL; tables: live_orders, live_events)
- `engine.py` — LiveMirrorEngine: try_place(), sync_all(), get_account_status()
- `reporter.py` — LiveHourlyReporter: hourly Telegram status
- `cli.py` — live status/orders/positions/cancel/close commands

## Activation
Set env secrets: `LIVE_TRADING_ENABLED=true`, `BINANCE_API_KEY`, `BINANCE_API_SECRET`.

Optional tuning: `ORDER_NOTIONAL_USDT` (default 1000), `MAX_PENDING_ORDERS` (10), `MAX_OPEN_POSITIONS` (5), `LIVE_SYNC_INTERVAL_MIN` (15), `LIVE_REPORT_INTERVAL_MIN` (60).

## Wire-up points
- `run_server.py` — creates LiveMirrorEngine if enabled, passes to build_v3_tasks()
- `tasks.py` — `build_v3_tasks(live_mirror=...)` adds v3_live_sync + v3_live_report tasks
- `_scan_task` in tasks.py — calls `live_mirror.try_place(candidate)` BEFORE send_candidate; result prefix prepended to Telegram message
- `notifier.py` `_format_candidate()` — accepts `live_prefix: str | None` and prepends it
- `__main__.py` — dispatches `python -m binance_ai_trader live <cmd>` to live/cli.py

## Flow per signal
1. try_place() → risk checks → set leverage → LIMIT entry order → save PENDING
2. Sync task (15m): if entry FILLED → place STOP_MARKET SL + TAKE_PROFIT_MARKET TP (both reduceOnly=true)
3. Sync task: if FILLED order with no SL → naked position alert + re-attempt SL
4. Sync task: if SL/TP order FILLED → update DB to CLOSED_SL / CLOSED_TP

## Risk checks (engine._risk_check)
- LIVE_TRADING_ENABLED=true required
- open_orders < MAX_PENDING_ORDERS
- positions < MAX_OPEN_POSITIONS
- SL distance ≤ 8% from entry (live-only cap, tighter than paper)
- Available balance ≥ notional / leverage
- Same-symbol conflicts are NOT handled here anymore — see order_manager below.

## Leverage rule
`min(10, max_leverage_from_binance)` — capped at 10x.

## Same-symbol conflict management (order_manager.py)
Before `_risk_check`, `try_place()` calls `_handle_conflicts()` which asks
`LiveOrderManager.resolve()` (pure decision logic, no I/O) what to do about an
existing PENDING order or FILLED position for the same symbol:
- Existing FILLED position, same direction → block (no pyramiding)
- Existing FILLED position, opposite direction → block, alert-only, never auto-flip
- Existing PENDING order, opposite direction → cancel old, also skip new (no auto-flip)
- Existing PENDING order, same direction, new entry ≥0.5% better → REPLACE (cancel old, place new)
- Existing PENDING order, same direction, within ±0.5% → duplicate, keep old
- Existing PENDING order, same direction, new entry worse by >0.5% → ignore new
- Pending orders also expire via `_expire_stale_pending()` (called from `sync_all`) after 24h or if price drifts >5% from entry.
All terminal (non-place) outcomes still write a `live_orders` row (status e.g. IGNORED_DUPLICATE, DIRECTION_CONFLICT, POSITION_EXISTS_*) and a `live_events` row with old/new signal context, so `/signals` and `/v4debug`-style joins can show the reason per signal.

**Why:** avoids duplicate/conflicting orders when the same symbol re-triggers across scans, without ever silently auto-flipping a position.

**How to apply:** Any new order-placement path should call `LiveOrderManager.resolve()` first, exactly like `try_place()` does — don't reintroduce ad-hoc same-symbol checks in risk checks.
