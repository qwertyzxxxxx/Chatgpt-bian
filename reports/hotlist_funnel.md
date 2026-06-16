# Hotlist Funnel Diagnostic

- **Generated at (UTC):** 2026-06-16T13:33:23+00:00
- **Research only:** True

## Parameters

| Parameter | Value |
| --- | --- |
| `min_move_pct` | 15 |
| `min_quote_volume` | 5000000 |
| `min_rr` | 2 |
| `max_stop_pct` | 5 |

## Funnel

| # | Step | Count | Dropped | Drop-off |
| ---: | --- | ---: | ---: | ---: |
| 1 | `universe_total` | **792** | 0 | 0.0% |
| 2 | `usdt_perpetual` | **527** | 265 | 33.5% |
| 3 | `after_exclusions` | **523** | 4 | 0.8% |
| 4 | `move_ge_min_move` | **21** | 502 | 96.0% |
| 5 | `volume_ge_min_quote_volume` | **21** | 0 | 0.0% |
| 6 | `gainers` | **13** | 8 | 38.1% |
| 7 | `losers` | **8** | 13 | 61.9% |
| 8 | `watchlist_active` | **13** | 8 | 38.1% |
| 9 | `review_candidates` | **13** | 0 | 0.0% |
| 10 | `rr_pass` | **13** | 0 | 0.0% |
| 11 | `stop_pass` | **7** | 6 | 46.2% |
| 12 | `final_opportunities` | **3** | 4 | 57.1% |

## Top Rejection Reasons

| Reason | Count |
| --- | ---: |
| `stop_too_wide` | 6 |
| `low_move` | 4 |

## Top 10 Rejected Symbols

| Symbol | Reason | Detail |
| --- | --- | --- |
| `EVAAUSDT` | `stop_too_wide` | stop_pct=5.0% > 5% |
| `VELVETUSDT` | `stop_too_wide` | stop_pct=17.0% > 5% |
| `BEATUSDT` | `stop_too_wide` | stop_pct=16.5% > 5% |
| `COAIUSDT` | `stop_too_wide` | stop_pct=8.0% > 5% |
| `HUSDT` | `stop_too_wide` | stop_pct=13.6% > 5% |
| `SIRENUSDT` | `stop_too_wide` | stop_pct=13.8% > 5% |
| `0GUSDT` | `low_move` | change=-5.3% < 15% |
| `1000000BOBUSDT` | `low_move` | change=-3.9% < 15% |
| `1000000MOGUSDT` | `low_move` | change=-1.1% < 15% |
| `1000BONKUSDT` | `low_move` | change=-6.7% < 15% |

## Final Opportunities

### `CLOUSDT`

| Field | Value |
| --- | --- |
| Direction | LONG |
| Entry | 0.15032464 |
| Stop Loss | 0.1438 |
| TP1 | 0.15684929 |
| TP2 | 0.16337393 |
| RR | 2.00 |

### `UAIUSDT`

| Field | Value |
| --- | --- |
| Direction | LONG |
| Entry | 0.30012321 |
| Stop Loss | 0.28981607 |
| TP1 | 0.31043036 |
| TP2 | 0.3207375 |
| RR | 2.00 |

### `TSTUSDT`

| Field | Value |
| --- | --- |
| Direction | SHORT |
| Entry | 0.01166147 |
| Stop Loss | 0.01212 |
| TP1 | 0.01120293 |
| TP2 | 0.0107444 |
| RR | 2.00 |


> Research only. No live trading is performed.
