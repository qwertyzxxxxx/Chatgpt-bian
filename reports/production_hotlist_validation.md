# Hotlist Unique Order Audit — Local Sample (June 18-23)

**Generated**: 2026-07-03T09:21:25+00:00
**DB**: `data/market_data.db` (read-only)
**Sample Period**: 2026-06-18 → 2026-06-23 (380 rows)
**Settlement**: 24h expiry · 15m klines · TP1/SL/Timeout · no TP2

> Note: This covers the local 6-day sample. Production DB has ~2,690 rows (June 18 → July 3).

---

## Table Counts

| Table | Count |
|---|---:|
| hotlist_opportunities | 380 |
| hotlist_alerts | 81 |
| hotlist_outcomes | 985 |
| strategy_results (hotlist) | 370 |

---

## Deduplication

| | Count |
|---|---:|
| Original Opportunities | 380 |
| Unique Orders | 273 |
| Duplicate Rate | 28.2% |

---

## Settlement Results

| Metric | Value |
|---|---:|
| Filled | 222 |
| Fill Rate | 81.3% |
| Expired (not filled) | 51 |
| Timeout | 11 |
| TP1 | 121 |
| SL | 90 |
| Settled | 211 |
| **Win Rate** | **57.3%** |
| Avg PnL | +1.31% |
| Avg Wait (min) | 247.0 |
| Avg Hold (min) | 234.2 |
| LONG WR | 56.3% (94/167) |
| SHORT WR | 61.4% (27/44) |

---

## Top 30 Most Duplicated

| Rank | Symbol_Direction | Count |
|---|---|---:|
| 1 | CLOUSDT_SHORT | 3 |
| 2 | SYNUSDT_LONG | 2 |
| 3 | BSBUSDT_SHORT | 2 |
| 4 | BLESSUSDT_LONG | 2 |
| 5 | UBUSDT_LONG | 2 |
| 6 | DRIFTUSDT_LONG | 2 |
| 7 | NAORISUSDT_LONG | 2 |
| 8 | LAYERUSDT_LONG | 2 |
| 9 | FOLKSUSDT_LONG | 1 |
| 10 | GUAUSDT_LONG | 1 |
| 11 | ZEREBROUSDT_LONG | 1 |
| 12 | HUSDT_LONG | 1 |
| 13 | BTWUSDT_LONG | 1 |
| 14 | ESPORTSUSDT_SHORT | 1 |
| 15 | VELVETUSDT_LONG | 1 |
| 16 | HEIUSDT_LONG | 1 |
| 17 | REUSDT_LONG | 1 |
| 18 | GUAUSDT_SHORT | 1 |
| 19 | BELUSDT_LONG | 1 |
| 20 | UBUSDT_SHORT | 1 |
| 21 | BICOUSDT_LONG | 1 |
| 22 | RIFUSDT_LONG | 1 |
| 23 | SLXUSDT_LONG | 1 |
| 24 | ALICEUSDT_LONG | 1 |
| 25 | TNSRUSDT_LONG | 1 |
| 26 | BTRUSDT_LONG | 1 |
| 27 | IDOLUSDT_SHORT | 1 |
| 28 | LABUSDT_LONG | 1 |
| 29 | BULLAUSDT_LONG | 1 |
| 30 | BTWUSDT_SHORT | 1 |
