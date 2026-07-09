---
name: Same-candle TP1/SL ambiguity in V3 settlement
description: 15m OHLC alone can't tell which of TP1/SL was hit first when a candle's range covers both
---

`V3Settler._settle_one()` checks 15m kline high/low against TP1 and SL. When a single 15m
candle's range satisfies both conditions (e.g. price wicked down through SL then back up through
TP1, or vice versa), checking "TP1 first" unconditionally can misreport the outcome vs. what
actually happened on the exchange in real time.

**Why:** user reported a live position stopped out earlier than the paper-trading report showed
(paper reported TP1, real account hit SL) — root cause was this same-candle ambiguity, not a
dedup/settlement-ordering bug elsewhere.

**How to apply:** when both TP1 and SL conditions are true within one 15m candle, disambiguate by
fetching 1m klines for that exact candle window (`BinancePublicClient.klines` now supports
`start_time_ms`/`end_time_ms` and interval `"1m"`) and walk them chronologically for the true first
touch. Fall back to SL (conservative) if 1m data is unavailable or still ambiguous.
