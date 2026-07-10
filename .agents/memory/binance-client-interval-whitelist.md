---
name: BinancePublicClient interval whitelist
description: The shared Binance public REST client only accepts a hardcoded set of kline intervals; new timeframes must be added in two places or fail silently.
---

`historical_klines()` in `infrastructure/binance_public.py` validates the `interval` argument against a hardcoded set, and separately `_validate_klines()` looks up the interval's millisecond duration in its own hardcoded dict.

**Why:** when a new strategy layer needs a timeframe the client didn't previously support (e.g. daily `1d` candles for a multi-timeframe reversal detector), every single call raises `ValueError: Unsupported interval` inside a broad `try/except` in the strategy's per-symbol evaluation loop. This doesn't crash the task — it just makes every symbol fail at that layer, so the strategy scans 0 candidates on every run, forever, with no obvious top-level error (only per-symbol warnings buried in logs).

**How to apply:** when adding a strategy that uses a new kline interval, grep `infrastructure/binance_public.py` for the interval whitelist set AND the `interval_ms` dict in `_validate_klines` — both need the new interval added, not just one. Then sanity-check by looking at deployment logs for `evaluate failed for ... Unsupported interval` after first deploy, not just for silence/zero-candidate reports.
