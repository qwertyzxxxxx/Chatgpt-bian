---
name: V2 Paper Fund Architecture
description: Paper fund module structure, DB tables, buy/sell rules, and CLI commands
---

## Module location
`market_hunter/paper_fund/`
- `db.py`       — all DB CRUD (5 independent tables, never touches scanner tables)
- `fund.py`     — core daily logic: process_orders → sell_rules → buy_candidates → create_orders → snapshot_equity
- `reporter.py` — Telegram daily report
- `__init__.py` — empty

## DB tables (paper_ prefix, all in market_hunter.db)
- `paper_fund`      — single-row cash/capital state
- `paper_positions` — open and closed positions; `partial_sold` flag for 50% sells
- `paper_orders`    — pending buy orders (filled at next-day open)
- `paper_trades`    — every transaction (buy, sell_partial, sell_full)
- `paper_equity`    — daily snapshot with SPY close for alpha calculation

## Buy filter (ALL required)
- action_status == "可关注买点"
- stars >= 4 (★★★★: score≥85, RR≥2.0, risk≤6%)
- score >= 85, RR >= 2.0, risk <= 6%, vol_ratio >= 1.2
- Cancel if open price > trigger × 1.02 (跳空过高)

## Fund constraints (enforced in code)
- Initial capital: $100,000
- Max positions: 5, Max new/day: 2
- Max position size: 20% of equity, Min cash: 10% of equity
- Max holding: 30 days

## Sell rules (priority order)
1. close <= stop_loss → sell all (止损)
2. holding_days >= 30 → sell all (时间止损)
3. close >= target1 AND not partial_sold → sell 50%
4. close >= target1 AND partial_sold AND close < MA20 → sell remaining

## Scheduler integration
In `runner.py._scan_job()`: after `run_us_scan(notify=True)`, call `run_daily(scan_date, scan_results=results, notify=True)`.
Guard: only runs if `pfdb.get_fund()` is not None (fund initialized).

## CLI commands
- `paper-init`   — initialize fund (idempotent)
- `paper-daily`  — standalone run without scan
- `paper-report` — send Telegram from current DB state

## record_push timing (V1.2 lesson applies here too)
Paper fund does NOT use signal_push_log — it has its own paper_orders + paper_positions.

**Why:** V2 needs orders to persist across days (fill next morning); the scanner's push_log is for same-day Telegram dedup only.
