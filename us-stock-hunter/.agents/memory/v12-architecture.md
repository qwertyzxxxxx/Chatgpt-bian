---
name: V1.2 Architecture
description: Key design decisions for market-hunter V1.2 scanner pipeline
---

## record_push timing
`db.record_push()` must ONLY be called AFTER `notifier.send_scan_report()` succeeds.
In `scanner.py`, it's called inside the `if notify:` block, after the send.
Calling it during `_save_signals` (before notify) causes re-push dedup to block the real send.

**Why:** Two scan calls in one day (first with notify=False, second with notify=True)
caused push_log to be written on the first pass, then the second pass found records
already present and sent nothing.

**How to apply:** `_save_signals()` only checks `should_push_telegram()` to build the
eligible list. `record_push()` is called in the `if notify:` block only.

## Two-stage action_status
- Stage 1: `compute_entry_plan(strategy, df, details)` → price levels only (entry zone, trigger, stop, RR, risk)
- Stage 2: `compute_trade_readiness(ep, close_price, vol_ratio, score, strategy)` → sets action_status
- Scanner always calls both, passing `diag.get("volume_ratio")` and `scores["total_score"]`

## Cooldown check
- Based on `strategy_results` table (signal_date < today)
- MA60=15d, Strong=10d, NewHigh=20d (config.COOLDOWN_DAYS)
- Cooldown → DB saved, no Telegram

## Telegram eligibility chain
1. action_status == "可关注买点"
2. not in cooldown (strategy_results check)
3. not in paper_holdings
4. should_push_telegram (signal_push_log check)
5. Only then: append to telegram_eligible + record_push after send

## Tables added in V1.2
- `signal_push_log(symbol, strategy_name, signal_date, action_status, total_score, pushed_at)` UNIQUE on (symbol,strategy,date)
- `paper_holdings(symbol, added_date, notes)` UNIQUE on symbol
